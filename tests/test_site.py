import json
import re

from buildall import build_all

from vi_precip import site


def _load(p):
    txt = p.read_text()
    m = re.match(r"window\.VIP=window\.VIP\|\|\{\};VIP\[\"(\w+)\"\]=(.*);\n$", txt, re.S)
    assert m, p
    return json.loads(m.group(2))


def test_site_build(cfg, tmp_path):
    build_all(cfg)
    out = tmp_path / "site"
    idx = site.run(cfg, out=out, synthetic=True)
    assert idx.exists() and (out / "assets" / "plotly.min.js").stat().st_size > 1_000_000
    meta = _load(out / "data" / "meta.js")
    assert all(meta["have"].values()) and meta["synthetic"]
    assert meta["headline"]["precip"]["n"] > 0 and "enso" in meta["headline"]
    pr = _load(out / "data" / "precip.js")
    assert "total_oct|1" in pr["values"] and len(pr["values"]["total_oct|1"]["value"]) == len(pr["units"]["unit_id"])
    st = list((out / "data" / "stations").glob("*.js"))
    assert len(st) == len(pr["units"]["unit_id"])
    one = _load(st[0])
    assert "cumulative" in one and len(one["cumulative"]["p50"]) == one["cumulative"]["ndays"]
    en = _load(out / "data" / "enso.js")
    assert en["grid"]["rho"] and en["summary"]["primary"]["short"]
    assert (out / "data" / "csv" / "enso_grid.csv").exists()
    assert "NaN" not in (out / "data" / "enso.js").read_text()


def test_composite_station_shows_member_temperatures(cfg, tmp_path):
    build_all(cfg)
    out = tmp_path / "site"
    site.run(cfg, out=out, synthetic=True)
    comp = _load(out / "data" / "stations" / "C_1021830.js")  # joined for precipitation, not for temperature
    assert "temp" not in comp and [m["id"] for m in comp["temp_members"]] == ["1021830", "1021831"]
    plain = _load(out / "data" / "stations" / "1018610.js")
    assert any(v is not None for v in plain["temp"]["oct"]["tmax"])
