import io
import zipfile

import numpy as np
import shapefile

from vi_precip import geo


def _zip_shapes(path, kind, parts):
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON if kind == "poly" else shapefile.POLYLINE)
    w.field("id", "N")
    for p in parts:
        (w.poly if kind == "poly" else w.line)([p])
        w.record(1)
    w.close()
    with zipfile.ZipFile(path, "w") as z:
        for ext, b in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"x.{ext}", b.getvalue())


def test_basemap_clip(cfg):
    d = cfg.data_dir / "geo"
    d.mkdir(parents=True)
    big = [[-140, 40], [-140, 60], [-110, 60], [-110, 40], [-140, 40]]
    far = [[0, 0], [0, 1], [1, 1], [0, 0]]
    _zip_shapes(d / "ne_10m_land.zip", "poly", [big, far])
    _zip_shapes(d / "ne_10m_coastline.zip", "line", [[[-140, 49], [-125, 49], [-100, 49]]])
    cfg.raw["basemap"]["enabled"] = True
    bm = geo.load_basemap(cfg, pad=0.5)
    assert len(bm["land"]) == 1
    ring = np.asarray(bm["land"][0])
    x0, y0, x1, y1 = bm["bbox"]
    assert ring[:, 0].min() >= x0 - 1e-9 and ring[:, 0].max() <= x1 + 1e-9
    assert len(bm["coast"]) == 1 and len(bm["coast"][0]) == 3
    assert (d / "basemap_domain.json").exists()
