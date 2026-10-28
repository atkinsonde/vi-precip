"""ECCC MSC GeoMet OGC API client with local caching.

Only this module knows about the API. Everything downstream works with the
standardized frames returned by `standardize_stations()` and `standardize_daily()`.

Robustness choices (the API has changed before and will again):
- Field names are resolved from candidate lists, case-insensitively; `probe` reports
  what was found so mismatches surface immediately.
- Optional query parameters (`sortby`, `properties`, `datetime`) are dropped
  automatically if the server rejects them, and that is remembered in
  `data/raw/eccc/api_capabilities.json`.
- Paging follows `rel=next` links, falling back to offset paging with `numberMatched`.
- Daily data are cached per station and calendar year. A cached year is re-fetched
  only if it is not yet "final" (fetched less than `final_after_days` after the year
  ended) and a refresh of recent data was requested.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config

log = logging.getLogger(__name__)

STATIONS = "climate-stations"
DAILY = "climate-daily"

# Canonical name -> candidate API property names (first match wins, case-insensitive).
STATION_FIELDS: dict[str, list[str]] = {
    "climate_id": ["CLIMATE_IDENTIFIER", "CLIMATE_ID"],
    "name": ["STATION_NAME"],
    "province": ["PROV_STATE_TERR_CODE", "PROVINCE_CODE", "ENG_PROV_NAME"],
    "elev_m": ["ELEVATION", "ELEVATION_M"],
    "stn_id": ["STN_ID"],
    "tc_id": ["TC_IDENTIFIER"],
    "wmo_id": ["WMO_IDENTIFIER"],
    "station_type": ["STATION_TYPE"],
    "operator": ["ENG_STN_OPERATOR_NAME", "ENG_STN_OPERATOR_ACRONYM"],
    "first_date": ["FIRST_DATE"],
    "last_date": ["LAST_DATE"],
    "dly_first": ["DLY_FIRST_DATE"],
    "dly_last": ["DLY_LAST_DATE"],
    "has_normals": ["HAS_NORMALS_DATA"],
    "lat_prop": ["LATITUDE"],
    "lon_prop": ["LONGITUDE"],
}

DAILY_FIELDS: dict[str, list[str]] = {
    "climate_id": ["CLIMATE_IDENTIFIER", "CLIMATE_ID"],
    "date": ["LOCAL_DATE", "DATE"],
    "precip": ["TOTAL_PRECIPITATION"],
    "precip_flag": ["TOTAL_PRECIPITATION_FLAG"],
    "rain": ["TOTAL_RAIN"],
    "rain_flag": ["TOTAL_RAIN_FLAG"],
    "snow": ["TOTAL_SNOW"],
    "snow_flag": ["TOTAL_SNOW_FLAG"],
    "tmax": ["MAX_TEMPERATURE"],
    "tmax_flag": ["MAX_TEMPERATURE_FLAG"],
    "tmin": ["MIN_TEMPERATURE"],
    "tmin_flag": ["MIN_TEMPERATURE_FLAG"],
    "tmean": ["MEAN_TEMPERATURE"],
    "tmean_flag": ["MEAN_TEMPERATURE_FLAG"],
}
DAILY_NUMERIC = ["precip", "rain", "snow", "tmax", "tmin", "tmean"]
DAILY_REQUIRED = ["climate_id", "date", "precip", "precip_flag"]


def resolve_fields(columns: list[str], candidates: dict[str, list[str]]) -> dict[str, str | None]:
    """Map canonical names to the actual column names present (case-insensitive)."""
    upper = {c.upper(): c for c in columns}
    out: dict[str, str | None] = {}
    for canon, cands in candidates.items():
        out[canon] = next((upper[c.upper()] for c in cands if c.upper() in upper), None)
    return out


def to_date(s: pd.Series) -> pd.Series:
    """Parse API date strings ('2026-10-01', '2026-10-01 00:00:00', ISO with Z) to naive dates."""
    d = pd.to_datetime(s, errors="coerce", format="mixed", utc=True)
    return d.dt.tz_localize(None).dt.normalize()


def _clean_flag(s: pd.Series) -> pd.Series:
    out = s.astype("object").where(s.notna(), None)
    out = out.map(lambda v: None if v is None or str(v).strip() in ("", "nan", "None") else str(v).strip())
    return out


class ECCCClient:
    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg
        e = cfg.eccc
        self.base = e["base_url"].rstrip("/")
        self.limit = int(e["page_limit"])
        self.pause = float(e["request_pause_s"])
        self.timeout = float(e["timeout_s"])
        self.final_after = dt.timedelta(days=int(e["final_after_days"]))
        self.station_cache_days = int(e["station_cache_days"])
        self.cache = cfg.data_dir / "raw" / "eccc"
        self.daily_cache = cfg.data_dir / "raw" / "eccc_daily"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.daily_cache.mkdir(parents=True, exist_ok=True)
        self.session = session or self._make_session(int(e["max_retries"]))
        self.n_requests = 0
        self._caps_path = self.cache / "api_capabilities.json"
        self.unsupported: dict[str, set[str]] = self._load_caps()

    # ------------------------------------------------------------------ http
    @staticmethod
    def _make_session(max_retries: int) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://", HTTPAdapter(max_retries=retry))
        s.headers["User-Agent"] = "vi-precip/0.1 (research; cached, low-volume)"
        return s

    def _get(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        if self.n_requests:
            time.sleep(self.pause)
        self.n_requests += 1
        r = self.session.get(url, params=params, timeout=self.timeout)
        log.debug("GET %s %s -> %s", url, params, r.status_code)
        return r

    def _load_caps(self) -> dict[str, set[str]]:
        if self._caps_path.exists():
            try:
                raw = json.loads(self._caps_path.read_text())
                return {k: set(v) for k, v in raw.get("unsupported", {}).items()}
            except (ValueError, OSError):
                pass
        return {}

    def _save_caps(self) -> None:
        self._caps_path.write_text(
            json.dumps({"unsupported": {k: sorted(v) for k, v in self.unsupported.items()}}, indent=2)
        )

    def iter_features(
        self,
        collection: str,
        params: dict[str, Any],
        optional: tuple[str, ...] = ("sortby", "properties", "datetime"),
        max_features: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield GeoJSON features, handling paging and unsupported optional params."""
        url = f"{self.base}/collections/{collection}/items"
        bad = self.unsupported.setdefault(collection, set())
        p = {k: v for k, v in params.items() if k not in bad}
        p.setdefault("f", "json")
        p.setdefault("limit", self.limit if max_features is None else min(self.limit, max_features))

        # First page, with fallback for rejected optional parameters.
        while True:
            r = self._get(url, p)
            if r.status_code == 400:
                drop = next((k for k in optional if k in p), None)
                if drop is None:
                    raise requests.HTTPError(f"400 from {r.url}: {r.text[:300]}", response=r)
                log.warning("Server rejected '%s' on %s; retrying without it", drop, collection)
                bad.add(drop)
                self._save_caps()
                p.pop(drop)
                continue
            r.raise_for_status()
            break

        n = 0
        offset = int(p.get("offset", 0))
        while True:
            js = r.json()
            feats = js.get("features", []) or []
            for f in feats:
                yield f
                n += 1
                if max_features is not None and n >= max_features:
                    return
            if not feats:
                return
            nxt = next((lk.get("href") for lk in js.get("links", []) if lk.get("rel") == "next"), None)
            matched = js.get("numberMatched")
            offset += len(feats)
            if nxt:
                r = self._get(nxt, None)
            elif matched is not None and offset < int(matched):
                p["offset"] = offset
                r = self._get(url, p)
            elif matched is None and len(feats) >= int(p["limit"]):
                p["offset"] = offset
                r = self._get(url, p)
            else:
                return
            r.raise_for_status()

    def datetime_supported(self, collection: str) -> bool:
        return "datetime" not in self.unsupported.get(collection, set())

    # -------------------------------------------------------------- stations
    def get_stations(self, refresh: bool = False) -> pd.DataFrame:
        """Raw station metadata for the domain bbox (cached)."""
        tag = "_".join(f"{x:g}" for x in self.cfg.domain["bbox"]).replace("-", "m")
        legacy = self.cache / "stations_raw.parquet"
        path = self.cache / f"stations_raw_{tag}.parquet"
        meta = self.cache / f"stations_raw_{tag}.json"
        if not path.exists() and legacy.exists() and (self.cache / "stations_raw.json").exists() \
                and list(self.cfg.domain["bbox"]) == [-128.8, 48.2, -121.3, 51.05]:
            path, meta = legacy, self.cache / "stations_raw.json"  # cache written by milestone 1
        if path.exists() and meta.exists() and not refresh:
            fetched = dt.datetime.fromisoformat(json.loads(meta.read_text())["fetched"])
            if dt.datetime.now() - fetched < dt.timedelta(days=self.station_cache_days):
                return pd.read_parquet(path)
        bbox = ",".join(str(x) for x in self.cfg.domain["bbox"])
        rows = []
        for f in self.iter_features(STATIONS, {"bbox": bbox}, optional=("sortby", "properties")):
            props = dict(f.get("properties") or {})
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") or [None, None]
            props["_lon"], props["_lat"] = coords[0], coords[1]
            rows.append(props)
        df = pd.DataFrame(rows)
        log.info("Fetched %d station records in bbox", len(df))
        # Store everything as strings except our coordinates: parquet-safe and lossless.
        for c in df.columns:
            if c not in ("_lon", "_lat"):
                df[c] = df[c].astype("string")
        df.to_parquet(path, index=False)
        meta.write_text(json.dumps({"fetched": dt.datetime.now().isoformat(timespec="seconds"), "n": len(df)}))
        return df

    # ----------------------------------------------------------------- daily
    def _manifest_path(self, cid: str) -> Path:
        return self.daily_cache / cid / "manifest.json"

    def _load_manifest(self, cid: str) -> dict[str, Any]:
        p = self._manifest_path(cid)
        return json.loads(p.read_text()) if p.exists() else {}

    def _year_is_final(self, year: int, entry: dict[str, Any]) -> bool:
        fetched = dt.datetime.fromisoformat(entry["fetched"])
        return fetched >= dt.datetime(year + 1, 1, 1) + self.final_after

    def years_to_fetch(self, cid: str, years: list[int], refresh_recent: bool) -> list[int]:
        man = self._load_manifest(cid)
        out = []
        for y in years:
            e = man.get(str(y))
            if e is None or (refresh_recent and not self._year_is_final(y, e)):
                out.append(y)
        return out

    def get_daily(
        self,
        cid: str,
        start: dt.date,
        end: dt.date,
        refresh_recent: bool = False,
        meta_first: dt.date | None = None,
        meta_last: dt.date | None = None,
    ) -> pd.DataFrame:
        """Raw daily records for one station between start and end (inclusive), via the year cache.

        meta_first/meta_last (from station metadata) let us skip years with no possible data.
        """
        years = list(range(start.year, end.year + 1))
        if meta_first is not None:
            years = [y for y in years if y >= meta_first.year]
        if meta_last is not None:
            # metadata can lag; allow one extra year
            years = [y for y in years if y <= meta_last.year + 1]
        todo = self.years_to_fetch(cid, years, refresh_recent)
        if todo:
            self._fetch_years(cid, todo)
        frames = []
        for y in years:
            p = self.daily_cache / cid / f"{y}.parquet"
            if p.exists():
                frames.append(pd.read_parquet(p))
        frames = [f for f in frames if len(f)]
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        f = resolve_fields(list(df.columns), DAILY_FIELDS)
        d = to_date(df[f["date"]])
        return df[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))].reset_index(drop=True)

    def _fetch_years(self, cid: str, years: list[int]) -> None:
        """Fetch contiguous runs of years in single paged queries; write one parquet per year."""
        years = sorted(years)
        runs: list[list[int]] = []
        for y in years:
            if runs and y == runs[-1][-1] + 1:
                runs[-1].append(y)
            else:
                runs.append([y])
        d = self.daily_cache / cid
        d.mkdir(parents=True, exist_ok=True)
        man = self._load_manifest(cid)
        now = dt.datetime.now().isoformat(timespec="seconds")
        for run in runs:
            a, b = dt.date(run[0], 1, 1), dt.date(run[-1], 12, 31)
            params: dict[str, Any] = {
                "CLIMATE_IDENTIFIER": cid,
                "sortby": "LOCAL_DATE",
                "datetime": f"{a.isoformat()}/{b.isoformat()}",
            }
            raw = [dict(f.get("properties") or {}) for f in self.iter_features(DAILY, params)]
            df = pd.DataFrame(raw)
            if len(df):
                f = resolve_fields(list(df.columns), DAILY_FIELDS)
                if f["date"] is None:
                    raise RuntimeError(f"No date field in climate-daily response; columns: {list(df.columns)}")
                dates = to_date(df[f["date"]])
                # If datetime filtering was dropped we may have the whole record: keep only this run.
                keep = (dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))
                df, dates = df[keep].copy(), dates[keep]
                for c in df.columns:
                    df[c] = df[c].astype("string")
                yrs = dates.dt.year
            else:
                yrs = pd.Series([], dtype=int)
            for y in run:
                part = df[yrs.values == y] if len(df) else df
                part.reset_index(drop=True).to_parquet(d / f"{y}.parquet", index=False)
                man[str(y)] = {"fetched": now, "rows": int(len(part))}
            log.info("  %s: fetched %d-%d (%d rows)", cid, run[0], run[-1], len(df))
        self._manifest_path(cid).write_text(json.dumps(man, indent=1, sort_keys=True))

    # ----------------------------------------------------------------- probe
    def probe(self) -> dict[str, Any]:
        """Check collections, field names and parameter support. Writes nothing to the cache
        except capability notes."""
        report: dict[str, Any] = {"base_url": self.base, "when": dt.datetime.now().isoformat(timespec="seconds")}
        r = self._get(f"{self.base}/collections", {"f": "json"})
        r.raise_for_status()
        ids = [c.get("id") for c in r.json().get("collections", [])]
        report["collections_found"] = {c: (c in ids) for c in (STATIONS, DAILY, "ahccd-stations", "ahccd-monthly")}
        report["ahccd_collections"] = sorted(i for i in ids if i and i.startswith("ahccd"))

        bbox = ",".join(str(x) for x in self.cfg.domain["bbox"])
        st = list(self.iter_features(STATIONS, {"bbox": bbox}, max_features=3))
        sprops = list((st[0].get("properties") or {}).keys()) if st else []
        report["stations"] = {
            "n_sample": len(st),
            "properties": sprops,
            "resolved": resolve_fields(sprops, STATION_FIELDS),
            "sample": [f.get("properties") for f in st[:2]],
            "geometry_sample": st[0].get("geometry") if st else None,
        }

        # A station with recent daily data for the daily probe: prefer one with DLY_LAST_DATE recent.
        cid = None
        if st:
            f = resolve_fields(sprops, STATION_FIELDS)
            cid = (st[0].get("properties") or {}).get(f["climate_id"] or "CLIMATE_IDENTIFIER")
        dly = list(self.iter_features(DAILY, {"CLIMATE_IDENTIFIER": cid} if cid else {}, max_features=3))
        dprops = list((dly[0].get("properties") or {}).keys()) if dly else []
        report["daily"] = {
            "probe_station": cid,
            "n_sample": len(dly),
            "properties": dprops,
            "resolved": resolve_fields(dprops, DAILY_FIELDS),
            "sample": [f.get("properties") for f in dly[:2]],
        }

        tests = {
            "bbox": {"bbox": bbox},
            "datetime": {"datetime": "2025-09-01/2025-09-03"},
            "sortby": {"sortby": "-LOCAL_DATE"},
            "properties": {"properties": "LOCAL_DATE,TOTAL_PRECIPITATION"},
        }
        caps = {}
        for name, extra in tests.items():
            p = {"f": "json", "limit": 2, **extra}
            if cid:
                p["CLIMATE_IDENTIFIER"] = cid
            try:
                rr = self._get(f"{self.base}/collections/{DAILY}/items", p)
                caps[name] = rr.status_code
            except requests.RequestException as ex:  # pragma: no cover - network
                caps[name] = f"error: {ex}"
        report["daily_param_status"] = caps
        bad = self.unsupported.setdefault(DAILY, set())
        for name in ("datetime", "sortby", "properties"):
            if caps.get(name) == 400:
                bad.add(name)
            elif caps.get(name) == 200:
                bad.discard(name)
        self._save_caps()
        missing = [k for k in DAILY_REQUIRED if report["daily"]["resolved"].get(k) is None]
        report["daily_required_missing"] = missing
        report["ok"] = bool(st) and bool(dly) and not missing
        return report


# ---------------------------------------------------------------- standardize
def standardize_stations(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["climate_id", "name", "lon", "lat", "elev_m"])
    f = resolve_fields(list(raw.columns), STATION_FIELDS)
    out = pd.DataFrame(index=raw.index)
    for canon, col in f.items():
        out[canon] = raw[col] if col is not None else pd.NA
    out["lon"] = pd.to_numeric(raw.get("_lon"), errors="coerce")
    out["lat"] = pd.to_numeric(raw.get("_lat"), errors="coerce")
    out["elev_m"] = pd.to_numeric(out["elev_m"], errors="coerce")
    for c in ("first_date", "last_date", "dly_first", "dly_last"):
        out[c] = to_date(out[c].astype("string")) if out[c].notna().any() else pd.NaT
    out["climate_id"] = out["climate_id"].astype("string").str.strip()
    out["name"] = out["name"].astype("string").str.strip()
    out = out.drop(columns=["lat_prop", "lon_prop"])
    out = out.dropna(subset=["climate_id", "lon", "lat"])
    # One row per climate ID: keep the record with the latest daily end date.
    out = out.sort_values(["climate_id", "dly_last"], na_position="first").drop_duplicates("climate_id", keep="last")
    return out.reset_index(drop=True)


def filter_province(stations_raw: pd.DataFrame, std: pd.DataFrame, province: str) -> pd.DataFrame:
    f = resolve_fields(list(stations_raw.columns), STATION_FIELDS)
    if f["province"] is None:
        log.warning("No province field in station metadata; relying on domain polygons only")
        return std
    p = std["province"].astype("string").str.upper().str.strip()
    keep = p.isin([province.upper(), "BRITISH COLUMBIA"]) | p.isna()
    return std[keep].reset_index(drop=True)


_warned: set[str] = set()


def standardize_daily(raw: pd.DataFrame, climate_id: str | None = None) -> pd.DataFrame:
    """Raw API daily records -> canonical columns, one row per date, sorted."""
    cols = ["climate_id", "date"] + [c for c in DAILY_FIELDS if c not in ("climate_id", "date")]
    if raw is None or raw.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}).assign(
            date=pd.Series(dtype="datetime64[ns]"), climate_id=pd.Series(dtype="string")
        )
    f = resolve_fields(list(raw.columns), DAILY_FIELDS)
    for k in DAILY_REQUIRED:
        if f[k] is None:
            raise RuntimeError(f"climate-daily response lacks required field '{k}'. Run `python run.py probe`.")
    out = pd.DataFrame(index=raw.index)
    for canon, col in f.items():
        if col is None:
            if canon not in _warned:
                log.warning("Daily field '%s' not found in API response; filled with missing", canon)
                _warned.add(canon)
            out[canon] = np.nan if canon in DAILY_NUMERIC else None
        else:
            out[canon] = raw[col]
    out["date"] = to_date(out["date"].astype("string"))
    for c in DAILY_NUMERIC:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    for c in [c for c in out.columns if c.endswith("_flag")]:
        out[c] = _clean_flag(out[c])
    out["climate_id"] = out["climate_id"].astype("string").str.strip()
    if climate_id is not None:
        out["climate_id"] = climate_id
    out = out.dropna(subset=["date"]).sort_values("date")
    dup = out["date"].duplicated(keep="last")
    if dup.any():
        log.warning("%s: %d duplicate dates dropped (kept last)", climate_id, int(dup.sum()))
        out = out[~dup]
    return out[cols].reset_index(drop=True)
