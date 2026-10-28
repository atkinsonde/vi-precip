"""Run every stage against the fake services (shared by the ENSO and site tests)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from fakeapi import FakeSession
from fakehydro import FakeHydroSession

from vi_precip import enso_stats, hydro, indices, longrecord, pipeline, temperature
from vi_precip.eccc import ECCCClient

ROOT = Path(__file__).resolve().parents[1]


def write_fake_indices(cfg) -> None:
    d = cfg.data_dir / "raw" / "indices"
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    years = range(1950, 2027)
    base = {y: rng.normal(0, 1) for y in years}
    base[2026] = 1.6
    for name in indices.DEFAULT_SOURCES:
        if name == "roni":
            rows = "".join(f"<tr><td>{y}</td>" + "".join(f"<td>{base[y] * 0.9 + rng.normal(0, .1):.2f}</td>"
                                                         for m in range(12 if y < 2026 else 8)) + "</tr>" for y in years)
            (d / "roni.txt").write_text(f"<table><tr><th>Year</th></tr>{rows}</table>")
            continue
        sign = -1 if name == "soi" else 1
        noise = 0.3 if name not in ("pdo", "pna") else 1.0
        lines = ["1950 2026"]
        for y in years:
            vals = [sign * base[y] * (0.3 + 0.7 * m / 12) + rng.normal(0, noise) for m in range(12)]
            if y == 2026:
                vals[9:] = [-99.99] * 3
            lines.append(f"{y} " + " ".join(f"{v:7.2f}" for v in vals))
        lines += ["-99.99", f"  fake {name}"]
        (d / f"{name}.txt").write_text("\n".join(lines) + "\n")


def build_all(cfg, hydro_cfg: dict | None = None) -> dict:
    shutil.copy(ROOT / "config" / "regulation.csv", cfg.root / "config")
    cfg.raw["hydro"] = hydro_cfg or {"pause_s": 0, "reviewed_natural": ["08HA003"], "min_years_enso": 30}
    cfg.raw["enso"] = {"n_perm": 499, "n_boot": 200, "min_units_short": 2, "min_units_long": 1, "min_units_river": 2,
                      "min_years_long": 12}
    s = FakeSession()
    paths = pipeline.run_inventory(cfg, client=ECCCClient(cfg, session=s))
    paths |= pipeline.run_metrics(cfg, n_boot=30)
    paths |= temperature.run(cfg)
    paths |= longrecord.run(cfg, client=ECCCClient(cfg, session=s))
    paths |= hydro.run(cfg, session=FakeHydroSession())
    write_fake_indices(cfg)
    paths |= indices.run(cfg)
    paths |= enso_stats.run(cfg)
    return paths
