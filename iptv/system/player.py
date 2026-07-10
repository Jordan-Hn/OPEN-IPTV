"""
Media-player discovery and launching.

The app does not bundle a player; it launches the user's installed one (VLC by
default). ``Playback`` finds the player, builds the stream URL, and opens it.
"""

import base64
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

POLL_INTERVAL = 4.0   # seconds between VLC position polls
_RESUME_MIN = 15      # don't resume if less than this many seconds were watched
_NEAR_END = 30        # within this many seconds of the end counts as "finished"


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
        # the previous one first unless the user opts out (its tracker saves the
        # position as it exits).
        if self.config.get("single_stream", True) and self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

        is_vlc = "vlc" in os.path.basename(self.player_path).lower()
        custom_args = self.config.get("player_args")
        port = pwd = None
        if custom_args:
            cmd = [self.player_path] + [a.replace("{url}", url) for a in custom_args]
        else:
            cmd = [self.player_path]
            # Only set a user agent if asked to. Forcing one (e.g. "Mozilla/5.0")
            # makes some panels answer 406; VLC's native handling is usually best.
            user_agent = self.config.get("user_agent")
            if is_vlc and user_agent:
                cmd.append(f"--http-user-agent={user_agent}")
            if is_vlc:
                # Show the channel name in VLC's title bar instead of the raw URL.
                cmd.append(f"--meta-title={channel.get('name') or 'IPTV'}")
            # Resume is for on-demand only; live has no meaningful position.
            if is_vlc and not channel.get("is_live") and self.config.get("resume", True):
                port, pwd = self._resume_setup(url, cmd)
            cmd.append(url)
        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as exc:
            messagebox.showerror("Playback error", f"Could not start the player:\n{exc}")
            return
        if port:
            self._track_position(self._proc, url, port, pwd)

    # -- resume (VLC http interface, best effort) --------------------------- #
    def _resume_setup(self, url, cmd):
        """Enable VLC's http control interface and resume the saved position.

        Returns ``(port, password)`` for the position poller, or ``(None, None)``
        if it couldn't be set up (in which case playback still proceeds normally).
        """
        try:
            port, pwd = self._free_port(), secrets.token_hex(8)
        except Exception:
            return None, None
        cmd += ["--extraintf=http", "--http-host=127.0.0.1",
                f"--http-port={port}", f"--http-password={pwd}"]
        saved = self.config.get_position(url)
        if saved:
            pos, length = saved.get("pos", 0), saved.get("length", 0)
            if pos > _RESUME_MIN and (not length or pos < length - _NEAR_END):
                cmd.append(f"--start-time={int(pos)}")
            elif length and pos >= length - _NEAR_END:
                self.config.clear_position(url)  # finished last time: start over
        return port, pwd

    @staticmethod
    def _free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _track_position(self, proc, url, port, pwd):
        """Poll VLC for the play position while it runs; save it once it exits."""
        def run():
            last = {}
            while proc.poll() is None:
                status = self._vlc_status(port, pwd)
                if status and status.get("time"):
                    last = status
                time.sleep(POLL_INTERVAL)
            self._persist_position(url, last)

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _vlc_status(port, pwd):
        try:
            req = Request(f"http://127.0.0.1:{port}/requests/status.xml")
            token = base64.b64encode(f":{pwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
            with urlopen(req, timeout=2) as resp:
                root = ET.fromstring(resp.read())
            return {"time": int(root.findtext("time") or 0),
                    "length": int(root.findtext("length") or 0)}
        except Exception:
            return None

    def _persist_position(self, url, last):
        t, length = last.get("time", 0), last.get("length", 0)
        if length and t >= length - _NEAR_END:
            self.config.clear_position(url)   # watched to the end
        elif t > _RESUME_MIN:
            self.config.save_position(url, t, length)
