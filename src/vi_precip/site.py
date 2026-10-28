"""Static dashboard: one folder that can be uploaded as is (e.g. with Cyberduck) to any web server,
or opened straight from disk.

    outputs/site/
      index.html            page shell (tabs: Summary, Maps, Stations, Coherence, ENSO, Water, Methods)
      assets/app.js         all interaction (no build step, no frameworks)
      assets/style.css
      assets/plotly.min.js  copied from the installed Python plotly package (no CDN)
      data/*.js             precomputed data, one global (window.VIP) per file
      data/stations/*.js    per-station series, loaded on demand
      data/csv/*.csv        the main tables, for download
      figures/*.png         the static figures (newspaper maps etc.)

Data are written as small JavaScript files instead of JSON so the page also works from file://
(browsers block fetch() of local files). Nothing is computed in the browser except selection,
sorting and a one-predictor least-squares line.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import shutil
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

from . import indices as idx_mod
from .config import Config
from .domain import VI_REGIONS

log = logging.getLogger(__name__)

PRECIP_MAP_METRICS = [  # metric, label, unit, family
    ("total_oct", "Oct 1–{d} total", "mm"), ("total_window", "Sep 1–{e} total", "mm"),
    ("total_sep", "September total", "mm"), ("driest30_mm", "Driest 30 days", "mm"),
    ("driest45_mm", "Driest 45 days", "mm"), ("n_wet_scaled", "Wet days", "days"),
    ("sdii", "Wet-day intensity", "mm/day"), ("spell_max_win", "Longest dry spell", "days"),
    ("frac_dry", "Fraction of days dry", ""), ("n_spells_ge7", "Dry spells ≥ 7 days", ""),
    ("onset_days", "Days to first ≥ 20 mm event", "days"), ("max_daily", "Wettest day", "mm"),
    ("n_heavy_p95", "Heavy-precipitation days", "days"), ("a_N", "Log anomaly: frequency", ""),
    ("a_I", "Log anomaly: intensity", ""),
]
STATION_SERIES = ["total_window", "total_oct", "total_sep", "driest30_mm", "n_wet_scaled", "sdii", "spell_max_win"]
TEMP_VARS = {"tmax": "Daily maximum", "tmin": "Daily minimum", "dtr": "Diurnal range"}
WATER_CATS = ["Lowest since {b}", "2nd or 3rd lowest", "Among the lowest quarter", "Near normal (middle half)",
              "Higher than usual"]
ENSO_METRIC_LABELS = {
    "total_window": "Sep 1–{e} precipitation", "total_oct": "Oct 1–{d} precipitation", "total_sep": "September precipitation",
    "n_wet_scaled": "Wet days", "sdii": "Wet-day intensity", "spell_max_win": "Longest dry spell",
    "driest30_mm": "Driest 30-day total", "tmax_oct": "Oct Tmax", "tmin_oct": "Oct Tmin", "dtr_oct": "Oct diurnal range",
    "tmax_window": "Sep 1–{e} Tmax", "tmin_window": "Sep 1–{e} Tmin", "dtr_window": "Sep 1–{e} diurnal range",
    "flow_oct": "Oct 1–{d} river flow", "flow_sepoct": "Sep 1–{e} river flow",
}


# ------------------------------------------------------------------ helpers
def _clean(o):
    """Recursively replace NaN/inf with None, numpy scalars with Python, round floats."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_clean(v) for v in o.tolist()]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        if not math.isfinite(f):
            return None
        return round(f, 4) if abs(f) < 1e4 else round(f, 1)
    if isinstance(o, (pd.Timestamp, dt.date)):
        return o.isoformat()[:10]
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def _write_js(path: Path, key: str, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = f"window.VIP=window.VIP||{{}};VIP[{json.dumps(key)}]=" + \
        json.dumps(_clean(obj), separators=(",", ":"), ensure_ascii=False, allow_nan=False) + ";\n"
    path.write_text(txt, encoding="utf-8")
    return len(txt)


def _cols(df: pd.DataFrame, cols: list[str]) -> dict:
    """Column-oriented export (compact)."""
    return {c: df[c].tolist() if c in df else [None] * len(df) for c in cols}


def _simplify(lines: list, tol: float = 0.006, nd: int = 3) -> list:
    out = []
    for ln in lines or []:
        pts = np.asarray(ln, float)
        if len(pts) < 3:
            continue
        keep = [0]
        for i in range(1, len(pts) - 1):
            if abs(pts[i, 0] - pts[keep[-1], 0]) + abs(pts[i, 1] - pts[keep[-1], 1]) >= tol:
                keep.append(i)
        keep.append(len(pts) - 1)
        p = np.round(pts[keep], nd)
        if len(p) >= 3:
            out.append(p.tolist())
    return out


def _basemap(cfg: Config, bbox) -> dict:
    from .geo import load_basemap

    try:
        bm = load_basemap(cfg, bbox=list(bbox), pad=0.3)
    except Exception as ex:
        log.warning("basemap unavailable (%s)", ex)
        bm = None
    if not bm:
        return {"land": [], "coast": [], "bbox": list(bbox)}
    return {"land": _simplify(bm.get("land")), "coast": _simplify(bm.get("coast")), "bbox": list(bbox)}


def _water_cat(rank, n) -> int | None:
    if rank is None or not np.isfinite(rank):
        return None
    if rank <= 1:
        return 0
    if rank <= 3.5:
        return 1
    p = (rank - 0.5) / n
    return 2 if p <= 0.25 else 3 if p < 0.75 else 4


def _read(p: Path, **kw) -> pd.DataFrame | None:
    if not p.exists():
        return None
    return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, **kw)


# ------------------------------------------------------------------ builders
class SiteBuilder:
    def __init__(self, cfg: Config, out: Path | None = None, synthetic: bool = False):
        self.cfg, self.synthetic = cfg, synthetic
        self.out = Path(out) if out else cfg.out_dir / "site"
        self.t, self.pdir = cfg.tables_dir, cfg.data_dir / "processed"
        e = cfg.end_date
        self.fmt = {"d": e.day, "e": f"{e:%b} {e.day}"}
        self.sizes: dict[str, int] = {}
        self.have: dict[str, bool] = {}

    def lab(self, s: str) -> str:
        return s.format(**self.fmt)

    def js(self, name: str, key: str, obj) -> None:
        self.sizes[name] = _write_js(self.out / "data" / name, key, obj)

    # ---- precipitation
    def precip(self) -> dict | None:
        an = _read(self.t / f"anomalies_{self.cfg.target_year}_long.parquet")
        units = _read(self.t / "units.parquet")
        if an is None or units is None:
            return None
        units = units[units["tier"].isin([1, 2])].copy()
        units["region"] = units["region"].astype(str)
        units = units.sort_values(["region", "name"])
        units["name"] = units["name"].str.replace(" (composite)", "", regex=False).str.title()
        ids = units["unit_id"].tolist()
        pos = {u: i for i, u in enumerate(ids)}
        vals = {}
        for (metric, thr), g in an.groupby(["metric", "threshold"]):
            g = g[g["unit_id"].isin(pos)].set_index("unit_id").reindex(ids)
            vals[f"{metric}|{thr:g}"] = _cols(g.reset_index(), ["value", "baseline_median", "pct_of_median",
                                                                 "diff_from_median", "rank_dry", "n_years", "pctl",
                                                                 "spi", "rp_emp", "rp_fit", "record_dry", "lower_bound"])
        dd = an.drop_duplicates("metric").set_index("metric")
        metrics = [{"key": m, "label": self.lab(lbl), "unit": u,
                    "dry": str(dd.loc[m, "dry_direction"]) if m in dd.index else "low",
                    "family": str(dd.loc[m, "family"]) if m in dd.index else ""}
                   for m, lbl, u in PRECIP_MAP_METRICS if m in dd.index]
        out = {"units": _cols(units, ["unit_id", "name", "region", "lon", "lat", "elev_m", "tier", "status", "kind",
                                      "members", "first_valid_year"]),
               "metrics": metrics, "values": vals, "thresholds": self.cfg.wet_thresholds}
        self.js("precip.js", "precip", out)
        self._csv(an, "precip_anomalies_long.csv")
        return out

    # ---- temperature
    def temperature(self) -> dict | None:
        an = _read(self.t / f"temperature_anomalies_{self.cfg.target_year}.csv", dtype={"unit_id": str})
        if an is None or an.empty:
            return None
        u = an.drop_duplicates("unit_id")[["unit_id", "name", "region", "lon", "lat", "elev_m", "kind"]].copy()
        u["name"] = u["name"].str.replace(" (composite)", "", regex=False).str.title()
        u = u.sort_values(["region", "name"])
        ids = u["unit_id"].tolist()
        vals = {}
        for (v, per), g in an.groupby(["var", "period"]):
            g = g.set_index("unit_id").reindex(ids).reset_index()
            vals[f"{v}|{per}"] = _cols(g, ["value", "base_mean", "anom", "z", "rank_high", "rank_low", "n_years",
                                           "record_high", "record_low", "tier"])
        out = {"units": _cols(u, ["unit_id", "name", "region", "lon", "lat", "elev_m", "kind"]), "values": vals,
               "vars": TEMP_VARS}
        self.js("temp.js", "temp", out)
        self._csv(an, "temperature_anomalies.csv")
        return out

    # ---- water
    def water(self) -> dict | None:
        r = _read(self.pdir / "hydro_ranks.parquet")
        if r is None or r.empty:
            return None
        r = r.copy()
        b0 = int(r["baseline_start"].iloc[0]) if "baseline_start" in r else 1985
        r["cat"] = [_water_cat(a, b) for a, b in zip(r["oct_rank_base"], r["oct_n_base"])]
        r["regulated"] = ~r["regulation"].astype(str).str.startswith("natural")
        from .plots.water_map import short_name

        r["short"] = r["name"].map(short_name)
        r = r.sort_values("lat", ascending=False)
        cols = ["station", "name", "short", "lon", "lat", "param", "regulated", "regulation", "regulation_note", "rhbn",
                "area_km2", "first_year", "cat", "oct_provisional_days"]
        for per in ("oct", "sepoct", "last7"):
            cols += [f"{per}_value", f"{per}_rank_base", f"{per}_n_base", f"{per}_median_base", f"{per}_pct_median_base",
                     f"{per}_diff_median_base", f"{per}_rank_rec", f"{per}_n_rec", f"{per}_last_lower_year"]
        out = {"baseline_start": b0, "cats": [c.format(b=b0) for c in WATER_CATS], "rows": _cols(r, cols)}
        self.js("water.js", "water", out)
        self._csv(r.drop(columns=["short"]), "water_ranks.csv")
        return out

    # ---- ENSO
    def enso(self) -> dict | None:
        d = self.pdir / "enso"
        if not (d / "summary.json").exists():
            return None
        summ = json.loads((d / "summary.json").read_text())
        seas = _read(self.pdir / "indices_seasonal.parquet")
        series = {}
        if seas is not None:
            for (name, s), g in seas.groupby(["index", "season"]):
                g = g[g["year"] >= 1950].sort_values("year")
                series[f"{name}|{s}"] = {"years": g["year"].tolist(), "values": g["value"].round(3).tolist()}
        reg = _read(d / "regional_series.parquet")
        regional = {}
        if reg is not None:
            for (rec, region, m), g in reg.groupby(["record", "region", "metric"]):
                g = g.sort_values("year")
                regional[f"{rec}|{region}|{m}"] = {"years": g["year"].tolist(), "z": g["z"].round(3).tolist(),
                                                   "n_units": g["n_units"].tolist()}
        grid = _read(d / "grid.parquet")
        regr = _read(d / "regressions.parquet")
        comp = _read(d / "composites.parquet")
        smap = _read(d / "station_maps.parquet")
        an = _read(d / "analogs.parquet")
        ah = _read(d / "ahccd_check.parquet")
        metrics = sorted(set(reg["metric"])) if reg is not None else []
        out = {
            "summary": summ, "index_series": series, "regional": regional,
            "index_labels": {k: idx_mod.LABELS.get(k, k) for k in idx_mod.ENSO_INDICES + ["pdo", "pna"]},
            "metric_labels": {m: self.lab(ENSO_METRIC_LABELS.get(m, m)) for m in metrics},
            "seasons": list(idx_mod.SEASONS),
            "grid": _cols(grid, ["record", "region", "metric", "index", "season", "n", "rho", "rho_lo", "rho_hi",
                                 "p_two", "p_neg", "q_bh", "family", "year_first", "year_last"]) if grid is not None else None,
            "regressions": regr.to_dict("records") if regr is not None else [],
            "composites": comp.to_dict("records") if comp is not None else [],
            "station_maps": _cols(smap, ["record", "unit_id", "metric", "name", "lon", "lat", "rho", "p_two", "fdr_sig",
                                         "p_fdr_threshold", "field_significant", "n", "z_target"]) if smap is not None else None,
            "analogs": an.to_dict("records") if an is not None else [],
            "ahccd": ah.to_dict("records") if ah is not None else [],
            "class_rule": self.cfg.raw.get("enso", {}).get("class_index", "oni").upper() + " JAS ± " +
            str(self.cfg.raw.get("enso", {}).get("class_threshold", 0.5)) + " °C",
        }
        self.js("enso.js", "enso", out)
        for k, df in (("enso_grid.csv", grid), ("enso_regressions.csv", regr), ("enso_composites.csv", comp),
                      ("enso_station_correlations.csv", smap), ("enso_analogs.csv", an), ("enso_regional_series.csv", reg)):
            if df is not None:
                self._csv(df, k)
        if seas is not None:
            self._csv(seas, "climate_indices_seasonal.csv")
        return out

    # ---- per-station files
    def stations(self, precip: dict | None) -> int:
        if not precip:
            return 0
        from .plots.brief import _cumulative

        met = _read(self.t / "metrics_unit_year.parquet")
        tst = _read(self.t / "temperature_unit_year.csv", dtype={"unit_id": str})
        y, end = self.cfg.target_year, self.cfg.end_date
        base = self.cfg.baseline_years
        proc = self.pdir / "daily"
        start_md, end_md = (9, 1), (end.month, end.day)
        ndays = (dt.date(y, *end_md) - dt.date(y, *start_md)).days + 1
        n = 0
        for uid in precip["units"]["unit_id"]:
            m = met[met["unit_id"] == uid]
            rec: dict = {"unit_id": uid, "series": {}}
            for thr, g in m.groupby("threshold"):
                g = g.sort_values("year")
                s = {"years": g["year"].tolist()}
                for c in STATION_SERIES:
                    if c not in g:
                        continue
                    vcol = {"total_window": "valid_total_window", "total_oct": "valid_total_oct",
                            "total_sep": "valid_total_sep", "driest30_mm": "valid_driest30"}.get(c, "valid_daily")
                    v = g[c].where(g[vcol].astype(bool)) if vcol in g else g[c]
                    s[c] = v.round(2).tolist()
                rec["series"][f"{thr:g}"] = s
            if tst is not None:
                tg = tst[tst["unit_id"] == uid]
                if len(tg):
                    ts = {}
                    for per in ("oct", "window"):
                        q = tg[tg["period"] == per].sort_values("year")
                        ts[per] = {"years": q["year"].tolist(),
                                   **{v: q[f"{v}_mean"].where(q[f"{v}_valid"].astype(bool)).round(2).tolist()
                                      for v in TEMP_VARS}}
                    rec["temp"] = ts
            p = proc / f"{uid}.parquet"
            if p.exists():
                d = pd.read_parquet(p)
                curves = []
                for yy in base:
                    c, miss = _cumulative(d, yy, start_md, end_md)
                    if len(c) == ndays and miss <= 2:
                        curves.append(c)
                c, miss = _cumulative(d, y, start_md, end_md)
                cum = {"ndays": ndays, "start": f"{y}-09-01", "n_years": len(curves)}
                if curves:
                    A = np.vstack(curves)
                    cum |= {"p10": np.percentile(A, 10, axis=0).round(1), "p50": np.median(A, axis=0).round(1),
                            "p90": np.percentile(A, 90, axis=0).round(1), "min": A.min(axis=0).round(1),
                            "max": A.max(axis=0).round(1)}
                if len(c):
                    cum["target"] = np.round(c, 1)
                    cum["target_missing"] = int(miss)
                dd = d.loc[pd.Timestamp(y, *start_md):pd.Timestamp(y, *end_md)]
                cum["daily"] = dd["precip"].round(1).tolist()
                cum["daily_status"] = dd["status"].astype(str).tolist() if "status" in dd else []
                rec["cumulative"] = cum
            _write_js(self.out / "data" / "stations" / f"{uid}.js", f"st_{uid}", rec)
            n += 1
        return n

    # ---- headline numbers (public summary)
    def headline(self, precip, temp, water, enso) -> dict:
        h: dict = {}
        if precip:
            st = pd.DataFrame(precip["units"])
            fin = (st["status"] == "final").to_numpy()
            thr = f"{self.cfg.wet_thresholds[0]:g}"

            def arr(metric, field):
                v = precip["values"].get(f"{metric}|{thr}", {}).get(field)
                return np.array([np.nan if x is None else x for x in v], float)[fin] if v else np.array([])

            o, orank, on = arr("total_oct", "pct_of_median"), arr("total_oct", "rank_dry"), arr("total_oct", "n_years")
            w, wrank = arr("total_window", "pct_of_median"), arr("total_window", "rank_dry")
            s = arr("total_sep", "pct_of_median")
            rp = arr("total_window", "rp_fit")
            isvi = st["region"].isin(VI_REGIONS).to_numpy()[fin]
            h["precip"] = {
                "n": int(fin.sum()), "n_years": int(np.nanmax(on)) if len(on) else None,
                "oct_pct_median": float(np.nanmedian(o)) if len(o) else None,
                "oct_pct_median_vi": float(np.nanmedian(o[isvi])) if isvi.any() else None,
                "oct_top3": int(np.nansum(orank <= 3)), "oct_driest": int(np.nansum(orank <= 1)),
                "window_pct_median": float(np.nanmedian(w)) if len(w) else None,
                "window_rank_median": float(np.nanmedian(wrank)) if len(wrank) else None,
                "window_top3": int(np.nansum(wrank <= 3)),
                "window_rp_median": float(np.nanmedian(rp)) if np.isfinite(rp).any() else None,
                "sep_pct_median": float(np.nanmedian(s)) if len(s) else None,
                "wet_days": float(np.nanmedian(arr("n_wet_scaled", "value"))) if len(arr("n_wet_scaled", "value")) else None,
                "wet_days_normal": float(np.nanmedian(arr("n_wet_scaled", "baseline_median"))) if len(arr("n_wet_scaled", "value")) else None,
            }
        if temp:
            v = temp["values"].get("tmax|oct")
            if v:
                a = np.array([np.nan if x is None else x for x in v["anom"]], float)
                r = np.array([np.nan if x is None else x for x in v["rank_high"]], float)
                h["temp"] = {"n": int(np.isfinite(a).sum()), "tmax_oct_anom": float(np.nanmedian(a)),
                             "tmax_oct_warmer": int(np.nansum(a > 0)), "tmax_oct_top3": int(np.nansum(r <= 3))}
                t2 = temp["values"].get("tmin|oct")
                if t2:
                    h["temp"]["tmin_oct_anom"] = float(np.nanmedian([np.nan if x is None else x for x in t2["anom"]]))
        if water:
            rr = pd.DataFrame(water["rows"])
            fl = rr[rr["param"] == "flow"]
            h["water"] = {"n": len(rr), "n_lowest": int((rr["cat"] == 0).sum()), "n_top3": int((rr["cat"] <= 1).sum()),
                          "n_low_quarter": int((rr["cat"] <= 2).sum()),
                          "flow_pct_median": float(fl["oct_pct_median_base"].median()) if len(fl) else None,
                          "baseline_start": water["baseline_start"]}
        if enso:
            s = enso["summary"]
            h["enso"] = {"index": s["target_index"], "primary": s["primary"]}
            rg = [r for r in enso["regressions"] if r.get("model") == "enso" and r.get("region") in ("island",)
                  and r.get("metric") == "total_window"]
            h["enso"]["expected"] = {r["record"]: {k: r.get(k) for k in ("target_expected", "target_lo90", "target_hi90",
                                                                           "target_observed", "target_rp_cond",
                                                                           "target_rp_uncond", "r2")} for r in rg}
        return h

    def _csv(self, df: pd.DataFrame, name: str) -> None:
        p = self.out / "data" / "csv" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)

    def assets(self) -> None:
        a = self.out / "assets"
        a.mkdir(parents=True, exist_ok=True)
        src = resources.files("vi_precip") / "site_assets"
        for f in ("app.js", "style.css"):
            (a / f).write_text((src / f).read_text(encoding="utf-8"), encoding="utf-8")
        html = (src / "index.html").read_text(encoding="utf-8")
        (self.out / "index.html").write_text(html.replace("{{TITLE}}", self.title()), encoding="utf-8")
        pj = a / "plotly.min.js"
        if not pj.exists() or pj.stat().st_size < 1_000_000:
            try:
                import plotly

                shutil.copy(Path(plotly.__file__).parent / "package_data" / "plotly.min.js", pj)
            except Exception as ex:
                raise SystemExit("The dashboard needs plotly.min.js: `pip install plotly` (it is copied from the "
                                 f"package; nothing is loaded from a CDN). ({ex})")
        figs = self.out / "figures"
        figs.mkdir(exist_ok=True)
        for p in list(self.cfg.figures_dir.glob("water_ranks_*.png")) + list((self.cfg.out_dir / "brief" / "figures").glob("*.png")):
            shutil.copy(p, figs / p.name)

    def title(self) -> str:
        return f"Autumn {self.cfg.target_year}: how dry, and why? South Coast BC"

    def build(self) -> Path:
        self.out.mkdir(parents=True, exist_ok=True)
        for sub in ("data/stations", "data/csv"):
            shutil.rmtree(self.out / sub, ignore_errors=True)
        precip = self.precip()
        temp = self.temperature()
        water = self.water()
        enso = self.enso()
        n_st = self.stations(precip)
        vint = {}
        vp = self.cfg.out_dir / "data_vintage.json"
        if vp.exists():
            vint = json.loads(vp.read_text())
        e = self.cfg.end_date
        sc_bbox = self.cfg.raw["domain"]["bbox"] if self.cfg.raw["domain"].get("spec", "south_coast") == "south_coast" \
            else self.cfg.spatial.bbox
        bms = {"precip": _basemap(self.cfg, sc_bbox)}
        if water:
            rr = pd.DataFrame(water["rows"])
            bms["water"] = _basemap(self.cfg, [rr["lon"].min() - 0.3, rr["lat"].min() - 0.15,
                                               rr["lon"].max() + 0.3, rr["lat"].max() + 0.15])
        if enso and enso.get("station_maps"):
            sm = pd.DataFrame(enso["station_maps"])
            bms["enso"] = _basemap(self.cfg, [sm["lon"].min() - 0.5, sm["lat"].min() - 0.3,
                                              sm["lon"].max() + 0.5, sm["lat"].max() + 0.3])
        meta = {
            "title": self.title(), "target_year": self.cfg.target_year, "end_date": e.isoformat(),
            "end_label": f"{e:%b} {e.day}", "oct_label": f"Oct 1–{e.day}", "window_label": f"Sep 1–{e:%b} {e.day}",
            "baseline": list(self.cfg.baseline), "thresholds": self.cfg.wet_thresholds,
            "regions": self.cfg.spatial.region_names, "vi_regions": VI_REGIONS,
            "built": dt.datetime.now().isoformat(timespec="minutes"), "vintage": vint, "synthetic": self.synthetic,
            "have": {"precip": bool(precip), "temp": bool(temp), "water": bool(water), "enso": bool(enso)},
            "n_station_files": n_st, "headline": self.headline(precip, temp, water, enso), "basemaps": bms,
            "figures": sorted(p.name for p in (self.out / "figures").glob("*.png")) if (self.out / "figures").exists() else [],
            "csv": sorted(p.name for p in (self.out / "data" / "csv").glob("*.csv")),
        }
        self.assets()
        meta["figures"] = sorted(p.name for p in (self.out / "figures").glob("*.png"))
        self.js("meta.js", "meta", meta)
        tot = sum(self.sizes.values())
        print(f"\nDashboard: {self.out / 'index.html'}  ({n_st} station files; data {tot / 1e6:.1f} MB; "
              f"components: {', '.join(k for k, v in meta['have'].items() if v) or 'none'})")
        return self.out / "index.html"


def run(cfg: Config, out: Path | None = None, synthetic: bool = False) -> Path:
    return SiteBuilder(cfg, out, synthetic).build()
