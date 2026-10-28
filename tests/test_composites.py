import pandas as pd

from vi_precip import composites


def test_name_similarity():
    assert composites.name_similarity("COMOX A", "COMOX AUTO") == 1.0
    assert composites.name_similarity("VICTORIA INTL A", "NANAIMO A") < 0.5


def test_build_series_switch():
    d1 = pd.DataFrame({"date": pd.date_range("2013-06-25", periods=10), "precip": 1.0})
    d2 = pd.DataFrame({"date": pd.date_range("2013-06-28", periods=10), "precip": 2.0})
    members = pd.DataFrame({
        "order": [1, 2], "climate_id": ["a", "b"],
        "use_from": [None, pd.Timestamp("2013-07-01")], "use_to": [pd.Timestamp("2013-06-30"), None],
    })
    s = composites.build_series(members, {"a": d1, "b": d2}, "C_a")
    assert s["date"].is_unique
    assert s.set_index("date").loc["2013-06-30", "source_id"] == "a"
    assert s.set_index("date").loc["2013-07-01", "source_id"] == "b"
    assert (s["climate_id"] == "C_a").all()


def test_contained_record_is_not_auto_joined(cfg):
    st = pd.DataFrame({
        "climate_id": ["a", "b"], "name": ["SQUAMISH AIRPORT", "SQUAMISH AIRPORT"], "lon": [-123.16, -123.161],
        "lat": [49.78, 49.781], "elev_m": [50.0, 51.0], "region": ["Sea-to-Sky"] * 2,
        "dly_first": pd.to_datetime(["2000-01-01", "2010-01-08"]), "dly_last": pd.to_datetime(["2026-10-25", "2010-04-01"]),
    })
    spans = pd.DataFrame({"climate_id": ["a", "b"], "data_first": pd.NaT, "data_last": pd.NaT})
    c = composites.find_candidates(st, spans, {}, cfg)
    assert c.iloc[0]["temporal"] == "contained" and c.iloc[0]["suggestion"] == "review"
