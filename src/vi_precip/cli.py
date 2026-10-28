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

    u = sub.add_parser("update", help="refresh recent data then rebuild (milestone 1: inventory)")
    u.add_argument("--refresh-stations", action="store_true")

    for name in ("metrics", "enso", "temperature", "site"):
        sub.add_parser(name, help="(later milestone)")

    a = p.parse_args(argv)
    cfg = load_config(a.config)
    if a.end_date:
        target = dt.date.fromisoformat(a.end_date).year
        cfg = cfg.with_overrides(end_date=a.end_date, target_year=target)
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
    if a.cmd == "update":
        paths = pipeline.run_inventory(cfg, refresh_recent=True, refresh_stations=a.refresh_stations)
        print("\nOutputs:")
        for k, v in paths.items():
            print(f"  {k:22s} {v}")
        return 0
    print(f"'{a.cmd}' arrives in a later milestone (see DESIGN.md §13).")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
