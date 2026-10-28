"""Temperature: Tmax, Tmin and DTR (Tmax - Tmin) period means, tiers and 2026 anomalies.

Reads the standardized daily files written by the inventory stage (no downloads).
- QC: flagged-missing values dropped; values outside -40..45 C dropped; days with Tmax < Tmin dropped.
- A period mean counts if no more than 5 days per 30 are missing and no more than 3 in a row
  (WMO-style "5/3" rule scaled to the period length).
- Composites: only those listed in config temperature.composites are joined. The zero-overlap
  airport ID changes (2012-13) show 0.2-1.1 C steps against neighbours, so they are not joined.
- Tiers per variable and period: Tier 1 >= 22 valid baseline years, Tier 2 >= 12, both + target year.
- Co-located duplicate IDs (< 1 km, |dz| < 20 m, both Tier 1/2) are counted once (longer baseline kept).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import composites
from .composites import haversine_km
from .config import Config

log = logging.getLogger(__name__)
VARS = ("tmax", "tmin", "dtr")


def _qc(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    for c in ("tmax", "tmin"):
        if c not in d:
            d[c] = np.nan
        fl = d.get(f"{c}_flag")
        if fl is not None:
            bad = fl.astype("object").map(lambda v: v is not None and "M" in str(v)).astype(bool)
            d.loc[bad, c] = np.nan
        d.loc[(d[c] < -40) | (d[c] > 45), c] = np.nan
    inv = d["tmax"] < d["tmin"]
    d.loc[inv, ["tmax", "tmin"]] = np.nan
    d["dtr"] = d["tmax"] - d["tmin"]
    return d


def _max_run(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    m = np.r_[0, mask.astype(np.int8), 0]
    dd = np.diff(m)
    return int((np.flatnonzero(dd == -1) - np.flatnonzero(dd == 1)).max())


def periods(cfg: Config) -> dict[str, tuple[tuple[int, int], tuple[int, int]]]:
    e = (cfg.end_date.month, cfg.end_date.day)
    return {"oct": ((10, 1), e), "window": ((9, 1), e), "sep": ((9, 1), (9, 30))}


def period_stats(d: pd.DataFrame, years: list[int], pers: dict) -> pd.DataFrame:
    d = d.set_index("date").sort_index()
    d = d[~d.index.duplicated(keep="last")]
    rows = []
    for y in years:
        for per, (a, b) in pers.items():
            idx = pd.date_range(pd.Timestamp(y, *a), pd.Timestamp(y, *b), freq="D")
            sub = d.reindex(idx)
            n = len(idx)
            allow = round(5 * n / 30)
            r = {"year": y, "period": per, "n_days": n}
            for v in VARS:
                ok = sub[v].notna().to_numpy()
                r[f"{v}_n"] = int(ok.sum())
                r[f"{v}_mean"] = float(sub[v].mean()) if ok.any() else np.nan
                r[f"{v}_valid"] = bool((n - ok.sum()) <= allow and _max_run(~ok) <= 3)
            r["tmax_max"] = float(sub["tmax"].max()) if sub["tmax"].notna().any() else np.nan
            r["tmin_min"] = float(sub["tmin"].min()) if sub["tmin"].notna().any() else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


def build(cfg: Config, years: list[int] | None = None, start=None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    inv = pd.read_parquet(cfg.data_dir / "interim" / "station_inventory.parquet")
    inv["climate_id"] = inv["climate_id"].astype(str)
    meta = inv.set_index("climate_id")
    ddir = cfg.data_dir / "interim" / "daily"
    daily = {}
    for cid in inv.loc[inv["api_has_daily"].astype(bool), "climate_id"]:
        p = ddir / f"{cid}.parquet"
        if p.exists():
            daily[cid] = _qc(pd.read_parquet(p))
    keep = set(cfg.raw.get("temperature", {}).get("composites", ["C_1018610"]))
    acc = composites.load_accepted(cfg.path("composites_accepted"))
    acc = acc[acc["composite_id"].isin(keep)] if len(acc) else acc
    units, members_of = [], {}
    for cid, mem in acc.groupby("composite_id", sort=False):
        s = composites.build_series(mem, daily, cid)
        if s.empty:
            continue
        daily[cid] = s.drop(columns=["source_id"])
        last = meta.loc[mem["climate_id"].iloc[-1]]
        units.append({"unit_id": cid, "kind": "composite", "members": ";".join(mem["climate_id"]), "name": last["name"],
                      "region": str(last["region"]), "lon": last["lon"], "lat": last["lat"], "elev_m": last["elev_m"]})
        for m in mem["climate_id"]:
            members_of[m] = cid
    for cid in daily:
        if cid in members_of or cid.startswith("C_") or cid not in meta.index:
            continue
        r = meta.loc[cid]
        units.append({"unit_id": cid, "kind": "station", "members": cid, "name": r["name"], "region": str(r["region"]),
                      "lon": r["lon"], "lat": r["lat"], "elev_m": r["elev_m"]})
    units = pd.DataFrame(units)
    years = years or cfg.analysis_years
    pers = periods(cfg)
    stats = []
    for uid in units["unit_id"]:
        st = period_stats(daily[uid], years, pers)
        st.insert(0, "unit_id", uid)
        stats.append(st)
    stats = pd.concat(stats, ignore_index=True)
    c = cfg.completeness
    base = cfg.baseline_years
    for v in VARS:
        for per in pers:
            g = stats[stats["period"] == per]
            nb = g[g["year"].isin(base)].groupby("unit_id")[f"{v}_valid"].sum()
            tv = g[g["year"] == cfg.target_year].set_index("unit_id")[f"{v}_valid"]
            nbv = units["unit_id"].map(nb).fillna(0)
            tvv = units["unit_id"].map(tv).fillna(False).astype(bool)
            units[f"tier_{v}_{per}"] = np.where(tvv & (nbv >= int(c["tier1_min_baseline_years"])), 1,
                                                np.where(tvv & (nbv >= int(c["tier2_min_baseline_years"])), 2, 3))
            units[f"nbase_{v}_{per}"] = nbv
    units["duplicate_of"] = None
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            a, b = units.iloc[i], units.iloc[j]
            if a["tier_tmax_oct"] <= 2 and b["tier_tmax_oct"] <= 2 and \
                    haversine_km(a.lon, a.lat, b.lon, b.lat) < 1.0 and abs((a.elev_m or 0) - (b.elev_m or 0)) < 20:
                lose, keep_u = (i, b) if a["nbase_tmax_oct"] < b["nbase_tmax_oct"] else (j, a)
                units.loc[units.index[lose], "duplicate_of"] = keep_u["unit_id"]
    return units, stats, daily


def anomalies(units: pd.DataFrame, stats: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    ty, base = cfg.target_year, cfg.baseline_years
    u = units.set_index("unit_id")
    rows = []
    for (uid, per), g in stats.groupby(["unit_id", "period"]):
        if pd.notna(u.at[uid, "duplicate_of"]):
            continue
        tgt = g[g["year"] == ty]
        if tgt.empty:
            continue
        tgt = tgt.iloc[0]
        for v in VARS:
            tier = u.at[uid, f"tier_{v}_{per}"]
            if tier > 2:
                continue
            b = g[g["year"].isin(base) & g[f"{v}_valid"]][f"{v}_mean"].to_numpy()
            x = tgt[f"{v}_mean"]
            rows.append({
                "unit_id": uid, "period": per, "var": v, "tier": int(tier), "value": x, "n_baseline": len(b),
                "base_mean": b.mean(), "base_sd": b.std(ddof=1), "anom": x - b.mean(), "z": (x - b.mean()) / b.std(ddof=1),
                "rank_high": float((b > x).sum() + 0.5 * (b == x).sum()) + 1,
                "rank_low": float((b < x).sum() + 0.5 * (b == x).sum()) + 1, "n_years": len(b) + 1,
                "pctl": 100.0 * float((b < x).sum() + 0.5 * (b == x).sum()) / len(b),
                "record_high": bool((b < x).all()), "record_low": bool((b > x).all()),
            })
    an = pd.DataFrame(rows)
    if len(an):
        an = an.merge(units[["unit_id", "kind", "name", "region", "lon", "lat", "elev_m"]], on="unit_id")
    return an


def run(cfg: Config) -> dict[str, Path]:
    units, stats, _ = build(cfg)
    an = anomalies(units, stats, cfg)
    t = cfg.tables_dir
    paths = {
        "temperature_units": t / "temperature_units.csv",
        "temperature_stats": t / "temperature_unit_year.csv",
        "temperature_anomalies": t / f"temperature_anomalies_{cfg.target_year}.csv",
    }
    units.to_csv(paths["temperature_units"], index=False)
    stats.to_csv(paths["temperature_stats"], index=False)
    an.to_csv(paths["temperature_anomalies"], index=False)
    if len(an):
        g = an[an["period"] == "oct"].groupby("var")
        print(f"\nTemperature, Oct 1–{cfg.end_date.day} {cfg.target_year} (median of {g.size().max()} units):")
        print(pd.DataFrame({"anom_C": g["anom"].median().round(2), "top3_warm": g["rank_high"].apply(lambda r: int((r <= 3).sum())),
                            "median_rank": g["rank_high"].median()}).to_string())
    return paths
