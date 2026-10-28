"""Basemap geometry: Natural Earth land polygons and coastlines, clipped by bounding box.

Downloaded once into data/geo/. If download fails, maps are drawn without a basemap.
The extracted parts are cached as JSON so the website can reuse them later.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import numpy as np
import requests

from .config import Config

log = logging.getLogger(__name__)


def clip_ring(ring: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray:
    """Sutherland–Hodgman clip of a polygon ring to a rectangle (fine for filling)."""
    x0, y0, x1, y1 = bbox
    edges = [
        (lambda p: p[0] >= x0, lambda p, q: _ix(p, q, x=x0)),
        (lambda p: p[0] <= x1, lambda p, q: _ix(p, q, x=x1)),
        (lambda p: p[1] >= y0, lambda p, q: _ix(p, q, y=y0)),
        (lambda p: p[1] <= y1, lambda p, q: _ix(p, q, y=y1)),
    ]
    pts = [tuple(p) for p in ring]
    for inside, inter in edges:
        if not pts:
            break
        out = []
        prev = pts[-1]
        for cur in pts:
            if inside(cur):
                if not inside(prev):
                    out.append(inter(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(inter(prev, cur))
            prev = cur
        pts = out
    return np.asarray(pts)


def _ix(p, q, x=None, y=None):
    (px, py), (qx, qy) = p, q
    if x is not None:
        t = (x - px) / (qx - px) if qx != px else 0.0
        return (x, py + t * (qy - py))
    t = (y - py) / (qy - py) if qy != py else 0.0
    return (px + t * (qx - px), y)


def split_line(line: np.ndarray, bbox: tuple[float, float, float, float]) -> list[np.ndarray]:
    """Keep the pieces of a polyline that lie inside the bbox."""
    x0, y0, x1, y1 = bbox
    inside = (line[:, 0] >= x0) & (line[:, 0] <= x1) & (line[:, 1] >= y0) & (line[:, 1] <= y1)
    # Keep one neighbour on each side so segments reach the edge (plots clip to the axes).
    inside = inside | np.r_[inside[1:], False] | np.r_[False, inside[:-1]]
    pieces, cur = [], []
    for p, ok in zip(line, inside):
        if ok:
            cur.append(p)
        elif cur:
            if len(cur) > 1:
                pieces.append(np.asarray(cur))
            cur = []
    if len(cur) > 1:
        pieces.append(np.asarray(cur))
    return pieces


def _parts_in_bbox(
    shp_bytes: dict[str, bytes], bbox: tuple[float, float, float, float], kind: str
) -> list[list[list[float]]]:
    import shapefile  # pyshp

    r = shapefile.Reader(
        shp=io.BytesIO(shp_bytes["shp"]), shx=io.BytesIO(shp_bytes["shx"]), dbf=io.BytesIO(shp_bytes["dbf"])
    )
    x0, y0, x1, y1 = bbox
    out = []
    for shape in r.iterShapes():
        if shape.bbox[2] < x0 or shape.bbox[0] > x1 or shape.bbox[3] < y0 or shape.bbox[1] > y1:
            continue
        pts = np.asarray(shape.points)
        idx = list(shape.parts) + [len(pts)]
        for a, b in zip(idx[:-1], idx[1:]):
            p = pts[a:b]
            if p[:, 0].max() < x0 or p[:, 0].min() > x1 or p[:, 1].max() < y0 or p[:, 1].min() > y1:
                continue
            if kind == "polygon":
                c = clip_ring(p, bbox)
                if len(c) >= 3:
                    out.append(np.round(c, 4).tolist())
            else:
                out.extend(np.round(s, 4).tolist() for s in split_line(p, bbox))
    return out


def _fetch_zip(url: str, dest: Path) -> dict[str, bytes]:
    if not dest.exists():
        log.info("Downloading basemap %s", url)
        r = requests.get(url, timeout=120, headers={"User-Agent": "vi-precip/0.1"})
        r.raise_for_status()
        dest.write_bytes(r.content)
    z = zipfile.ZipFile(dest)
    files = {}
    for n in z.namelist():
        ext = n.rsplit(".", 1)[-1].lower()
        if ext in ("shp", "shx", "dbf"):
            files[ext] = z.read(n)
    return files


def load_basemap(cfg: Config, pad: float = 1.0, bbox: list[float] | None = None) -> dict[str, list] | None:
    """Return {'land': [rings], 'coast': [lines]} for a bbox (default: the domain's; padded), or None."""
    bm = cfg.basemap
    if not bm.get("enabled", False):
        return None
    d = cfg.data_dir / "geo"
    d.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = bbox or cfg.domain["bbox"]
    default = [round(v, 2) for v in (x0, y0, x1, y1)] == [-128.8, 48.2, -121.3, 51.05]
    tag = "domain" if default else "_".join(f"{v:g}" for v in (x0, y0, x1, y1)).replace("-", "m")
    cache = d / f"basemap_{tag}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    bbox = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    try:
        land = _parts_in_bbox(_fetch_zip(bm["land_url"], d / "ne_10m_land.zip"), bbox, "polygon")
        coast = _parts_in_bbox(_fetch_zip(bm["coast_url"], d / "ne_10m_coastline.zip"), bbox, "line")
    except Exception as ex:  # network or format problems should not stop the analysis
        log.warning("Basemap unavailable (%s); maps will have no coastline", ex)
        return None
    out = {"land": land, "coast": coast, "bbox": list(bbox), "source": "Natural Earth 10m"}
    cache.write_text(json.dumps(out))
    return out
