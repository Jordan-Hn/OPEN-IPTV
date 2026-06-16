"""
Channel-logo loading with on-disk and in-memory caching.

The original app called ``requests.get`` on the UI thread for every channel,
which froze the interface for minutes on large groups. Here the network work is
designed to run on background worker threads (the caller schedules it); results
are cached on disk (keyed by URL) and in a small in-memory LRU so repeated views
are instant.

Returns ``PIL.Image`` objects. ``ImageTk.PhotoImage`` must be created on the
Tk main thread, so that final step is left to the caller.

stdlib + Pillow only.
"""

from __future__ import annotations

import hashlib
import os
import threading
from io import BytesIO

from PIL import Image

import net

try:  # Pillow >= 9.1
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - older Pillow
    _RESAMPLE = Image.LANCZOS


class ImageStore:
    def __init__(self, cache_dir: str, timeout: int = 6, mem_cap: int = 800):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.timeout = timeout
        self.mem_cap = mem_cap
        self._mem: dict[str, Image.Image] = {}
        self._order: list[str] = []
        self._failed: set[str] = set()  # URLs that errored, skip re-fetching this session
        self._lock = threading.Lock()

    def _disk_path(self, url: str) -> str:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest)

    def _key(self, url: str, size) -> str:
        return f"{url}@{size[0]}x{size[1]}"

    def cached(self, url: str, size=(60, 60)):
        """Return an already-decoded image without any I/O, or ``None``."""
        with self._lock:
            return self._mem.get(self._key(url, size))

    def _remember(self, key: str, img: Image.Image):
        with self._lock:
            if key in self._mem:
                return
            self._mem[key] = img
            self._order.append(key)
            while len(self._order) > self.mem_cap:
                self._mem.pop(self._order.pop(0), None)

    def get(self, url: str, size=(60, 60)):
        """Fetch + decode + resize a logo (blocking). Safe on worker threads.

        Returns a ``PIL.Image`` or ``None`` on any failure.
        """
        if not url:
            return None
        key = self._key(url, size)
        hit = self.cached(url, size)
        if hit is not None:
            return hit
        with self._lock:
            if url in self._failed:
                return None  # known-bad host: don't waste a worker on another timeout

        data = None
        path = self._disk_path(url)
        try:
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    data = fh.read()
        except OSError:
            data = None

        if data is None:
            try:
                with net.open_url(url, timeout=self.timeout, user_agent="Mozilla/5.0") as resp:
                    data = resp.read()
                try:
                    with open(path, "wb") as fh:
                        fh.write(data)
                except OSError:
                    pass
            except Exception:
                with self._lock:
                    self._failed.add(url)
                return None

        try:
            img = Image.open(BytesIO(data)).convert("RGBA").resize(size, _RESAMPLE)
        except Exception:
            with self._lock:
                self._failed.add(url)
            return None
        self._remember(key, img)
        return img
