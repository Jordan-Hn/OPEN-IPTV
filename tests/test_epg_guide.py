"""Tests for the XMLTV guide: time parsing, progress fraction, and queries.

The query tests insert rows straight into a temp guide database, so they need no
network (the refresh path is the only part that fetches, and that is guarded
elsewhere by net.open_url's http/https check).
"""

import sqlite3

from epg_guide import EpgGuide, Programme, parse_xmltv_time


def test_parse_xmltv_time_utc_and_offset():
    base = parse_xmltv_time("20260615120000 +0000")
    assert base > 0
    # Same wall clock at +0100 is one hour earlier in unix terms than at +0000.
    assert base - parse_xmltv_time("20260615120000 +0100") == 3600
    # No zone is treated as UTC.
    assert parse_xmltv_time("20260615120000") == base


def test_parse_xmltv_time_bad_input():
    assert parse_xmltv_time("") == 0
    assert parse_xmltv_time("garbage") == 0


def test_programme_fraction():
    p = Programme("Show", 100, 200)
    assert p.fraction(at=100) == 0.0
    assert p.fraction(at=150) == 0.5
    assert p.fraction(at=300) == 1.0                       # clamped to 1.0
    assert Programme("Zero", 100, 100).fraction(at=100) == 0.0  # no divide by zero


def _guide_with_rows(tmp_path):
    guide = EpgGuide(str(tmp_path / "epg.db"), epg_url="http://example.com/epg.xml")
    conn = sqlite3.connect(guide.db_path)
    conn.executemany(
        "INSERT INTO programmes(channel_id, start, stop, title, descr) VALUES(?,?,?,?,?)",
        [("c1", 100, 200, "Morning Show", "d"),
         ("c1", 200, 300, "Noon Show", "d"),
         ("c2", 100, 400, "All Day", "d")],
    )
    conn.commit()
    conn.close()
    return guide


def test_now_next(tmp_path):
    guide = _guide_with_rows(tmp_path)
    current, nxt = guide.now_next("c1", at=150)
    assert current.title == "Morning Show"
    assert nxt.title == "Noon Show"
    assert guide.now_next("missing", at=150) == (None, None)
    guide.close()


def test_search_now(tmp_path):
    guide = _guide_with_rows(tmp_path)
    titles = [r[3] for r in guide.search_now("Show", at=150)]
    assert "Morning Show" in titles          # on now
    assert "Noon Show" in titles             # starting soon
    assert titles[0] == "Morning Show"       # currently airing sorts first
    guide.close()


def test_channel_ids_and_count(tmp_path):
    guide = _guide_with_rows(tmp_path)
    assert guide.channel_ids() == {"c1", "c2"}
    assert guide.programme_count() == 3
    guide.close()
