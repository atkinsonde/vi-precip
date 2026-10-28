"""Hydrometric data: river flow and lake level, for any spatial domain.

- Stations: GeoMet OGC hydrometric-stations (bbox of the domain), kept if inside the domain polygons.
- Approved daily means: Water Office daily_data (HYDAT); fallback OGC hydrometric-daily-mean.
- Provisional daily means (last 60 months): Water Office real_time_data parameters 6 (flow) and
  3 (level); fallback OGC hydrometric-realtime (30 days). Approved values win where both exist.
- Regulation: config/regulation.csv (pattern in the station name -> regulated / check / natural).
- Natural set for the ENSO analysis: Reference Hydrometric Basin Network (RHBN) stations, plus
  stations listed in hydro.reviewed_natural, minus anything matched as regulated or check.

Uses the same parsing as water/water.py (the published south-Island map), so its cache
(water/cache/*_v2.parquet) is reused when present.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import Config
from .spatial import Domain, get_domain

log = logging.getLogger(__name__)
WO = "https://wateroffice.ec.gc.ca/services"
OGC = "https://api.weather.gc.ca"
_FLOW = {"flow", "discharge", "1", "6", "47", "q"}
_LEVEL = {"level", "water level", "2", "3", "46", "h"}


def settings(cfg: Config) -> dict:
    h = cfg.raw.get("hydro", {})
    return {
        "baseline_start": int(h.get("baseline_start", 1985)),
        "min_years_map": int(h.get("min_years_map", 10)),
        "min_years_enso": int(h.get("min_years_enso", 30)),
        "enso_start": int(h.get("enso_start", 1950)),
        "max_missing": h.get("max_missing", {"oct": 2, "sepoct": 4, "last7": 1}),
        "exclude": h.get("exclude", {"08HB084": "Puntledge below the diversion"}),
        "reviewed_natural": [str(x) for x in h.get("reviewed_natural", [])],
        "map_domain": h.get("map_domain", "south_vi"),
        "enso_domain": h.get("enso_domain", "coastal_bc"),
        "pause_s": float(h.get("pause_s", 0.4)),
        "realtime_months": int(h.get("realtime_months", 60)),
    }


class Http:
    def __init__(self, pause: float, session: requests.Session | None = None):
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        self.s = session or requests.Session()
        if session is None:
            self.s.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1.5,
                                                                     status_forcelist=(429, 500, 502, 503, 504),
                                                                     allowed_methods=("GET",))))
            self.s.headers["User-Agent"] = "vi-precip-hydro/0.1 (research; cached, low-volume)"
        self.pause, self.n = pause, 0

    def get(self, url, params=None):
        if self.n:
            time.sleep(self.pause)
        self.n += 1
        return self.s.get(url, params=params, timeout=120)


def ogc_features(http: Http, collection: str, params: dict, max_features: int | None = None):
    r = http.get(f"{OGC}/collections/{collection}/items", {"f": "json", "limit": 10000, **params})
    r.raise_for_status()
    n = 0
    while True:
        js = r.json()
        feats = js.get("features") or []
        for f in feats:
            yield f
            n += 1
            if max_features and n >= max_features:
                return
        nxt = next((lk["href"] for lk in js.get("links", []) if lk.get("rel") == "next"), None)
        if not feats or not nxt:
            return
        r = http.get(nxt)
        r.raise_for_status()


def _first(props: dict, *names, default=None):
    up = {k.upper(): v for k, v in props.items()}
    for n in names:
        if n.upper() in up and up[n.upper()] not in (None, ""):
            return up[n.upper()]
    return default


# ------------------------------------------------------------------ parsing (as water/water.py)
def _norm(c: str) -> str:
    return c.replace("﻿", "").strip().split("/")[0].strip().lower()


def parse_wo_csv(text: str) -> pd.DataFrame:
    text = text.lstrip("﻿")
    if not text.strip():
        return pd.DataFrame(columns=["station", "date", "param", "value", "symbol"])
    df = pd.read_csv(io.StringIO(text), dtype=str, skipinitialspace=True)
    cols = {_norm(c): c for c in df.columns}

    def pick(*keys):
        for k in keys:
            for nc, c in cols.items():
                if nc == k or nc.startswith(k):
                    return c
        return None

    c_id, c_date, c_par, c_val, c_sym = pick("id", "station"), pick("date"), pick("parameter", "param"), \
        pick("value"), pick("symbol", "sym")
    if not all((c_id, c_date, c_par, c_val)):
        raise ValueError(f"Unrecognised Water Office CSV header: {list(df.columns)}")
    out = pd.DataFrame({
        "station": df[c_id].str.strip(),
        "date": pd.to_datetime(df[c_date].str.strip().str[:10], errors="coerce"),
        "param_raw": df[c_par].str.strip().str.lower().str.split("/").str[0].str.strip(),
        "value": pd.to_numeric(df[c_val], errors="coerce"),
        "symbol": df[c_sym].fillna("").str.strip() if c_sym else "",
    })
    out["param"] = np.where(out["param_raw"].isin(_FLOW), "flow", np.where(out["param_raw"].isin(_LEVEL), "level", None))
    return out.dropna(subset=["date", "param"]).drop(columns="param_raw")


def _from_ogc_daily(feats):
    rows = []
    for f in feats:
        p = f.get("properties") or {}
        for par, key in (("flow", "DISCHARGE"), ("level", "LEVEL")):
            v = _first(p, key)
            if v is not None:
                rows.append({"station": _first(p, "STATION_NUMBER"), "date": _first(p, "DATE"), "param": par,
                             "value": v, "symbol": ""})
    df = pd.DataFrame(rows, columns=["station", "date", "param", "value", "symbol"])
    df["date"] = pd.to_datetime(df["date"].astype(str).str[:10], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


# ------------------------------------------------------------------ fetch
class Hydro:
    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg
        self.S = settings(cfg)
        self.http = Http(self.S["pause_s"], session)
        self.cache = cfg.data_dir / "raw" / "hydro"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.alt_cache = cfg.root / "water" / "cache"
        self.rules = pd.read_csv(cfg.root / "config" / "regulation.csv").fillna("") \
            if (cfg.root / "config" / "regulation.csv").exists() else pd.DataFrame(columns=["pattern", "status", "note"])

    def regulation_of(self, name: str) -> tuple[str, str]:
        for _, r in self.rules.iterrows():
            if r["pattern"] and r["pattern"].upper() in name.upper():
                return r["status"], r["note"]
        return "natural (not checked)", ""

    def stations(self, dom: Domain) -> pd.DataFrame:
        tag = "_".join(f"{x:g}" for x in dom.bbox).replace("-", "m")
        p = self.cache / f"stations_{tag}.json"
        if p.exists():
            feats = json.loads(p.read_text())
        else:
            feats = list(ogc_features(self.http, "hydrometric-stations", {"bbox": ",".join(map(str, dom.bbox))}))
            p.write_text(json.dumps(feats))
        rows = []
        for f in feats:
            pr = f.get("properties") or {}
            g = (f.get("geometry") or {}).get("coordinates") or [None, None]
            rows.append({"station": str(_first(pr, "STATION_NUMBER", "IDENTIFIER", default="")).strip(),
                         "name": str(_first(pr, "STATION_NAME", default="")).strip(),
                         "prov": _first(pr, "PROV_TERR_STATE_LOC", default=""),
                         "status": str(_first(pr, "STATUS_EN", default="")),
                         "rhbn": bool(int(_first(pr, "RHBN", default=0) or 0)),
                         "real_time": bool(int(_first(pr, "REAL_TIME", default=0) or 0)),
                         "area_km2": pd.to_numeric(_first(pr, "DRAINAGE_AREA_GROSS"), errors="coerce"),
                         "lon": g[0], "lat": g[1]})
        st = pd.DataFrame(rows).drop_duplicates("station")
        if st.empty:
            return st
        st["lon"], st["lat"] = pd.to_numeric(st["lon"]), pd.to_numeric(st["lat"])
        st = st[dom.contains(st["lon"].values, st["lat"].values)]
        if dom.province:
            st = st[st["prov"].astype(str).str.upper().isin([dom.province, ""])]
        st = st.copy()
        st["regulation"], st["regulation_note"] = zip(*st["name"].map(self.regulation_of)) if len(st) else ([], [])
        st["is_lake"] = st["name"].str.contains(r"\bLAKE\b", regex=True) & \
            ~st["name"].str.contains(r"\b(?:RIVER|CREEK)\b", regex=True)
        st["natural_for_enso"] = (st["rhbn"] | st["station"].isin(self.S["reviewed_natural"])) & \
            ~st["regulation"].isin(["regulated", "check"]) & ~st["is_lake"]
        return st.sort_values("station").reset_index(drop=True)

    def _cached(self, stn: str, kind: str) -> Path | None:
        for d in (self.cache, self.alt_cache):
            p = d / f"{stn}_{kind}_v2.parquet"
            if p.exists():
                return p
        return None

    def approved(self, stn: str) -> pd.DataFrame:
        p = self._cached(stn, "approved")
        if p:
            return pd.read_parquet(p)
        end = self.cfg.end_date
        params = [("stations[]", stn), ("parameters[]", "flow"), ("parameters[]", "level"),
                  ("start_date", "1900-01-01"), ("end_date", end.isoformat())]
        try:
            r = self.http.get(f"{WO}/daily_data/csv/inline", params)
            r.raise_for_status()
            df = parse_wo_csv(r.text)
        except Exception as ex:
            log.warning("%s: daily_data failed (%s); OGC fallback", stn, ex)
            df = _from_ogc_daily(list(ogc_features(self.http, "hydrometric-daily-mean", {"STATION_NUMBER": stn})))
        df["source"] = "approved"
        df.to_parquet(self.cache / f"{stn}_approved_v2.parquet", index=False)
        return df

    def provisional(self, stn: str, refresh: bool = False) -> pd.DataFrame:
        p = self._cached(stn, "provisional")
        if p and not refresh:
            return pd.read_parquet(p)
        end = self.cfg.end_date
        start = (pd.Timestamp(end) - pd.DateOffset(months=self.S["realtime_months"]) + pd.Timedelta(days=2)).date()
        params = [("stations[]", stn), ("parameters[]", "6"), ("parameters[]", "3"),
                  ("start_date", f"{start} 00:00:00"), ("end_date", f"{end + dt.timedelta(days=1)} 23:59:59")]
        try:
            r = self.http.get(f"{WO}/real_time_data/csv/inline", params)
            r.raise_for_status()
            df = parse_wo_csv(r.text)
        except Exception as ex:
            log.warning("%s: real_time_data failed (%s)", stn, ex)
            df = pd.DataFrame(columns=["station", "date", "param", "value", "symbol"])
        df["source"] = "provisional"
        df.to_parquet(self.cache / f"{stn}_provisional_v2.parquet", index=False)
        return df

    def daily(self, stn: str, with_recent: bool = True, refresh: bool = False) -> pd.DataFrame:
        a = self.approved(stn).dropna(subset=["value"])
        if not with_recent:
            return a
        p = self.provisional(stn, refresh).dropna(subset=["value"])
        key = a.set_index(["param", "date"]).index
        p = p[~p.set_index(["param", "date"]).index.isin(key)]
        return pd.concat([a, p], ignore_index=True).sort_values(["param", "date"]).reset_index(drop=True)


# ------------------------------------------------------------------ annual series and ranks
def _bounds(per: str, y: int, end: dt.date):
    e = pd.Timestamp(y, end.month, end.day)
    if per == "last7":
        return e - pd.Timedelta(days=6), e
    return (pd.Timestamp(y, 10, 1) if per == "oct" else pd.Timestamp(y, 9, 1)), e


def annual(d: pd.DataFrame, par: str, end: dt.date, max_missing: dict) -> pd.DataFrame:
    s = d[d["param"] == par].set_index("date")["value"]
    s = s[~s.index.duplicated(keep="first")]
    rows = []
    if s.empty:
        return pd.DataFrame(columns=["year", "period", "value"])
    for y in range(s.index.min().year, end.year + 1):
        for per in ("oct", "sepoct", "last7"):
            a, b = _bounds(per, y, end)
            sub = s.loc[a:b]
            if (b - a).days + 1 - sub.notna().sum() <= int(max_missing[per]):
                rows.append({"year": y, "period": per, "value": float(sub.mean())})
    return pd.DataFrame(rows)


def rank_of(series: pd.DataFrame, per: str, ty: int, y0: int, tag: str, par: str = "flow") -> dict:
    """Rank of the target year (1 = lowest) among valid years y0..ty, plus median and last lower year."""
    g = series[series["period"] == per].set_index("year")["value"]
    if ty not in g.index:
        return {}
    x = g[ty]
    hist = g[(g.index >= y0) & (g.index < ty)]
    if hist.empty:
        return {}
    k = float((hist < x).sum() + 0.5 * (hist == x).sum())
    med = float(hist.median())
    lower = g[(g.index < ty) & (g < x)]
    return {f"{per}_value": x, f"{per}_rank_{tag}": k + 1, f"{per}_n_{tag}": len(hist) + 1,
            f"{per}_median_{tag}": med,
            f"{per}_pct_median_{tag}": 100 * x / med if par == "flow" and med > 0 else np.nan,
            f"{per}_diff_median_{tag}": x - med if par == "level" else np.nan,
            f"{per}_last_lower_year": int(lower.index.max()) if len(lower) else None}


def station_ranks(a: pd.DataFrame, par: str, ty: int, y0: int) -> dict:
    res = {}
    for per in ("oct", "sepoct", "last7"):
        res |= rank_of(a, per, ty, y0, "base", par) | rank_of(a, per, ty, 0, "rec", par)
    return res


def run(cfg: Config, refresh_recent: bool = False, session=None) -> dict[str, Path]:
    H = Hydro(cfg, session)
    S, end, ty = H.S, cfg.end_date, cfg.target_year
    out = cfg.data_dir / "processed"
    out.mkdir(parents=True, exist_ok=True)
    paths = {}

    # --- map domain: ranks for all active stations
    md = get_domain(S["map_domain"])
    st = H.stations(md)
    st = st[st["status"].str.lower().str.startswith("active") | (st["status"] == "")]
    rows, series_all = [], []
    for _, r in st.iterrows():
        if r["station"] in S["exclude"]:
            continue
        try:
            d = H.daily(r["station"], refresh=refresh_recent)
        except Exception as ex:
            log.error("%s: %s", r["station"], ex)
            continue
        par = "flow" if (d["param"] == "flow").any() and not r["is_lake"] else "level"
        a = annual(d, par, end, S["max_missing"])
        if a.empty:
            continue
        rk = station_ranks(a, par, ty, S["baseline_start"])
        if not rk.get("oct_n_base") or rk["oct_n_base"] < S["min_years_map"]:
            continue
        a0, b0 = _bounds("oct", ty, end)
        src = d[(d["param"] == par) & d["date"].between(a0, b0)]["source"]
        rows.append({**r.to_dict(), "param": par, "first_year": int(a["year"].min()), **rk,
                     "baseline_start": S["baseline_start"], "oct_provisional_days": int((src == "provisional").sum())})
        series_all.append(a.assign(station=r["station"], param=par))
    ranks = pd.DataFrame(rows)
    ranks.to_csv(cfg.tables_dir / f"hydro_ranks_{ty}.csv", index=False)
    ranks.to_parquet(out / "hydro_ranks.parquet", index=False)
    paths["hydro_ranks"] = out / "hydro_ranks.parquet"
    if len(ranks):
        try:
            from .geo import load_basemap
            from .plots.water_map import make_map

            ext = [md.bbox[0] - 0.3, md.bbox[1] - 0.15, md.bbox[2] + 0.3, min(md.bbox[3], md.lat_max or 99) + 0.15]
            mp = make_map(ranks.copy(), cfg.figures_dir, end,
                          {"target_year": ty, "baseline_start": S["baseline_start"], "map_extent": ext},
                          basemap=load_basemap(cfg, bbox=ext) or {"land": [], "coast": []})
            paths |= {f"hydro_map_{k}": v for k, v in mp.items()}
        except Exception as ex:  # the map is a convenience; the tables are the product
            log.warning("water map not drawn (%s)", ex)

    # --- ENSO domain: natural rivers with long records
    ed = get_domain(S["enso_domain"])
    est = H.stations(ed)
    est = est[est["natural_for_enso"]]
    log.info("%d natural (RHBN or reviewed) river stations in %s", len(est), ed.name)
    nat = []
    for _, r in est.iterrows():
        try:
            d = H.daily(r["station"], with_recent=r["status"].lower().startswith("active"), refresh=refresh_recent)
        except Exception as ex:
            log.error("%s: %s", r["station"], ex)
            continue
        a = annual(d, "flow", end, S["max_missing"])
        a = a[a["year"] >= S["enso_start"]]
        n = a[(a["period"] == "oct") & (a["year"] < ty)]["year"].nunique()
        if n < S["min_years_enso"]:
            continue
        nat.append(a.assign(station=r["station"], name=r["name"], lon=r["lon"], lat=r["lat"], rhbn=r["rhbn"],
                            area_km2=r["area_km2"], status=r["status"]))
    nat = pd.concat(nat, ignore_index=True) if nat else pd.DataFrame()
    nat.to_parquet(out / "hydro_natural_annual.parquet", index=False)
    paths["hydro_natural"] = out / "hydro_natural_annual.parquet"
    if len(series_all):
        pd.concat(series_all, ignore_index=True).to_parquet(out / "hydro_map_annual.parquet", index=False)
    print(f"\nHydro: {len(ranks)} stations ranked in {md.name} (since {S['baseline_start']}); "
          f"{nat['station'].nunique() if len(nat) else 0} natural long-record rivers in {ed.name} for ENSO")
    return paths
