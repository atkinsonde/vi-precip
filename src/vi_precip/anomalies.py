"""Target-year anomalies against the baseline (target excluded). DESIGN.md §7.

Every metric has a 'dry direction': for totals, low is dry; for spell lengths, high is dry.
rank_dry = 1 means the driest of all years compared (baseline + target).
p_dry    = Weibull plotting position of the target on the dry side, baseline only:
           (k + 1) / (n + 1), k = baseline years drier than the target (ties count half).
rp_emp   = 1 / p_dry, so at most n + 1 years.
spi      = standardized anomaly of a total via a gamma fit (Thom estimator) with a mixed
           probability of zero; rp_fit = 1 / H(x) with a bootstrap 95% interval.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

from .config import Config

# metric: (dry direction, validity column, family)
METRICS: dict[str, tuple[str, str, str]] = {
    "total_window": ("low", "valid_total_window", "total"),
    "total_sep": ("low", "valid_total_sep", "total"),
    "total_oct": ("low", "valid_total_oct", "total"),
    "driest30_mm": ("low", "valid_driest30", "total"),
    "driest45_mm": ("low", "valid_driest45", "total"),
    "n_wet_scaled": ("low", "valid_daily", "count"),
    "sdii": ("low", "valid_daily", "amount"),
    "max_daily": ("low", "valid_daily", "amount"),
    "n_heavy_p95": ("low", "valid_daily", "count"),
    "r95ptot": ("low", "valid_daily", "fraction"),
    "n_ge10": ("low", "valid_daily", "count"),
    "a_N": ("low", "valid_daily", "log"),
    "a_I": ("low", "valid_daily", "log"),
    "a_P": ("low", "valid_daily", "log"),
    "spell_max_win": ("high", "valid_daily", "count"),
    "spell_max_full": ("high", "valid_daily", "count"),
    "spell_mean": ("high", "valid_daily", "amount"),
    "n_spells_ge5": ("high", "valid_daily", "count"),
    "n_spells_ge7": ("high", "valid_daily", "count"),
    "n_spells_ge10": ("high", "valid_daily", "count"),
    "frac_dry": ("high", "valid_daily", "fraction"),
    "n_events": ("low", "valid_daily", "count"),
    "event_max_total": ("low", "valid_daily", "amount"),
    "onset_days": ("high", "valid_total_window", "days"),
}


def gamma_fit(x: np.ndarray) -> tuple[float, float, float]:
    """(q0, alpha, beta): probability of zero and Thom-estimator gamma parameters for x > 0."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    q0 = float((x <= 0).mean()) if len(x) else np.nan
    pos = x[x > 0]
    if len(pos) < 5 or np.allclose(pos, pos[0]):
        return q0, np.nan, np.nan
    A = math.log(pos.mean()) - np.log(pos).mean()
    alpha = (1 + math.sqrt(1 + 4 * A / 3)) / (4 * A)
    return q0, alpha, pos.mean() / alpha


def gamma_cdf_mixed(v: float, q0: float, alpha: float, beta: float) -> float:
    if not np.isfinite(alpha):
        return np.nan
    if v <= 0:
        return q0
    return q0 + (1 - q0) * float(stats.gamma.cdf(v, alpha, scale=beta))


def spi_and_rp(base: np.ndarray, x: float, n_boot: int = 1000, seed: int = 0) -> dict:
    q0, a, b = gamma_fit(base)
    H = gamma_cdf_mixed(x, q0, a, b)
    out = {"gamma_alpha": a, "gamma_beta": b, "p_fit": H}
    if not np.isfinite(H):
        return out | {"spi": np.nan, "rp_fit": np.nan, "rp_fit_lo": np.nan, "rp_fit_hi": np.nan}
    Hc = min(max(H, 1e-6), 1 - 1e-6)
    out["spi"] = float(stats.norm.ppf(Hc))
    out["rp_fit"] = 1 / Hc
    rng = np.random.default_rng(seed)
    hs = []
    for _ in range(n_boot):
        s = rng.choice(base, size=len(base), replace=True)
        q, aa, bb = gamma_fit(s)
        h = gamma_cdf_mixed(x, q, aa, bb)
        if np.isfinite(h):
            hs.append(min(max(h, 1e-6), 1 - 1e-6))
    if len(hs) >= n_boot // 2:
        lo, hi = np.percentile(hs, [2.5, 97.5])
        out["rp_fit_lo"], out["rp_fit_hi"] = 1 / hi, 1 / lo
    else:
        out["rp_fit_lo"] = out["rp_fit_hi"] = np.nan
    return out


def anomalies(metrics: pd.DataFrame, units: pd.DataFrame, cfg: Config, n_boot: int = 1000) -> pd.DataFrame:
    """Long table: one row per unit × threshold × metric for the target year."""
    min_n = int(cfg.completeness["tier2_min_baseline_years"])
    ok_units = units[units["status"].isin(["final", "provisional"])].set_index("unit_id")
    base_mask = metrics["year"].isin(cfg.baseline_years)
    rows = []
    for (uid, t), g in metrics[metrics["unit_id"].isin(ok_units.index)].groupby(["unit_id", "threshold"]):
        tgt = g[g["year"] == cfg.target_year]
        if tgt.empty:
            continue
        tgt = tgt.iloc[0]
        gb = g[base_mask.loc[g.index]]
        prov = ok_units.at[uid, "status"] == "provisional"
        for m, (direction, vcol, family) in METRICS.items():
            if m not in g:
                continue
            valid_t = bool(tgt.get(vcol, False))
            # Provisional units: totals are lower bounds; still reported, flagged.
            if not valid_t and not (prov and vcol.startswith("valid_total")):
                continue
            base = gb.loc[gb[vcol].astype(bool), m].to_numpy(dtype=float)
            x = float(tgt[m]) if pd.notna(tgt[m]) else np.nan
            if m == "onset_days":
                # censored = not reached by the end date: treat as later than any observed onset
                base = np.where(np.isnan(base) & gb.loc[gb[vcol].astype(bool), "onset_censored"].astype(bool).to_numpy(), np.inf, base)
                if np.isnan(x) and bool(tgt.get("onset_censored", False)):
                    x = np.inf
            base = base[~np.isnan(base)]
            n = len(base)
            if n < min_n or np.isnan(x):
                continue
            s_base, s_x = (base, x) if direction == "low" else (-base, -x)
            k = float((s_base < s_x).sum() + 0.5 * (s_base == s_x).sum())
            med = float(np.median(base[np.isfinite(base)])) if np.isfinite(base).any() else np.nan
            r = {
                "unit_id": uid, "threshold": t, "metric": m, "family": family, "dry_direction": direction,
                "value": x, "n_baseline": n, "baseline_median": med,
                "baseline_min": float(np.min(base)), "baseline_max": float(np.max(base)),
                "rank_dry": k + 1, "n_years": n + 1,
                "pctl": 100.0 * float((base < x).sum() + 0.5 * (base == x).sum()) / n,
                "p_dry": (k + 1) / (n + 1), "rp_emp": (n + 1) / (k + 1),
                "record_dry": bool(k == 0 and (s_base > s_x).all()),
                "provisional": prov, "lower_bound": bool(prov and family == "total" and not valid_t),
            }
            if family in ("total", "count", "amount") and np.isfinite(x) and np.isfinite(med):
                r["diff_from_median"] = x - med
                r["pct_of_median"] = 100.0 * x / med if med > 0 else np.nan
            if family == "total":
                r.update(spi_and_rp(base, x, n_boot=n_boot))
                r["rp_fit_extrapolated"] = bool(np.isfinite(r.get("rp_fit", np.nan)) and r["rp_fit"] > 2 * (n + 1))
                # Bootstrap upper bounds explode when the fit is unstable; report them as unbounded.
                if np.isfinite(r.get("rp_fit_hi", np.nan)) and r["rp_fit_hi"] > 10 * (n + 1):
                    r["rp_fit_hi"] = np.inf
            rows.append(r)
    out = pd.DataFrame(rows)
    if len(out):
        out = out.merge(units[["unit_id", "name", "region", "lon", "lat", "elev_m", "tier", "status", "kind"]],
                        on="unit_id", how="left")
    return out


def wide_summary(anom: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """One row per unit for the primary threshold: the headline numbers."""
    t0 = cfg.wet_thresholds[0]
    a = anom[anom["threshold"] == t0]
    if a.empty:
        return a
    keys = ["unit_id", "name", "region", "lon", "lat", "elev_m", "tier", "status", "kind"]
    base = a[keys].drop_duplicates("unit_id").set_index("unit_id")
    pieces = {}
    spec = {
        "total_oct": ["value", "baseline_median", "pct_of_median", "pctl", "rank_dry", "n_years", "spi", "rp_emp",
                      "record_dry", "lower_bound"],
        "total_sep": ["value", "baseline_median", "pct_of_median", "pctl", "rank_dry", "n_years", "spi"],
        "driest30_mm": ["value", "baseline_median", "pct_of_median", "pctl", "rank_dry", "n_years", "record_dry"],
        "driest45_mm": ["value", "baseline_median", "pct_of_median", "pctl", "rank_dry", "n_years", "record_dry"],
        "total_window": ["value", "baseline_median", "pct_of_median", "pctl", "rank_dry", "n_years", "spi", "rp_emp",
                         "rp_fit", "rp_fit_lo", "rp_fit_hi", "record_dry", "lower_bound"],
        "n_wet_scaled": ["value", "baseline_median", "pctl", "rank_dry"],
        "sdii": ["value", "baseline_median", "rank_dry"],
        "a_N": ["value"], "a_I": ["value"],
        "r95ptot": ["value", "baseline_median"],
        "n_heavy_p95": ["value", "baseline_median"],
        "spell_max_win": ["value", "baseline_median", "pctl", "rank_dry", "n_years"],
        "frac_dry": ["value", "rank_dry"],
        "onset_days": ["value", "baseline_median", "rank_dry"],
    }
    for m, cols in spec.items():
        s = a[a["metric"] == m].set_index("unit_id")[cols]
        s.columns = [f"{m}__{c}" for c in cols]
        pieces[m] = s
    out = base.join(list(pieces.values()), how="left").reset_index()
    sort = "total_oct__pct_of_median" if "total_oct__pct_of_median" in out else "total_window__pct_of_median"
    return out.sort_values(["region", sort]).reset_index(drop=True)
