"""Simulated GeoMet hydrometric-stations + Water Office CSV services for offline tests.
Station numbers and coordinates are approximate fixtures, not authoritative metadata."""

from __future__ import annotations

import io
from urllib.parse import parse_qsl, urlparse

import numpy as np
import pandas as pd

# number, name, lon, lat, first year, rhbn, status
STATIONS = [
    ("08HA011", "COWICHAN RIVER NEAR DUNCAN", -123.79, 48.77, 1960, 0, "Active"),
    ("08HA003", "KOKSILAH RIVER AT COWICHAN STATION", -123.67, 48.73, 1955, 0, "Active"),
    ("08HA001", "CHEMAINUS RIVER NEAR WESTHOLME", -123.80, 48.88, 1950, 1, "Active"),
    ("08HA009", "COWICHAN LAKE NEAR LAKE COWICHAN", -124.06, 48.83, 1960, 0, "Active"),
    ("08HB011", "TSOLUM RIVER NEAR COURTENAY", -125.01, 49.72, 1965, 0, "Active"),
    ("08HB084", "PUNTLEDGE RIVER BELOW DIVERSION", -125.05, 49.68, 1990, 0, "Active"),
    ("08HA070", "HARRIS CREEK NEAR LAKE COWICHAN", -124.18, 48.70, 2004, 0, "Active"),
    ("08HD000", "NORTH ISLAND TEST CREEK", -125.25, 50.03, 1960, 1, "Active"),       # north of 49.80: ENSO only
    ("08FA000", "NORTH COAST TEST RIVER", -128.6, 54.0, 1960, 1, "Discontinued"),    # ENSO only
    ("08MH000", "INTERIOR TEST STATION", -120.5, 50.6, 1960, 1, "Active"),          # outside coastal BC
]
APPROVED_END = pd.Timestamp("2024-12-31")


def series(stn: str, first: int) -> pd.DataFrame:
    rng = np.random.default_rng(int("".join(c for c in stn if c.isdigit())))
    idx = pd.date_range(f"{first}-01-01", "2026-10-28", freq="D")
    doy = idx.dayofyear.to_numpy()
    base = 5 + 25 * (1 + np.cos(2 * np.pi * (doy - 15) / 365.25)) / 2
    yrs = np.arange(first, 2027)
    yf = pd.Series(rng.lognormal(0, 0.35, len(yrs)), index=yrs)
    q = base * yf.reindex(idx.year).to_numpy() * rng.lognormal(0, 0.15, len(idx))
    q[(idx.year == 2026) & (idx.month >= 9)] *= 0.35
    return pd.DataFrame({"date": idx, "flow": q.round(3), "level": (162 + 0.08 * np.log(q)).round(3)})


class Resp:
    def __init__(self, status, text="", js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(str(self.status_code))


class FakeHydroSession:
    def __init__(self):
        self.headers, self.calls, self._data = {}, [], {}

    def mount(self, *a):
        pass

    def data(self, stn):
        if stn not in self._data:
            s = next(s for s in STATIONS if s[0] == stn)
            self._data[stn] = series(stn, s[4])
        return self._data[stn]

    def get(self, url, params=None, timeout=None):
        p = urlparse(url)
        q = parse_qsl(p.query) + (list(params.items()) if isinstance(params, dict) else list(params or []))
        qd: dict = {}
        for k, v in q:
            qd.setdefault(k, []).append(str(v))
        self.calls.append(p.path)
        if p.path.endswith("/collections/hydrometric-stations/items"):
            x0, y0, x1, y1 = map(float, qd["bbox"][0].split(","))
            feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [s[2], s[3]]},
                      "properties": {"STATION_NUMBER": s[0], "STATION_NAME": s[1], "PROV_TERR_STATE_LOC": "BC",
                                     "STATUS_EN": s[6], "REAL_TIME": 1, "RHBN": s[5], "DRAINAGE_AREA_GROSS": 100.0}}
                     for s in STATIONS if x0 <= s[2] <= x1 and y0 <= s[3] <= y1]
            return Resp(200, js={"features": feats, "links": []})
        stn = qd["stations[]"][0]
        d = self.data(stn)
        if p.path.endswith("/daily_data/csv/inline"):
            a, b = pd.Timestamp(qd["start_date"][0]), min(pd.Timestamp(qd["end_date"][0]), APPROVED_END)
            sub = d[d["date"].between(a, b)]
            buf = io.StringIO()
            buf.write("﻿ ID,Date,Parameter/Paramètre,Value/Valeur,Symbol/Symbole\n")
            for r in sub.itertuples():
                if stn != "08HA009":
                    buf.write(f"{stn},{r.date.date()},discharge/débit,{r.flow},\n")
                buf.write(f"{stn},{r.date.date()},water level/niveau,{r.level},\n")
            return Resp(200, buf.getvalue())
        if p.path.endswith("/real_time_data/csv/inline"):
            a, b = pd.Timestamp(qd["start_date"][0][:10]), pd.Timestamp(qd["end_date"][0][:10])
            sub = d[d["date"].between(max(a, APPROVED_END + pd.Timedelta(days=1)), b)]
            buf = io.StringIO()
            buf.write("﻿ ID,Date,Parameter/Paramètre,Value/Valeur,Qualifier/Qualificatif,Symbol/Symbole,"
                      "Approval/Approbation,Grade/Classification,Qualifiers/Qualificatifs\n")
            for r in sub.itertuples():
                if stn != "08HA009":
                    buf.write(f"{stn},{r.date:%Y-%m-%dT08:00:00Z},6,{r.flow:.3f},,,Provisional/Provisoire,,\n")
                buf.write(f"{stn},{r.date:%Y-%m-%dT08:00:00Z},3,{r.level:.3f},,,Provisional/Provisoire,,\n")
            return Resp(200, buf.getvalue())
        return Resp(404, "not found")
