"""Quality control: flag statuses, accumulation blocks, period validity and completeness.

Terminology
- *daily-valid*: a day usable for daily-distribution, spell and event metrics.
- *total-valid*: a day whose precipitation is accounted for in a period total. Days in an
  accumulation block are total-valid only when the whole block lies inside the period.
See DESIGN.md §4.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config, Period

log = logging.getLogger(__name__)

STATUSES = ["ok", "trace", "missing", "accumulated", "estimated", "uncertain", "incomplete", "unknown", "invalid"]
MAX_ACCUM_DAYS_DEFAULT = 10


@dataclass
class FlagRules:
    flag_status: dict[str, str]
    status_treatment: dict[str, str]
    max_accum_days: int = MAX_ACCUM_DAYS_DEFAULT

    @classmethod
    def from_config(cls, cfg: Config) -> "FlagRules":
        q = cfg.qc
        return cls(
            flag_status={str(k): str(v) for k, v in q["flag_status"].items()},
            status_treatment={str(k): str(v) for k, v in q.get("status_treatment", {}).items()},
            max_accum_days=int(q.get("max_accumulation_days", MAX_ACCUM_DAYS_DEFAULT)),
        )

    def status_of(self, flag) -> str:
        if flag is None or (not isinstance(flag, str) and pd.isna(flag)):
            return "ok"
        flag = flag.strip()
        if not flag:
            return "ok"
        # Some records carry more than one flag character; use the most restrictive known one.
        for ch in ("M", "A", "F"):
            if ch in flag and ch in self.flag_status:
                return self.flag_status[ch]
        if flag in self.flag_status:
            return self.flag_status[flag]
        for ch in flag:
            if ch in self.flag_status:
                return self.flag_status[ch]
        return "unknown"


def complete_index(daily: pd.DataFrame, start: dt.date, end: dt.date, climate_id: str) -> pd.DataFrame:
    """Reindex a standardized daily frame onto every calendar day in [start, end]."""
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D", name="date")
    d = daily.set_index("date") if len(daily) else pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
    d = d[~d.index.duplicated(keep="last")].reindex(idx)
    d["climate_id"] = climate_id
    for c in ("precip", "tmax", "tmin"):
        if c not in d:
            d[c] = np.nan
    for c in ("precip_flag", "tmax_flag", "tmin_flag"):
        if c not in d:
            d[c] = None
    return d


def _find_accumulation_blocks(vals: np.ndarray, is_acc: np.ndarray, max_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (block_id per day, -1 if none) and an 'unresolved' mask.

    Encoding A: the accumulated flag sits on the day carrying the total; preceding no-value days
    belong to the block. Encoding B: a run of no-value accumulated-flag days is followed by the
    day carrying the total. Both are handled. A block longer than max_days is unresolved.
    """
    n = len(vals)
    block = np.full(n, -1, dtype=np.int64)
    unresolved = np.zeros(n, dtype=bool)
    isnan = np.isnan(vals)
    bid = 0
    # Encoding A
    for i in np.flatnonzero(is_acc & ~isnan):
        j = i
        while j - 1 >= 0 and isnan[j - 1] and block[j - 1] == -1:
            j -= 1
        if i - j + 1 > max_days:
            unresolved[j : i + 1] = True
        else:
            block[j : i + 1] = bid
            bid += 1
    # Encoding B
    i = 0
    while i < n:
        if is_acc[i] and isnan[i] and block[i] == -1 and not unresolved[i]:
            s = i
            while i + 1 < n and is_acc[i + 1] and isnan[i + 1] and block[i + 1] == -1:
                i += 1
            e = i
            if e + 1 < n and not isnan[e + 1] and block[e + 1] == -1 and (e + 2 - s) <= max_days:
                block[s : e + 2] = bid
                bid += 1
                i = e + 1
            else:
                unresolved[s : e + 1] = True
        i += 1
    return block, unresolved


def annotate(daily: pd.DataFrame, rules: FlagRules) -> pd.DataFrame:
    """Add status, validity and accumulation columns to a complete-index daily frame."""
    d = daily.copy()
    flags = d["precip_flag"].tolist()
    status = np.array([rules.status_of(f) for f in flags], dtype=object)
    vals = d["precip"].to_numpy(dtype=float).copy()

    # Trace: 0 mm, valid.
    is_trace = status == "trace"
    vals[is_trace] = 0.0
    # Configurable treatments (e.g. incomplete -> invalid).
    for st, treat in rules.status_treatment.items():
        if treat == "invalid":
            m = status == st
            vals[m] = np.nan
            status[m] = "invalid"
    # Missing flag means missing whatever the value says.
    vals[status == "missing"] = np.nan

    is_acc = status == "accumulated"
    block, unresolved = _find_accumulation_blocks(vals, is_acc, rules.max_accum_days)
    status[unresolved] = "missing"
    vals[unresolved] = np.nan
    # Days without a value and not part of a block are missing.
    nov = np.isnan(vals) & (block == -1)
    status[nov & (status != "invalid")] = "missing"
    status[block >= 0] = "accumulated"

    d["precip"] = vals
    d["status"] = status
    d["acc_block"] = block
    d["is_trace"] = is_trace
    d["daily_valid"] = ~np.isnan(vals) & (block == -1)
    # Temperature validity (milestone 5 will refine; flags 'M' already NaN in the API).
    for c in ("tmax", "tmin"):
        fl = d[f"{c}_flag"].astype("object")
        missing_flag = fl.map(lambda v: v is not None and "M" in str(v)).to_numpy(dtype=bool)
        d[f"{c}_valid"] = d[c].notna().to_numpy() & ~missing_flag
    return d


@dataclass
class PeriodStats:
    n_days: int
    n_valid_total: int
    total_mm: float
    n_valid_daily: int
    max_consec_missing_daily: int
    counts: dict[str, int]
    n_tmax: int
    n_tmin: int


def _max_run(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    m = np.concatenate([[0], mask.astype(np.int8), [0]])
    d = np.diff(m)
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return int((ends - starts).max())


def period_stats(ann: pd.DataFrame, a: dt.date, b: dt.date) -> PeriodStats:
    """Validity and totals for one period of an annotated, complete-index frame."""
    sub = ann.loc[pd.Timestamp(a) : pd.Timestamp(b)]
    n_days = (b - a).days + 1
    block = sub["acc_block"].to_numpy()
    vals = sub["precip"].to_numpy(dtype=float)
    daily_valid = sub["daily_valid"].to_numpy(dtype=bool)

    total_valid = daily_valid.copy()
    total = float(np.nansum(np.where(daily_valid, vals, 0.0)))
    in_block = block >= 0
    if in_block.any():
        full = ann["acc_block"]
        for bid in np.unique(block[in_block]):
            dates = full.index[full.to_numpy() == bid]
            if dates.min() >= pd.Timestamp(a) and dates.max() <= pd.Timestamp(b):
                m = block == bid
                total_valid |= m
                total += float(np.nansum(vals[m]))
    status = sub["status"].to_numpy()
    counts = {s: int((status == s).sum()) for s in ("trace", "estimated", "uncertain", "incomplete", "unknown", "invalid")}
    counts["accumulated_days"] = int(in_block.sum())
    counts["missing"] = int(n_days - total_valid.sum())
    return PeriodStats(
        n_days=n_days,
        n_valid_total=int(total_valid.sum()),
        total_mm=round(total, 2),
        n_valid_daily=int(daily_valid.sum()),
        max_consec_missing_daily=_max_run(~daily_valid),
        counts=counts,
        n_tmax=int(sub["tmax_valid"].sum()),
        n_tmin=int(sub["tmin_valid"].sum()),
    )


def completeness_table(ann: pd.DataFrame, cfg: Config, years: list[int]) -> pd.DataFrame:
    """One row per (year, period) with validity statistics for a single station."""
    c = cfg.completeness
    rows = []
    for y in years:
        for name, per in cfg.periods.items():
            a, b = per.bounds(y)
            if pd.Timestamp(a) < ann.index.min() or pd.Timestamp(b) > ann.index.max():
                # Outside the downloaded range: count as entirely missing.
                ps = PeriodStats(per.n_days(y), 0, np.nan, 0, per.n_days(y), {"missing": per.n_days(y)}, 0, 0)
            else:
                ps = period_stats(ann, a, b)
            n_missing_total = ps.n_days - ps.n_valid_total
            n_missing_daily = ps.n_days - ps.n_valid_daily
            rows.append(
                {
                    "year": y,
                    "period": name,
                    "n_days": ps.n_days,
                    "n_valid_total": ps.n_valid_total,
                    "n_missing_total": n_missing_total,
                    "total_mm": ps.total_mm if ps.n_valid_total else np.nan,
                    "n_valid_daily": ps.n_valid_daily,
                    "n_missing_daily": n_missing_daily,
                    "max_consec_missing_daily": ps.max_consec_missing_daily,
                    "valid_total": n_missing_total <= int(c["totals_max_missing_days"]),
                    "valid_daily": n_missing_daily <= int(c["daily_max_missing_days"]),
                    "n_trace": ps.counts.get("trace", 0),
                    "n_estimated": ps.counts.get("estimated", 0),
                    "n_uncertain": ps.counts.get("uncertain", 0),
                    "n_incomplete": ps.counts.get("incomplete", 0),
                    "n_unknown_flag": ps.counts.get("unknown", 0),
                    "n_invalid_flag": ps.counts.get("invalid", 0),
                    "n_accumulated_days": ps.counts.get("accumulated_days", 0),
                    "n_tmax": ps.n_tmax,
                    "n_tmin": ps.n_tmin,
                }
            )
    return pd.DataFrame(rows)


def assign_tiers(comp: pd.DataFrame, cfg: Config, id_col: str = "climate_id") -> pd.DataFrame:
    """Per-station tier from the window-period completeness rows (all stations stacked)."""
    c = cfg.completeness
    w = comp[comp["period"] == "window"]
    base = w[w["year"].isin(cfg.baseline_years)]
    tgt = w[w["year"] == cfg.target_year].set_index(id_col)
    out = base.groupby(id_col).agg(
        n_baseline_valid_total=("valid_total", "sum"),
        n_baseline_valid_daily=("valid_daily", "sum"),
        n_baseline_any_data=("n_valid_daily", lambda s: int((s > 0).sum())),
    )
    out["target_valid_total"] = tgt["valid_total"].reindex(out.index).fillna(False).astype(bool)
    out["target_valid_daily"] = tgt["valid_daily"].reindex(out.index).fillna(False).astype(bool)
    out["target_missing_total"] = tgt["n_missing_total"].reindex(out.index)
    vy = base[base["valid_total"]].groupby(id_col)["year"].agg(["min", "max"])
    out["first_valid_year"] = vy["min"].reindex(out.index)
    out["last_valid_baseline_year"] = vy["max"].reindex(out.index)
    t1, t2 = int(c["tier1_min_baseline_years"]), int(c["tier2_min_baseline_years"])
    tier = np.where(
        out["target_valid_total"] & (out["n_baseline_valid_total"] >= t1),
        1,
        np.where(out["target_valid_total"] & (out["n_baseline_valid_total"] >= t2), 2, 3),
    )
    out["tier"] = tier
    return out.reset_index()
