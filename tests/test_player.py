"""Tests for stream URL construction in the player layer.

``player`` imports no GUI toolkit at module load, so these run headless. Only the
URL building is exercised, not actually launching a player.
"""

from iptv.data.config_store import Config
from iptv.system.player import Playback, parse_tracks, pick_track, track_choice
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
    assert "--extraintf=rc" in cmd                 # control interface for tracking
    assert any(c.startswith("--start-time=300") for c in cmd)  # resumes where left off


def test_play_live_has_title_but_no_resume(tmp_path, monkeypatch):
    pb, _config, captured = _vlc_playback(tmp_path, monkeypatch)
    pb.play({"name": "News HD", "url": "http://host/u/p/5", "is_live": 1, "stream_id": "5"})
    cmd = captured["cmd"]
    assert any(c.startswith("--meta-title=") for c in cmd)
    assert "--extraintf=rc" not in cmd             # live has no resume position


def test_series_tracked_even_with_resume_off(tmp_path, monkeypatch):
    pb, config, captured = _vlc_playback(tmp_path, monkeypatch)
    config.data["resume"] = False
    pb.play({"name": "Show S01E02", "url": "http://host/series/u/p/7.mkv", "is_live": 0,
             "series": "Show", "season": 1, "episode": 2, "grp": "TV"})
    assert "--extraintf=rc" in captured["cmd"]      # still watching for track changes
    assert not any(c.startswith("--start-time") for c in captured["cmd"])


# Real ``atrack`` output captured from VLC 3.0.21.
ATRACK = """status change: ( play state: 3 )
+----[ Audio Track ]
| -1 - Disable
| 1 - Track 1 - [English]
| 2 - Track 2 - [French] *
+----[ end of Audio Track ]
atrack: returned 0 (no error)
"""


def test_parse_tracks_reads_selection_and_language():
    assert parse_tracks(ATRACK) == [(-1, None, False), (1, "English", False), (2, "French", True)]


def test_track_choice_by_language_then_position():
    assert track_choice(parse_tracks(ATRACK)) == {"lang": "French"}
    unnamed = [(-1, None, False), (5, None, False), (6, None, True)]
    assert track_choice(unnamed) == {"index": 1}
    assert track_choice([(-1, None, True), (3, "English", False)]) == {"off": True}
    assert track_choice([(-1, None, True)]) is None   # no tracks: nothing learned


def test_pick_track_matches_language_across_renumbered_episodes():
    next_ep = [(-1, None, False), (11, "French", False), (12, "English", True)]
    assert pick_track(next_ep, {"lang": "french"}) == 11
    assert pick_track(next_ep, {"off": True}) == -1
    # A language this episode lacks matches nothing, rather than the same slot.
    assert pick_track(next_ep, {"lang": "German"}) is None
    assert pick_track(next_ep, {"index": 1}) == 12
    assert pick_track(next_ep, None) is None
