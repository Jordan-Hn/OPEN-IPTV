"""
SQLite-backed channel catalog for M3U playlists.

The playlist is streamed once into an indexed SQLite database and groups /
channels / search are served lazily and paginated, so memory use stays small and
start-up after the first build is effectively instant. This keeps the app
responsive on playlists of any size, including very large ones.

No third-party dependencies (stdlib only).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from urllib.parse import urlencode, urlsplit

import net

# Bump when the parsing logic or schema changes so existing DBs are rebuilt.
SCHEMA_VERSION = 2

_ATTR_RE = {
    "tvg-name": re.compile(r'tvg-name="([^"]*)"'),
    "tvg-logo": re.compile(r'tvg-logo="([^"]*)"'),
    "tvg-id": re.compile(r'tvg-id="([^"]*)"'),
    "group-title": re.compile(r'group-title="([^"]*)"'),
}


def _attr(line: str, name: str) -> str:
    m = _ATTR_RE[name].search(line)
    return m.group(1).strip() if m else ""


def classify_url(url: str):
    """Return (is_live, stream_id) for an Xtream-style URL.

    Live channels look like ``http://host:port/user/pass/<digits>`` while VOD
    looks like ``.../movie/...`` or ``.../series/...`` with a file extension.
    Only live channels have a stream id usable for EPG lookups.
    """
    try:
        path = urlsplit(url).path
    except Exception:
        return False, None
    segs = [s for s in path.split("/") if s]
    if segs and segs[0] not in ("movie", "series") and segs[-1].isdigit():
        return True, segs[-1]
    return False, None


def parse_provider(url: str):
    """Extract Xtream provider credentials from a live stream URL."""
    try:
        u = urlsplit(url)
    except Exception:
        return None
    segs = [s for s in u.path.split("/") if s]
    if len(segs) >= 3 and segs[0] not in ("movie", "series"):
        return {
            "base": f"{u.scheme}://{u.netloc}",
            "username": segs[0],
            "password": segs[1],
        }
    return None


def provider_from_m3u(m3u_path: str, max_lines: int = 400000):
    """Scan a playlist for the first live URL to recover provider credentials.

    The playlist itself may be stale (dead stream ids), but the host / user /
    password stay valid, and that's all we need to query the provider's player API.
    """
    try:
        with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(max_lines):
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("http"):
                    prov = parse_provider(line)
                    if prov:
                        return prov
    except OSError:
        pass
    return None


def parse_extinf(extinf: str, url: str):
    """Replicate the original parsing rules, plus tvg-id / stream classification.

    Returns a tuple matching the ``channels`` columns, or ``None`` to skip.
    """
    url = url.strip()
    if not url:
        return None

    name = _attr(extinf, "tvg-name")
    if not name:
        if "," in extinf:
            name = extinf.split(",", 1)[1].strip()
        else:
            name = "Unknown"
    name = name.strip()
    if not name:
        return None

    logo = _attr(extinf, "tvg-logo")
    tvg_id = _attr(extinf, "tvg-id")
    group = _attr(extinf, "group-title") or "Ungrouped"
    if not group.strip():
        group = "Ungrouped"

    is_live, stream_id = classify_url(url)
    return (group, name, logo, url, tvg_id, stream_id, 1 if is_live else 0)


def fingerprint(m3u_path: str) -> str:
    st = os.stat(m3u_path)
    return f"{st.st_size}:{int(st.st_mtime)}:{SCHEMA_VERSION}"


def _like_escape(keyword: str) -> str:
    """Escape LIKE wildcards so the keyword matches as a literal substring."""
    return (
        keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS groups (grp TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS channels (
    id        INTEGER PRIMARY KEY,
    grp       TEXT NOT NULL,
    name      TEXT NOT NULL,
    logo      TEXT,
    url       TEXT NOT NULL,
    tvg_id    TEXT,
    stream_id TEXT,
    is_live   INTEGER NOT NULL DEFAULT 0
);
"""


class Catalog:
    """Lazy, paginated access to a large channel playlist via SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # -- connection helpers ------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # -- building ----------------------------------------------------------
    def build(self, m3u_path: str, progress=None, batch_size: int = 20000,
              source_type: str = "m3u_file", source_key: str = None,
              epg_url_override: str = None):
        """(Re)build the database by streaming the playlist.

        ``progress`` is an optional callable ``progress(records_done)`` invoked
        periodically so a UI can show a progress indicator. ``source_type`` /
        ``source_key`` tag where the playlist came from (e.g. an M3U url) so the
        launcher can decide when to refresh; ``epg_url_override`` forces a guide URL.
        Returns the detected provider dict (or ``None``).
        """
        tmp_path = self.db_path + ".building"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(tmp_path + suffix)
            except OSError:
                pass

        conn = sqlite3.connect(tmp_path)
        # Bulk-load tuning: this DB is a disposable cache, so we favour speed.
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.executescript(_SCHEMA)

        provider = None
        epg_url = ""
        group_counts: dict[str, int] = {}
        batch: list[tuple] = []
        done = 0
        insert = (
            "INSERT INTO channels(grp,name,logo,url,tvg_id,stream_id,is_live)"
            " VALUES(?,?,?,?,?,?,?)"
        )

        with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
            extinf = None
            for line in f:
                if line.startswith("#EXTM3U"):
                    m = re.search(r'(?:url-tvg|x-tvg-url)="([^"]+)"', line)
                    if m:
                        epg_url = m.group(1).split(",")[0].strip()
                    continue
                if line.startswith("#EXTINF"):
                    extinf = line
                elif extinf is not None:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        # Not a URL line; keep the pending EXTINF only if blank,
                        # otherwise drop it (mirrors lenient original behaviour).
                        if stripped.startswith("#"):
                            extinf = None
                        continue
                    rec = parse_extinf(extinf, stripped)
                    extinf = None
                    if rec is None:
                        continue
                    if provider is None and rec[6]:  # is_live
                        provider = parse_provider(rec[3])
                    batch.append(rec)
                    group_counts[rec[0]] = group_counts.get(rec[0], 0) + 1
                    done += 1
                    if len(batch) >= batch_size:
                        conn.executemany(insert, batch)
                        batch.clear()
                        if progress:
                            progress(done)

        if batch:
            conn.executemany(insert, batch)
        if progress:
            progress(done)

        conn.executemany(
            "INSERT INTO groups(grp,n) VALUES(?,?)", list(group_counts.items())
        )

        # Indexes are created *after* bulk insert for speed. The tvg-id index is
        # partial (only the ~thousands of live channels carry one) so it stays tiny.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_grp ON channels(grp, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_tvg "
                     "ON channels(tvg_id) WHERE tvg_id <> ''")

        if epg_url_override:
            epg_url = epg_url_override.strip()
        meta = {
            "fingerprint": fingerprint(m3u_path),
            "built_at": str(int(time.time())),
            "total": str(done),
            "source_type": source_type,
            "source_key": source_key or fingerprint(m3u_path),
            "epg_url": epg_url,
        }
        if provider:
            meta["provider_base"] = provider["base"]
            meta["provider_username"] = provider["username"]
            meta["provider_password"] = provider["password"]
            # Xtream panels serve their guide here even when the M3U omits url-tvg.
            if not epg_url:
                meta["epg_url"] = (f"{provider['base']}/xmltv.php?"
                                   f"username={provider['username']}&password={provider['password']}")
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", list(meta.items())
        )
        conn.commit()
        conn.close()
        self._replace_db(tmp_path)  # atomically swap the freshly built DB into place
        return provider

    def _replace_db(self, tmp_path):
        """Swap a freshly built DB into place.

        Prefers a fast atomic rename, but on Windows a virus scanner can briefly
        hold the new file, so we fall back to SQLite's own backup copy (which
        doesn't need an OS-level rename) before giving up.
        """
        import gc
        gc.collect()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass
        for _ in range(6):
            try:
                os.replace(tmp_path, self.db_path)
                return
            except PermissionError:
                time.sleep(0.3)
        # Fallback: copy the contents through SQLite, then drop the temp file.
        src = sqlite3.connect(tmp_path)
        try:
            dst = sqlite3.connect(self.db_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        for _ in range(10):
            try:
                os.remove(tmp_path)
                break
            except OSError:
                time.sleep(0.3)

    # -- build from the live Xtream player API -----------------------------
    def build_from_api(self, base, username, password, progress=None, status=None,
                       include_vod=True):
        """Build the catalog from the provider's player API instead of the M3U.

        The M3U can be months old, and panels reassign stream ids, so its ids go
        dead (HTTP 406). The player API always returns current ids and the provider's
        own category names and EPG ids. Returns the number of channels indexed.

        ``status`` is an optional ``status(message)`` callback so a UI can show
        which phase is running; the channel lists download in one big response
        each, so without it those seconds look frozen.
        """
        base = base.rstrip("/")

        def say(msg):
            if status:
                status(msg)

        def api(action, on_bytes=None):
            url = f"{base}/player_api.php?" + urlencode(
                {"username": username, "password": password, "action": action})
            last = None
            for _ in range(4):
                try:
                    with net.open_url(url, timeout=90) as resp:
                        buf = bytearray()
                        while True:
                            chunk = resp.read(1_000_000)
                            if not chunk:
                                break
                            buf += chunk
                            if on_bytes:
                                on_bytes(len(buf))
                        return json.loads(bytes(buf).decode("utf-8", "replace"))
                except Exception as exc:  # transient provider/network hiccups
                    last = exc
                    time.sleep(2)
            raise last or RuntimeError(f"API call failed: {action}")

        def cats(action):
            try:
                data = api(action) or []
            except Exception:
                data = []
            return {str(c.get("category_id")): (c.get("category_name") or "").strip()
                    for c in data if isinstance(c, dict)}

        tmp_path = self.db_path + ".building"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(tmp_path + suffix)
            except OSError:
                pass
        conn = sqlite3.connect(tmp_path)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(_SCHEMA)
        cur = conn.cursor()
        insert = ("INSERT INTO channels(grp,name,logo,url,tvg_id,stream_id,is_live)"
                  " VALUES(?,?,?,?,?,?,?)")
        group_counts: dict[str, int] = {}
        batch: list[tuple] = []
        done = 0

        def add(grp, name, logo, url, tvg, sid, is_live):
            nonlocal done
            batch.append((grp or "Ungrouped", name, logo or "", url, tvg or "",
                          str(sid), is_live))
            group_counts[grp or "Ungrouped"] = group_counts.get(grp or "Ungrouped", 0) + 1
            done += 1
            if len(batch) >= 5000:
                cur.executemany(insert, batch)
                batch.clear()
                if progress:
                    progress(done)

        # Live channels (the urgent part).
        say("Connecting to your provider…")
        live_cats = cats("get_live_categories")
        say("Downloading live channels…")
        live = api("get_live_streams",
                   on_bytes=lambda n: say(f"Downloading live channels… {n // 1_000_000} MB")) or []
        say("Indexing live channels…")
        for s in live:
            sid = s.get("stream_id")
            name = (s.get("name") or "").strip()
            if sid is None or not name:
                continue
            grp = live_cats.get(str(s.get("category_id")), "Live")
            url = f"{base}/live/{username}/{password}/{sid}.ts"
            add(grp, name, s.get("stream_icon"), url, s.get("epg_channel_id"), sid, 1)

        # VOD movies (current ids, so they also actually play).
        if include_vod:
            vod_cats = cats("get_vod_categories")
            say("Downloading movies… this is the big one")
            try:
                vod = api("get_vod_streams",
                          on_bytes=lambda n: say(f"Downloading movies… {n // 1_000_000} MB")) or []
            except Exception:
                vod = []
            say("Indexing movies…")
            for s in vod:
                sid = s.get("stream_id")
                name = (s.get("name") or "").strip()
                if sid is None or not name:
                    continue
                grp = vod_cats.get(str(s.get("category_id")), "Movies")
                ext = s.get("container_extension") or "mp4"
                url = f"{base}/movie/{username}/{password}/{sid}.{ext}"
                add(grp, name, s.get("stream_icon"), url, "", sid, 0)

        if batch:
            cur.executemany(insert, batch)
        conn.executemany("INSERT INTO groups(grp,n) VALUES(?,?)", list(group_counts.items()))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_grp ON channels(grp, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_tvg "
                     "ON channels(tvg_id) WHERE tvg_id <> ''")
        meta = {
            "source": "api",
            "source_type": "xtream",
            "source_key": f"{base}|{username}",
            "epg_url": f"{base}/xmltv.php?username={username}&password={password}",
            "built_at": str(int(time.time())),
            "total": str(done),
            "provider_base": base,
            "provider_username": username,
            "provider_password": password,
        }
        conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", list(meta.items()))
        conn.commit()
        conn.close()
        self._replace_db(tmp_path)
        if progress:
            progress(done)
        return done

    # -- queries -----------------------------------------------------------
    def provider(self):
        try:
            conn = self._connect()
        except sqlite3.Error:
            return None
        try:
            rows = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT key,value FROM meta WHERE key LIKE 'provider_%'"
                )
            }
        finally:
            conn.close()
        if "provider_base" in rows:
            return {
                "base": rows["provider_base"],
                "username": rows.get("provider_username", ""),
                "password": rows.get("provider_password", ""),
            }
        return None

    def meta_value(self, key, default=""):
        if not os.path.exists(self.db_path):
            return default
        try:
            conn = self._connect()
        except sqlite3.Error:
            return default
        try:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
        except sqlite3.Error:
            return default
        finally:
            conn.close()

    def epg_url(self):
        """The XMLTV guide URL for the current source (or empty)."""
        return self.meta_value("epg_url", "")

    def source_key(self):
        """Identifier of the source the catalog was built from (url / fingerprint)."""
        return self.meta_value("source_key", "")

    def is_fresh_for(self, source_key, max_age_hours=24):
        """True if the catalog was built from ``source_key`` recently enough.

        Remote sources (xtream / m3u url) are refreshed on age; this lets the
        launcher decide when to rebuild without caring about the source type.
        """
        if not os.path.exists(self.db_path):
            return False
        if self.source_key() != source_key:
            return False
        try:
            return (time.time() - int(self.meta_value("built_at", "0"))) < max_age_hours * 3600
        except ValueError:
            return False

    def total_channels(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='total'").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def live_counts(self):
        """Return ``(live_channels, live_channels_with_a_tvg_id)`` for diagnostics."""
        conn = self._connect()
        try:
            live = conn.execute("SELECT COUNT(*) FROM channels WHERE is_live=1").fetchone()[0]
            tagged = conn.execute(
                "SELECT COUNT(*) FROM channels WHERE is_live=1 AND tvg_id <> ''").fetchone()[0]
            return live, tagged
        finally:
            conn.close()

    def live_tvg_ids(self):
        """Distinct, non-empty tvg-ids carried by live channels."""
        conn = self._connect()
        try:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT tvg_id FROM channels WHERE is_live=1 AND tvg_id <> ''")]
        finally:
            conn.close()

    def groups(self):
        """Return list of (group, channel_count) ordered by group name."""
        conn = self._connect()
        try:
            return [
                (r["grp"], r["n"])
                for r in conn.execute(
                    "SELECT grp, n FROM groups WHERE grp <> '' ORDER BY grp"
                )
            ]
        finally:
            conn.close()

    def group_count(self, group: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT n FROM groups WHERE grp=?", (group,)
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def channels(self, group: str, offset: int = 0, limit: int = 60):
        conn = self._connect()
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id,grp,name,logo,url,tvg_id,stream_id,is_live "
                    "FROM channels WHERE grp=? ORDER BY id LIMIT ? OFFSET ?",
                    (group, limit, offset),
                )
            ]
        finally:
            conn.close()

    def ensure_indexes(self):
        """Add the tvg-id index to a DB that predates it (no rebuild needed)."""
        try:
            conn = self._connect()
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_tvg "
                         "ON channels(tvg_id) WHERE tvg_id <> ''")
            conn.commit()
            conn.close()
        except sqlite3.Error:
            pass

    def live_channels_by_tvg_ids(self, tvg_ids, disabled=None, limit=120):
        """Return live channels for a set of tvg-ids, grouped by tvg-id.

        Used by "what's on" search to map guide hits back to playable channels.
        """
        ids = list(dict.fromkeys(t for t in tvg_ids if t))
        if not ids:
            return {}
        disabled = list(disabled or [])
        params: list = list(ids)
        placeholders = ",".join("?" * len(ids))
        sql = (
            "SELECT id,grp,name,logo,url,tvg_id,stream_id,is_live FROM channels "
            f"WHERE tvg_id IN ({placeholders}) AND tvg_id <> '' AND is_live=1"
        )
        if disabled:
            sql += f" AND grp NOT IN ({','.join('?' * len(disabled))})"
            params.extend(disabled)
        sql += " LIMIT ?"
        params.append(limit)
        conn = self._connect()
        try:
            rows = [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()
        out: dict = {}
        for r in rows:
            out.setdefault(r["tvg_id"], []).append(r)
        return out

    def search(self, keyword: str, disabled=None, offset: int = 0, limit: int = 60):
        """Substring search over channel names, excluding disabled groups.

        Fetches ``limit + 1`` rows so the caller can tell whether more results
        exist without a second COUNT scan.
        """
        disabled = list(disabled or [])
        params: list = [f"%{_like_escape(keyword)}%"]
        sql = (
            "SELECT id,grp,name,logo,url,tvg_id,stream_id,is_live "
            "FROM channels WHERE name LIKE ? ESCAPE '\\'"
        )
        if disabled:
            placeholders = ",".join("?" * len(disabled))
            sql += f" AND grp NOT IN ({placeholders})"
            params.extend(disabled)
        sql += " ORDER BY id LIMIT ? OFFSET ?"
        params.extend([limit + 1, offset])

        conn = self._connect()
        try:
            rows = [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()
        has_more = len(rows) > limit
        return rows[:limit], has_more

    def search_count(self, keyword: str, disabled=None) -> int:
        disabled = list(disabled or [])
        params: list = [f"%{_like_escape(keyword)}%"]
        sql = "SELECT COUNT(*) FROM channels WHERE name LIKE ? ESCAPE '\\'"
        if disabled:
            placeholders = ",".join("?" * len(disabled))
            sql += f" AND grp NOT IN ({placeholders})"
            params.extend(disabled)
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()
