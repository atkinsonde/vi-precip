"""Climate indices: download, parse, and seasonal means.

Sources (configurable in config.yaml -> indices.sources):
- NOAA PSL "standard format" monthly files: Nino 3.4, Nino 3, Nino 4, Nino 1+2 (ERSSTv5 anomalies),
  ONI, SOI, PNA, PDO, MEI.v2. Layout: a line "start end", one row per year (year + 12 values),
  then a line with the missing-value code, then free-text notes.
- CPC relative Nino 3.4 (RONI): HTML table, one row per year, 12 overlapping 3-month seasons.

Raw files are cached in data/raw/indices/ and re-downloaded after `max_age_days`. A file can also
be dropped there by hand (same name) if a server is unreachable.

Seasonal means: JJA, JAS, AS (Aug-Sep) and S from monthly values. For the 3-month-running
indices (ONI, RONI), JJA and JAS are read directly as the season centred on Jul and Aug.
"Flavour" = Nino 3 minus Nino 4 (positive = eastern-Pacific type).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import Config

log = logging.getLogger(__name__)

DEFAULT_SOURCES = {
    "nino34": "https://www.psl.noaa.gov/data/correlation/nina34.anom.data",
    "nino3": "https://www.psl.noaa.gov/data/correlation/nina3.anom.data",
    "nino4": "https://www.psl.noaa.gov/data/correlation/nina4.anom.data",
    "nino12": "https://www.psl.noaa.gov/data/correlation/nina1.anom.data",
    "oni": "https://www.psl.noaa.gov/data/correlation/oni.data",
    "soi": "https://www.psl.noaa.gov/data/correlation/soi.data",
    "pna": "https://www.psl.noaa.gov/data/correlation/pna.data",
    "pdo": "https://www.psl.noaa.gov/data/correlation/pdo.data",
    "mei": "https://www.psl.noaa.gov/enso/mei/data/meiv2.data",
    "roni": "https://cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni",
}
LABELS = {
    "nino34": "Niño 3.4", "nino3": "Niño 3", "nino4": "Niño 4", "nino12": "Niño 1+2", "oni": "ONI",
    "roni": "Relative Niño 3.4 (RONI)", "mei": "MEI.v2", "soi": "SOI", "pna": "PNA", "pdo": "PDO",
    "flavour": "Niño 3 − Niño 4 (EP flavour)",
}
RUNNING3 = {"oni", "roni"}  # values are 3-month running means; month = centre month
SEASONS = {"JJA": (6, 7, 8), "JAS": (7, 8, 9), "AS": (8, 9), "S": (9,)}
ENSO_INDICES = ["nino34", "roni", "oni", "mei", "soi", "flavour"]
FILTER_INDICES = ["pna", "pdo"]


# ------------------------------------------------------------------ parsing
def parse_psl(text: str) -> pd.DataFrame:
    """PSL standard format -> long frame (year, month, value)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = end = None
    i0 = 0
    for i, ln in enumerate(lines):
        tok = ln.split()
        if len(tok) == 2 and all(re.fullmatch(r"-?\d{4}", t) for t in tok):
            start, end, i0 = int(tok[0]), int(tok[1]), i + 1
            break
    rows, missing = [], None
    for ln in lines[i0:]:
        tok = ln.split()
        if len(tok) == 13 and re.fullmatch(r"\d{4}", tok[0]):
            y = int(tok[0])
            if start is None or start <= y <= (end or 9999):
                rows.append([y] + [float(t) for t in tok[1:]])
                continue
        if rows and len(tok) == 1:
            try:
                missing = float(tok[0])
            except ValueError:
                pass
            break
    if not rows:
        raise ValueError("No PSL-format rows found")
    df = pd.DataFrame(rows, columns=["year"] + list(range(1, 13))).melt("year", var_name="month", value_name="value")
    df["month"] = df["month"].astype(int)
    bad = df["value"] <= -99 if missing is None else np.isclose(df["value"], missing)
    df.loc[bad | (df["value"] <= -99.0), "value"] = np.nan
    return df.dropna().sort_values(["year", "month"]).reset_index(drop=True)


_SEAS12 = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


def parse_roni_html(text: str) -> pd.DataFrame:
    """CPC RONI table -> long frame (year, month=centre month, value)."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) >= 2 and re.fullmatch(r"\d{4}", cells[0]):
            vals = cells[1:13]
            for k, v in enumerate(vals):
                try:
                    rows.append((int(cells[0]), k + 1, float(v)))
                except ValueError:
                    pass
    if not rows:
        # plain-text fallback: "YEAR v1 ... v12" lines
        for ln in text.splitlines():
            tok = ln.split()
            if len(tok) >= 2 and re.fullmatch(r"\d{4}", tok[0]):
                for k, v in enumerate(tok[1:13]):
                    try:
                        rows.append((int(tok[0]), k + 1, float(v)))
                    except ValueError:
                        pass
    if not rows:
        raise ValueError("No RONI rows found")
    return pd.DataFrame(rows, columns=["year", "month", "value"])


# ------------------------------------------------------------------ fetch
def fetch_all(cfg: Config, refresh: bool = False) -> pd.DataFrame:
    icfg = cfg.raw.get("indices", {})
    sources = {**DEFAULT_SOURCES, **icfg.get("sources", {})}
    max_age = dt.timedelta(days=int(icfg.get("max_age_days", 7)))
    d = cfg.data_dir / "raw" / "indices"
    d.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "vi-precip/0.1 (research; cached)"
    frames, status = [], {}
    for name, url in sources.items():
        p = d / f"{name}.txt"
        fresh = p.exists() and dt.datetime.now() - dt.datetime.fromtimestamp(p.stat().st_mtime) < max_age
        if not fresh or refresh:
            try:
                r = s.get(url, timeout=60)
                r.raise_for_status()
                p.write_text(r.text)
            except Exception as ex:
                log.warning("%s: download failed (%s)%s", name, ex, "; using cached copy" if p.exists() else "")
        if not p.exists():
            status[name] = "missing"
            continue
        try:
            df = parse_roni_html(p.read_text()) if name == "roni" else parse_psl(p.read_text())
        except Exception as ex:
            log.warning("%s: could not parse %s (%s)", name, p, ex)
            status[name] = f"parse error: {ex}"
            continue
        frames.append(df.assign(index=name))
        last = df.iloc[-1]
        status[name] = f"{int(df['year'].min())}-{int(last['year'])}/{int(last['month']):02d}"
    if not frames:
        raise SystemExit("No climate index could be read. See data/raw/indices/.")
    m = pd.concat(frames, ignore_index=True)
    # flavour = Nino3 - Nino4
    if {"nino3", "nino4"} <= set(m["index"]):
        a = m[m["index"] == "nino3"].set_index(["year", "month"])["value"]
        b = m[m["index"] == "nino4"].set_index(["year", "month"])["value"]
        f = (a - b).dropna().reset_index().assign(index="flavour")
        m = pd.concat([m, f], ignore_index=True)
        status["flavour"] = "derived"
    for k, v in status.items():
        log.info("index %-8s %s", k, v)
    return m[["index", "year", "month", "value"]]


def seasonal(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, g in monthly.groupby("index"):
        s = g.set_index(["year", "month"])["value"]
        for y in sorted(g["year"].unique()):
            for sn, months in SEASONS.items():
                if name in RUNNING3:
                    centre = {"JJA": 7, "JAS": 8, "AS": None, "S": None}[sn]
                    if centre is None:
                        continue
                    v = s.get((y, centre), np.nan)
                else:
                    vals = [s.get((y, mo), np.nan) for mo in months]
                    v = float(np.mean(vals)) if all(np.isfinite(vals)) else np.nan
                if np.isfinite(v):
                    rows.append({"index": name, "year": int(y), "season": sn, "value": round(float(v), 4)})
    return pd.DataFrame(rows)


def run(cfg: Config, refresh: bool = False) -> dict[str, Path]:
    m = fetch_all(cfg, refresh)
    s = seasonal(m)
    out = cfg.data_dir / "processed"
    out.mkdir(parents=True, exist_ok=True)
    m.to_parquet(out / "indices_monthly.parquet", index=False)
    s.to_parquet(out / "indices_seasonal.parquet", index=False)
    t = cfg.tables_dir
    s.pivot_table(index="year", columns=["index", "season"], values="value").round(3).to_csv(t / "indices_seasonal.csv")
    ty = cfg.target_year
    cur = s[s["year"] == ty].pivot(index="index", columns="season", values="value")
    print(f"\n{ty} index values:\n{cur.round(2).to_string()}")
    return {"indices_monthly": out / "indices_monthly.parquet", "indices_seasonal": out / "indices_seasonal.parquet"}


def load_seasonal(cfg: Config) -> pd.DataFrame:
    p = cfg.data_dir / "processed" / "indices_seasonal.parquet"
    if not p.exists():
        raise SystemExit("Run `python run.py indices` first.")
    return pd.read_parquet(p)
