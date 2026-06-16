"""Tests for stream URL construction in the player layer.

``player`` imports no GUI toolkit at module load, so these run headless. Only the
URL building is exercised, not actually launching a player.
"""

from config_store import Config
from player import Playback


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
