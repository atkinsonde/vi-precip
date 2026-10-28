"""Neighbour-based homogeneity checks (DESIGN.md §5 threshold decision, §4.5 composites).

For each final Tier 1/2 unit and wet threshold t, an annual difference series is built against
up to four neighbours (final Tier 1/2, ≤ 75 km):
    d_y = mean over neighbours of [f_unit(y) − f_nbr(y)], f = share of jointly valid days ≥ t
and likewise the log ratio of annual totals over jointly valid days.
- Pettitt test on d_y finds a single change point anywhere (no metadata needed).
- For composites, a Welch t-test compares d_y before and after each known switch year.
If breaks show up at 0.2 mm much more often than at 1.0 mm, small amounts are not comparable
across instruments and 1.0 mm stays the primary threshold.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats

from .composites import load_accepted
from .config import Config
from .metrics import block_totals, day_states, wet_frequency
from .units import neighbours

log = logging.getLogger(__name__)


def pettitt(x: np.ndarray) -> tuple[int, float, float]:
    """Return (index of last point before the change, K statistic, approximate p-value)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    r = stats.rankdata(x)
    U = np.array([2 * r[: k + 1].sum() - (k + 1) * (n + 1) for k in range(n - 1)])
    k = int(np.argmax(np.abs(U)))
    K = float(np.abs(U[k]))
    p = float(min(1.0, 2 * np.exp(-6 * K**2 / (n**3 + n**2))))
    return k, K, p


def _states(ann: pd.DataFrame, t: float) -> tuple[pd.Series, float]:
    st = pd.Series(day_states(ann, t, block_totals(ann)), index=ann.index)
    known = st.isin(["W", "D"])
    p = float((st[known] == "W").mean()) if known.any() else np.nan
    return st, p


def annual_difference(su: tuple[pd.Series, float], sn: dict[str, tuple[pd.Series, float]],
                      pu: pd.Series, pn: dict[str, pd.Series], years: list[int]) -> pd.DataFrame:
    """su/sn: (day states, wet probability) for unit and neighbours; pu/pn: daily-valid precip (NaN otherwise)."""
    rows = []
    for y in years:
        dfreq, dlog = [], []
        a_st = su[0].loc[str(y)]
        for n, (n_st, n_p) in sn.items():
            b_st = n_st.reindex(a_st.index)
            ok = (a_st != "M").to_numpy() & (b_st != "M").fillna(False).to_numpy() if b_st.notna().any() else None
            if ok is None or ok.sum() < 200:
                continue
            fa = wet_frequency(a_st.to_numpy()[ok], su[1])[0]
            fb = wet_frequency(b_st.to_numpy()[ok], n_p)[0]
            dfreq.append(fa - fb)
            va = pu.loc[str(y)]
            vb = pn[n].reindex(va.index)
            both = va.notna() & vb.notna()
            if both.sum() >= 200 and va[both].sum() > 0 and vb[both].sum() > 0:
                dlog.append(np.log(va[both].sum() / vb[both].sum()))
        rows.append({"year": y, "d_freq": np.mean(dfreq) if dfreq else np.nan,
                     "d_logratio": np.mean(dlog) if dlog else np.nan, "n_nbr": len(dfreq)})
    return pd.DataFrame(rows)


def run(units: pd.DataFrame, annotated: dict[str, pd.DataFrame], cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (per-unit break table, per-unit annual difference series)."""
    years = [y for y in range(cfg.download_start.year, cfg.target_year)]  # complete calendar years only
    pool = (units["tier"] <= 2)
    acc = load_accepted(cfg.path("composites_accepted"))
    switches: dict[str, list[int]] = {}
    for cid, g in acc.groupby("composite_id"):
        sw = [d.year for d in g["use_from"].dropna()]
        switches[cid] = sw
    rows, series = [], []
    ids = units.loc[pool, "unit_id"].tolist()
    precip = {u: annotated[u]["precip"].where(annotated[u]["daily_valid"]) for u in ids}
    for t in cfg.wet_thresholds:
        st = {u: _states(annotated[u], t) for u in ids}
        for uid in ids:
            nb = neighbours(units, uid, k=4, max_km=75.0, pool=pool)
            if len(nb) < 2:
                continue
            ds = annual_difference(st[uid], {n: st[n] for n in nb["unit_id"]}, precip[uid],
                                   {n: precip[n] for n in nb["unit_id"]}, years)
            ds = ds.dropna(subset=["d_freq"])
            ds.insert(0, "threshold", t)
            ds.insert(0, "unit_id", uid)
            series.append(ds)
            if len(ds) < 10:
                continue
            k, K, p = pettitt(ds["d_freq"].to_numpy())
            kr, Kr, pr = pettitt(ds["d_logratio"].fillna(ds["d_logratio"].median()).to_numpy())
            r = {
                "unit_id": uid, "threshold": t, "n_years": len(ds), "neighbours": ";".join(nb["unit_id"]),
                "pettitt_freq_year": int(ds["year"].iloc[k + 1]), "pettitt_freq_p": p,
                "freq_shift": float(ds["d_freq"].iloc[k + 1:].mean() - ds["d_freq"].iloc[: k + 1].mean()),
                "pettitt_ratio_year": int(ds["year"].iloc[kr + 1]), "pettitt_ratio_p": pr,
                "ratio_shift": float(np.exp(ds["d_logratio"].iloc[kr + 1:].mean() - ds["d_logratio"].iloc[: kr + 1].mean())),
            }
            for sy in switches.get(uid, []):
                before = ds.loc[ds["year"] < sy, "d_freq"]
                after = ds.loc[ds["year"] > sy, "d_freq"]
                if len(before) >= 3 and len(after) >= 3:
                    tt = stats.ttest_ind(before, after, equal_var=False)
                    r[f"switch_{sy}_freq_shift"] = float(after.mean() - before.mean())
                    r[f"switch_{sy}_freq_p"] = float(tt.pvalue)
                    lb = ds.loc[ds["year"] < sy, "d_logratio"].dropna()
                    la = ds.loc[ds["year"] > sy, "d_logratio"].dropna()
                    if len(lb) >= 3 and len(la) >= 3:
                        r[f"switch_{sy}_ratio_shift"] = float(np.exp(la.mean() - lb.mean()))
                        r[f"switch_{sy}_ratio_p"] = float(stats.ttest_ind(lb, la, equal_var=False).pvalue)
            rows.append(r)
    return pd.DataFrame(rows), (pd.concat(series, ignore_index=True) if series else pd.DataFrame())


def summarize(breaks: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    if breaks.empty:
        return breaks
    g = breaks.groupby("threshold")
    return pd.DataFrame({
        "n_units": g.size(),
        "freq_breaks": g["pettitt_freq_p"].apply(lambda p: int((p < alpha).sum())),
        "ratio_breaks": g["pettitt_ratio_p"].apply(lambda p: int((p < alpha).sum())),
        "median_abs_freq_shift": g["freq_shift"].apply(lambda s: float(np.median(np.abs(s)))),
    }).reset_index()
