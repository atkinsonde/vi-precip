"""Spatial domains: where stations are searched for and how they are grouped into regions.

A domain is one or more polygons (lon, lat). It can be
- a preset name: "south_coast" (default; Vancouver Island, Gulf Islands, Sunshine Coast,
  Sea-to-Sky, Lower Mainland to Hope), "south_vi" (Vancouver Island south of 49.80 N),
  "coastal_bc" (BC coast west of the Coast Mountains crest, approximately), or
- a path to a GeoJSON file (Feature or FeatureCollection of Polygon/MultiPolygon; an optional
  "region" property on each feature names a region; a bare bounding box can be written as a
  Polygon with five corner points).

Every fetch routine takes the domain's bounding box for the server query and then keeps only
stations inside the polygons. A future interactive selector only needs to write a GeoJSON file
and pass it with --domain path/to/file.geojson (or set domain.spec in config.yaml).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath

from . import domain as sc  # the south-coast region polygons

log = logging.getLogger(__name__)

COASTAL_BC = [
    (-128.9, 48.25), (-123.0, 48.22), (-121.3, 48.98), (-121.3, 49.85), (-122.6, 50.6), (-124.4, 51.55),
    (-126.3, 52.55), (-127.7, 53.55), (-128.8, 54.55), (-129.9, 55.95), (-131.1, 56.15), (-133.7, 54.6),
    (-133.7, 52.0), (-131.0, 50.9), (-129.4, 50.15),
]


@dataclass
class Domain:
    name: str
    polygons: list[list[tuple[float, float]]]
    region_polys: list[tuple[str, list[tuple[float, float]]]] = field(default_factory=list)
    province: str | None = "BC"
    lat_max: float | None = None

    @property
    def bbox(self) -> list[float]:
        pts = np.vstack([np.asarray(p) for p in self.polygons])
        return [round(float(pts[:, 0].min()), 4), round(float(pts[:, 1].min()), 4),
                round(float(pts[:, 0].max()), 4), round(float(pts[:, 1].max()), 4)]

    @property
    def region_names(self) -> list[str]:
        if self.name == "south_coast":
            return list(sc.REGIONS)
        names = [n for n, _ in self.region_polys]
        return list(dict.fromkeys(names)) or [self.name]

    def contains(self, lon, lat) -> np.ndarray:
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        xy = np.column_stack([lon, lat])
        inside = np.zeros(len(lon), bool)
        for p in self.polygons:
            inside |= MplPath(np.asarray(p)).contains_points(xy)
        if self.lat_max is not None:
            inside &= lat <= self.lat_max
        return inside

    def assign(self, lon, lat) -> np.ndarray:
        """Region name per point (None outside the domain)."""
        if self.name == "south_coast":
            return sc.assign_region(lon, lat)
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        out = np.full(len(lon), None, dtype=object)
        inside = self.contains(lon, lat)
        if self.region_polys:
            xy = np.column_stack([lon, lat])
            for nm, ring in self.region_polys:
                hit = MplPath(np.asarray(ring)).contains_points(xy) & inside & (out == None)  # noqa: E711
                out[hit] = nm
        out[inside & (out == None)] = self.name  # noqa: E711
        return out

    def to_geojson(self) -> dict:
        feats = [{"type": "Feature", "properties": {"region": n},
                  "geometry": {"type": "Polygon", "coordinates": [list(map(list, r)) + [list(r[0])]]}}
                 for n, r in (self.region_polys or [(self.name, p) for p in self.polygons])]
        return {"type": "FeatureCollection", "name": self.name, "features": feats}


def _south_coast() -> Domain:
    polys = [sc.VANCOUVER_ISLAND, sc.LOWER_MAINLAND, sc.SEA_TO_SKY, sc.SUNSHINE_COAST]
    a, b, c, d = sc.GULF_BOX
    polys.append([(a, b), (c, b), (c, d), (a, d)])
    return Domain("south_coast", polys)


def _south_vi() -> Domain:
    return Domain("south_vi", [sc.VANCOUVER_ISLAND], region_polys=[], lat_max=49.80)


PRESETS = {"south_coast": _south_coast, "south_vi": _south_vi,
           "coastal_bc": lambda: Domain("coastal_bc", [COASTAL_BC])}


def from_geojson(path: Path) -> Domain:
    gj = json.loads(Path(path).read_text())
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    polys, regions = [], []
    for f in feats:
        g = f.get("geometry") or f
        rings = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
        for poly in rings:
            outer = [tuple(map(float, pt[:2])) for pt in poly[0]]
            polys.append(outer)
            nm = (f.get("properties") or {}).get("region")
            if nm:
                regions.append((str(nm), outer))
    if not polys:
        raise ValueError(f"{path}: no Polygon or MultiPolygon geometry found")
    name = gj.get("name") or Path(path).stem
    return Domain(name, polys, regions)


def get_domain(spec: str | None) -> Domain:
    spec = spec or "south_coast"
    if spec in PRESETS:
        return PRESETS[spec]()
    p = Path(spec)
    if p.suffix.lower() in (".geojson", ".json") and p.exists():
        return from_geojson(p)
    raise ValueError(f"Unknown domain '{spec}': use one of {sorted(PRESETS)} or a .geojson path")


def bbox_domain(lon0: float, lat0: float, lon1: float, lat1: float, name: str = "bbox") -> Domain:
    """Convenience for an interactive selector: a rectangular domain."""
    return Domain(name, [[(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1)]])


def add_regions(st: pd.DataFrame, dom: Domain, overrides_path: Path) -> pd.DataFrame:
    """Region per station, overrides applied, stations outside the domain dropped."""
    st = st.copy()
    st["region"] = dom.assign(st["lon"].values, st["lat"].values)
    st["region_source"] = np.where(st["region"].isna(), "outside", "auto")
    if overrides_path and Path(overrides_path).exists():
        ov = sc.load_overrides(Path(overrides_path)) if dom.name == "south_coast" else \
            pd.read_csv(overrides_path, dtype=str).fillna("")
        ov = ov[ov["climate_id"].str.strip() != ""]
        if len(ov):
            m = dict(zip(ov["climate_id"].str.strip(), ov["region"].str.strip()))
            hit = st["climate_id"].isin(m) & st["region"].notna()
            st.loc[hit, "region"] = st.loc[hit, "climate_id"].map(m)
            st.loc[hit, "region_source"] = "override"
    st = st[st["region"].notna() & (st["region"] != "exclude")].copy()
    st["is_vi"] = st["region"].isin(sc.VI_REGIONS) if dom.name == "south_coast" else False
    st["region"] = pd.Categorical(st["region"], categories=dom.region_names, ordered=True)
    return st.reset_index(drop=True)
