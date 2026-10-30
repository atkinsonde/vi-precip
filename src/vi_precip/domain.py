"""Study domain and region assignment.

Polygons are deliberately coarse, drawn through water between land masses. Stations
are on land, so the polygons only need to separate the land areas correctly; borderline
cases are fixed in config/region_overrides.csv rather than by refining geometry.

Order of tests: mainland regions, then Vancouver Island, then Gulf Islands (the
remainder of the Strait of Georgia box). Vancouver Island is split into West/East by a
polyline along the island ranges, and North VI is everything north of 50.25°N.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath

log = logging.getLogger(__name__)

REGIONS = [
    "West VI",
    "East VI",
    "North VI",
    "Gulf Islands",
    "Sunshine Coast",
    "Sea-to-Sky",
    "Lower Mainland",
]
VI_REGIONS = ["West VI", "East VI", "North VI"]

# (lon, lat) rings ------------------------------------------------------------
VANCOUVER_ISLAND = [
    (-123.27, 48.28), (-123.15, 48.40), (-123.20, 48.50), (-123.30, 48.60), (-123.37, 48.665),
    (-123.47, 48.69), (-123.60, 48.72), (-123.585, 48.80), (-123.615, 48.87),
    (-123.66, 48.92), (-123.74, 48.97), (-123.80, 49.03), (-123.84, 49.10),
    (-123.90, 49.16), (-123.98, 49.22), (-124.10, 49.29), (-124.30, 49.345),
    (-124.45, 49.38), (-124.62, 49.42), (-124.72, 49.47), (-124.84, 49.53),
    (-124.88, 49.60), (-124.85, 49.66), (-124.82, 49.72), (-125.00, 49.85),
    (-125.15, 49.97), (-125.05, 50.05), (-125.00, 50.30), (-125.40, 50.40),
    (-126.00, 50.46), (-126.50, 50.56), (-127.00, 50.70), (-127.50, 50.85),
    (-128.20, 50.97), (-129.20, 50.90), (-128.60, 50.20), (-127.60, 49.60),
    (-126.60, 49.10), (-125.70, 48.75), (-124.80, 48.45), (-124.00, 48.27),
]

LOWER_MAINLAND = [
    (-123.30, 48.99), (-121.35, 49.00), (-121.35, 49.62), (-122.60, 49.62),
    (-123.05, 49.55), (-123.30, 49.50), (-123.42, 49.45), (-123.43, 49.32),
]

SEA_TO_SKY = [
    (-123.42, 49.45), (-123.30, 49.50), (-123.05, 49.55), (-122.70, 49.95),
    (-122.75, 50.20), (-123.15, 50.20), (-123.40, 49.80), (-123.38, 49.55),
]

SUNSHINE_COAST = [
    (-123.43, 49.32), (-123.55, 49.36), (-123.80, 49.42), (-124.00, 49.48),
    (-124.05, 49.60), (-124.30, 49.70), (-124.40, 49.78), (-124.62, 49.82),
    (-124.85, 49.95), (-124.95, 50.10), (-124.00, 50.10), (-123.15, 50.20), (-123.40, 49.80),
    (-123.38, 49.55), (-123.42, 49.45),
]

# Gulf Islands: what remains inside this Strait of Georgia box after the tests above.
GULF_BOX = (-124.95, 48.60, -123.00, 49.98)  # lon_min, lat_min, lon_max, lat_max

# West/East divide along the Vancouver Island Ranges (south to north).
VI_SPINE = [
    (-124.05, 48.25), (-124.25, 48.65), (-124.55, 49.05), (-124.65, 49.35),
    (-125.30, 49.75), (-125.75, 50.05), (-126.60, 50.40),
]
NORTH_VI_LAT = 50.25


def _contains(ring: list[tuple[float, float]], lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    return MplPath(np.asarray(ring)).contains_points(np.column_stack([lon, lat]))


def _spine_lon(lat: np.ndarray) -> np.ndarray:
    s = np.asarray(VI_SPINE)
    return np.interp(lat, s[:, 1], s[:, 0])


def assign_region(lon, lat) -> np.ndarray:
    """Vectorized region assignment; returns object array with region names or None."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    out = np.full(lon.shape, None, dtype=object)

    lm = _contains(LOWER_MAINLAND, lon, lat)
    s2s = _contains(SEA_TO_SKY, lon, lat) & ~lm
    sc = _contains(SUNSHINE_COAST, lon, lat) & ~lm & ~s2s
    vi = _contains(VANCOUVER_ISLAND, lon, lat) & ~lm & ~s2s & ~sc
    a, b, c, d = GULF_BOX
    gi = (lon >= a) & (lon <= c) & (lat >= b) & (lat <= d) & ~lm & ~s2s & ~sc & ~vi

    out[lm] = "Lower Mainland"
    out[s2s] = "Sea-to-Sky"
    out[sc] = "Sunshine Coast"
    out[gi] = "Gulf Islands"
    north = vi & (lat >= NORTH_VI_LAT)
    west = vi & ~north & (lon < _spine_lon(lat))
    east = vi & ~north & ~west
    out[north] = "North VI"
    out[west] = "West VI"
    out[east] = "East VI"
    return out


def load_overrides(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["climate_id", "region", "note"])
    ov = pd.read_csv(path, dtype=str).fillna("")
    ov = ov[ov["climate_id"].str.strip() != ""]
    bad = ov[~ov["region"].isin(REGIONS + ["exclude"])]
    if len(bad):
        raise ValueError(f"Unknown regions in {path}: {sorted(bad['region'].unique())}. Use one of {REGIONS} or 'exclude'.")
    return ov


def add_regions(stations: pd.DataFrame, overrides_path: Path) -> pd.DataFrame:
    """Add region, region_source, is_vi; drop stations outside the domain (or excluded)."""
    st = stations.copy()
    st["region"] = assign_region(st["lon"].values, st["lat"].values)
    st["region_source"] = np.where(st["region"].isna(), "outside", "auto")
    ov = load_overrides(overrides_path)
    if len(ov):
        m = dict(zip(ov["climate_id"].str.strip(), ov["region"].str.strip()))
        hit = st["climate_id"].isin(m)
        st.loc[hit, "region"] = st.loc[hit, "climate_id"].map(m)
        st.loc[hit, "region_source"] = "override"
        log.info("Applied %d region overrides", int(hit.sum()))
    st = st[st["region"].notna() & (st["region"] != "exclude")].copy()
    st["is_vi"] = st["region"].isin(VI_REGIONS)
    st["region"] = pd.Categorical(st["region"], categories=REGIONS, ordered=True)
    return st.reset_index(drop=True)
