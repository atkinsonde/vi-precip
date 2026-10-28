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
