"""
User configuration: the config.json store, plus favourites and the disabled
category set.

``Config`` behaves like a dict (get / [] / in / del) for the raw settings, and
adds typed helpers for the disabled-category set and the favourites map.
"""

import json

_FAV_KEYS = ("name", "logo", "url", "grp", "tvg_id", "stream_id", "is_live")


class Config:
    def __init__(self, path):
        self.path = path
        self.data = {"disabled_groups": []}
        self.disabled_groups = set()
        self.favourites = {}  # url -> minimal channel record, insertion-ordered

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

    def save(self):
        self.data["disabled_groups"] = sorted(g for g in self.disabled_groups if g and g.strip())
        self.data["favourites"] = list(self.favourites.values())
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
