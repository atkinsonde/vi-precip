"""Pipeline stages. Each stage reads cached/previous outputs and writes its own.

Milestone 1: probe, smoke, inventory (+ update = inventory with a refresh of recent data).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import composites, domain, qc
from .config import Config
from .eccc import ECCCClient, filter_province, standardize_daily, standardize_stations
from .geo import load_basemap
from .plots import inventory as invplots

log = logging.getLogger(__name__)


def _save(df: pd.DataFrame, path_csv: Path, parquet: bool = True) -> Path:
    path_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path_csv, index=False)
    if parquet:
        try:
            df.to_parquet(path_csv.with_suffix(".parquet"), index=False)
        except (ValueError, TypeError) as ex:  # mixed object columns etc.
            log.debug("parquet skipped for %s: %s", path_csv.name, ex)
    return path_csv


def domain_stations(cfg: Config, client: ECCCClient, refresh: bool = False) -> pd.DataFrame:
    raw = client.get_stations(refresh=refresh)
    st = standardize_stations(raw)
    st = filter_province(raw, st, cfg.domain["province"])
    st = domain.add_regions(st, cfg.path("region_overrides"))
    log.info("%d stations in domain (%s)", len(st), st["region"].value_counts().to_dict())
    return st


def _download_set(st: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    start = pd.Timestamp(cfg.download_start)
    keep = st["dly_last"].isna() | (st["dly_last"] >= start)
    return st[keep].sort_values("dly_first").reset_index(drop=True)


def _station_daily(client: ECCCClient, row, start: dt.date, end: dt.date, refresh_recent: bool) -> pd.DataFrame:
    mf = row["dly_first"].date() if pd.notna(row["dly_first"]) else None
    ml = row["dly_last"].date() if pd.notna(row["dly_last"]) else None
    raw = client.get_daily(row["climate_id"], start, end, refresh_recent=refresh_recent, meta_first=mf, meta_last=ml)
    return standardize_daily(raw, row["climate_id"])


def _span(ann: pd.DataFrame) -> dict:
    v = ann.index[ann["daily_valid"].to_numpy()]
    return {
        "data_first": v.min() if len(v) else pd.NaT,
        "data_last": v.max() if len(v) else pd.NaT,
        "n_valid_days": int(len(v)),
    }


# --------------------------------------------------------------------- probe
def run_probe(cfg: Config, client: ECCCClient | None = None) -> dict:
    client = client or ECCCClient(cfg)
    rep = client.probe()
    out = cfg.diagnostics_dir / "api_probe.json"
    out.write_text(json.dumps(rep, indent=2, default=str))
    print(f"\nAPI probe -> {out}")
    print(f"  collections: {rep['collections_found']}")
    print(f"  AHCCD collections: {rep['ahccd_collections']}")
    for k in ("stations", "daily"):
        res = rep[k]["resolved"]
        miss = [c for c, v in res.items() if v is None]
        print(f"  {k}: {rep[k]['n_sample']} sample records; unresolved fields: {miss or 'none'}")
    print(f"  daily parameter status codes: {rep['daily_param_status']}")
    print(f"  OK: {rep['ok']}" + ("" if rep["ok"] else f"  (missing required: {rep['daily_required_missing']})"))
    return rep


# --------------------------------------------------------------------- smoke
def run_smoke(cfg: Config, client: ECCCClient | None = None, station: str | None = None, name: str | None = None) -> dict:
    """End-to-end check on one station over the last two seasons. Fast; touches every layer."""
    client = client or ECCCClient(cfg)
    rep = run_probe(cfg, client)
    if not rep["ok"]:
        raise SystemExit("Probe failed; see outputs/diagnostics/api_probe.json before continuing.")
    st = domain_stations(cfg, client)
    if station:
        sel = st[st["climate_id"] == station]
    elif name:
        sel = st[st["name"].str.upper().str.contains(name.upper(), na=False)]
    else:
        recent = st[st["dly_last"] >= pd.Timestamp(cfg.target_year, 1, 1)]
        recent = recent[recent["region"] == "East VI"] if (recent["region"] == "East VI").any() else recent
        sel = recent.sort_values("dly_first").head(1)
    if sel.empty:
        raise SystemExit(f"No station matched (station={station}, name={name}). See outputs/tables/stations_domain.csv")
    row = sel.iloc[0]
    span = (f", metadata daily record {row['dly_first']:%Y-%m-%d} – {row['dly_last']:%Y-%m-%d}"
            if pd.notna(row["dly_first"]) and pd.notna(row["dly_last"]) else "")
    print(f"\nSmoke station: {row['name']} ({row['climate_id']}), {row['region']}{span}")
    _save(st, cfg.tables_dir / "stations_domain.csv")

    years = [cfg.target_year - 1, cfg.target_year]
    start = dt.date(years[0], 1, 1)
    end = cfg.end_date
    rules = qc.FlagRules.from_config(cfg)
    daily = _station_daily(client, row, start, end, refresh_recent=True)
    ann = qc.annotate(qc.complete_index(daily, start, end, row["climate_id"]), rules)
    comp = qc.completeness_table(ann, cfg, years)
    comp.insert(0, "climate_id", row["climate_id"])
    flags = daily["precip_flag"].fillna("(none)").value_counts().to_dict()
    ext = cfg.periods["extended"]
    by_year = {}
    for y in years:
        a, b = ext.bounds(y)
        by_year[y] = ann.loc[pd.Timestamp(a) : pd.Timestamp(b)]
    sdir = cfg.diagnostics_dir
    fig = invplots.smoke_plot(by_year, row["climate_id"], row["name"], sdir / f"smoke_{row['climate_id']}.png")
    comp.to_csv(sdir / f"smoke_{row['climate_id']}_completeness.csv", index=False)
    summary = {
        "station": row["climate_id"], "name": row["name"], "region": str(row["region"]),
        "rows_downloaded": int(len(daily)), "precip_flag_counts": flags,
        "window": comp[comp["period"] == "window"][["year", "total_mm", "n_missing_total", "valid_total", "valid_daily"]]
        .to_dict(orient="records"),
        "requests": client.n_requests, "figure": str(fig),
    }
    (sdir / f"smoke_{row['climate_id']}.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"  rows downloaded: {len(daily)}; precip flags: {flags}")
    print(comp[["year", "period", "total_mm", "n_missing_total", "valid_total", "valid_daily", "n_tmax", "n_tmin"]]
          .to_string(index=False))
    print(f"  figure: {fig}\n  HTTP requests: {client.n_requests}")
    return summary


# ----------------------------------------------------------------- inventory
def run_inventory(
    cfg: Config,
    client: ECCCClient | None = None,
    refresh_recent: bool = False,
    max_stations: int | None = None,
    station_ids: list[str] | None = None,
    refresh_stations: bool = False,
) -> dict[str, Path]:
    client = client or ECCCClient(cfg)
    st = domain_stations(cfg, client, refresh=refresh_stations)
    dl = _download_set(st, cfg)
    if station_ids:
        dl = dl[dl["climate_id"].isin(station_ids)]
    if max_stations:
        dl = dl.head(max_stations)
    log.info("Downloading/reading daily data for %d stations (%s .. %s)", len(dl), cfg.download_start, cfg.end_date)

    rules = qc.FlagRules.from_config(cfg)
    start, end = cfg.download_start, cfg.end_date
    interim = cfg.data_dir / "interim" / "daily"
    interim.mkdir(parents=True, exist_ok=True)
    std_daily: dict[str, pd.DataFrame] = {}
    annotated: dict[str, pd.DataFrame] = {}
    comps, spans, flag_rows = [], [], []
    for i, (_, row) in enumerate(dl.iterrows()):
        cid = row["climate_id"]
        try:
            d = _station_daily(client, row, start, end, refresh_recent)
        except Exception as ex:
            log.error("%s %s: download failed (%s); skipped", cid, row["name"], ex)
            continue
        log.info("[%d/%d] %s %s: %d rows", i + 1, len(dl), cid, row["name"], len(d))
        if d.empty:
            spans.append({"climate_id": cid, "data_first": pd.NaT, "data_last": pd.NaT, "n_valid_days": 0})
            continue
        d.to_parquet(interim / f"{cid}.parquet", index=False)
        std_daily[cid] = d
        ann = qc.annotate(qc.complete_index(d, start, end, cid), rules)
        annotated[cid] = ann
        c = qc.completeness_table(ann, cfg, cfg.analysis_years)
        c.insert(0, "climate_id", cid)
        comps.append(c)
        spans.append({"climate_id": cid, **_span(ann)})
        vc = d["precip_flag"].fillna("(none)").value_counts()
        flag_rows += [{"climate_id": cid, "flag": k, "n": int(v)} for k, v in vc.items()]

    spans_df = pd.DataFrame(spans, columns=["climate_id", "data_first", "data_last", "n_valid_days"])
    comp = pd.concat(comps, ignore_index=True) if comps else pd.DataFrame(columns=["climate_id", "year", "period"])
    tiers = qc.assign_tiers(comp, cfg) if len(comp) else pd.DataFrame(columns=["climate_id", "tier"])

    inv = st.merge(spans_df, on="climate_id", how="left").merge(tiers, on="climate_id", how="left")
    inv["downloaded"] = inv["climate_id"].isin(spans_df["climate_id"])
    inv["api_has_daily"] = inv["n_valid_days"].fillna(0) > 0
    inv["tier"] = inv["tier"].fillna(3).astype(int)
    inv["long_record_candidate"] = inv["dly_first"].notna() & (inv["dly_first"] <= pd.Timestamp(1950, 12, 31)) & (
        inv["dly_last"] >= pd.Timestamp(cfg.target_year - 6, 1, 1)
    )

    # Composites: candidates, proposals, and what tier each proposal would reach.
    cand = composites.find_candidates(st, spans_df, annotated, cfg)
    prop = composites.propose(cand, spans_df)
    if len(prop):
        ptiers = []
        for cid, members in prop.groupby("composite_id"):
            s = composites.build_series(members, std_daily, cid)
            if s.empty:
                continue
            a = qc.annotate(qc.complete_index(s.drop(columns=["source_id"]), start, end, cid), rules)
            c = qc.completeness_table(a, cfg, cfg.analysis_years)
            c.insert(0, "climate_id", cid)
            t = qc.assign_tiers(c, cfg)
            ptiers.append(t[["climate_id", "tier", "n_baseline_valid_total", "target_valid_total"]]
                          .rename(columns={"climate_id": "composite_id", "tier": "tier_if_joined",
                                           "n_baseline_valid_total": "baseline_years_if_joined",
                                           "target_valid_total": "target_valid_if_joined"}))
        if ptiers:
            prop = prop.merge(pd.concat(ptiers), on="composite_id", how="left")
        best = inv.set_index("climate_id")["tier"]
        prop["tier_member_alone"] = prop["climate_id"].map(best)
    inv["proposed_composite"] = inv["climate_id"].map(
        dict(zip(prop["climate_id"], prop["composite_id"])) if len(prop) else {}
    )

    cols = [
        "climate_id", "name", "region", "region_source", "is_vi", "lon", "lat", "elev_m", "tier",
        "n_baseline_valid_total", "n_baseline_valid_daily", "target_valid_total", "target_valid_daily",
        "target_missing_total", "first_valid_year", "last_valid_baseline_year", "data_first", "data_last",
        "n_valid_days", "dly_first", "dly_last", "downloaded", "api_has_daily", "proposed_composite",
        "long_record_candidate", "station_type", "operator", "stn_id", "tc_id", "wmo_id",
    ]
    inv = inv[[c for c in cols if c in inv.columns]].sort_values(["region", "tier", "lat"], ascending=[True, True, False])
    for c in ("data_first", "data_last", "dly_first", "dly_last"):
        inv[c] = pd.to_datetime(inv[c]).dt.date

    t = cfg.tables_dir
    paths = {
        "inventory": _save(inv, t / "station_inventory.csv"),
        "completeness": _save(comp, t / "completeness_station_year.csv"),
        "composite_candidates": _save(cand, t / "composite_candidates.csv", parquet=False),
        "composites_proposed": _save(prop, t / "composites_proposed.csv", parquet=False),
        "flag_counts": _save(pd.DataFrame(flag_rows), cfg.diagnostics_dir / "precip_flag_counts.csv", parquet=False),
    }
    data_dir = cfg.data_dir / "interim"
    inv.to_parquet(data_dir / "station_inventory.parquet", index=False)

    # Figures
    basemap = load_basemap(cfg)
    bbox = tuple(cfg.domain["bbox"])
    plot_inv = inv.copy()
    plot_inv["region"] = pd.Categorical(plot_inv["region"], categories=domain.REGIONS, ordered=True)
    with_data = plot_inv[plot_inv["api_has_daily"]]
    f = cfg.figures_dir
    paths["map"] = invplots.station_map(
        with_data, prop, basemap, bbox, f / "station_map_candidates.png",
        f"Candidate stations with daily data since {cfg.download_start.year} — tiers for window "
        f"Sep 1–{cfg.end_date:%b %d}, target {cfg.target_year}",
    )
    paths["regions"] = invplots.region_check(with_data, basemap, bbox, f / "station_regions_check.png")
    paths["timeline"] = invplots.timeline(comp, plot_inv, cfg.target_year, f / "station_timeline.png")

    _print_summary(inv, cand, prop, comp, cfg, client)
    _write_vintage(cfg, client)
    return paths


def _print_summary(inv, cand, prop, comp, cfg, client) -> None:
    d = inv[inv["api_has_daily"]]
    print("\n=== Inventory summary ===")
    print(f"Stations in domain: {len(inv)}; with daily data in the API since {cfg.download_start.year}: {len(d)}")
    tab = pd.crosstab(d["region"].astype(str), d["tier"]).reindex(columns=[1, 2, 3], fill_value=0)
    tab = tab.reindex([r for r in domain.REGIONS if r in tab.index])
    print(tab.to_string())
    if len(cand):
        print(f"Composite candidate pairs: {len(cand)} ({(cand['suggestion'] == 'join').sum()} join, "
              f"{(cand['suggestion'] == 'review').sum()} review); proposed composites: "
              f"{prop['composite_id'].nunique() if len(prop) else 0}")
        if len(prop) and "tier_if_joined" in prop:
            gain = prop.groupby("composite_id").agg(joined=("tier_if_joined", "first"), alone=("tier_member_alone", "min"))
            print(f"  composites that would improve a tier: {(gain['joined'] < gain['alone']).sum()}")
    w = comp[(comp["period"] == "window")]
    if len(w):
        print(f"Flag tallies in windows: incomplete(^)={int(w['n_incomplete'].sum())}, "
              f"accumulated days={int(w['n_accumulated_days'].sum())}, unknown={int(w['n_unknown_flag'].sum())}")
    print(f"HTTP requests this run: {client.n_requests}")


def _write_vintage(cfg: Config, client: ECCCClient) -> None:
    v = {
        "built": dt.datetime.now().isoformat(timespec="seconds"),
        "end_date": cfg.end_date.isoformat(),
        "target_year": cfg.target_year,
        "baseline": list(cfg.baseline),
        "source": client.base,
    }
    (cfg.out_dir / "data_vintage.json").write_text(json.dumps(v, indent=2))
