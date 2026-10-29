"""
User configuration: the config.json store, plus favourites, favourited series,
watch history, and the disabled category set.

``Config`` behaves like a dict (get / [] / in / del) for the raw settings, and
adds typed helpers for the disabled-category set, favourites, favourited series,
and the recently-watched history.
"""

import json
import threading
import time

_FAV_KEYS = ("name", "logo", "url", "grp", "tvg_id", "stream_id", "is_live")
_HISTORY_KEYS = ("name", "logo", "url", "grp", "tvg_id", "stream_id", "is_live",
                 "series", "season", "episode")
_HISTORY_CAP = 200
_POSITIONS_CAP = 500


def _series_key(name, grp):
    return f"{name}\x00{grp or ''}"


class Config:
    def __init__(self, path):
        self.path = path
        self.data = {"disabled_groups": []}
        self.disabled_groups = set()
        self.favourites = {}        # url -> minimal channel record, insertion-ordered
        self.favourite_series = {}  # "name\x00grp" -> {name, grp, logo, ep_count}
        self.history = []           # most-recent-first list of played records
        self.positions = {}         # url -> {pos, length, at}, for resume
        self.track_prefs = {}       # "name\x00grp" -> {audio, sub}, carried across episodes
        self._save_lock = threading.Lock()  # position saves arrive from a poller thread

    # dict-like access to the raw settings ---------------------------------- #
    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def __contains__(self, key):
        return key in self.data

    # load / save ----------------------------------------------------------- #
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except FileNotFoundError:
            return
        except Exception as exc:
            print(f"Error loading config: {exc}")
            return
        self.disabled_groups = {g for g in self.data.get("disabled_groups", [])
                                if g and g.strip()}
        self.favourites = {}
        for rec in self.data.get("favourites", []):
            if isinstance(rec, dict) and rec.get("url"):
                self.favourites[rec["url"]] = rec
        self.favourite_series = {}
        for rec in self.data.get("favourite_series", []):
            if isinstance(rec, dict) and rec.get("name"):
                self.favourite_series[_series_key(rec["name"], rec.get("grp", ""))] = rec
        self.history = [r for r in self.data.get("history", [])
                        if isinstance(r, dict) and r.get("url")]
        self.positions = {u: p for u, p in (self.data.get("positions") or {}).items()
                          if isinstance(p, dict) and "pos" in p}
        self.track_prefs = {k: v for k, v in (self.data.get("track_prefs") or {}).items()
                            if isinstance(v, dict)}

    def save(self):
        with self._save_lock:
            self.data["disabled_groups"] = sorted(g for g in self.disabled_groups if g and g.strip())
            self.data["favourites"] = list(self.favourites.values())
            self.data["favourite_series"] = list(self.favourite_series.values())
            self.data["history"] = self.history
            self.data["positions"] = self.positions
            self.data["track_prefs"] = self.track_prefs
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2)
            except Exception as exc:
                print(f"Error saving config: {exc}")

    # favourites ------------------------------------------------------------ #
    def is_favourite(self, url):
        return url in self.favourites

    def toggle_favourite(self, channel):
        """Add/remove a channel from favourites; persist; return the new state."""
        url = channel.get("url")
        if not url:
            return False
        if url in self.favourites:
            del self.favourites[url]
            state = False
        else:
            self.favourites[url] = {k: channel.get(k) for k in _FAV_KEYS}
            state = True
        self.save()
        return state

    # favourited series ----------------------------------------------------- #
    def is_favourite_series(self, name, grp=""):
        return _series_key(name, grp) in self.favourite_series

    def toggle_favourite_series(self, series):
        """Add/remove a whole series from favourites; persist; return new state.

        ``series`` is ``{name, grp, logo, ep_count}`` (name is the series name).
        """
        name = series.get("name")
        if not name:
            return False
        key = _series_key(name, series.get("grp", ""))
        if key in self.favourite_series:
            del self.favourite_series[key]
            state = False
        else:
            self.favourite_series[key] = {
                "name": name, "grp": series.get("grp", ""),
                "logo": series.get("logo", ""), "ep_count": series.get("ep_count", 0)}
            state = True
        self.save()
        return state

    # watch history --------------------------------------------------------- #
    def add_history(self, channel):
        """Record a play at the front of the history, deduped by url."""
        url = channel.get("url")
        if not url:
            return
        rec = {k: channel.get(k) for k in _HISTORY_KEYS}
        rec["at"] = int(time.time())
        self.history = [r for r in self.history if r.get("url") != url]
        self.history.insert(0, rec)
        del self.history[_HISTORY_CAP:]
        self.save()

    def history_items(self):
        return list(self.history)

    def clear_history(self):
        self.history = []
        self.save()

    def remove_history(self, url):
        self.history = [r for r in self.history if r.get("url") != url]
        self.save()

    # resume positions ------------------------------------------------------ #
    def get_position(self, url):
        return self.positions.get(url)

    def save_position(self, url, pos, length=0):
        """Remember how far into ``url`` playback reached, for resume."""
        if not url or not pos:
            return
        self.positions[url] = {"pos": int(pos), "length": int(length or 0),
                               "at": int(time.time())}
        if len(self.positions) > _POSITIONS_CAP:
            for old in sorted(self.positions,
                              key=lambda u: self.positions[u].get("at", 0))[:-_POSITIONS_CAP]:
                self.positions.pop(old, None)
        self.save()

    def clear_position(self, url):
        if self.positions.pop(url, None) is not None:
            self.save()

    # audio / subtitle choices per series ----------------------------------- #
    def get_track_prefs(self, series, grp=""):
        return dict(self.track_prefs.get(_series_key(series, grp)) or {})

    def save_track_prefs(self, series, grp, prefs):
        if not series:
            return
        self.track_prefs[_series_key(series, grp)] = prefs
        self.save()
