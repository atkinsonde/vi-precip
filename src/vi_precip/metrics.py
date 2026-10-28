"""Precipitation metrics per unit × year × wet threshold (DESIGN.md §6).

Day states for threshold t:
- wet:        daily-valid and precip ≥ t
- dry:        daily-valid and precip < t, OR inside an accumulation block whose total < t
              (every day of such a block must have been below t)
- ambiguous:  inside a block whose total ≥ t (at least one wet day, unknown which)
- missing:    everything else
Occurrence and spell metrics use wet/dry; amount metrics (intensity, bins, maxima) use
daily-valid days only; totals and events also use block totals.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger(__name__)

FIXED_EDGES = [0.0, 0.2, 1.0, 5.0, 10.0, 25.0, 50.0, np.inf]
FIXED_LABELS = ["<0.2", "0.2–1", "1–5", "5–10", "10–25", "25–50", "≥50"]
REL_LABELS = ["dry", "<P50", "P50–75", "P75–90", "P90–95", ">P95"]
SPELL_LOOKBACK_MD = (6, 1)


# ------------------------------------------------------------------ day states
def block_totals(ann: pd.DataFrame) -> pd.Series:
    """Per-day total of the accumulation block the day belongs to (NaN outside blocks)."""
    b = ann["acc_block"]
    tot = ann["precip"].groupby(b).transform(lambda s: s.sum(min_count=1))
    return tot.where(b >= 0)


def day_states(ann: pd.DataFrame, t: float, btot: pd.Series | None = None) -> np.ndarray:
    """Array of 'W' wet, 'D' dry, 'A' ambiguous, 'M' missing."""
    if btot is None:
        btot = block_totals(ann)
    v = ann["precip"].to_numpy(dtype=float)
    dv = ann["daily_valid"].to_numpy(dtype=bool)
    inb = ann["acc_block"].to_numpy() >= 0
    bt = btot.to_numpy(dtype=float)
    s = np.full(len(v), "M", dtype="<U1")
    s[dv & (v >= t)] = "W"
    s[dv & (v < t)] = "D"
    s[inb & (bt < t)] = "D"
    s[inb & (bt >= t)] = "A"
    return s


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of True runs."""
    m = np.r_[0, mask.astype(np.int8), 0]
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def wet_frequency(states: np.ndarray, p: float) -> tuple[float, float, int]:
    """Wet-day frequency counting ambiguous accumulation blocks without bias.

    Dropping ambiguous (rainy) blocks would bias the frequency low, because only rainy blocks are
    ambiguous. A block of L days known to hold ≥1 wet day contributes its expected wet-day count
    E = L·p / (1 − (1 − p)^L), with p the unit's unconditional wet-day probability.
    Returns (frequency, estimated wet days, days counted)."""
    n_w = int((states == "W").sum())
    n_d = int((states == "D").sum())
    lens = [e - s + 1 for s, e in runs(states == "A")]
    if lens and np.isfinite(p) and 0 < p < 1:
        e = float(sum(L * p / (1 - (1 - p) ** L) for L in lens))
    else:
        e = float(len(lens))
    n = n_w + n_d + int(sum(lens))
    return ((n_w + e) / n if n else np.nan), n_w + e, n


# ------------------------------------------------------------------ references
def baseline_refs(ann: pd.DataFrame, cfg: Config, t: float, valid_years: list[int], sep_totals: list[float]) -> dict:
    """Station-specific references from baseline years: wet-day percentiles and median Sept total."""
    w = cfg.periods["window"]
    amounts = []
    for y in valid_years:
        a, b = w.bounds(y)
        sub = ann.loc[pd.Timestamp(a): pd.Timestamp(b)]
        v = sub["precip"].to_numpy(dtype=float)[sub["daily_valid"].to_numpy(dtype=bool)]
        amounts.append(v[v >= t])
    amounts = np.concatenate(amounts) if amounts else np.array([])
    refs = {f"p{q}": float(np.percentile(amounts, q)) if len(amounts) >= 20 else np.nan for q in (50, 75, 90, 95)}
    refs["n_wet_days_pooled"] = int(len(amounts))
    # unconditional wet-day probability on fully known days (for the ambiguous-block estimator)
    nw = nd = 0
    btot = block_totals(ann)
    for y in valid_years:
        a, b = w.bounds(y)
        sub = ann.loc[pd.Timestamp(a): pd.Timestamp(b)]
        st = day_states(sub, t, btot.loc[sub.index])
        nw += int((st == "W").sum())
        nd += int((st == "D").sum())
    refs["p_wet"] = nw / (nw + nd) if nw + nd else np.nan
    sep = [x for x in sep_totals if np.isfinite(x)]
    refs["sep_median"] = float(np.median(sep)) if len(sep) >= 5 else np.nan
    return refs


# --------------------------------------------------------------------- metrics
def year_metrics(ann: pd.DataFrame, cfg: Config, year: int, t: float, refs: dict,
                 btot: pd.Series) -> tuple[dict, list[dict], list[dict]]:
    """Return (metrics row, bin rows, spell rows) for one unit-year-threshold."""
    win = cfg.periods["window"]
    wa, wb = win.bounds(year)
    lb = dt.date(year, *SPELL_LOOKBACK_MD)
    seg = ann.loc[pd.Timestamp(lb): pd.Timestamp(wb)]
    if seg.empty:
        return {}, [], []
    states = day_states(seg, t, btot.loc[seg.index])
    in_win = np.asarray(seg.index >= pd.Timestamp(wa))
    v = seg["precip"].to_numpy(dtype=float)
    dv = seg["daily_valid"].to_numpy(dtype=bool)
    sw, vw, dvw = states[in_win], v[in_win], dv[in_win]
    n_days = int(in_win.sum())

    n_wet, n_dry = int((sw == "W").sum()), int((sw == "D").sum())
    n_known = n_wet + n_dry
    fw, n_wet_est, n_counted = wet_frequency(sw, refs.get("p_wet", np.nan))
    wet_amt = vw[(sw == "W")]
    valid_amt = vw[dvw]
    tot_valid = float(valid_amt.sum()) if len(valid_amt) else np.nan
    row: dict = {
        "n_days": n_days,
        "n_known": n_known,
        "n_ambiguous": int((sw == "A").sum()),
        "n_wet": n_wet,
        "n_dry": n_dry,
        "n_wet_est": n_wet_est,
        "frac_wet": fw,
        "n_wet_scaled": fw * n_days if n_counted else np.nan,
        "p_wet_mm": float(wet_amt.sum()),
        "sdii": float(wet_amt.mean()) if n_wet else np.nan,
        "max_daily": float(valid_amt.max()) if len(valid_amt) else np.nan,
        "n_ge10": int((valid_amt >= 10).sum()),
        "n_ge25": int((valid_amt >= 25).sum()),
    }
    p95 = refs.get("p95", np.nan)
    if np.isfinite(p95):
        heavy = valid_amt > p95
        row["n_heavy_p95"] = int(heavy.sum())
        row["r95ptot"] = float(valid_amt[heavy].sum() / tot_valid) if tot_valid and tot_valid > 0 else np.nan
    else:
        row["n_heavy_p95"] = np.nan
        row["r95ptot"] = np.nan

    # ---- bins (fraction of daily-valid days and of their precipitation)
    bins = []
    n_dv = len(valid_amt)
    if t == cfg.wet_thresholds[0]:  # fixed bins are threshold-independent; store once
        idx = np.digitize(valid_amt, FIXED_EDGES[1:-1], right=False)
        for k, lab in enumerate(FIXED_LABELS):
            m = idx == k
            bins.append({"scheme": "fixed", "bin": lab, "order": k, "n_days": int(m.sum()),
                         "frac_days": m.sum() / n_dv if n_dv else np.nan,
                         "mm": float(valid_amt[m].sum()),
                         "frac_mm": float(valid_amt[m].sum() / tot_valid) if tot_valid else np.nan})
    if all(np.isfinite(refs.get(f"p{q}", np.nan)) for q in (50, 75, 90, 95)):
        edges = [t, refs["p50"], refs["p75"], refs["p90"], refs["p95"]]
        cat = np.where(valid_amt < t, 0, 1 + np.digitize(valid_amt, edges[1:], right=False))
        for k, lab in enumerate(REL_LABELS):
            m = cat == k
            bins.append({"scheme": f"rel{t:g}", "bin": lab, "order": k, "n_days": int(m.sum()),
                         "frac_days": m.sum() / n_dv if n_dv else np.nan,
                         "mm": float(valid_amt[m].sum()),
                         "frac_mm": float(valid_amt[m].sum() / tot_valid) if tot_valid else np.nan})

    # ---- dry spells on the lookback+window series
    spells = []
    first_win = int(np.argmax(in_win))
    nseg = len(states)
    for s, e in runs(states == "D"):
        if e < first_win:
            continue
        lo = max(s, first_win)
        spells.append({
            "start": seg.index[s].date(), "end": seg.index[e].date(), "full_len": int(e - s + 1),
            "days_in_window": int(e - lo + 1),
            "left_censored": bool(s == 0), "right_censored": bool(e == nseg - 1),
            "touches_missing": bool((s > 0 and states[s - 1] in ("M", "A")) or (e + 1 < nseg and states[e + 1] in ("M", "A"))),
        })
    lens = np.array([sp["days_in_window"] for sp in spells]) if spells else np.array([], dtype=int)
    row.update({
        "n_spells": int(len(lens)),
        "spell_mean": float(lens.mean()) if len(lens) else 0.0,
        "spell_median": float(np.median(lens)) if len(lens) else 0.0,
        "spell_max_win": int(lens.max()) if len(lens) else 0,
        "spell_max_full": int(max((sp["full_len"] for sp in spells), default=0)),
        "n_spells_ge3": int((lens >= 3).sum()),
        "n_spells_ge5": int((lens >= 5).sum()),
        "n_spells_ge7": int((lens >= 7).sum()),
        "n_spells_ge10": int((lens >= 10).sum()),
        "frac_dry": 1 - fw if n_counted else np.nan,
        "n_spells_touch_missing": int(sum(sp["touches_missing"] for sp in spells)),
    })

    # ---- events: runs of wet or ambiguous days (blocks with total ≥ t count as wet runs)
    wetlike = np.isin(states, ["W", "A"])
    ev = []
    for s, e in runs(wetlike):
        if not in_win[s]:
            continue
        ev.append((float(np.nansum(v[s: e + 1])), int(e - s + 1)))
    row.update({
        "n_events": len(ev),
        "event_mean_total": float(np.mean([x for x, _ in ev])) if ev else 0.0,
        "event_max_total": float(max((x for x, _ in ev), default=0.0)),
        "event_max_dur": int(max((d for _, d in ev), default=0)),
    })

    # ---- wet-season onset: cumulative from Sep 1 reaches the baseline median September total
    thr = refs.get("sep_median", np.nan)
    wv = np.nan_to_num(v[in_win], nan=0.0)
    cum = np.cumsum(wv)
    if np.isfinite(thr) and thr > 0:
        hit = np.flatnonzero(cum >= thr)
        if len(hit):
            row["onset_days"] = int(hit[0] + 1)  # 1 = Sep 1
            row["onset_censored"] = False
            row["onset_missing_before"] = int((~dvw[: hit[0] + 1] & ~(seg["acc_block"].to_numpy()[in_win][: hit[0] + 1] >= 0)).sum())
        else:
            row["onset_days"] = np.nan
            row["onset_censored"] = True
            row["onset_missing_before"] = int((sw == "M").sum())
    else:
        row["onset_days"], row["onset_censored"], row["onset_missing_before"] = np.nan, False, np.nan
    row["onset_threshold_mm"] = thr
    return row, bins, spells


def unit_metrics(uid: str, ann: pd.DataFrame, comp: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """All years and thresholds for one unit. `comp` = this unit's completeness rows."""
    c = comp.set_index(["year", "period"])
    btot = block_totals(ann)
    base_years = cfg.baseline_years
    win_ok = {y: bool(c.loc[(y, "window"), "valid_daily"]) for y in cfg.analysis_years if (y, "window") in c.index}
    sep_ok = {y: bool(c.loc[(y, "sep"), "valid_total"]) for y in cfg.analysis_years if (y, "sep") in c.index}
    rows, binrows, spellrows = [], [], []
    for t in cfg.wet_thresholds:
        sep_tot = [float(c.loc[(y, "sep"), "total_mm"]) for y in base_years if sep_ok.get(y)]
        refs = baseline_refs(ann, cfg, t, [y for y in base_years if win_ok.get(y)], sep_tot)
        for y in cfg.analysis_years:
            r, b, s = year_metrics(ann, cfg, y, t, refs, btot)
            if not r:
                continue
            key = {"unit_id": uid, "year": y, "threshold": t}
            r = {**key, **r, "valid_daily": win_ok.get(y, False)}
            for per in cfg.periods:
                if (y, per) in c.index and per != "extended":
                    r[f"total_{per}"] = c.loc[(y, per), "total_mm"]
                    r[f"valid_total_{per}"] = bool(c.loc[(y, per), "valid_total"])
                    r[f"missing_total_{per}"] = int(c.loc[(y, per), "n_missing_total"])
            for k in ("p50", "p95"):
                r[f"ref_{k}"] = refs.get(k)
            rows.append(r)
            binrows += [{**key, **x} for x in b]
            spellrows += [{**key, **x} for x in s]
    m = pd.DataFrame(rows)
    if len(m):
        m = _decompose(m, cfg)
        for per, per_obj in cfg.periods.items():
            if per != "extended" and f"total_{per}" in m:
                m[f"rate_{per}"] = m[f"total_{per}"] / m["year"].map(per_obj.n_days)
    return m, pd.DataFrame(binrows), pd.DataFrame(spellrows)


def _decompose(m: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Additive log decomposition of wet-day precipitation into occurrence and intensity,
    relative to baseline geometric means: a_P = a_N + a_I exactly."""
    m = m.copy()
    for col in ("a_N", "a_I", "a_P"):
        m[col] = np.nan
    for t, g in m.groupby("threshold"):
        N = g["n_wet_scaled"].where(g["valid_daily"] & (g["n_wet_scaled"] > 0))
        I = g["sdii"].where(N.notna())
        lnN, lnI = np.log(N), np.log(I)
        base = g["year"].isin(cfg.baseline_years)
        aN = lnN - lnN[base].mean()
        aI = lnI - lnI[base].mean()
        m.loc[g.index, "a_N"] = aN
        m.loc[g.index, "a_I"] = aI
        m.loc[g.index, "a_P"] = aN + aI
    return m
