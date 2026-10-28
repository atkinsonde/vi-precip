import json

import numpy as np
import pandas as pd
from buildall import build_all

from vi_precip import enso_stats as E


def test_helpers():
    p = np.array([0.001, 0.01, 0.02, 0.5, 0.9])
    q = E.bh(p)
    assert np.allclose(q, [0.005, 0.025, 0.0333333, 0.625, 0.9])
    sig, thr = E.wilks_fdr(p, 0.05)
    assert sig.tolist() == [True, True, True, False, False] and thr == 0.02
    rs = E.Resampler(999, 200, 1)
    t = np.arange(1950, 2026)
    rng = np.random.default_rng(0)
    x = rng.normal(size=len(t))
    y = -0.6 * x + rng.normal(scale=0.6, size=len(t)) + 0.05 * (t - t.mean())  # trend removed by detrending
    r = E.perm_spearman(x, y, t, rs)
    assert r["rho"] < -0.4 and r["p_neg"] < 0.01 and r["p_pos"] > 0.9 and r["rho_lo"] < r["rho"] < r["rho_hi"]
    f = E.ols(y, x[:, None], ["x"])
    assert f["coef"]["x"]["b"] < 0 and 0 < f["r2"] < 1
    pr = E.predict(f, np.array([2.0]))
    assert pr["lo"] < pr["mean"] < pr["hi"]


def test_enso_end_to_end(cfg):
    paths = build_all(cfg)
    summ = json.loads(paths["enso_summary"].read_text())
    assert summ["target_index"]["class"] == "El Niño"
    assert {"short", "long"} <= set(summ["primary"]) and summ["primary"]["short"]["n"] >= 20
    g = pd.read_parquet(paths["enso_grid"])
    assert {"short", "long", "rivers"} <= set(g["record"])
    assert {"precipitation", "temperature", "rivers"} <= set(g["family"])
    assert g["q_bh"].between(0, 1).all()
    reg = pd.read_parquet(paths["enso_regressions"])
    row = reg[(reg["record"] == "short") & (reg["region"] == "island") & (reg["metric"] == "total_window")
              & (reg["model"] == "enso")].iloc[0]
    assert row["target_lo90"] < row["target_expected"] < row["target_hi90"] and row["target_observed"] < 0
    comp = pd.read_parquet(paths["enso_composites"])
    assert set(comp["class"]) == {"El Niño", "Neutral", "La Niña"}
    sm = pd.read_parquet(paths["enso_station_maps"])
    assert "fdr_sig" in sm and sm["lon"].notna().all()
    ah = pd.read_parquet(paths["enso_ahccd_check"])
    assert len(ah) == 1 and ah["n_common"].iloc[0] > 10
