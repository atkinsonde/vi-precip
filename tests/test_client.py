import datetime as dt
import json

from fakeapi import FakeSession

from vi_precip.eccc import ECCCClient, standardize_daily, standardize_stations


def test_paging_and_stations(cfg):
    s = FakeSession(max_limit=3)
    c = ECCCClient(cfg, session=s)
    raw = c.get_stations()
    st = standardize_stations(raw)
    assert "1018610" in set(st["climate_id"])
    assert "1123970" not in set(st["climate_id"])  # Kelowna is outside the bbox
    assert st["lon"].between(-129, -121).all()
    assert len([1 for p, _ in s.calls if p.endswith("climate-stations/items")]) >= 3  # paged


def test_daily_cache_and_refresh(cfg):
    s = FakeSession()
    c = ECCCClient(cfg, session=s)
    d = c.get_daily("1018610", dt.date(2024, 1, 1), dt.date(2026, 10, 26))
    n1 = len(s.calls)
    std = standardize_daily(d, "1018610")
    assert std["date"].min() == dt.datetime(2024, 1, 1) and std["date"].max() == dt.datetime(2026, 10, 26)
    # second call served from cache
    c.get_daily("1018610", dt.date(2024, 1, 1), dt.date(2026, 10, 26))
    assert len(s.calls) == n1
    # refresh_recent re-fetches only years not yet final (fetched < 60 days after year end)
    today = dt.date.today()
    expected = [y for y in (2024, 2025, 2026) if dt.date(y + 1, 1, 1) + dt.timedelta(days=60) > today]
    assert c.years_to_fetch("1018610", [2024, 2025, 2026], refresh_recent=True) == expected
    assert c.years_to_fetch("1018610", [2024, 2025, 2026], refresh_recent=False) == []
    man = json.loads((cfg.data_dir / "raw" / "eccc_daily" / "1018610" / "manifest.json").read_text())
    assert set(man) == {"2024", "2025", "2026"}


def test_rejected_param_fallback(cfg):
    s = FakeSession(reject=("datetime",))
    c = ECCCClient(cfg, session=s)
    d = c.get_daily("1016995", dt.date(2025, 1, 1), dt.date(2025, 12, 31))
    std = standardize_daily(d, "1016995")
    assert std["date"].dt.year.unique().tolist() == [2025]  # client-side filter applied
    caps = json.loads((cfg.data_dir / "raw" / "eccc" / "api_capabilities.json").read_text())
    assert "datetime" in caps["unsupported"]["climate-daily"]
    assert not c.datetime_supported("climate-daily")


def test_probe(cfg):
    c = ECCCClient(cfg, session=FakeSession())
    rep = c.probe()
    assert rep["ok"]
    assert rep["daily"]["resolved"]["precip"] == "TOTAL_PRECIPITATION"
