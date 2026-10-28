"""Configuration loading and derived settings (periods, paths)."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"


def _md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


@dataclass(frozen=True)
class Period:
    """A period defined by month-day bounds, applied to any year."""

    name: str
    start_md: tuple[int, int]
    end_md: tuple[int, int]

    def bounds(self, year: int) -> tuple[dt.date, dt.date]:
        return dt.date(year, *self.start_md), dt.date(year, *self.end_md)

    def n_days(self, year: int) -> int:
        a, b = self.bounds(year)
        return (b - a).days + 1


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = PROJECT_ROOT

    # ---- analysis -------------------------------------------------------
    @property
    def target_year(self) -> int:
        return int(self.raw["analysis"]["target_year"])

    @property
    def end_date(self) -> dt.date:
        return dt.date.fromisoformat(str(self.raw["analysis"]["end_date"]))

    @property
    def baseline(self) -> tuple[int, int]:
        a, b = self.raw["analysis"]["baseline"]
        return int(a), int(b)

    @property
    def baseline_years(self) -> list[int]:
        a, b = self.baseline
        return [y for y in range(a, b + 1) if y != self.target_year]

    @property
    def analysis_years(self) -> list[int]:
        return sorted(set(self.baseline_years) | {self.target_year})

    @property
    def download_start(self) -> dt.date:
        return dt.date.fromisoformat(str(self.raw["analysis"]["download_start"]))

    @property
    def periods(self) -> dict[str, Period]:
        a = self.raw["analysis"]
        end_md = (self.end_date.month, self.end_date.day)
        ws = _md(a["window_start"])
        es = _md(a["extended_start"])
        periods = {
            "window": Period("window", ws, end_md),
            "extended": Period("extended", es, end_md),
        }
        # Calendar sub-periods inside the window, clipped to the end date.
        if end_md >= (9, 30):
            periods["sep"] = Period("sep", (9, 1), (9, 30))
        if end_md >= (10, 1):
            periods["oct"] = Period("oct", (10, 1), end_md)
        return periods

    @property
    def wet_thresholds(self) -> list[float]:
        return [float(x) for x in self.raw["thresholds"]["wet_mm"]]

    # ---- sections -------------------------------------------------------
    @property
    def completeness(self) -> dict[str, Any]:
        return self.raw["completeness"]

    @property
    def qc(self) -> dict[str, Any]:
        return self.raw["qc"]

    @property
    def composites(self) -> dict[str, Any]:
        return self.raw["composites"]

    @property
    def domain(self) -> dict[str, Any]:
        return self.raw["domain"]

    @property
    def spatial(self):
        """The spatial Domain (preset or GeoJSON); see spatial.py."""
        if "_spatial" not in self.__dict__:
            from .spatial import get_domain

            spec = self.raw["domain"].get("spec", "south_coast")
            if spec and not spec in ("south_coast", "south_vi", "coastal_bc") and not Path(spec).is_absolute():
                spec = str(self.root / spec)
            self.__dict__["_spatial"] = get_domain(spec)
        return self.__dict__["_spatial"]

    @property
    def eccc(self) -> dict[str, Any]:
        return self.raw["eccc"]

    @property
    def basemap(self) -> dict[str, Any]:
        return self.raw.get("basemap", {"enabled": False})

    # ---- paths ----------------------------------------------------------
    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else self.root / p

    @property
    def data_dir(self) -> Path:
        return self.path("data")

    @property
    def out_dir(self) -> Path:
        return self.path("outputs")

    @property
    def tables_dir(self) -> Path:
        d = self.out_dir / "tables"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def figures_dir(self) -> Path:
        d = self.out_dir / "figures"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def diagnostics_dir(self) -> Path:
        d = self.out_dir / "diagnostics"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def with_overrides(self, **kw: Any) -> "Config":
        """Return a copy with selected analysis keys overridden (e.g. end_date)."""
        import copy

        raw = copy.deepcopy(self.raw)
        for k, v in kw.items():
            if v is None:
                continue
            raw["analysis"][k] = v
        return Config(raw=raw, root=self.root)

    def with_domain(self, spec: str | None) -> "Config":
        """Return a copy using another spatial domain; the station-query bbox follows it."""
        import copy

        if not spec:
            return self
        raw = copy.deepcopy(self.raw)
        raw["domain"]["spec"] = spec
        cfg = Config(raw=raw, root=self.root)
        if spec != "south_coast":
            cfg.raw["domain"]["bbox"] = cfg.spatial.bbox
        return cfg


def load_config(path: str | Path | None = None, root: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = Config(raw=raw, root=Path(root) if root else PROJECT_ROOT)
    spec = raw.get("domain", {}).get("spec")
    if spec and spec != "south_coast":
        cfg.raw["domain"]["bbox"] = cfg.spatial.bbox
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    end = cfg.end_date
    if end.year != cfg.target_year:
        raise ValueError(f"end_date {end} is not in target_year {cfg.target_year}")
    ws = _md(cfg.raw["analysis"]["window_start"])
    if (end.month, end.day) < ws:
        raise ValueError("end_date falls before window_start")
    if not cfg.wet_thresholds:
        raise ValueError("thresholds.wet_mm must list at least one value")
