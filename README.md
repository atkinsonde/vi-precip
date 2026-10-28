# vi-precip

Autumn 2026 dryness on Vancouver Island and the South Coast, compared with
2000–2025 and examined against ENSO. The methodology is in [DESIGN.md](DESIGN.md).

**Status: milestone 2.**
- **Milestone 1** covers the ECCC client and cache, an API probe, a single-station
  smoke test, and the station inventory.
- **Milestone 2** adds analysis units (stations plus accepted composites),
  precipitation metrics, 2026 anomalies, neighbour-based homogeneity checks, and a
  quick-look anomaly map.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                          # offline tests against a fake API; about 20 s
```

This needs Python 3.10 or newer. All commands below run from the project folder.

## Running, in order

| Step | Command | What it does | Time |
|---|---|---|---|
| 1 | `python run.py probe` | Checks the API: collections, field names, and which query parameters it accepts. Writes `outputs/diagnostics/api_probe.json`. | seconds |
| 2 | `python run.py smoke --name GONZALES` | Runs one station end to end for the last two seasons: download, QC, completeness, and a plot. Uses `--station <climate id>` or `--name <substring>`; with neither, it picks a long-running East VI station. | < 1 min |
| 3 | `python run.py inventory --max-stations 5` | A quick partial inventory, to check the whole stage before the full run. | 1–2 min |
| 4 | `python run.py inventory` | The full inventory. The first run downloads everything; later runs read the cache. | several min first time |
| 5 | `python run.py metrics` | Builds units, metrics, anomalies and homogeneity tests from the cached data, then writes the briefing page. It makes no downloads. | a few min |
| – | `python run.py brief` | Rebuilds only the briefing page `outputs/brief/brief_2026.html` from the metrics tables. | seconds |

Smaller options:

- `--end-date 2026-10-31` overrides the analysis end date for one run without
  editing the config.
- `python run.py update` refreshes recent data, then reruns the inventory and
  metrics. A cached year is re-downloaded only until 60 days after that year ends,
  because ECCC revises recent values. Late-reporting staffed stations move from
  provisional to final as their data arrive.
- `-v` gives verbose logging. A full log of every run is always written to
  `outputs/logs/`.

## What to send back after step 5

- `outputs/tables/anomalies_2026.csv`: one row per unit, with the headline numbers.
- `outputs/tables/units.csv`
- `outputs/diagnostics/homogeneity_breaks.csv`
- `outputs/diagnostics/accumulation_patterns.csv`
- `outputs/figures/quicklook_2026_window_total.png`
- The console summary printed at the end of the run (paste it or save it).

The remaining tables stay on your machine:
- `metrics_unit_year`: every metric by unit, year and threshold.
- `bins_unit_year`
- `dry_spells`: one row per spell.
- `anomalies_2026_long`: every metric with rank, percentile, % of median, SPI and
  return periods.

## Files you edit

- **`config/config.yaml`**: end date, baseline, thresholds, completeness rules,
  flag handling, and composite search distances.
- **`config/region_overrides.csv`**: reassigns a station's region, or removes it
  from the analysis with `exclude`.
- **`config/composites.csv`**: the composite joins you accept. Copy rows from
  `outputs/tables/composites_proposed.csv` and edit `use_from`/`use_to` if needed.
  Nothing is joined unless it is listed here.

## Layout

```
config/            settings and the two hand-edited CSVs
src/vi_precip/
  eccc.py          the only module that talks to the ECCC API (paging, fallbacks, cache)
  domain.py        region polygons and assignment
  qc.py            flags, accumulation blocks, period validity, completeness, tiers
  composites.py    candidate detection, proposals, series splicing
  units.py         analysis units (stations + accepted composites), tiers, provisional checks
  metrics.py       day states, totals, wet days, bins, decomposition, spells, events, onset
  anomalies.py     ranks, plotting positions, % of median, SPI and return periods
  homogeneity.py   neighbour difference series, Pettitt and switch-year tests
  geo.py           Natural Earth basemap (downloaded once, clipped, cached)
  pipeline.py      stages: probe, smoke, inventory, metrics
  plots/           figures
data/raw/          API cache: one parquet per station-year, plus manifests (never edited)
data/interim/      standardized daily series and the inventory
data/processed/    annotated daily series per analysis unit
outputs/           tables, figures, diagnostics, logs
tests/             offline tests; fakeapi.py imitates the ECCC API
```

## Data sources

Environment and Climate Change Canada, MSC GeoMet OGC API (`api.weather.gc.ca`),
used under the Open Government Licence – Canada. Natural Earth 10 m land and
coastline data (public domain).
