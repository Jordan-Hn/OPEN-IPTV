"""Tests for the SQLite catalog: parsing helpers and a small end-to-end build.

These run with no display and no network: the catalog is built from a tiny
in-memory playlist written to a temp file.
"""

import pytest

from iptv.data import catalog
from iptv.data.catalog import Catalog

SAMPLE_M3U = """#EXTM3U url-tvg="http://example.com/epg.xml"
#EXTINF:-1 tvg-id="news.one" tvg-name="News One" tvg-logo="http://logos/n1.png" group-title="News",News One
http://host:8080/u/p/1001
#EXTINF:-1 tvg-id="sports.one" tvg-name="Sports One" group-title="Sports",Sports One
http://host:8080/u/p/1002
#EXTINF:-1 tvg-name="A Quiet Film" group-title="Movies",A Quiet Film
http://host:8080/movie/u/p/2002.mkv
"""


def test_classify_url_live_vs_vod():
    assert catalog.classify_url("http://host:8080/u/p/1001") == (True, "1001")
    assert catalog.classify_url("http://host:8080/movie/u/p/2002.mkv") == (False, None)
    assert catalog.classify_url("http://host:8080/series/u/p/3.mkv") == (False, None)
    assert catalog.classify_url("not a url") == (False, None)


def test_parse_provider():
    assert catalog.parse_provider("http://host:8080/u/p/1001") == {
        "base": "http://host:8080", "username": "u", "password": "p"}
    assert catalog.parse_provider("http://host:8080/movie/u/p/2.mkv") is None


def test_parse_extinf_fields():
    line = '#EXTINF:-1 tvg-id="news.one" tvg-name="News One" group-title="News",News One'
    rec = catalog.parse_extinf(line, "http://host:8080/u/p/1001")
    grp, name, logo, url, tvg, sid, is_live, series, season, episode = rec
    assert (grp, name, tvg, sid, is_live) == ("News", "News One", "news.one", "1001", 1)
    assert (series, season, episode) == ("", 0, 0)  # a live channel is not a series


def test_parse_series():
    assert catalog.parse_series("Wandering Tales S01E01") == ("Wandering Tales", 1, 1)
    assert catalog.parse_series("Show s1 e2") == ("Show", 1, 2)
    assert catalog.parse_series("Show - S03E10") == ("Show", 3, 10)
    assert catalog.parse_series("Docu Season 2 Episode 5") == ("Docu", 2, 5)
    assert catalog.parse_series("Cooking Basics 1x03") == ("Cooking Basics", 1, 3)
    assert catalog.parse_series("A Quiet Film") is None
    assert catalog.parse_series("S01E01") is None            # nothing before the marker
    assert catalog.parse_series("Movie 1920x1080") is None   # a resolution, not an episode


def test_parse_extinf_name_fallback_and_skip():
    rec = catalog.parse_extinf("#EXTINF:-1,Plain Name", "http://host/u/p/9")
    assert rec[1] == "Plain Name"
    assert catalog.parse_extinf("#EXTINF:-1,Plain Name", "") is None


def _build(tmp_path):
    m3u = tmp_path / "play.m3u"
    m3u.write_text(SAMPLE_M3U, encoding="utf-8")
    cat = Catalog(str(tmp_path / "cat.db"))
    provider = cat.build(str(m3u))
    return cat, provider


def test_build_counts_and_provider(tmp_path):
    cat, provider = _build(tmp_path)
    assert cat.total_channels() == 3
    assert provider == {"base": "http://host:8080", "username": "u", "password": "p"}
    assert cat.epg_url() == "http://example.com/epg.xml"


def test_build_groups_and_channels(tmp_path):
    cat, _ = _build(tmp_path)
    assert dict(cat.groups()) == {"News": 1, "Sports": 1, "Movies": 1}
    assert cat.browse_count("News") == 1
    news = cat.browse_items("News")
    assert len(news) == 1
    assert news[0]["name"] == "News One"
    assert news[0]["is_live"] == 1
    assert news[0]["stream_id"] == "1001"


def test_search(tmp_path):
    cat, _ = _build(tmp_path)
    rows, has_more = cat.search_items("one")
    assert sorted(r["name"] for r in rows) == ["News One", "Sports One"]
    assert has_more is False
    assert cat.search_items("nothing-here")[0] == []


def test_live_helpers(tmp_path):
    cat, _ = _build(tmp_path)
    assert cat.live_counts() == (2, 2)
    assert set(cat.live_tvg_ids()) == {"news.one", "sports.one"}


def test_startup_reads_on_missing_db_are_safe(tmp_path):
    """A fresh run (no catalog yet) must not crash or create a stray db file.

    This is the path that used to freeze the window on 'Loading...'.
    """
    import os
    db = tmp_path / "nope.db"
    cat = Catalog(str(db))
    assert cat.provider() is None
    assert cat.total_channels() == 0
    assert cat.source_key() == ""
    assert cat.is_fresh_for("anything") is False
    assert not os.path.exists(str(db))  # reads must not create the file


SERIES_M3U = """#EXTM3U
#EXTINF:-1 group-title="Series",Wandering Tales S01E01
http://host:8080/series/u/p/5001.mkv
#EXTINF:-1 group-title="Series",Wandering Tales S01E02
http://host:8080/series/u/p/5002.mkv
#EXTINF:-1 group-title="Series",Wandering Tales S02E01
http://host:8080/series/u/p/5003.mkv
#EXTINF:-1 group-title="Series",Cooking Basics 1x03
http://host:8080/series/u/p/5004.mkv
#EXTINF:-1 group-title="Movies",A Quiet Film
http://host:8080/movie/u/p/2002.mkv
"""


def _build_series(tmp_path):
    m3u = tmp_path / "series.m3u"
    m3u.write_text(SERIES_M3U, encoding="utf-8")
    cat = Catalog(str(tmp_path / "cat.db"))
    cat.build(str(m3u))
    return cat


def test_browse_collapses_series(tmp_path):
    cat = _build_series(tmp_path)
    assert cat.browse_count("Series") == 2  # two shows, no standalone entries
    by_label = {it["label"]: (it["kind"], it["ep_count"]) for it in cat.browse_items("Series")}
    assert by_label["Wandering Tales"] == ("series", 3)
    assert by_label["Cooking Basics"] == ("series", 1)
    movies = cat.browse_items("Movies")
    assert len(movies) == 1 and movies[0]["kind"] == "item"
    assert movies[0]["name"] == "A Quiet Film"


def test_series_episodes_sorted(tmp_path):
    cat = _build_series(tmp_path)
    eps = cat.series_episodes("Wandering Tales")
    assert [(e["season"], e["episode"]) for e in eps] == [(1, 1), (1, 2), (2, 1)]
    assert all(e["is_live"] == 0 for e in eps)


def test_next_episode(tmp_path):
    cat = _build_series(tmp_path)
    n1 = cat.next_episode("Wandering Tales", 1, 1)
    assert (n1["season"], n1["episode"]) == (1, 2)
    n2 = cat.next_episode("Wandering Tales", 1, 2)
    assert (n2["season"], n2["episode"]) == (2, 1)
    assert cat.next_episode("Wandering Tales", 2, 1) is None   # last episode
    assert cat.next_episode("Nonexistent", 1, 1) is None


def test_search_items_groups_series(tmp_path):
    cat = _build_series(tmp_path)
    rows, has_more = cat.search_items("Wandering")
    assert len(rows) == 1 and has_more is False
    assert rows[0]["kind"] == "series" and rows[0]["label"] == "Wandering Tales"
    assert rows[0]["ep_count"] == 3
    rows, _ = cat.search_items("Quiet")
    assert len(rows) == 1 and rows[0]["kind"] == "item"
    assert rows[0]["name"] == "A Quiet Film"


def test_build_from_api_rejects_non_list(tmp_path, monkeypatch):
    """A provider that answers with an error string (not a channel list) should
    raise a clear error, not crash with an AttributeError."""

    class FakeResp:
        def __init__(self, data):
            self._data, self._sent = data, False

        def read(self, _n=-1):
            if self._sent:
                return b""
            self._sent = True
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(catalog.net, "open_url",
                        lambda url, timeout=30, user_agent=None: FakeResp(b'"Access Denied"'))
    cat = Catalog(str(tmp_path / "cat.db"))
    with pytest.raises(RuntimeError, match="did not return a channel list"):
        cat.build_from_api("http://provider.example", "user", "pass", include_vod=False)
