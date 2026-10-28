"""Analysis units: single stations, plus the composites accepted in config/composites.csv.

A station that belongs to an accepted composite is analysed only through that composite.
Each unit gets its annotated daily series (data/processed/daily/<unit_id>.parquet),
completeness, tier and status (final / provisional / insufficient).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import composites, qc
from .composites import haversine_km
from .config import Config

log = logging.getLogger(__name__)


def load_interim_daily(cfg: Config, ids: list[str]) -> dict[str, pd.DataFrame]:
    d = cfg.data_dir / "interim" / "daily"
    out = {}
    for cid in ids:
        p = d / f"{cid}.parquet"
        if p.exists():
            out[cid] = pd.read_parquet(p)
    return out


def build_units(cfg: Config, inventory: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    """Return (units table, annotated daily per unit, completeness rows per unit)."""
    rules = qc.FlagRules.from_config(cfg)
    start, end = cfg.download_start, cfg.end_date
    inv = inventory.copy()
    inv["climate_id"] = inv["climate_id"].astype(str)
    have = inv[inv["api_has_daily"].astype(bool)]
    daily = load_interim_daily(cfg, have["climate_id"].tolist())

    acc = composites.load_accepted(cfg.path("composites_accepted"))
    member_of: dict[str, str] = {}
    rows = []
    for cid, members in acc.groupby("composite_id", sort=False):
        ids = members["climate_id"].tolist()
        missing = [m for m in ids if m not in daily]
        if missing:
            log.warning("%s: members without downloaded data: %s", cid, missing)
        s = composites.build_series(members, daily, cid)
        if s.empty:
            log.warning("%s: no data; composite skipped", cid)
            continue
        daily[cid] = s.drop(columns=["source_id"])
        last = inv.set_index("climate_id").reindex(ids).dropna(subset=["lon"]).iloc[-1]
        rows.append({
            "unit_id": cid, "kind": "composite", "members": ";".join(ids), "name": f"{last['name']} (composite)",
            "region": last["region"], "lon": last["lon"], "lat": last["lat"], "elev_m": last["elev_m"],
            "station_type": last.get("station_type"),
        })
        for m in ids:
            member_of[m] = cid
    for _, r in have.iterrows():
        if r["climate_id"] in member_of or r["climate_id"] not in daily:
            continue
        rows.append({
            "unit_id": r["climate_id"], "kind": "station", "members": r["climate_id"], "name": r["name"],
            "region": r["region"], "lon": r["lon"], "lat": r["lat"], "elev_m": r["elev_m"],
            "station_type": r.get("station_type"),
        })
    units = pd.DataFrame(rows)

    annotated, comps = {}, []
    for uid in units["unit_id"]:
        ann = qc.annotate(qc.complete_index(daily[uid], start, end, uid), rules)
        annotated[uid] = ann
        c = qc.completeness_table(ann, cfg, cfg.analysis_years)
        c.insert(0, "unit_id", uid)
        comps.append(c)
    comp = pd.concat(comps, ignore_index=True)
    tiers = qc.assign_tiers(comp, cfg, id_col="unit_id")
    units = units.merge(tiers, on="unit_id", how="left")
    units["tier"] = units["tier"].fillna(3).astype(int)
    units["status"] = units["status"].fillna("insufficient")
    units["tier_provisional"] = units["tier_provisional"].fillna(0).astype(int)
    units = _neighbour_check(units, annotated, cfg)
    log.info("Units: %d (%d composites); final T1=%d T2=%d; provisional=%d",
             len(units), (units["kind"] == "composite").sum(), ((units["tier"] == 1)).sum(),
             ((units["tier"] == 2)).sum(), (units["status"] == "provisional").sum())
    return units, annotated, comp


def neighbours(units: pd.DataFrame, uid: str, k: int = 3, max_km: float = 50.0,
               pool: pd.Series | None = None) -> pd.DataFrame:
    """Nearest final Tier 1/2 units (excluding uid), within max_km."""
    u = units.set_index("unit_id")
    cand = units[(units["tier"] <= 2) & (units["unit_id"] != uid)] if pool is None else units[pool & (units["unit_id"] != uid)]
    if cand.empty:
        return cand.assign(dist_km=[])
    d = haversine_km(u.at[uid, "lon"], u.at[uid, "lat"], cand["lon"].to_numpy(), cand["lat"].to_numpy())
    cand = cand.assign(dist_km=d)
    return cand[cand["dist_km"] <= max_km].nsmallest(k, "dist_km")


def _neighbour_check(units: pd.DataFrame, annotated: dict[str, pd.DataFrame], cfg: Config) -> pd.DataFrame:
    """For provisional units: on their missing target-window days, did nearby final stations get rain?"""
    units = units.copy()
    for c in ("prov_missing_days", "prov_missing_days_nbr_wet", "prov_nbr_precip_mm", "prov_neighbours"):
        units[c] = np.nan if c != "prov_neighbours" else None
    a, b = cfg.periods["window"].bounds(cfg.target_year)
    for i, r in units[units["status"] == "provisional"].iterrows():
        ann = annotated[r["unit_id"]].loc[pd.Timestamp(a): pd.Timestamp(b)]
        # total-invalid days = not daily_valid and not in a block fully inside the window
        missing = ann.index[~ann["daily_valid"].to_numpy() & ~(ann["acc_block"].to_numpy() >= 0)]
        nb = neighbours(units, r["unit_id"])
        units.at[i, "prov_missing_days"] = len(missing)
        units.at[i, "prov_neighbours"] = ";".join(nb["unit_id"])
        if nb.empty or not len(missing):
            continue
        mat = pd.concat({n: annotated[n]["precip"].where(annotated[n]["daily_valid"]).reindex(missing)
                         for n in nb["unit_id"]}, axis=1)
        units.at[i, "prov_missing_days_nbr_wet"] = int((mat.max(axis=1) >= 1.0).sum())
        units.at[i, "prov_nbr_precip_mm"] = round(float(mat.mean(axis=1).sum()), 1)
    return units
