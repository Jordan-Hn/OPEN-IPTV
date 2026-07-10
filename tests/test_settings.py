"""Tests for playlist download settings and timeout handling."""

from iptv.ui.views import settings as settings_view


def test_source_timeout_defaults_and_clamps():
    assert settings_view._source_timeout({}) == settings_view.DEFAULT_DOWNLOAD_TIMEOUT
    assert settings_view._source_timeout({"download_timeout": "45"}) == 45
    assert settings_view._source_timeout({"download_timeout": 0}) == 1
    assert settings_view._source_timeout({"download_timeout": "bad"}) == settings_view.DEFAULT_DOWNLOAD_TIMEOUT


def test_source_changed_only_on_real_source_changes():
    xt = {"type": "xtream", "server": "http://h", "username": "u", "password": "p"}
    # Identical source (e.g. only the player path was edited): no rebuild.
    assert settings_view._source_changed(xt, dict(xt)) is False
    # A trailing slash on the server is not a real change.
    assert settings_view._source_changed(xt, {**xt, "server": "http://h/"}) is False
    # Credential or server changes do count.
    assert settings_view._source_changed(xt, {**xt, "password": "x"}) is True
    assert settings_view._source_changed(xt, {**xt, "server": "http://other"}) is True
    # Switching type counts.
    assert settings_view._source_changed(xt, {"type": "m3u_url", "m3u_url": "http://h/p.m3u"}) is True
    m = {"type": "m3u_url", "m3u_url": "http://h/p.m3u", "epg_url": "http://h/e.xml"}
    assert settings_view._source_changed(m, dict(m)) is False
    # Editing only the EPG url is not a catalog change.
    assert settings_view._source_changed(m, {**m, "epg_url": "http://h/e2.xml"}) is False
    assert settings_view._source_changed(m, {**m, "m3u_url": "http://h/other.m3u"}) is True
    assert settings_view._source_changed(None, xt) is True


def test_download_to_file_passes_custom_timeout(tmp_path, monkeypatch):
    captured = {}

    class Response:
        def __init__(self):
            self._chunks = [b"abc", b""]

        def read(self, _size):
            return self._chunks.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_open_url(url, timeout, user_agent=settings_view.net.USER_AGENT):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["user_agent"] = user_agent
        return Response()

    monkeypatch.setattr(settings_view.net, "open_url", fake_open_url)

    dest = tmp_path / "playlist.m3u"
    got = settings_view._download_to_file("http://example.com/playlist.m3u", str(dest), 321)

    assert got == 3
    assert dest.read_bytes() == b"abc"
    assert captured == {
        "url": "http://example.com/playlist.m3u",
        "timeout": 321,
        "user_agent": settings_view.net.USER_AGENT,
    }