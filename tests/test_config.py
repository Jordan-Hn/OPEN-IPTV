"""Tests for the config store: favourites, favourited series, and history."""

from iptv.data.config_store import Config


def _cfg(tmp_path):
    return Config(str(tmp_path / "config.json"))


def test_favourite_series_toggle(tmp_path):
    c = _cfg(tmp_path)
    s = {"name": "Wandering Tales", "grp": "Series", "logo": "l", "ep_count": 3}
    assert c.is_favourite_series("Wandering Tales", "Series") is False
    assert c.toggle_favourite_series(s) is True
    assert c.is_favourite_series("Wandering Tales", "Series") is True
    assert c.toggle_favourite_series(s) is False
    assert c.is_favourite_series("Wandering Tales", "Series") is False


def test_history_add_dedup_and_order(tmp_path):
    c = _cfg(tmp_path)
    c.add_history({"name": "A", "url": "u1"})
    c.add_history({"name": "B", "url": "u2"})
    c.add_history({"name": "A again", "url": "u1"})  # same url: dedup + move to front
    assert [r["url"] for r in c.history_items()] == ["u1", "u2"]
    assert c.history_items()[0]["name"] == "A again"


def test_history_remove_and_clear(tmp_path):
    c = _cfg(tmp_path)
    c.add_history({"name": "A", "url": "u1"})
    c.add_history({"name": "B", "url": "u2"})
    c.remove_history("u1")
    assert [r["url"] for r in c.history_items()] == ["u2"]
    c.clear_history()
    assert c.history_items() == []


def test_history_records_series_fields(tmp_path):
    c = _cfg(tmp_path)
    c.add_history({"name": "Show S01E02", "url": "u", "series": "Show", "season": 1, "episode": 2})
    rec = c.history_items()[0]
    assert (rec["series"], rec["season"], rec["episode"]) == ("Show", 1, 2)


def test_resume_positions(tmp_path):
    c = _cfg(tmp_path)
    assert c.get_position("u") is None
    c.save_position("u", 120, 3600)
    assert c.get_position("u")["pos"] == 120
    assert c.get_position("u")["length"] == 3600
    c.clear_position("u")
    assert c.get_position("u") is None
    c.save_position("u", 0, 3600)     # a zero position is ignored
    assert c.get_position("u") is None


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "config.json")
    c = Config(path)
    c.toggle_favourite({"name": "Chan", "url": "cu"})
    c.toggle_favourite_series({"name": "Show", "grp": "S", "logo": "", "ep_count": 5})
    c.add_history({"name": "Chan", "url": "cu"})
    c.save_position("vod", 90, 1800)

    reloaded = Config(path)
    reloaded.load()
    assert reloaded.is_favourite("cu")
    assert reloaded.is_favourite_series("Show", "S")
    assert [r["url"] for r in reloaded.history_items()] == ["cu"]
    assert reloaded.get_position("vod")["pos"] == 90
