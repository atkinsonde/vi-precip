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
