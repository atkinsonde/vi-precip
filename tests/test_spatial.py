import json

import numpy as np

from vi_precip import spatial


def test_presets():
    sc = spatial.get_domain("south_coast")
    assert sc.assign([-123.325], [48.413])[0] == "East VI"
    assert sc.assign([-119.4], [49.9])[0] is None
    sv = spatial.get_domain("south_vi")
    assert sv.contains([-123.72, -125.27, -123.18], [48.82, 49.95, 49.19]).tolist() == [True, False, False]
    cb = spatial.get_domain("coastal_bc")
    assert cb.contains([-130.3, -123.2, -119.5], [54.3, 49.2, 49.9]).tolist() == [True, True, False]
    x0, y0, x1, y1 = cb.bbox
    assert x0 < -133 and x1 > -121.4 and y1 > 56


def test_geojson_roundtrip(tmp_path):
    gj = {"type": "FeatureCollection", "name": "test_box", "features": [
        {"type": "Feature", "properties": {"region": "A"},
         "geometry": {"type": "Polygon", "coordinates": [[[-124, 48], [-123, 48], [-123, 49], [-124, 49], [-124, 48]]]}},
        {"type": "Feature", "properties": {},
         "geometry": {"type": "MultiPolygon", "coordinates": [[[[-126, 50], [-125, 50], [-125, 51], [-126, 50]]]]}}]}
    p = tmp_path / "d.geojson"
    p.write_text(json.dumps(gj))
    d = spatial.get_domain(str(p))
    assert d.name == "test_box" and d.bbox == [-126.0, 48.0, -123.0, 51.0]
    r = d.assign([-123.5, -125.3, -100.0], [48.5, 50.2, 50.0])
    assert r.tolist() == ["A", "test_box", None]
    b = spatial.bbox_domain(-125, 48, -123, 49)
    assert b.contains([-124], [48.5])[0]


def test_config_domain_switch(cfg):
    c2 = cfg.with_domain("coastal_bc")
    assert c2.raw["domain"]["bbox"] == spatial.get_domain("coastal_bc").bbox
    assert cfg.spatial.name == "south_coast"
