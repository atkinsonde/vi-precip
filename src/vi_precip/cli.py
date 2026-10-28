"""Command-line entry point: python run.py <command> [options]."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from . import pipeline
from .config import load_config


def _setup_logging(cfg, verbose: bool) -> None:
    logs = cfg.out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(logs / f"run_{stamp}.log")]
    handlers[0].setLevel(logging.DEBUG if verbose else logging.INFO)
    handlers[1].setLevel(logging.DEBUG)
    logging.basicConfig(level=logging.DEBUG, format=fmt, handlers=handlers, force=True)
    for noisy in ("urllib3", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run.py", description=__doc__)
    p.add_argument("--config", help="path to config.yaml (default: config/config.yaml)")
    p.add_argument("--end-date", help="override analysis.end_date (YYYY-MM-DD)")
    p.add_argument("--domain", help="spatial domain: south_coast (default), south_vi, coastal_bc, or a .geojson path")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="check the ECCC API: collections, field names, parameter support")

    s = sub.add_parser("smoke", help="end-to-end test on a single station (last two seasons)")
    s.add_argument("--station", help="climate ID")
    s.add_argument("--name", help="substring of station name, e.g. GONZALES")

    i = sub.add_parser("inventory", help="station inventory, completeness, tiers, composite candidates, maps")
    i.add_argument("--max-stations", type=int, help="limit the number of stations (testing)")
    i.add_argument("--stations", help="comma-separated climate IDs to restrict to (testing)")
    i.add_argument("--refresh-stations", action="store_true", help="re-download station metadata")

    u = sub.add_parser("update", help="refresh recent data, then rebuild inventory and metrics")
    u.add_argument("--refresh-stations", action="store_true")

    m = sub.add_parser("metrics", help="units, precipitation metrics, anomalies, homogeneity checks")
    m.add_argument("--n-boot", type=int, default=1000, help="bootstrap samples for return-period intervals")

    sub.add_parser("brief", help="rebuild the briefing page from the metrics outputs (no downloads)")

    ix = sub.add_parser("indices", help="download/parse climate indices (NOAA PSL, CPC RONI) and seasonal means")
    ix.add_argument("--refresh", action="store_true", help="re-download even if the cached copy is fresh")
    sub.add_parser("temperature", help="Tmax/Tmin/DTR period means, tiers and anomalies (no downloads)")
    sub.add_parser("longrecord", help="long-record stations from 1950 + AHCCD cross-check")
    h = sub.add_parser("hydro", help="river flow / lake level ranks (map domain) and natural-river series (ENSO domain)")
    h.add_argument("--refresh", action="store_true", help="re-download the provisional (real-time) data")
    sub.add_parser("enso", help="ENSO correspondence statistics (needs indices; uses whatever else exists)")
    st = sub.add_parser("site", help="build the static dashboard in outputs/site/")
    st.add_argument("--out", help="output folder (default outputs/site)")
    al = sub.add_parser("all", help="update + temperature + indices + longrecord + hydro + enso + site")
    al.add_argument("--refresh", action="store_true", help="also re-download indices and provisional hydrometric data")

    a = p.parse_args(argv)
    cfg = load_config(a.config)
    if a.end_date:
        target = dt.date.fromisoformat(a.end_date).year
        cfg = cfg.with_overrides(end_date=a.end_date, target_year=target)
    cfg = cfg.with_domain(a.domain)
    _setup_logging(cfg, a.verbose)

    if a.cmd == "probe":
        rep = pipeline.run_probe(cfg)
        return 0 if rep["ok"] else 1
    if a.cmd == "smoke":
        pipeline.run_smoke(cfg, station=a.station, name=a.name)
        return 0
    if a.cmd == "inventory":
        ids = [x.strip() for x in a.stations.split(",")] if a.stations else None
        paths = pipeline.run_inventory(cfg, max_stations=a.max_stations, station_ids=ids,
                                       refresh_stations=a.refresh_stations)
        print("\nOutputs:")
        for k, v in paths.items():
            print(f"  {k:22s} {v}")
        return 0
    if a.cmd == "metrics":
        paths = pipeline.run_metrics(cfg, n_boot=a.n_boot)
        print("\nOutputs:")
        for k, v in paths.items():
            print(f"  {k:22s} {v}")
        return 0
    if a.cmd == "brief":
        from .brief import run_brief
        print(f"Briefing page: {run_brief(cfg)}")
        return 0
    if a.cmd == "update":
        paths = pipeline.run_inventory(cfg, refresh_recent=True, refresh_stations=a.refresh_stations)
        paths |= pipeline.run_metrics(cfg)
        print("\nOutputs:")
        for k, v in paths.items():
            print(f"  {k:22s} {v}")
        return 0
    if a.cmd == "indices":
        from . import indices
        _show(indices.run(cfg, refresh=a.refresh))
        return 0
    if a.cmd == "temperature":
        from . import temperature
        _show(temperature.run(cfg))
        return 0
    if a.cmd == "longrecord":
        from . import longrecord
        _show(longrecord.run(cfg))
        return 0
    if a.cmd == "hydro":
        from . import hydro
        _show(hydro.run(cfg, refresh_recent=a.refresh))
        return 0
    if a.cmd == "enso":
        from . import enso_stats
        _show(enso_stats.run(cfg))
        return 0
    if a.cmd == "site":
        from . import site
        site.run(cfg, out=a.out)
        return 0
    if a.cmd == "all":
        from . import enso_stats, hydro, indices, longrecord, site, temperature
        paths = pipeline.run_inventory(cfg, refresh_recent=True)
        paths |= pipeline.run_metrics(cfg)
        paths |= temperature.run(cfg)
        for name, fn in (("indices", lambda: indices.run(cfg, refresh=a.refresh)), ("longrecord", lambda: longrecord.run(cfg)),
                         ("hydro", lambda: hydro.run(cfg, refresh_recent=a.refresh)), ("enso", lambda: enso_stats.run(cfg))):
            try:
                paths |= fn()
            except (Exception, SystemExit) as ex:  # one failed source should not stop the rest
                logging.getLogger("vi_precip").error("stage %s failed: %s", name, ex)
        site.run(cfg)
        return 0
    return 2


def _show(paths: dict) -> None:
    print("\nOutputs:")
    for k, v in paths.items():
        print(f"  {k:22s} {v}")


if __name__ == "__main__":
    raise SystemExit(main())
