# Design: Vancouver Island / South Coast autumn 2026 dryness and ENSO

This document records the methodology agreed before implementation. Code should
follow it; when the code departs from it, this file is updated in the same commit.

**Question.** How unusual has the dry September–October 2026 period been on
Vancouver Island and the adjacent South Coast, and to what extent does it resemble
precipitation behaviour associated with ENSO?

**Framing of the ENSO answer.** "Indicative of, but not fully explained by." ENSO's
precipitation teleconnection to coastal BC is mainly a winter (DJF) phenomenon. In
early autumn it is weak and modulated by the North Pacific state (PNA, PDO). A null
or weak result is a legitimate outcome. Correlation and association are never
presented as attribution of one season.

---

## 1. Domain and regions

The domain is Vancouver Island, the Gulf Islands, the Lower Mainland east to Hope,
the Sunshine Coast, and the Sea-to-Sky corridor north to Whistler.

| Region | Definition (approximate; see `domain.py`) |
|---|---|
| West VI | Vancouver Island west of the island spine (includes Alberni Valley, Port Renfrew) |
| East VI | Vancouver Island east of the spine, south of 50.25°N (Victoria to Campbell River, incl. Quadra) |
| North VI | Vancouver Island north of 50.25°N (incl. adjacent small islands) |
| Gulf Islands | Southern and northern Gulf Islands (incl. Texada, Lasqueti, Denman, Hornby) |
| Lower Mainland | Metro Vancouver and Fraser Valley to Hope (−121.35°) |
| Sunshine Coast | Gibsons to Lund |
| Sea-to-Sky | Squamish to Whistler (≤ 50.2°N) |

Regions are assigned automatically by point-in-polygon. Polygons are deliberately
coarse, so borderline stations can be reassigned in `config/region_overrides.csv`.
Latitude, longitude and elevation are also used as continuous covariates. "North vs
south" is treated continuously rather than as a region.

## 2. Data sources (priority order)

1. **ECCC MSC GeoMet OGC API** (`api.weather.gc.ca`): `climate-stations` (metadata)
   and `climate-daily` (daily precipitation, Tmax, Tmin, flags). This is the primary
   source. The collection holds a *subset* of stations, prioritizing currently
   operational stations, long records, and co-located stations that extend records.
   Stations absent from the API are out of scope for now.
2. **ECCC AHCCD** (adjusted and homogenized; Mekis & Vincent 2011; Vincent et al.
   2012). Used for the long-record (from 1950) ENSO analysis. It ends before the
   present, so 2026 comes from raw daily data, after checking that AHCCD and raw
   data agree over the overlap.
3. **Climate indices**: NOAA PSL and CPC (ONI/Niño 3.4, relative Niño 3.4 (RONI),
   Niño 3, Niño 4, Niño 1+2, MEI.v2, SOI, PNA) and NOAA NCEI (PDO).
4. **PCIC BC Station Data** (phase 2): BC Wildfire Service, BC Hydro and MoTI
   networks. These form a separate, flagged layer. They enter the regional index only
   if they pass the same completeness tiers.

All downloads are cached locally. The analysis layer never calls an API directly.

## 3. Time windows

- **Target year** 2026. **Baseline** 2000–2025. The target is *excluded* from its
  own baseline for every fitted or reference statistic. Rank is reported among all
  27 years.
- **End date** is a configuration parameter (`analysis.end_date`, initially
  2026-10-26). Its month-day is applied to every year, so historical years are
  compared over the same calendar period.
- **Periods** (same month-day bounds in every year):
  - `sep`: Sep 1–Sep 30
  - `oct`: Oct 1–end date
  - `window`: Sep 1–end date (primary)
  - `extended`: Aug 1–end date. Continuous series for spells, events and onset, so
    monthly boundaries do not truncate them.
- **Long record**: 1950 onward at a few long-record stations (ideally AHCCD),
  spanning east, west, north and mainland.

## 4. Daily data handling

### 4.1 Climate day

ECCC daily values are used as published, without re-deriving days from hourly data.
For 24-hour stations since 1961 the precipitation day ends at 0600 UTC (22:00 PST).
Ordinary climate stations end the day at the next morning's observation, and
once-daily sites use the calendar day. The resulting uncertainty of about ±1 day
matters for 1-day bins and spell edges, and negligibly for totals. Event metrics are
robust to it. When stations are joined into a composite, a lag-±1 cross-correlation
between members is used to flag a change in the day definition.

### 4.2 Flags

ECCC flags are mapped to statuses in `config.yaml` (`qc.flag_status`). Each status
has a treatment:

| Flag | Status | Treatment |
|---|---|---|
| M / no value | missing | invalid |
| T | trace | value 0 mm, valid, counted separately; never a wet day |
| A, F | accumulated | multi-day block, see 4.3 |
| E | estimated | valid, counted |
| C, L | uncertain | valid, counted |
| ^ | incomplete | valid, counted. The 2000–2026 inventory contains no `^` flags. |
| other | unknown | valid, counted, warned |

### 4.3 Accumulated values

An accumulation block is the accumulated-flag day plus the immediately preceding
no-value days. Equivalently, it is a run of no-value accumulated-flag days plus the
first following day that has a value.

**Inventory finding.** At volunteer (co-op) stations, C days outnumber A days by about
1.6 to 1. This is the weekend pattern: Saturday and Sunday are flagged C, and Monday
carries an accumulated total flagged A. So C days immediately before an A/F day join
that block (`qc.absorb_uncertain_into_accumulation`). The metrics stage writes
`accumulation_patterns.csv`, which confirms per station what C days hold (no value,
zero, or positive), how often they precede an A day, and the weekday distribution of
A days.

- **Totals**: the block counts as valid only if the whole block lies inside the
  period. A block straddling a period boundary makes its in-period days missing.
- **Daily distribution and spell metrics**: every block day is invalid, and a spell
  ends at an invalid day.
- **Event metrics**: a resolved block is one event (or part of one).

### 4.4 Completeness

| Use | Rule (configurable) |
|---|---|
| Period total valid | ≤ 2 missing days in the period |
| Daily-distribution metrics valid | ≤ 5 missing days in the window. Counts are converted to fractions of valid days. |
| Tier 1 | Window total valid in ≥ 22 of 26 baseline years **and** in the target year |
| Tier 2 | ≥ 12 baseline years **and** target year valid. The baseline period is reported per station. |
| Provisional | The baseline qualifies for Tier 1 or 2, but the target window has 3–10 missing days, usually because staffed stations report late. Mapped with a distinct symbol, and the total is a lower bound. Missing days are checked against neighbours. Excluded from regional indices until the data arrive. |
| Tier 3 | Everything else (inventory and context only) |

The island-wide and regional indices use Tier 1 stations only, so the station set is
the same every year.

### 4.5 Composite stations

Candidate joins are **proposed automatically and accepted manually**. Nothing is
combined silently.

- **Spatial**: distance ≤ 1 km and |Δz| ≤ 20 m qualifies as a "join". Anything up
  to 5 km and 50 m is listed for "review".
- **Temporal**: complementary records, meaning the overlap is ≤ 3 years and the gap
  ≤ 2 years. Longer concurrent overlap is labelled "parallel" and needs a
  user-chosen switch date. A record lying entirely inside another's span is labelled
  "contained" (a side record, not a continuation) and is never joined automatically.
- **Overlap gate**: an automatic join is demoted to "review" if the daily correlation
  over the overlap is below 0.8, or the overlap total ratio differs from 1 by more
  than 10%.
- **Evidence over the overlap**: station-name similarity, correlation of daily
  precipitation at lags −1, 0 and +1, ratio of overlap totals, and wet-day frequency
  at 0.2 mm and 1.0 mm for each member.
- **Default switch**: the newer member takes over from its first valid day, which
  keeps the composite consistent with the target-year instrument.
- **Files**: proposals go to `outputs/tables/composites_proposed.csv`. Accepted joins
  live in `config/composites.csv`. Composites carry their own ID (`C_<first member>`)
  and a distinct map symbol.
- **Zero-overlap joins** (the airport ID changes of 2012–13) cannot be checked by
  overlap statistics. They are tested instead against neighbours at the switch year
  (§5).
- **Accepted after the inventory** (2000–2026 analysis):
  - Victoria Intl A, Vancouver Intl A (HWOS excluded), Abbotsford A and Port Hardy A.
    These are ID changes with no overlap, pending the break test.
  - Victoria Gonzales Hts → Gonzales CS: corr 0.997 and ratio 1.000 over 1,723 days.
    This is also the backbone of the long record.
- **Rejected or deferred**:
  - Hope: the old Hope/Hope AUT and the new Hope A/Airport records disagree by a factor
    of two over their overlap (ratio 0.49).
  - Saanichton CDA → CFIA: ratio 0.91 and best lag −1. Splicing would bias 2026 dry by
    about 9%.
  - Estevan Point manual → CS: ratio 0.90, so the same objection applies. CS is used
    alone for 2000–2026, and the long record is deferred to AHCCD.
  - Squamish, Callaghan and Cypress Bowl: contained side records.
  - Nanaimo A: no 2026 data.

## 5. Definitions

| Concept | Primary | Sensitivity |
|---|---|---|
| Wet day | ≥ 1.0 mm | ≥ 0.2 mm |
| Dry day | < 1.0 mm | < 0.2 mm |
| Dry spell | run of consecutive dry days; a missing day ends the spell and flags it | as above at 0.2 mm |
| Event | run of consecutive wet days, with total, duration, peak and start date | as above at 0.2 mm |
| Trace | 0 mm in totals, flagged, never wet | — |

**Day states** for threshold t:
- **wet**: daily-valid and ≥ t.
- **dry**: daily-valid and < t, or inside an accumulation block whose total is < t
  (every day of such a block must have been below t).
- **ambiguous**: inside a block whose total is ≥ t.
- **missing**: everything else.

Wet-day frequency counts each ambiguous block of L days as its expected number of wet
days, E = L·p / (1 − (1 − p)^L), where p is the unit's wet-day probability. Simply
dropping such blocks would bias the frequency low, because only rainy blocks are
ambiguous.

**Threshold decision (data-driven).** For every final Tier 1/2 unit, build an annual
series of wet-day frequency minus that of up to four neighbours (≤ 75 km).
- A Pettitt test finds a change point anywhere in the series, with no metadata needed.
- For composites, a Welch t-test compares the series before and after each switch
  year.
- If breaks are clearly more common at 0.2 mm than at 1.0 mm, 1.0 mm stays primary.
  Otherwise 0.2 mm becomes primary.

Both thresholds are always computed, and the website exposes a switch.

## 6. Metrics (per station × year)

The configured precipitation threshold applies throughout.

- **Totals** for `sep`, `oct` and `window`, as mm and as mm/day.
- **Occurrence and intensity**: wet days N, dry days, mean wet-day amount I (SDII),
  maximum daily amount.
- **Decomposition** of the window total P into wet-day count and intensity:
  log(P/P̄) = log(N/N̄) + log(I/Ī), where bars are baseline medians. This separates:
  - **A**, fewer wet days, from the first term;
  - **B**, smaller events, from the second term;
  - **C**, an absence of large events, from R95pTOT (the fraction of the total from
    days above the station's baseline wet-day 95th percentile) and the heavy-day
    count.
- **Bins**:
  - Fixed physical bins: dry, 0.2–1, 1–5, 5–10, 10–25, 25–50 and ≥ 50 mm.
  - Station-relative bins from baseline wet-day percentiles: < P50, P50–75, P75–90,
    P90–95 and > P95.
  - Both as fractions of valid days, plus each bin's contribution to the total.
- **Dry spells**, computed on the `extended` series:
  - Every spell keeps its days inside the window, its full length, and a censoring
    flag.
  - Per year: count, mean, median and maximum length; counts of spells ≥ 3, ≥ 5,
    ≥ 7 and ≥ 10 days; the fraction of window days that were dry.
- **Events**: count, mean and maximum event total, and maximum event duration.
- **Wet-season onset**: the first date after Sep 1 on which cumulative precipitation
  reaches the station's baseline median September total. If it is not reached by
  the end date, the value is censored and reported as "> end date".
- **Temperature** (module 2): Tmax, Tmin and DTR (diurnal temperature range)
  window means and anomalies, with the same tiers and completeness rules.

## 7. Anomalies

Reported for each station and metric:

- rank among 27 years (1 = driest);
- percent of the baseline median;
- empirical non-exceedance probability (plotting position) against the 26 baseline
  years;
- an SPI-style standardized anomaly for precipitation totals: fit a gamma
  distribution to the baseline, with a mixed zero probability, and transform to a
  standard normal value.

Return periods are empirical only up to about 1-in-27 years. Beyond that the fitted
gamma value is shown with a confidence interval (bootstrap over baseline years) and
an explicit caution, alongside long-record context.

## 8. Spatial coherence

- **Station-based maps only**; no interpolation.
- **Unit of inference: the year, not the station.**
  - The island-wide and regional index for each year is the median SPI over Tier 1
    stations.
  - 2026's index is ranked among the 27 yearly indices.
  - Subsets: East VI, West VI, North VI, Gulf Islands and the mainland regions.
- **Precipitation EOFs.** An EOF/PCA of station SPI anomalies (Tier 1 stations,
  2000–2025 baseline) identifies the spatial modes, such as island-wide versus an
  east–west dipole. 2026 is projected onto the leading modes. Correlating PC time
  series with ENSO replaces many station-level tests with two or three. Sampling
  error of the modes follows North et al. (1982).
- A coherence matrix (stations × metrics, coloured by 2026 percentile) is the
  primary summary figure.

## 9. ENSO and North Pacific analysis

**Indices**:
- Niño 3.4 (ONI, ERSSTv5; primary; from 1950);
- relative Niño 3.4 (RONI);
- MEI.v2 (from 1979; bimonthly seasons; now computed with JRA-3Q);
- SOI;
- ENSO "flavour", as Niño 3 − Niño 4 or Niño 1+2;
- PNA and PDO as regional filters.

The website has a switch for choosing among them.

**Averaging seasons**: JJA, JAS, ASO (or the closest available) and SO, restricted to
months available by the end date.

**Pre-registered primary test**:
- Island-wide window SPI against JAS Niño 3.4.
- One-sided, expecting El Niño to be drier.
- Spearman ρ, permutation p-value, both series detrended.
- Run on the long record and, separately, on 2000–2025.

**Secondary tests** (Benjamini–Hochberg FDR):
- Predictors: 3 seasons × the indices.
- Responses: 4 metrics (window total, wet days, maximum dry spell, heavy-day count).
- Everything else is labelled exploratory.

**Regression models.** PNA is largely a **mediator** of ENSO's effect, and PDO is
partly ENSO-forced. Hence:

1. Precipitation ~ ENSO: total association.
2. Precipitation ~ ENSO + PDO⊥, where PDO⊥ is the residual after regressing PDO on
   the ENSO index. This asks whether the North Pacific state adds information.
3. Precipitation ~ PNA, reported separately as the pathway. PNA is never entered
   alongside ENSO as a competing cause.

At most two predictors with n = 26, and up to three with the long record. p-values
come from permutation tests with all series detrended.

**Predictor-side PCA is not used**, because Niño 3.4, SOI and MEI are collinear (MEI
is itself an EOF), and mixing mediators and confounders inside a PC obscures
interpretation.

**Composites**: precipitation distributions for strong El Niño, neutral and La Niña
autumns (long record), and where 2026 sits relative to the El Niño-conditional
distribution.

**Station maps** of correlations use Wilks (2016) FDR field significance.

## 10. Trend and normals

- Sen's slope with a Mann–Kendall p-value per station and metric (baseline period),
  shown as context.
- Detrending is applied before ENSO tests.
- ECCC 1991–2020 normals are a cross-check against the custom baseline.

## 11. Outputs and website

**Tables** (CSV and parquet):
- station inventory;
- completeness by station and year;
- metrics by station and year;
- 2026 anomalies by station;
- regional indices by year;
- ENSO test results.

**Website**: a static folder uploaded to web.uvic.ca with Cyberduck. It needs no
server and no build step.

```
site/
  index.html          vanilla JS; tabs: Map · Station · Coherence · Dry spells · ENSO · Methods
  lib/plotly.min.js   vendored (a partial cartesian bundle if sufficient)
  data/*.json         precomputed metrics for every option combination
  data/daily/<id>.json  lazy-loaded per station
  geo/coast.geojson   vendored coastline; no tile server dependency
  figures/*.png       static report figures
  methods.html        definitions, thresholds, data vintage
```

Options (threshold, window, anomaly type, index, season) select among
*precomputed* results. The JavaScript does no metric computation, so there is only
one implementation of each metric.

## 12. Updating

- `python run.py update --end-date YYYY-MM-DD` re-downloads only years that are not
  final, then rebuilds metrics, figures and the site.
- A cached year is "final" once it was fetched more than 60 days after that year
  ended.
- Every build stamps the data retrieval date on the site.

## 13. Milestones

| # | Content | Status |
|---|---|---|
| 1 | Skeleton, ECCC client and cache, probe and smoke test, inventory, completeness, tiers, composite candidates, inventory maps and timeline | done |
| 2 | Accepted composites applied; standardized dataset; precipitation metrics (totals, bins, decomposition, spells, events, onset); anomalies; threshold step test; quick-look map | **this commit** |
| 2b | Driest 30/45-day period (search Sep 1 → end date, each year its own minimum); Oct 1–end as headline period; self-contained briefing page (`run.py brief`) | **this commit** |
| 3 | Pairwise homogeneity (replaces neighbour-mean test), regional indices, EOFs, coherence figures, anomaly maps | |
| 4 | Long record (AHCCD + raw), climate indices, ENSO tests and regressions | |
| 5 | Temperature module (Tmax, Tmin, DTR) | |
| 6 | Static website build | |
| 7 | PCIC adapter | |

## 14. Findings from the inventory, and open items

**Findings**
- **Reporting lag.** Staffed stations (volunteer and lightstation) report days to
  weeks late. Several strong West VI and North VI records are provisional for now:
  Ucluelet Kennedy Camp, Cape Beale, Quatsino Lightstation, Nootka and Merry Island.
  Re-running `update` in mid-November and again in early December should promote
  most of them.
- **No precipitation at new automatic airport stations.** The new automatic airport
  IDs (Tofino A 1038210, Nanaimo A 1025365, and others from 2018) report no
  precipitation in the daily data. Tofino has no 2026 precipitation; Lennard Island
  is the nearby Tier 1 substitute.
- **Long-record candidates**, spanning south-east, mid-east, west and north Vancouver
  Island plus the Fraser Valley and Metro Vancouver:
  - Victoria Gonzales (1898–, composite);
  - Comox A (1944–, a single ID);
  - Estevan Point (1908–);
  - Quatsino (1895–);
  - Agassiz CDA → RCS (1889–; corr 0.75, ratio 1.006);
  - Vancouver Harbour (1925–).
  These are to be checked against AHCCD in milestone 4.

**Open**
- C-day encoding: confirm from `accumulation_patterns.csv`.
- Primary wet-day threshold: decide from `homogeneity_breaks.csv`.
- Acceptance of the zero-overlap airport joins: decide from the switch-year tests.
