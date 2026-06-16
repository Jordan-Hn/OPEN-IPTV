"""Tests for the SQLite catalog: parsing helpers and a small end-to-end build.

These run with no display and no network: the catalog is built from a tiny
in-memory playlist written to a temp file.
"""

import catalog
from catalog import Catalog

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
    grp, name, logo, url, tvg, sid, is_live = catalog.parse_extinf(
        line, "http://host:8080/u/p/1001")
    assert (grp, name, tvg, sid, is_live) == ("News", "News One", "news.one", "1001", 1)


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
    news = cat.channels("News")
    assert len(news) == 1
    assert news[0]["name"] == "News One"
    assert news[0]["is_live"] == 1
    assert news[0]["stream_id"] == "1001"


def test_search(tmp_path):
    cat, _ = _build(tmp_path)
    rows, has_more = cat.search("one")
    assert sorted(r["name"] for r in rows) == ["News One", "Sports One"]
    assert has_more is False
    assert cat.search_count("one") == 2
    assert cat.search("nothing-here")[0] == []


def test_live_helpers(tmp_path):
    cat, _ = _build(tmp_path)
    assert cat.live_counts() == (2, 2)
    assert set(cat.live_tvg_ids()) == {"news.one", "sports.one"}
