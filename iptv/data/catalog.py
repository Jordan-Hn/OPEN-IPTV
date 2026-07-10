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

from iptv.system import net

# Bump when the parsing logic or schema changes so existing DBs are rebuilt.
SCHEMA_VERSION = 3

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
    series, season, episode = "", 0, 0
    if not is_live:
        parsed = parse_series(name)
        if parsed:
            series, season, episode = parsed
    return (group, name, logo, url, tvg_id, stream_id, 1 if is_live else 0,
            series, season, episode)


def fingerprint(m3u_path: str) -> str:
    st = os.stat(m3u_path)
    return f"{st.st_size}:{int(st.st_mtime)}:{SCHEMA_VERSION}"


def like_escape(keyword: str) -> str:
    """Escape LIKE wildcards so the keyword matches as a literal substring."""
    return (
        keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


# Episode markers: "S01E02", "S1 E2", "Season 1 Episode 2", "1x02".
_EPISODE_RES = (
    re.compile(r"(?i)\bS(\d{1,3})\s*[ ._-]?\s*E(\d{1,3})\b"),
    re.compile(r"(?i)\bSeason\s*(\d{1,3})\s*Episode\s*(\d{1,3})\b"),
    re.compile(r"(?i)(?<![\d.])(\d{1,2})x(\d{1,3})\b"),
)


def parse_series(name: str):
    """If ``name`` looks like a series episode, return ``(series, season,
    episode)``; otherwise ``None``.

    The series name is whatever precedes the episode marker, tidied up. If there
    is nothing before the marker, the entry is treated as standalone (``None``).
    """
    if not name:
        return None
    for rx in _EPISODE_RES:
        m = rx.search(name)
        if not m:
            continue
        series = re.sub(r"\s+", " ", name[:m.start()].strip(" .-_:|()[]")).strip()
        if not series:
            return None
        try:
            return series, int(m.group(1)), int(m.group(2))
        except (TypeError, ValueError):
            return None
    return None


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
    is_live   INTEGER NOT NULL DEFAULT 0,
    series    TEXT NOT NULL DEFAULT '',
    season    INTEGER NOT NULL DEFAULT 0,
    episode   INTEGER NOT NULL DEFAULT 0
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
            "INSERT INTO channels(grp,name,logo,url,tvg_id,stream_id,is_live,series,season,episode)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_series "
                     "ON channels(series, season, episode) WHERE series <> ''")

        if epg_url_override:
            epg_url = epg_url_override.strip()
        meta = {
            "fingerprint": fingerprint(m3u_path),
            "built_at": str(int(time.time())),
            "total": str(done),
            "source_type": source_type,
            "source_key": source_key or fingerprint(m3u_path),
            "epg_url": epg_url,
            "schema_version": str(SCHEMA_VERSION),
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
            for attempt in range(2):
                try:
                    with net.open_url(url, timeout=30) as resp:
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
                    if attempt == 0:
                        time.sleep(1.5)
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
        insert = ("INSERT INTO channels(grp,name,logo,url,tvg_id,stream_id,is_live,series,season,episode)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)")
        group_counts: dict[str, int] = {}
        batch: list[tuple] = []
        done = 0

        def add(grp, name, logo, url, tvg, sid, is_live):
            nonlocal done
            series, season, episode = "", 0, 0
            if not is_live:
                parsed = parse_series(name)
                if parsed:
                    series, season, episode = parsed
            batch.append((grp or "Ungrouped", name, logo or "", url, tvg or "",
                          str(sid), is_live, series, season, episode))
            group_counts[grp or "Ungrouped"] = group_counts.get(grp or "Ungrouped", 0) + 1
            done += 1
            if len(batch) >= 5000:
                cur.executemany(insert, batch)
                batch.clear()
                if progress:
                    progress(done)

        # Live channels (the urgent part). Fetch the stream list first so an
        # unreachable panel fails fast and clearly, rather than after several calls.
        say("Connecting to your provider…")
        live = api("get_live_streams",
                   on_bytes=lambda n: say(f"Downloading live channels… {n // 1_000_000} MB")) or []
        if not isinstance(live, list):
            # A dict or string here is an error / auth response, not a channel list.
            raise RuntimeError(
                "The provider did not return a channel list. Check the server "
                "address and your username and password.")
        live_cats = cats("get_live_categories")
        say("Indexing live channels…")
        for s in live:
            if not isinstance(s, dict):
                continue
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
            if not isinstance(vod, list):
                vod = []
            say("Indexing movies…")
            for s in vod:
                if not isinstance(s, dict):
                    continue
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_series "
                     "ON channels(series, season, episode) WHERE series <> ''")
        meta = {
            "source": "api",
            "schema_version": str(SCHEMA_VERSION),
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
        if not os.path.exists(self.db_path):
            return None  # fresh run, no catalog yet: nothing to migrate
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
        except sqlite3.Error:
            return None  # db exists but has no tables yet (interrupted build)
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
        if not os.path.exists(self.db_path):
            return 0  # don't create an empty db just to count it
        try:
            conn = self._connect()
        except sqlite3.Error:
            return 0
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='total'").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0
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

    def browse_items(self, group, offset=0, limit=60):
        """A page of a group's contents, with series collapsed into one row.

        A series row has ``kind='series'``, ``label`` (series name), ``ep_count``,
        and a representative episode's fields (used for the logo). A standalone
        row has ``kind='item'`` plus the channel's own fields.
        """
        conn = self._connect()
        try:
            try:
                rows = conn.execute(
                    "SELECT 'series' AS kind, series AS label, COUNT(*) AS ep_count, "
                    "MIN(id) AS id, grp, name, logo, url, tvg_id, stream_id, is_live "
                    "FROM channels WHERE grp=? AND series<>'' GROUP BY series "
                    "UNION ALL "
                    "SELECT 'item' AS kind, name AS label, 1 AS ep_count, "
                    "id, grp, name, logo, url, tvg_id, stream_id, is_live "
                    "FROM channels WHERE grp=? AND series='' "
                    "ORDER BY label COLLATE NOCASE LIMIT ? OFFSET ?",
                    (group, group, limit, offset),
                )
            except sqlite3.OperationalError:
                # Older catalog without series columns (mid-upgrade): list flat.
                rows = conn.execute(
                    "SELECT 'item' AS kind, name AS label, 1 AS ep_count, "
                    "id, grp, name, logo, url, tvg_id, stream_id, is_live "
                    "FROM channels WHERE grp=? ORDER BY id LIMIT ? OFFSET ?",
                    (group, limit, offset),
                )
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def browse_count(self, group):
        """Number of rows ``browse_items`` pages through for ``group``."""
        conn = self._connect()
        try:
            try:
                series = conn.execute(
                    "SELECT COUNT(DISTINCT series) FROM channels WHERE grp=? AND series<>''",
                    (group,)).fetchone()[0]
                items = conn.execute(
                    "SELECT COUNT(*) FROM channels WHERE grp=? AND series=''",
                    (group,)).fetchone()[0]
                return series + items
            except sqlite3.OperationalError:
                return conn.execute(
                    "SELECT COUNT(*) FROM channels WHERE grp=?", (group,)).fetchone()[0]
        finally:
            conn.close()

    def series_episodes(self, series_name, disabled=None, group=None):
        """Every episode of a series, ordered by season then episode."""
        disabled = list(disabled or [])
        params: list = [series_name]
        sql = ("SELECT id,grp,name,logo,url,tvg_id,stream_id,is_live,series,season,episode "
               "FROM channels WHERE series=?")
        if group:
            sql += " AND grp=?"
            params.append(group)
        if disabled:
            sql += f" AND grp NOT IN ({','.join('?' * len(disabled))})"
            params.extend(disabled)
        sql += " ORDER BY season, episode, name"
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(sql, params)]
        except sqlite3.OperationalError:
            return []  # older catalog without series columns
        finally:
            conn.close()

    def next_episode(self, series, season, episode, disabled=None, group=None):
        """The episode right after ``(season, episode)`` in a series, or None."""
        if not series:
            return None
        disabled = list(disabled or [])
        params: list = [series, season, season, episode]
        sql = ("SELECT id,grp,name,logo,url,tvg_id,stream_id,is_live,series,season,episode "
               "FROM channels WHERE series=? AND (season>? OR (season=? AND episode>?))")
        if group:
            sql += " AND grp=?"
            params.append(group)
        if disabled:
            sql += f" AND grp NOT IN ({','.join('?' * len(disabled))})"
            params.extend(disabled)
        sql += " ORDER BY season, episode, name LIMIT 1"
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()

    def search_items(self, keyword, disabled=None, offset=0, limit=60):
        """Search with series collapsed. Returns ``(rows, has_more)``.

        Series match on the series name, standalone items on the channel name.
        Row shape matches ``browse_items``.
        """
        disabled = list(disabled or [])
        kw = f"%{like_escape(keyword)}%"
        dis_sql = ""
        dis_params: list = []
        if disabled:
            dis_sql = f" AND grp NOT IN ({','.join('?' * len(disabled))})"
            dis_params = list(disabled)
        sql = (
            "SELECT 'series' AS kind, series AS label, COUNT(*) AS ep_count, "
            "MIN(id) AS id, grp, name, logo, url, tvg_id, stream_id, is_live "
            f"FROM channels WHERE series<>'' AND series LIKE ? ESCAPE '\\'{dis_sql} "
            "GROUP BY series "
            "UNION ALL "
            "SELECT 'item' AS kind, name AS label, 1 AS ep_count, "
            "id, grp, name, logo, url, tvg_id, stream_id, is_live "
            f"FROM channels WHERE series='' AND name LIKE ? ESCAPE '\\'{dis_sql} "
            "ORDER BY label COLLATE NOCASE LIMIT ? OFFSET ?"
        )
        params = [kw] + dis_params + [kw] + dis_params + [limit + 1, offset]
        conn = self._connect()
        try:
            try:
                rows = [dict(r) for r in conn.execute(sql, params)]
            except sqlite3.OperationalError:
                # Older catalog without series columns (mid-upgrade): flat search.
                flat = (
                    "SELECT 'item' AS kind, name AS label, 1 AS ep_count, "
                    "id, grp, name, logo, url, tvg_id, stream_id, is_live "
                    f"FROM channels WHERE name LIKE ? ESCAPE '\\'{dis_sql} "
                    "ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?"
                )
                rows = [dict(r) for r in conn.execute(
                    flat, [kw] + dis_params + [limit + 1, offset])]
        finally:
            conn.close()
        return rows[:limit], len(rows) > limit

    def ensure_indexes(self):
        """Add later indexes to a DB that predates them (no rebuild needed)."""
        try:
            conn = self._connect()
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_tvg "
                         "ON channels(tvg_id) WHERE tvg_id <> ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_series "
                         "ON channels(series, season, episode) WHERE series <> ''")
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

