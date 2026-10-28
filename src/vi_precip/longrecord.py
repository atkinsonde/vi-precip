"""Long-record stations (1950 onward) for the ENSO analysis.

1. Raw ECCC daily data at a few long-running stations (config long_record.stations), joined where
   a station ID changed with a checked overlap. The same QC, completeness and metric code as the
   2000-2026 analysis is used, so the October 1-26 and September 1-October 26 windows are exact.
2. AHCCD (adjusted and homogenized; Mekis & Vincent 2011; Vincent et al. 2012) monthly values at the
   nearest AHCCD station (within 15 km) as a cross-check. AHCCD gives whole months only
   (full October), and usually ends a few years before the present.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import metrics, qc, temperature
from .composites import haversine_km
from .config import Config
from .eccc import ECCCClient, standardize_daily

log = logging.getLogger(__name__)

DEFAULT_STATIONS = [
    {"id": "L_GONZALES", "name": "Victoria Gonzales", "members": [
        {"climate_id": "1018610", "use_to": "2004-11-30"}, {"climate_id": "1018611", "use_from": "2004-12-01"}]},
    {"id": "L_SHAWNIGAN", "name": "Shawnigan Lake", "members": [{"climate_id": "1017230"}]},
    {"id": "L_COMOX", "name": "Comox A", "members": [{"climate_id": "1021830"}]},
    {"id": "L_ESTEVAN", "name": "Estevan Point", "members": [{"climate_id": "1032730"}]},
    {"id": "L_QUATSINO", "name": "Quatsino", "members": [{"climate_id": "1036570"}]},
    {"id": "L_AGASSIZ", "name": "Agassiz", "members": [
        {"climate_id": "1100120", "use_to": "2019-12-31"}, {"climate_id": "1100119", "use_from": "2020-01-01"}]},
    {"id": "L_VANHARBOUR", "name": "Vancouver Harbour", "members": [{"climate_id": "1108446"}]},
]


def _settings(cfg: Config) -> dict:
    lr = cfg.raw.get("long_record", {})
    return {"start": int(lr.get("start_year", cfg.raw["analysis"].get("long_record_start", 1950))),
            "stations": lr.get("stations", DEFAULT_STATIONS)}


def _long_cfg(cfg: Config, start: int) -> Config:
    return cfg.with_overrides(baseline=[start, cfg.target_year - 1],
                              download_start=f"{start}-01-01")


def fetch_and_compute(cfg: Config, client: ECCCClient | None = None) -> dict[str, pd.DataFrame]:
    S = _settings(cfg)
    lcfg = _long_cfg(cfg, S["start"])
    client = client or ECCCClient(cfg)
    inv = pd.read_parquet(cfg.data_dir / "interim" / "station_inventory.parquet")
    inv["climate_id"] = inv["climate_id"].astype(str)
    meta = inv.set_index("climate_id")
    rules = qc.FlagRules.from_config(cfg)
    start, end = dt.date(S["start"], 1, 1), cfg.end_date
    years = list(range(S["start"], cfg.target_year + 1))
    rows_p, rows_t, units = [], [], []
    for st in S["stations"]:
        parts = []
        for m in st["members"]:
            cid = str(m["climate_id"])
            row = meta.loc[cid] if cid in meta.index else None
            mf = row["dly_first"] if row is not None and pd.notna(row["dly_first"]) else None
            ml = row["dly_last"] if row is not None and pd.notna(row["dly_last"]) else None
            mf = pd.Timestamp(mf).date() if mf is not None else None
            ml = pd.Timestamp(ml).date() if ml is not None else None
            try:
                raw = client.get_daily(cid, start, end, meta_first=mf, meta_last=ml)
            except Exception as ex:
                log.error("%s: long-record download failed (%s)", cid, ex)
                continue
            d = standardize_daily(raw, cid)
            if m.get("use_from"):
                d = d[d["date"] >= pd.Timestamp(m["use_from"])]
            if m.get("use_to"):
                d = d[d["date"] <= pd.Timestamp(m["use_to"])]
            parts.append(d)
        if not parts:
            continue
        d = pd.concat(parts, ignore_index=True).sort_values("date").drop_duplicates("date", keep="last")
        uid = st["id"]
        present = [str(m["climate_id"]) for m in st["members"] if str(m["climate_id"]) in meta.index]
        last = meta.loc[present[-1]] if present else None
        units.append({"unit_id": uid, "name": st["name"], "members": ";".join(str(m["climate_id"]) for m in st["members"]),
                      "lon": None if last is None else last["lon"], "lat": None if last is None else last["lat"],
                      "region": None if last is None else str(last["region"]),
                      "first_date": d["date"].min().date().isoformat() if len(d) else None})
        ann = qc.annotate(qc.complete_index(d, start, end, uid), rules)
        comp = qc.completeness_table(ann, lcfg, years)
        comp.insert(0, "unit_id", uid)
        m_, _, _ = metrics.unit_metrics(uid, ann, comp, lcfg)
        rows_p.append(m_[m_["threshold"] == cfg.wet_thresholds[0]])
        ts = temperature.period_stats(temperature._qc(d), years, temperature.periods(cfg))
        ts.insert(0, "unit_id", uid)
        rows_t.append(ts)
        n_ok = int(comp[(comp["period"] == "window")]["valid_total"].sum())
        log.info("long record %-14s %s..%s, %d valid Sep-Oct windows", uid, units[-1]["first_date"], d["date"].max().date(), n_ok)
    out = {"units": pd.DataFrame(units),
           "precip": pd.concat(rows_p, ignore_index=True) if rows_p else pd.DataFrame(),
           "temp": pd.concat(rows_t, ignore_index=True) if rows_t else pd.DataFrame()}
    return out


# ------------------------------------------------------------------ AHCCD
def _pick(cols, *subs):
    for s in subs:
        for c in cols:
            if s in c.lower():
                return c
    return None


def ahccd(cfg: Config, units: pd.DataFrame, client: ECCCClient | None = None) -> pd.DataFrame:
    """October (and September) AHCCD monthly values at the AHCCD station nearest each long-record unit."""
    client = client or ECCCClient(cfg)
    try:
        bbox = ",".join(str(x) for x in cfg.domain["bbox"])
        feats0 = list(client.iter_features("ahccd-stations", {"bbox": bbox}))
        st = [f.get("properties") or {} for f in feats0]
        geo = [(f.get("geometry") or {}).get("coordinates") for f in feats0]
    except Exception as ex:
        log.warning("AHCCD stations unavailable (%s)", ex)
        return pd.DataFrame()
    if not st:
        return pd.DataFrame()
    sdf = pd.DataFrame(st)
    sdf["lon"] = [g[0] if g else np.nan for g in geo]
    sdf["lat"] = [g[1] if g else np.nan for g in geo]
    id_col = _pick(sdf.columns, "station_id", "identifier")
    name_col = _pick(sdf.columns, "station_name", "name")
    out = []
    for _, u in units.dropna(subset=["lon"]).iterrows():
        d = haversine_km(u["lon"], u["lat"], sdf["lon"].to_numpy(float), sdf["lat"].to_numpy(float))
        k = int(np.nanargmin(d))
        if d[k] > 15:
            continue
        sid = sdf.iloc[k][id_col]
        try:
            feats = list(client.iter_features("ahccd-monthly", {id_col: sid}, optional=("sortby", "properties", "datetime")))
        except Exception as ex:
            log.warning("AHCCD monthly for %s failed (%s)", sid, ex)
            continue
        df = pd.DataFrame([f.get("properties") or {} for f in feats])
        if df.empty:
            continue
        c_date = _pick(df.columns, "date")
        c_p = _pick(df.columns, "total_precip", "precip")
        c_tx = _pick(df.columns, "temp_max")
        c_tn = _pick(df.columns, "temp_min")
        dates = pd.to_datetime(df[c_date].astype(str).str[:7], format="%Y-%m", errors="coerce") if c_date else None
        if dates is None:
            continue
        rec = pd.DataFrame({"year": dates.dt.year, "month": dates.dt.month})
        for nm, c in (("precip", c_p), ("tmax", c_tx), ("tmin", c_tn)):
            rec[nm] = pd.to_numeric(df[c], errors="coerce") if c else np.nan
        rec = rec[rec["month"].isin([9, 10])].dropna(subset=["year"])
        rec["unit_id"] = u["unit_id"]
        rec["ahccd_id"] = sid
        rec["ahccd_name"] = sdf.iloc[k][name_col] if name_col else ""
        rec["ahccd_km"] = round(float(d[k]), 1)
        out.append(rec)
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if len(res):
        for c in ("precip", "tmax", "tmin"):
            res.loc[res[c] <= -999, c] = np.nan
    return res


def run(cfg: Config, client: ECCCClient | None = None) -> dict[str, Path]:
    res = fetch_and_compute(cfg, client)
    a = ahccd(cfg, res["units"], client)
    out = cfg.data_dir / "processed"
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for k, df in (("long_units", res["units"]), ("long_precip", res["precip"]), ("long_temp", res["temp"]),
                  ("long_ahccd", a)):
        p = out / f"{k}.parquet"
        df.to_parquet(p, index=False)
        df.to_csv(cfg.tables_dir / f"{k}.csv", index=False)
        paths[k] = p
    if len(res["units"]):
        print("\nLong-record units:\n" + res["units"][["unit_id", "name", "members", "first_date"]].to_string(index=False))
    print(f"AHCCD cross-check rows: {len(a)}" + (f" ({a['unit_id'].nunique()} units)" if len(a) else ""))
    return paths
