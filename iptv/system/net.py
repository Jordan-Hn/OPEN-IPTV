"""
Shared HTTP helper.

Centralises the user agent and timeout used across the app, and adds a small
security guard: only http and https URLs are opened. Playlists and guides supply
logo and stream URLs, so without this a crafted entry like ``file:///etc/passwd``
could be handed to ``urlopen`` and read a local file. The guard refuses anything
that isn't a web URL.

stdlib only.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# A media-player user agent. Some IPTV panels answer 406 to browser-like agents.
USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"


def is_web_url(url) -> bool:
    """True only for http / https URLs."""
    try:
        return urlsplit(url).scheme in ("http", "https")
    except Exception:
        return False


def open_url(url, timeout=30, user_agent=USER_AGENT):
    """Open an http/https URL with a standard user agent.

    Raises ``ValueError`` for any other scheme so untrusted playlist data can't
    reach local files or unexpected protocols.
    """
    if not is_web_url(url):
        raise ValueError(f"refusing non-web URL: {str(url)[:60]}")
    return urlopen(Request(url, headers={"User-Agent": user_agent}), timeout=timeout)
