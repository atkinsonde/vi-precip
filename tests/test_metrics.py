import datetime as dt

import numpy as np
import pandas as pd

from vi_precip import anomalies, homogeneity, metrics, qc

RULES = qc.FlagRules(flag_status={"M": "missing", "T": "trace", "A": "accumulated", "C": "uncertain"},
                     status_treatment={}, absorb_uncertain=True)


def ann_from(values, flags=None, start="2026-06-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    d = pd.DataFrame({"date": idx, "precip": values, "precip_flag": flags or [None] * len(values),
                      "tmax": 15.0, "tmin": 8.0, "tmax_flag": None, "tmin_flag": None})
    return qc.annotate(qc.complete_index(d, idx[0].date(), idx[-1].date(), "X"), RULES)


def test_weekend_c_days_join_block():
    # Sat, Sun flagged C without values, Monday A carries 12 mm
    a = ann_from([0.0, np.nan, np.nan, 12.0, 0.0], [None, "C", "C", "A", None])
    assert a["acc_block"].tolist() == [-1, 0, 0, 0, -1]
    ps = qc.period_stats(a, dt.date(2026, 6, 1), dt.date(2026, 6, 5))
    assert ps.total_mm == 12.0 and ps.n_valid_total == 5


def test_day_states_blocks():
    a = ann_from([2.0, np.nan, 0.4, 0.5, np.nan, 3.0, np.nan], [None, "C", "A", None, "C", "A", "M"])
    s = metrics.day_states(a, 1.0)
    # block 1 total 0.4 < 1 -> both days dry; block 2 total 3.0 -> ambiguous; last day missing
    assert "".join(s) == "WDDDAAM"


def test_wet_frequency_estimator():
    s = np.array(list("WDDDAAA"))
    f, nw, n = metrics.wet_frequency(s, 0.5)
    e = 3 * 0.5 / (1 - 0.5 ** 3)
    assert n == 7 and np.isclose(nw, 1 + e) and np.isclose(f, (1 + e) / 7)


def test_spells_censoring_and_window(cfg):
    # 2026: wet until Aug 20, dry Aug 21 .. Sep 10, wet Sep 11, then dry to the end date (Oct 26)
    idx = pd.date_range("2026-06-01", "2026-10-26", freq="D")
    v = np.where(idx < "2026-08-21", 5.0, 0.0)
    v[idx == "2026-09-11"] = 8.0
    a = ann_from(list(v))
    refs = {"p50": 3, "p75": 5, "p90": 8, "p95": 9, "sep_median": 50.0, "p_wet": 0.4}
    row, bins, spells = metrics.year_metrics(a, cfg, 2026, 1.0, refs, metrics.block_totals(a))
    first, last = spells[0], spells[-1]
    assert first["full_len"] == 21 and first["days_in_window"] == 10 and not first["left_censored"]
    assert last["right_censored"] and last["days_in_window"] == (dt.date(2026, 10, 26) - dt.date(2026, 9, 12)).days + 1
    assert row["spell_max_win"] == last["days_in_window"]
    assert row["spell_max_full"] == last["days_in_window"]
    assert row["n_wet"] == 1 and row["onset_censored"]  # 8 mm never reaches the 50 mm September median
    fixed = {b["bin"]: b["n_days"] for b in bins if b["scheme"] == "fixed"}
    assert fixed["5–10"] == 1 and fixed["<0.2"] == 55


def test_decomposition_is_additive(cfg):
    yrs = cfg.analysis_years
    rng = np.random.default_rng(0)
    m = pd.DataFrame({"year": yrs, "threshold": 1.0, "valid_daily": True,
                      "n_wet_scaled": rng.uniform(10, 30, len(yrs)), "sdii": rng.uniform(3, 12, len(yrs))})
    out = metrics._decompose(m, cfg)
    assert np.allclose(out["a_P"], out["a_N"] + out["a_I"])
    base = out["year"].isin(cfg.baseline_years)
    assert abs(out.loc[base, "a_N"].mean()) < 1e-12


def test_anomaly_ranks(cfg):
    units = pd.DataFrame({"unit_id": ["U"], "status": ["final"], "name": ["u"], "region": ["East VI"], "lon": [-123.0],
                          "lat": [48.5], "elev_m": [10.0], "tier": [1], "kind": ["station"]})
    yrs = cfg.analysis_years
    vals = {y: 100.0 + i for i, y in enumerate(cfg.baseline_years)}
    vals[cfg.target_year] = 20.0
    m = pd.DataFrame({"unit_id": "U", "year": yrs, "threshold": 1.0,
                      "total_window": [vals[y] for y in yrs], "valid_total_window": True,
                      "spell_max_win": [3 if y != cfg.target_year else 30 for y in yrs], "valid_daily": True})
    a = anomalies.anomalies(m, units, cfg, n_boot=50).set_index("metric")
    tw = a.loc["total_window"]
    n = len(cfg.baseline_years)
    assert tw["rank_dry"] == 1 and tw["record_dry"] and np.isclose(tw["p_dry"], 1 / (n + 1))
    assert tw["rp_emp"] == n + 1 and tw["spi"] < -2 and tw["rp_fit"] > tw["rp_emp"]
    sp = a.loc["spell_max_win"]
    assert sp["rank_dry"] == 1 and sp["record_dry"]


def test_pettitt_finds_step():
    rng = np.random.default_rng(3)
    x = np.r_[rng.normal(0, 0.01, 13), rng.normal(0.05, 0.01, 13)]
    k, K, p = homogeneity.pettitt(x)
    assert k == 12 and p < 0.01
