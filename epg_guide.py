"""
Live EPG from the provider's XMLTV guide.

The per-stream Xtream EPG API (``get_short_epg``) is unreliable on many panels;
it often returns nothing even when guide data exists. The full XMLTV feed
(``xmltv.php``) is comprehensive and keyed by ``tvg-id``, so this module
downloads it once, parses it into an indexed SQLite table, and answers
now/next per channel instantly. The guide is refreshed in the background on a
schedule and every operation degrades gracefully if the provider is unreachable.

stdlib only.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import net


def parse_xmltv_time(value: str) -> int:
    """Parse an XMLTV timestamp like '20260615120000 +0000' to a unix time."""
    if not value:
        return 0
    value = value.strip()
    try:
        dt = datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    except (ValueError, IndexError):
        return 0
    rest = value[14:].strip()
    if len(rest) >= 5 and rest[0] in "+-":
        try:
            sign = 1 if rest[0] == "+" else -1
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(rest[1:3]),
                                                             minutes=int(rest[3:5]))))
        except ValueError:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class Programme:
    __slots__ = ("title", "start", "stop", "desc")

    def __init__(self, title, start, stop, desc=""):
        self.title = title
        self.start = start
        self.stop = stop
        self.desc = desc

    def fraction(self, at=None):
        if self.stop <= self.start:
            return 0.0
        at = at if at is not None else time.time()
        return max(0.0, min(1.0, (at - self.start) / (self.stop - self.start)))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS programmes (
    channel_id TEXT NOT NULL,
    start      INTEGER NOT NULL,
    stop       INTEGER NOT NULL,
    title      TEXT,
    descr      TEXT
);
"""


class EpgGuide:
    def __init__(self, db_path, base="", username="", password="", epg_url=None, timeout=30):
        self.db_path = db_path
        self.base = (base or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        # An explicit XMLTV URL wins (e.g. an M3U's url-tvg). Otherwise derive the
        # Xtream guide endpoint from credentials.
        self.epg_url = (epg_url or "").strip() or None
        self.timeout = timeout
        self.enabled = bool(self.epg_url or (self.base and self.username and self.password))
        self._read = None              # reused read connection (guarded by lock)
        self._lock = threading.Lock()
        self._ensure_schema()

    # -- setup -------------------------------------------------------------
    def _ensure_schema(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _meta_int(self, key):
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def programme_count(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM programmes").fetchone()[0]
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def channel_count(self):
        return self._meta_int("channels")

    def last_fetch(self):
        return self._meta_int("last_fetch")

    def is_fresh(self, max_age_hours=12):
        return self.programme_count() > 0 and \
            (time.time() - self.last_fetch()) < max_age_hours * 3600

    # -- refresh -----------------------------------------------------------
    def _url(self):
        if self.epg_url:
            return self.epg_url
        return f"{self.base}/xmltv.php?" + urlencode(
            {"username": self.username, "password": self.password})

    def refresh(self, progress=None):
        """Download + parse the XMLTV guide into the DB. Returns programme count.

        ``progress(phase, value)`` is called periodically with phase
        ``"download"`` (value = bytes) or ``"parse"`` (value = programmes).
        Raises on network/parse failure so the caller can keep any stale guide.
        """
        if not self.enabled:
            return 0
        tmp_xml = self.db_path + ".xmltv"

        # 1) stream the feed to disk (avoid holding ~80 MB in memory)
        downloaded = 0
        with net.open_url(self._url(), timeout=self.timeout) as resp, open(tmp_xml, "wb") as out:
            while True:
                chunk = resp.read(1_000_000)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress("download", downloaded)

        # 2) stream-parse into the DB (one transaction; index built afterwards)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(_SCHEMA)
        conn.execute("DROP INDEX IF EXISTS idx_prog")
        conn.execute("DELETE FROM programmes")
        cur = conn.cursor()
        batch, total, channels = [], 0, set()
        try:
            for _event, elem in ET.iterparse(tmp_xml, events=("end",)):
                if elem.tag != "programme":
                    continue
                ch = elem.get("channel") or ""
                start = parse_xmltv_time(elem.get("start"))
                if ch and start:
                    stop = parse_xmltv_time(elem.get("stop"))
                    title_el = elem.find("title")
                    desc_el = elem.find("desc")
                    batch.append((
                        ch, start, stop,
                        (title_el.text if title_el is not None else "") or "",
                        (desc_el.text if desc_el is not None else "") or "",
                    ))
                    channels.add(ch)
                    total += 1
                    if len(batch) >= 5000:
                        cur.executemany("INSERT INTO programmes VALUES(?,?,?,?,?)", batch)
                        batch.clear()
                        if progress:
                            progress("parse", total)
                elem.clear()
            if batch:
                cur.executemany("INSERT INTO programmes VALUES(?,?,?,?,?)", batch)
            conn.execute("CREATE INDEX idx_prog ON programmes(channel_id, start)")
            conn.executemany(
                "INSERT OR REPLACE INTO meta VALUES(?,?)",
                [("last_fetch", str(int(time.time()))),
                 ("channels", str(len(channels))),
                 ("programmes", str(total))],
            )
            conn.commit()
        finally:
            conn.close()
            try:
                os.remove(tmp_xml)
            except OSError:
                pass
        return total

    # -- queries -----------------------------------------------------------
    def now_next(self, channel_id, at=None):
        """Return ``(current, next)`` Programmes for a tvg-id (either may be None)."""
        if not channel_id:
            return (None, None)
        at = int(at if at is not None else time.time())
        with self._lock:
            try:
                if self._read is None:
                    self._read = sqlite3.connect(self.db_path, check_same_thread=False)
                    self._read.execute("PRAGMA journal_mode=WAL")
                rows = self._read.execute(
                    "SELECT start, stop, title, descr FROM programmes "
                    "WHERE channel_id=? AND stop>? ORDER BY start LIMIT 3",
                    (channel_id, at),
                ).fetchall()
            except sqlite3.Error:
                return (None, None)
        current = nxt = None
        for start, stop, title, descr in rows:
            if start <= at < stop and current is None:
                current = Programme(title, start, stop, descr)
            elif start > at and nxt is None:
                nxt = Programme(title, start, stop, descr)
        return (current, nxt)

    def search_now(self, keyword, at=None, window_hours=12, limit=200):
        """Find programmes whose title matches ``keyword`` and are on now or
        starting within ``window_hours``. Returns (channel_id, start, stop, title)
        rows, currently-airing first. Powers "what's on" search.
        """
        if not keyword:
            return []
        at = int(at if at is not None else time.time())
        horizon = at + window_hours * 3600
        kw = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            try:
                if self._read is None:
                    self._read = sqlite3.connect(self.db_path, check_same_thread=False)
                    self._read.execute("PRAGMA journal_mode=WAL")
                return self._read.execute(
                    "SELECT channel_id, start, stop, title FROM programmes "
                    "WHERE title LIKE ? ESCAPE '\\' AND stop > ? AND start < ? "
                    "ORDER BY CASE WHEN start <= ? THEN 0 ELSE 1 END, start LIMIT ?",
                    (f"%{kw}%", at, horizon, at, limit),
                ).fetchall()
            except sqlite3.Error:
                return []

    def channel_ids(self):
        """Return the set of distinct channel ids present in the guide.

        Used by diagnostics to gauge how well the guide matches the catalog.
        """
        with self._lock:
            try:
                if self._read is None:
                    self._read = sqlite3.connect(self.db_path, check_same_thread=False)
                    self._read.execute("PRAGMA journal_mode=WAL")
                return {r[0] for r in self._read.execute(
                    "SELECT DISTINCT channel_id FROM programmes")}
            except sqlite3.Error:
                return set()

    def close(self):
        with self._lock:
            if self._read is not None:
                try:
                    self._read.close()
                except sqlite3.Error:
                    pass
                self._read = None
