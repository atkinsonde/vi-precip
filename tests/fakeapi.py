"""A small in-memory imitation of the ECCC OGC API for offline tests.

It mimics what matters to the client: GeoJSON features with upper-case property names,
paging through rel=next links and numberMatched, the datetime/bbox/property filters, and
(optionally) rejecting a parameter with HTTP 400 to exercise the fallback path.
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import parse_qs, urlencode, urlparse

import numpy as np

BASE = "https://api.example.test"

# id, name, lon, lat, elev, daily first, daily last
STATIONS = [
    ("1018610", "VICTORIA GONZALES", -123.325, 48.413, 69.5, "1990-01-01", "2026-12-31"),
    ("1021830", "COMOX A", -124.887, 49.717, 25.6, "1990-01-01", "2013-06-30"),
    ("1021831", "COMOX AUTO", -124.889, 49.718, 26.0, "2013-07-01", "2026-12-31"),
    ("1038205", "TOFINO A", -125.776, 49.082, 24.4, "1990-01-01", "2026-12-31"),
    ("1026270", "PORT HARDY A", -127.367, 50.683, 21.6, "1990-01-01", "2010-12-31"),
    ("1016995", "SATURNA ISLAND", -123.17, 48.78, 50.0, "2000-01-01", "2026-12-31"),
    ("1108447", "VANCOUVER INTL A", -123.181, 49.195, 4.3, "1990-01-01", "2026-12-31"),
    ("1100031", "ABBOTSFORD A", -122.36, 49.025, 59.1, "1995-01-01", "2026-12-31"),
    ("1100032", "ABBOTSFORD AUT", -122.362, 49.027, 60.0, "2005-01-01", "2026-12-31"),
    ("1123970", "KELOWNA A", -119.38, 49.96, 429.0, "1990-01-01", "2026-12-31"),
]
DATA_END = dt.date(2026, 10, 27)  # last day "published"


def _daily_records(cid: str, first: dt.date, last: dt.date) -> list[dict]:
    rng = np.random.default_rng(int(cid))
    recs = []
    d = first
    while d <= min(last, DATA_END):
        doy = d.timetuple().tm_yday
        p_wet = 0.25 + 0.35 * (1 + np.cos(2 * np.pi * (doy - 15) / 365.25)) / 2
        if d.year == 2026 and d.month in (9, 10):
            p_wet *= 0.3  # a dry autumn
        wet = rng.random() < p_wet
        val = round(float(rng.gamma(0.8, 9.0)), 1) if wet else 0.0
        flag = None
        if wet and val < 0.2:
            val, flag = 0.0, "T"
        # Tofino: poor coverage in 2003-2011 (many missing days in autumn)
        if cid == "1038205" and 2003 <= d.year <= 2011 and d.month in (9, 10) and rng.random() < 0.3:
            val, flag = None, "M"
        tmax = round(float(14 + 8 * np.sin(2 * np.pi * (doy - 110) / 365.25) + rng.normal(0, 2)), 1)
        tmin = round(tmax - 7 + float(rng.normal(0, 1.5)), 1)
        recs.append(
            {
                "CLIMATE_IDENTIFIER": cid,
                "LOCAL_DATE": f"{d.isoformat()} 00:00:00",
                "TOTAL_PRECIPITATION": val,
                "TOTAL_PRECIPITATION_FLAG": flag,
                "MAX_TEMPERATURE": tmax,
                "MAX_TEMPERATURE_FLAG": None,
                "MIN_TEMPERATURE": tmin,
                "MIN_TEMPERATURE_FLAG": None,
                "MEAN_TEMPERATURE": round((tmax + tmin) / 2, 1),
                "STATION_NAME": "x",
                "PROVINCE_CODE": "BC",
            }
        )
        d += dt.timedelta(days=1)
    if cid == "1016995":  # an accumulation block inside the 2026 window: Sep 10-12 missing, Sep 13 accumulated
        for r in recs:
            if r["LOCAL_DATE"][:10] in ("2026-09-10", "2026-09-11", "2026-09-12"):
                r["TOTAL_PRECIPITATION"], r["TOTAL_PRECIPITATION_FLAG"] = None, "M"
            if r["LOCAL_DATE"][:10] == "2026-09-13":
                r["TOTAL_PRECIPITATION"], r["TOTAL_PRECIPITATION_FLAG"] = 25.0, "A"
    return recs


class FakeResponse:
    def __init__(self, status: int, payload: dict | None, url: str):
        self.status_code = status
        self._payload = payload
        self.url = url
        self.text = json.dumps(payload)[:500] if payload is not None else ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} for {self.url}", response=self)


class FakeSession:
    def __init__(self, reject: tuple[str, ...] = (), max_limit: int = 500):
        self.reject = set(reject)
        self.max_limit = max_limit
        self.calls: list[tuple[str, dict]] = []
        self.headers: dict = {}
        self._daily = {}

    def mount(self, *a, **k):
        pass

    def _daily_for(self, cid):
        if cid not in self._daily:
            s = next(s for s in STATIONS if s[0] == cid)
            self._daily[cid] = _daily_records(cid, dt.date.fromisoformat(s[5]), dt.date.fromisoformat(s[6]))
        return self._daily[cid]

    def get(self, url, params=None, timeout=None):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        q.update({k: str(v) for k, v in (params or {}).items()})
        path = u.path
        self.calls.append((path, dict(q)))
        full = f"{BASE}{path}?{urlencode(q)}"
        for k in self.reject:
            if k in q:
                return FakeResponse(400, {"description": f"Invalid parameter {k}"}, full)
        if path == "/collections":
            return FakeResponse(200, {"collections": [{"id": c} for c in ("climate-stations", "climate-daily", "ahccd-monthly")]}, full)
        if path == "/collections/climate-stations/items":
            feats = []
            for s in STATIONS:
                if "bbox" in q:
                    x0, y0, x1, y1 = map(float, q["bbox"].split(","))
                    if not (x0 <= s[2] <= x1 and y0 <= s[3] <= y1):
                        continue
                feats.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [s[2], s[3]]},
                        "properties": {
                            "CLIMATE_IDENTIFIER": s[0], "STATION_NAME": s[1], "ELEVATION": str(s[4]),
                            "PROV_STATE_TERR_CODE": "BC", "DLY_FIRST_DATE": f"{s[5]} 00:00:00",
                            "DLY_LAST_DATE": f"{s[6]} 00:00:00", "STN_ID": "1",
                        },
                    }
                )
            return self._page(feats, q, path)
        if path == "/collections/climate-daily/items":
            cid = q.get("CLIMATE_IDENTIFIER")
            recs = self._daily_for(cid) if cid else self._daily_for(STATIONS[0][0])[-50:]
            if "datetime" in q:
                a, b = q["datetime"].split("/")
                recs = [r for r in recs if a <= r["LOCAL_DATE"][:10] <= b]
            if q.get("sortby", "").startswith("-"):
                recs = list(reversed(recs))
            feats = [{"type": "Feature", "geometry": None, "properties": dict(r)} for r in recs]
            return self._page(feats, q, path)
        return FakeResponse(404, {"description": "not found"}, full)

    def _page(self, feats, q, path):
        limit = min(int(q.get("limit", 10)), self.max_limit)
        off = int(q.get("offset", 0))
        page = feats[off : off + limit]
        links = []
        if off + limit < len(feats):
            nq = dict(q, offset=off + limit)
            links.append({"rel": "next", "href": f"{BASE}{path}?{urlencode(nq)}"})
        return FakeResponse(
            200,
            {"type": "FeatureCollection", "features": page, "numberMatched": len(feats),
             "numberReturned": len(page), "links": links},
            f"{BASE}{path}",
        )
