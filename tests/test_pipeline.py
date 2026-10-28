import pandas as pd
from fakeapi import FakeSession

from vi_precip import pipeline
from vi_precip.eccc import ECCCClient


def test_inventory_end_to_end(cfg):
    client = ECCCClient(cfg, session=FakeSession())
    paths = pipeline.run_inventory(cfg, client=client)
    for k in ("inventory", "completeness", "composite_candidates", "composites_proposed", "map", "timeline", "regions"):
        assert paths[k].exists(), k

    inv = pd.read_csv(paths["inventory"], dtype={"climate_id": str}).set_index("climate_id")
    assert "1123970" not in inv.index  # outside domain
    assert inv.loc["1018610", "tier"] == 1  # continuous record
    assert inv.loc["1018610", "region"] == "East VI"
    assert inv.loc["1021830", "tier"] == 3  # closed 2013
    assert inv.loc["1021831", "tier"] == 2  # 2013 onward only
    assert inv.loc["1038205", "tier"] == 2  # Tofino: gappy years
    assert inv.loc["1026270", "tier"] == 3  # closed 2010
    assert inv.loc["1016995", "target_valid_total"]  # accumulation fully inside the window

    cand = pd.read_csv(paths["composite_candidates"], dtype=str)
    pairs = {(a, b): s for a, b, s in zip(cand["id_a"], cand["id_b"], cand["suggestion"])}
    assert pairs[("1021830", "1021831")] == "join"
    assert pairs[("1100031", "1100032")] == "review"  # long concurrent overlap -> parallel

    prop = pd.read_csv(paths["composites_proposed"], dtype={"climate_id": str})
    comox = prop[prop["composite_id"] == "C_1021830"]
    assert comox["climate_id"].tolist() == ["1021830", "1021831"]
    assert (comox["tier_if_joined"] == 1).all()

    comp = pd.read_csv(paths["completeness"], dtype={"climate_id": str})
    sat = comp[(comp["climate_id"] == "1016995") & (comp["year"] == 2026) & (comp["period"] == "window")].iloc[0]
    assert sat["n_accumulated_days"] == 4
    assert sat["n_missing_daily"] == 4 and sat["n_missing_total"] == 0


def test_inventory_rerun_uses_cache(cfg):
    s = FakeSession()
    client = ECCCClient(cfg, session=s)
    pipeline.run_inventory(cfg, client=client)
    n = len(s.calls)
    client2 = ECCCClient(cfg, session=s)
    pipeline.run_inventory(cfg, client=client2)
    assert len(s.calls) == n  # nothing re-downloaded


def test_smoke(cfg):
    client = ECCCClient(cfg, session=FakeSession())
    out = pipeline.run_smoke(cfg, client=client, name="GONZALES")
    assert out["station"] == "1018610"
    assert any(r["year"] == 2026 and r["valid_total"] for r in out["window"])


def test_metrics_end_to_end(cfg):
    client = ECCCClient(cfg, session=FakeSession())
    pipeline.run_inventory(cfg, client=client)
    paths = pipeline.run_metrics(cfg, n_boot=50)
    units = pd.read_csv(paths["units"], dtype={"unit_id": str}).set_index("unit_id")
    assert "C_1021830" in units.index and "1021830" not in units.index  # members replaced by composite
    assert units.loc["C_1021830", "tier"] == 1
    wide = pd.read_csv(paths["anomalies"], dtype={"unit_id": str}).set_index("unit_id")
    assert (wide["total_window__pct_of_median"] < 100).all()  # the fake autumn is dry everywhere
    met = pd.read_csv(paths["metrics"], dtype={"unit_id": str})
    ab = met[(met["unit_id"] == "1100031") & (met["year"] == 2020) & (met["threshold"] == 1.0)].iloc[0]
    assert ab["n_ambiguous"] >= 0 and 0 < ab["frac_wet"] < 1
    pat = pd.read_csv(paths["accumulation_patterns"], dtype={"climate_id": str}).set_index("climate_id")
    assert pat.loc["1100031", "unc_runs_followed_by_acc"] == pat.loc["1100031", "n_unc_runs"] > 0
    assert paths["breaks"].exists() and paths["map_window"].exists()
    assert paths["brief"].exists() and "driest30_mm__rank_dry" in wide


def test_temperature_stage(cfg):
    from vi_precip import temperature

    pipeline.run_inventory(cfg, client=ECCCClient(cfg, session=FakeSession()))
    paths = temperature.run(cfg)
    an = pd.read_csv(paths["temperature_anomalies"], dtype={"unit_id": str})
    assert {"tmax", "tmin", "dtr"} <= set(an["var"])
    units = pd.read_csv(paths["temperature_units"], dtype={"unit_id": str})
    assert "C_1021830" not in set(units["unit_id"])  # Comox composite not in temperature.composites
    assert (an["n_years"] <= len(cfg.baseline_years) + 1).all()


def test_longrecord_stage(cfg):
    from vi_precip import longrecord

    s = FakeSession()
    pipeline.run_inventory(cfg, client=ECCCClient(cfg, session=s))
    paths = longrecord.run(cfg, client=ECCCClient(cfg, session=s))
    lp = pd.read_parquet(paths["long_precip"])
    assert "L_GONZALES" in set(lp["unit_id"]) and lp["year"].min() == 1950
    g = lp[(lp["unit_id"] == "L_GONZALES") & lp["valid_total_window"].astype(bool)]
    assert g["year"].min() == 1990  # fake data start
    ah = pd.read_parquet(paths["long_ahccd"])
    assert set(ah["month"]) == {9, 10} and ah["unit_id"].iloc[0] == "L_GONZALES"
