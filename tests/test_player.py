"""Tests for stream URL construction in the player layer.

``player`` imports no GUI toolkit at module load, so these run headless. Only the
URL building is exercised, not actually launching a player.
"""

from iptv.data.config_store import Config
from iptv.system.player import Playback
import iptv.system.player as player_mod


def _playback(tmp_path, **flags):
    config = Config(str(tmp_path / "config.json"))
    config.data.update(flags)
    return Playback(config, str(tmp_path))


def test_live_url_rebuilt_to_ts(tmp_path):
    pb = _playback(tmp_path)
    channel = {"url": "http://host:8080/user/pass/1001", "is_live": 1, "stream_id": "1001"}
    assert pb.stream_url(channel) == "http://host:8080/live/user/pass/1001.ts"


def test_live_format_m3u8(tmp_path):
    pb = _playback(tmp_path, live_format="m3u8")
    channel = {"url": "http://host:8080/user/pass/1001", "is_live": 1, "stream_id": "1001"}
    assert pb.stream_url(channel).endswith("/live/user/pass/1001.m3u8")


def test_use_live_url_disabled(tmp_path):
    pb = _playback(tmp_path, use_live_url=False)
    channel = {"url": "http://host:8080/user/pass/1001", "is_live": 1, "stream_id": "1001"}
    assert pb.stream_url(channel) == "http://host:8080/user/pass/1001"


def test_vod_url_untouched(tmp_path):
    pb = _playback(tmp_path)
    channel = {"url": "http://host:8080/movie/user/pass/2002.mkv", "is_live": 0, "stream_id": "2002"}
    assert pb.stream_url(channel) == "http://host:8080/movie/user/pass/2002.mkv"


def test_already_formatted_live_url_untouched(tmp_path):
    pb = _playback(tmp_path)
    channel = {"url": "http://host:8080/live/user/pass/1001.ts", "is_live": 1, "stream_id": "1001"}
    assert pb.stream_url(channel) == "http://host:8080/live/user/pass/1001.ts"


def _vlc_playback(tmp_path, monkeypatch):
    """A Playback pointed at a fake VLC, with Popen captured (nothing launches)."""
    captured = {}

    class FakeProc:
        def poll(self):
            return 0  # already exited, so the position tracker does nothing

    def fake_popen(cmd, *a, **k):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(player_mod.subprocess, "Popen", fake_popen)
    (tmp_path / "vlc.exe").write_text("")
    config = Config(str(tmp_path / "config.json"))
    config.data["player_path"] = str(tmp_path / "vlc.exe")
    return Playback(config, str(tmp_path)), config, captured


def test_play_sets_meta_title_and_resume(tmp_path, monkeypatch):
    pb, config, captured = _vlc_playback(tmp_path, monkeypatch)
    url = "http://host/movie/u/p/9.mkv"
    config.save_position(url, 300, 3600)
    pb.play({"name": "My Movie", "url": url, "is_live": 0, "stream_id": "9"})
    cmd = captured["cmd"]
    assert "--meta-title=My Movie" in cmd          # VLC shows the name
    assert "--extraintf=http" in cmd               # control interface for tracking
    assert any(c.startswith("--start-time=300") for c in cmd)  # resumes where left off


def test_play_live_has_title_but_no_resume(tmp_path, monkeypatch):
    pb, _config, captured = _vlc_playback(tmp_path, monkeypatch)
    pb.play({"name": "News HD", "url": "http://host/u/p/5", "is_live": 1, "stream_id": "5"})
    cmd = captured["cmd"]
    assert any(c.startswith("--meta-title=") for c in cmd)
    assert "--extraintf=http" not in cmd           # live has no resume position
