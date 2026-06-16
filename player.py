"""
Media-player discovery and launching.

The app does not bundle a player; it launches the user's installed one (VLC by
default). ``Playback`` finds the player, builds the stream URL, and opens it.
"""

import os
import shutil
import subprocess
import webbrowser
from urllib.parse import urlsplit


class Playback:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self._proc = None
        self.player_path = self.find_player()

    def find_player(self):
        """Locate a media player. A ``player_path`` in config wins, otherwise fall
        back to a local VLC, VLC on PATH, then the standard install locations."""
        custom = self.config.get("player_path")
        if custom and os.path.isfile(custom):
            return custom
        local = os.path.join(self.base_dir, "VLC", "vlc.exe")
        if os.path.isfile(local):
            return local
        if shutil.which("vlc"):
            return shutil.which("vlc")
        for path in (
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            "/usr/bin/vlc",
            "/Applications/VLC.app/Contents/MacOS/VLC",
        ):
            if os.path.isfile(path):
                return path
        return None

    def refresh(self):
        """Re-detect the player (after the user changes settings)."""
        self.player_path = self.find_player()
        return self.player_path

    def stream_url(self, channel):
        """Build the URL to hand the player.

        For live channels the canonical Xtream form is
        ``host/live/user/pass/id.ts``. A bare ``host/user/pass/id`` URL has no
        output format, which some panels answer with HTTP 406, so we rebuild it.
        VOD URLs already carry an extension and are left untouched. Set
        ``use_live_url`` false to revert.
        """
        url = channel.get("url", "")
        if not (self.config.get("use_live_url", True)
                and channel.get("is_live") and channel.get("stream_id")):
            return url
        try:
            u = urlsplit(url)
            segs = [s for s in u.path.split("/") if s]
            if len(segs) == 3 and segs[0] not in ("movie", "series"):
                ext = self.config.get("live_format", "ts")
                return f"{u.scheme}://{u.netloc}/live/{segs[0]}/{segs[1]}/{segs[2]}.{ext}"
        except Exception:
            pass
        return url

    def play(self, channel):
        from tkinter import messagebox  # imported lazily so the module stays GUI-free
        url = self.stream_url(channel)
        if not self.player_path:
            if messagebox.askyesno(
                "Media player needed",
                "No media player was found to open this stream.\n\n"
                "VLC is a free, open-source player that works well for IPTV. "
                "Install it, then reopen the app.\n\n"
                "Open the VLC download page now?",
            ):
                webbrowser.open("https://www.videolan.org/vlc/")
            return

        # Most IPTV plans allow only one stream at a time. A player left open
        # holds that single connection, so the next channel gets refused. Close
        # the previous one first unless the user opts out.
        if self.config.get("single_stream", True) and self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

        is_vlc = "vlc" in os.path.basename(self.player_path).lower()
        custom_args = self.config.get("player_args")
        if custom_args:
            cmd = [self.player_path] + [a.replace("{url}", url) for a in custom_args]
        else:
            cmd = [self.player_path]
            # Only set a user agent if asked to. Forcing one (e.g. "Mozilla/5.0")
            # makes some panels answer 406; VLC's native handling is usually best.
            user_agent = self.config.get("user_agent")
            if is_vlc and user_agent:
                cmd.append(f"--http-user-agent={user_agent}")
            cmd.append(url)
        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as exc:
            messagebox.showerror("Playback error", f"Could not start the player:\n{exc}")
