"""
Media-player discovery and launching.

The app does not bundle a player; it launches the user's installed one (VLC by
default). ``Playback`` finds the player, builds the stream URL, and opens it.
"""

import os
import re
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from urllib.parse import urlsplit

POLL_INTERVAL = 4.0   # seconds between VLC position polls
_TRACK_POLL = 0.5     # faster polls while waiting to apply saved tracks to a new episode
_TRACK_WAIT = 45      # stop waiting for tracks to appear after this many seconds
_RESUME_MIN = 15      # don't resume if less than this many seconds were watched
_NEAR_END = 30        # within this many seconds of the end counts as "finished"

_TRACK_LINE = re.compile(r"^\|\s*(-?\d+) - (.*?)(\s\*)?\s*$")


def parse_tracks(text):
    """Parse VLC's rc ``atrack`` / ``strack`` listing into ``(id, language,
    selected)`` tuples. ``language`` is the last ``[...]`` VLC shows for the
    track, or None when the file doesn't say. Id -1 is VLC's "Disable" entry.
    """
    tracks = []
    for line in text.splitlines():
        m = _TRACK_LINE.match(line.strip())
        if m:
            langs = re.findall(r"\[([^\]]+)\]", m.group(2))
            tracks.append((int(m.group(1)), langs[-1] if langs else None, bool(m.group(3))))
    return tracks


def track_choice(tracks):
    """The selected track, described so it carries over to another episode:
    by language when VLC knows it, by position among the tracks otherwise.
    None when the file has no tracks of this kind, so nothing is learned."""
    real = [t for t in tracks if t[0] >= 0]
    if not real:
        return None
    for tid, lang, selected in tracks:
        if selected:
            if tid < 0:
                return {"off": True}
            return {"lang": lang} if lang else {"index": [t[0] for t in real].index(tid)}
    return None


def pick_track(tracks, choice):
    """The track id in ``tracks`` matching a saved ``choice``, or None to leave
    VLC's default alone. A saved language that this episode lacks matches
    nothing, rather than falling back to whatever sits in the same slot."""
    if not choice:
        return None
    if choice.get("off"):
        return -1
    real = [t for t in tracks if t[0] >= 0]
    if choice.get("lang"):
        want = choice["lang"].lower()
        return next((tid for tid, lang, _ in real if lang and lang.lower() == want), None)
    idx = choice.get("index")
    if isinstance(idx, int) and 0 <= idx < len(real):
        return real[idx][0]
    return None


def _last_int(text):
    for line in reversed(text.splitlines()):
        if line.strip().isdigit():
            return int(line.strip())
    return 0


class _VlcRc:
    """One connection to VLC's rc control interface on localhost."""

    def __init__(self, port):
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self._sock.settimeout(0.3)

    def ask(self, command):
        """Send one command and return what VLC prints, read until it goes quiet."""
        self._sock.sendall(command.encode() + b"\n")
        out, deadline = b"", time.time() + 2.0
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                if out:
                    break
                continue
            if not chunk:
                break
            out += chunk
        return out.decode("utf-8", "replace")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        try:
            self._sock.close()
        except OSError:
            pass


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
        resume = bool(self.config.get("resume", True))
        port = None
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
            # Only on-demand video is tracked: live has no position to resume, and
            # no next episode to carry audio / subtitle choices over to.
            if is_vlc and not channel.get("is_live") and (resume or channel.get("series")):
                port = self._control_setup(url, cmd, resume)
            cmd.append(url)
        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as exc:
            messagebox.showerror("Playback error", f"Could not start the player:\n{exc}")
            return
        if port:
            self._track(self._proc, url, port, channel, resume)

    # -- VLC control (rc interface, best effort) ---------------------------- #
    def _control_setup(self, url, cmd, resume):
        """Enable VLC's rc control interface on a private localhost port, and
        resume the saved position if asked. Returns the port, or None if it
        couldn't be set up (in which case playback still proceeds normally).

        rc rather than VLC's http interface because only rc reports which audio
        and subtitle tracks are selected; http can set them but not read them.
        """
        try:
            port = self._free_port()
        except Exception:
            return None
        cmd += ["--extraintf=rc", f"--rc-host=127.0.0.1:{port}"]
        if os.name == "nt":
            cmd.append("--rc-quiet")  # Windows-only flag: no console window for rc
        if resume:
            saved = self.config.get_position(url)
            if saved:
                pos, length = saved.get("pos", 0), saved.get("length", 0)
                if pos > _RESUME_MIN and (not length or pos < length - _NEAR_END):
                    cmd.append(f"--start-time={int(pos)}")
                elif length and pos >= length - _NEAR_END:
                    self.config.clear_position(url)  # finished last time: start over
        return port

    @staticmethod
    def _free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _track(self, proc, url, port, channel, resume):
        """Poll VLC while it runs: the play position (saved on exit, for resume)
        and, for a series, the audio and subtitle tracks (carried to the next
        episode)."""
        series = channel.get("series")
        grp = channel.get("grp") or ""

        def run():
            last = {}
            # A new episode first gets the series' saved tracks; until then VLC's
            # defaults are showing, and recording those would overwrite the choice.
            pending = bool(series)
            baseline = None
            started = time.time()
            while proc.poll() is None:
                try:
                    with _VlcRc(port) as rc:
                        if resume:
                            t = _last_int(rc.ask("get_time"))
                            if t:
                                last = {"time": t, "length": _last_int(rc.ask("get_length"))}
                        if series:
                            audio = parse_tracks(rc.ask("atrack"))
                            subs = parse_tracks(rc.ask("strack"))
                            if pending and any(t[0] >= 0 for t in audio + subs):
                                self._apply_tracks(rc, audio, subs,
                                                   self.config.get_track_prefs(series, grp))
                                audio = parse_tracks(rc.ask("atrack"))
                                subs = parse_tracks(rc.ask("strack"))
                                pending = False
                            elif pending and time.time() - started > _TRACK_WAIT:
                                pending = False
                            if not pending:
                                baseline = self._record_tracks(series, grp, audio, subs, baseline)
                except OSError:
                    pass  # rc not listening yet, or VLC is closing
                time.sleep(_TRACK_POLL if pending else POLL_INTERVAL)
            if resume:
                self._persist_position(url, last)

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _apply_tracks(rc, audio, subs, prefs):
        for command, tracks, kind in (("atrack", audio, "audio"), ("strack", subs, "sub")):
            tid = pick_track(tracks, prefs.get(kind))
            current = next((t[0] for t in tracks if t[2]), None)
            if tid is not None and tid != current:
                rc.ask(f"{command} {tid}")

    def _record_tracks(self, series, grp, audio, subs, baseline):
        """Save the series' tracks when the user changes them mid-episode.

        Only a change from what the episode opened with counts, so an episode
        that lacks the preferred language (and falls back to another) doesn't
        overwrite the preference for the rest of the series. Returns the new
        baseline."""
        now = {"audio": track_choice(audio), "sub": track_choice(subs)}
        if baseline is None:
            return now
        changed = {k: v for k, v in now.items() if v is not None and v != baseline.get(k)}
        if changed:
            prefs = self.config.get_track_prefs(series, grp)
            prefs.update(changed)
            self.config.save_track_prefs(series, grp, prefs)
        return now

    def _persist_position(self, url, last):
        t, length = last.get("time", 0), last.get("length", 0)
        if length and t >= length - _NEAR_END:
            self.config.clear_position(url)   # watched to the end
        elif t > _RESUME_MIN:
            self.config.save_position(url, t, length)
