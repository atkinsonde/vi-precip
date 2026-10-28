"""ENSO correspondence: how the autumn precipitation, temperature and river-flow anomalies relate to
ENSO (and PDO/PNA), and whether 2026 looks like what ENSO alone would suggest.

Everything is computed on years before the target year (the target year is never used to fit),
then the target year is compared with the fitted expectation.

Series
- Station anomalies: each station-year value is turned into a rank-based normal score
  z = Phi^-1((rank - 0.5) / n) over that station's valid years (target year included in the
  ranking; it is not used for fitting). Rank scores make gauges, rain gauges and thermometers
  comparable and are insensitive to skew and to zero totals.
- Regional series: the mean station score per year over units valid that year (at least
  `min_units` of them), re-scored the same way. Regions: "island" (Vancouver Island units) and
  "all" (the whole domain). Long record: the long-record stations (1950 onward). Rivers: the
  natural (RHBN or reviewed) gauges of the ENSO hydro domain.

Tests
- Primary, pre-registered: island Sep 1-end window total (short record 2000-2025), against JAS
  Nino 3.4, one-sided (El Nino -> drier: rho < 0), both series linearly detrended, Spearman,
  permutation p (default 9999). The same test is repeated on the long record as a check.
- Secondary grid: seasons {JJA, JAS, AS, S} x indices {Nino 3.4, RONI, ONI, MEI, SOI, flavour, PDO,
  PNA} x metrics x records (short, long, rivers); two-sided permutation p; Benjamini-Hochberg FDR
  across the whole grid (q reported).
- Regressions (OLS on the scores, years before the target): y ~ ENSO; y ~ ENSO + PDO_perp, where
  PDO_perp is PDO residualised on ENSO so the ENSO coefficient is unchanged in meaning; y ~ PNA;
  y ~ ENSO + PNA (if the ENSO coefficient shrinks toward 0, PNA carries the ENSO signal).
  For the target year: expected score, 90 % prediction interval, conditional and unconditional
  return periods.
- Composites: years classed El Nino / neutral / La Nina by JAS ONI (+-0.5 C); mean score and the
  share of years in the driest tercile, per class; permutation test El Nino vs others.
- Station maps: per-station detrended Spearman rho with the primary index and season; the field
  is controlled with the Wilks (2016) FDR rule at alpha_FDR = 2 x alpha_global.
- AHCCD cross-check: October rho from homogenised AHCCD monthly totals vs raw station totals.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import indices as idx_mod
from .config import Config
from .domain import VI_REGIONS

log = logging.getLogger(__name__)

PRECIP_METRICS = {  # metric -> (validity column, label, unit-free direction: + = wetter)
    "total_window": ("valid_total_window", "Sep 1–end total"),
    "total_oct": ("valid_total_oct", "Oct 1–end total"),
    "total_sep": ("valid_total_sep", "September total"),
    "n_wet_scaled": ("valid_daily", "Wet days (≥ threshold)"),
    "sdii": ("valid_daily", "Wet-day intensity"),
    "spell_max_win": ("valid_daily", "Longest dry spell"),
    "driest30_mm": ("valid_driest30", "Driest 30-day total"),
}
TEMP_METRICS = {f"{v}_{p}": (v, p) for v in ("tmax", "tmin", "dtr") for p in ("oct", "window")}
RIVER_METRICS = {"flow_oct": "oct", "flow_sepoct": "sepoct"}
GRID_INDICES = idx_mod.ENSO_INDICES + ["pdo", "pna"]


def settings(cfg: Config) -> dict:
    e = cfg.raw.get("enso", {})
    return {
        "primary_index": e.get("primary_index", "nino34"),
        "primary_season": e.get("primary_season", "JAS"),
        "primary_metric": e.get("primary_metric", "total_window"),
        "primary_region": e.get("primary_region", "island"),
        "class_index": e.get("class_index", "oni"),
        "class_season": e.get("class_season", "JAS"),
        "class_threshold": float(e.get("class_threshold", 0.5)),
        "n_perm": int(e.get("n_perm", 9999)),
        "alpha": float(e.get("alpha", 0.05)),
        "min_units_short": int(e.get("min_units_short", 5)),
        "min_units_long": int(e.get("min_units_long", 3)),
        "min_units_river": int(e.get("min_units_river", 5)),
        "min_years_station": int(e.get("min_years_station", 15)),
        "min_years_long": int(e.get("min_years_long", 30)),
        "seed": int(e.get("seed", 20261026)),
    }


# ------------------------------------------------------------------ helpers
def normal_scores(x: pd.Series) -> pd.Series:
    ok = x.notna()
    out = pd.Series(np.nan, index=x.index)
    n = int(ok.sum())
    if n >= 3:
        out[ok] = stats.norm.ppf((x[ok].rank(method="average") - 0.5) / n)
    return out


def detrend(y: np.ndarray, t: np.ndarray) -> np.ndarray:
    A = np.column_stack([np.ones_like(t, float), t - t.mean()])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    return y - A @ beta


def _ranks(a: np.ndarray) -> np.ndarray:
    return stats.rankdata(a)


class Resampler:
    """Permutation and bootstrap index matrices, cached by sample size (reused across tests)."""

    def __init__(self, n_perm: int, n_boot: int, seed: int):
        self.n_perm, self.n_boot = n_perm, n_boot
        self.rng = np.random.default_rng(seed)
        self._p: dict[int, np.ndarray] = {}
        self._b: dict[int, np.ndarray] = {}

    def perms(self, n: int) -> np.ndarray:
        if n not in self._p:
            self._p[n] = np.argsort(self.rng.random((self.n_perm, n)), axis=1)
        return self._p[n]

    def boots(self, n: int) -> np.ndarray:
        if n not in self._b:
            self._b[n] = self.rng.integers(0, n, (self.n_boot, n))
        return self._b[n]


def _std_rank(a: np.ndarray, axis: int = -1) -> np.ndarray:
    r = stats.rankdata(a, axis=axis)
    r = r - r.mean(axis=axis, keepdims=True)
    return r / np.sqrt((r ** 2).sum(axis=axis, keepdims=True))


def perm_spearman(x: np.ndarray, y: np.ndarray, years: np.ndarray, rs: Resampler, detrended: bool = True) -> dict:
    ok = np.isfinite(x) & np.isfinite(y)
    x, y, t = x[ok], y[ok], years[ok].astype(float)
    n = len(x)
    if n < 8:
        return {"n": n, "rho": np.nan, "p_two": np.nan, "p_neg": np.nan, "p_pos": np.nan}
    if detrended:
        x, y = detrend(x, t), detrend(y, t)
    rx, ry = _std_rank(x), _std_rank(y)
    rho = float(rx @ ry)
    null = ry[rs.perms(n)] @ rx
    N = rs.n_perm
    p_neg = (1 + (null <= rho + 1e-12).sum()) / (N + 1)
    p_pos = (1 + (null >= rho - 1e-12).sum()) / (N + 1)
    p_two = (1 + (np.abs(null) >= abs(rho) - 1e-12).sum()) / (N + 1)
    b = rs.boots(n)
    with np.errstate(invalid="ignore", divide="ignore"):
        bs = (_std_rank(x[b], axis=1) * _std_rank(y[b], axis=1)).sum(axis=1)
    return {"n": n, "rho": rho, "p_two": float(p_two), "p_neg": float(p_neg), "p_pos": float(p_pos),
            "rho_lo": float(np.nanpercentile(bs, 5)), "rho_hi": float(np.nanpercentile(bs, 95)),
            "year_first": int(t.min()), "year_last": int(t.max())}


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p (q values)."""
    p = np.asarray(p, float)
    q = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    pv = p[ok]
    m = len(pv)
    if not m:
        return q
    o = np.argsort(pv)
    adj = pv[o] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out = np.empty(m)
    out[o] = np.minimum(adj, 1)
    q[ok] = out
    return q


def wilks_fdr(p: np.ndarray, alpha_global: float) -> tuple[np.ndarray, float]:
    """Wilks (2016): reject p_i <= p_FDR, p_FDR = max{p_(i): p_(i) <= (i/N) alpha_FDR}, alpha_FDR = 2 alpha_global."""
    p = np.asarray(p, float)
    ok = np.isfinite(p)
    ps = np.sort(p[ok])
    n = len(ps)
    a = 2 * alpha_global
    hit = ps <= a * np.arange(1, n + 1) / max(n, 1)
    thr = float(ps[hit].max()) if hit.any() else 0.0
    return (p <= thr) & ok, thr


def ols(y: np.ndarray, X: np.ndarray, names: list[str]) -> dict:
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    y, X = y[ok], X[ok]
    n, k = X.shape
    A = np.column_stack([np.ones(n), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    res = y - A @ beta
    dof = n - k - 1
    s2 = float(res @ res / dof) if dof > 0 else np.nan
    cov = s2 * np.linalg.pinv(A.T @ A)
    se = np.sqrt(np.diag(cov))
    tval = beta / se
    p = 2 * stats.t.sf(np.abs(tval), dof)
    r2 = 1 - float(res @ res) / float(((y - y.mean()) ** 2).sum())
    return {"n": n, "dof": dof, "r2": r2, "s2": s2, "cov": cov, "beta": beta,
            "coef": {nm: {"b": float(beta[i + 1]), "se": float(se[i + 1]), "t": float(tval[i + 1]), "p": float(p[i + 1])}
                     for i, nm in enumerate(names)},
            "intercept": float(beta[0])}


def predict(fit: dict, x_new: np.ndarray, level: float = 0.90) -> dict:
    a = np.r_[1.0, x_new]
    m = float(a @ fit["beta"])
    se = float(np.sqrt(fit["s2"] + a @ fit["cov"] @ a))
    tq = stats.t.ppf(0.5 + level / 2, fit["dof"])
    return {"mean": m, "lo": m - tq * se, "hi": m + tq * se, "se": se}


# ------------------------------------------------------------------ series assembly
def _units_short(cfg: Config) -> pd.DataFrame:
    return pd.read_parquet(cfg.tables_dir / "metrics_unit_year.parquet")


def station_scores_precip(met: pd.DataFrame, thr: float, years_max: int, min_years: int) -> pd.DataFrame:
    """Long frame unit_id, year, metric, value, z for the precipitation metrics."""
    m = met[(met["threshold"] == thr) & (met["year"] <= years_max)]
    rows = []
    for metric, (vcol, _) in PRECIP_METRICS.items():
        if metric not in m:
            continue
        v = m.loc[m[vcol].astype(bool) if vcol in m else slice(None), ["unit_id", "year", metric]]
        v = v.rename(columns={metric: "value"}).dropna()
        v["metric"] = metric
        rows.append(v)
    long = pd.concat(rows, ignore_index=True)
    long["nyr"] = long.groupby(["unit_id", "metric"])["year"].transform("size")
    long = long[long["nyr"] >= min_years].drop(columns="nyr")
    long["z"] = long.groupby(["unit_id", "metric"])["value"].transform(normal_scores)
    return long


def station_scores_temp(stats_df: pd.DataFrame, units: pd.DataFrame | None, years_max: int, min_years: int) -> pd.DataFrame:
    rows = []
    keep = None
    if units is not None and "duplicate_of" in units:
        keep = set(units.loc[units["duplicate_of"].isna(), "unit_id"])
    for metric, (v, per) in TEMP_METRICS.items():
        g = stats_df[(stats_df["period"] == per) & stats_df[f"{v}_valid"].astype(bool) & (stats_df["year"] <= years_max)]
        g = g[["unit_id", "year", f"{v}_mean"]].rename(columns={f"{v}_mean": "value"}).dropna()
        if keep is not None:
            g = g[g["unit_id"].isin(keep)]
        g["metric"] = metric
        rows.append(g)
    long = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["unit_id", "year", "value", "metric"])
    long["nyr"] = long.groupby(["unit_id", "metric"])["year"].transform("size")
    long = long[long["nyr"] >= min_years].drop(columns="nyr")
    long["z"] = long.groupby(["unit_id", "metric"])["value"].transform(normal_scores)
    return long


def station_scores_river(nat: pd.DataFrame, years_max: int, min_years: int) -> pd.DataFrame:
    rows = []
    for metric, per in RIVER_METRICS.items():
        g = nat[(nat["period"] == per) & (nat["year"] <= years_max)][["station", "year", "value"]].dropna()
        g = g.rename(columns={"station": "unit_id"})
        g["metric"] = metric
        rows.append(g)
    long = pd.concat(rows, ignore_index=True)
    long["nyr"] = long.groupby(["unit_id", "metric"])["year"].transform("size")
    long = long[long["nyr"] >= min_years].drop(columns="nyr")
    long["z"] = long.groupby(["unit_id", "metric"])["value"].transform(normal_scores)
    return long


def regional(scores: pd.DataFrame, units: list[str] | set[str], min_units: int) -> pd.DataFrame:
    s = scores[scores["unit_id"].isin(units)]
    g = s.groupby(["metric", "year"])["z"].agg(["mean", "size"]).reset_index()
    g = g[g["size"] >= min_units].rename(columns={"mean": "zmean", "size": "n_units"})
    g["z"] = g.groupby("metric")["zmean"].transform(normal_scores)
    return g


# ------------------------------------------------------------------ analysis
class Analysis:
    def __init__(self, cfg: Config):
        self.cfg, self.S = cfg, settings(cfg)
        self.ty = cfg.target_year
        self.rng = np.random.default_rng(self.S["seed"])
        self.rs = Resampler(self.S["n_perm"], int(cfg.raw.get("enso", {}).get("n_boot", 1000)), self.S["seed"])
        seas = idx_mod.load_seasonal(cfg)
        self.idx = seas.pivot_table(index="year", columns=["index", "season"], values="value")
        self.series: dict[str, pd.DataFrame] = {}  # record -> regional frame (region, metric, year, z, zmean, n_units)
        self.scores: dict[str, pd.DataFrame] = {}  # record -> station scores
        self.meta: dict[str, pd.DataFrame] = {}  # record -> station metadata (unit_id, name, lon, lat, region)

    def index_series(self, name: str, season: str) -> pd.Series:
        if (name, season) not in self.idx.columns:
            return pd.Series(dtype=float)
        return self.idx[(name, season)]

    # ---------- inputs
    def load(self) -> None:
        cfg, S, ty = self.cfg, self.S, self.ty
        thr = cfg.wet_thresholds[0]
        tdir, pdir = cfg.tables_dir, cfg.data_dir / "processed"
        # short record, precipitation (+ temperature)
        units = pd.read_parquet(tdir / "units.parquet")
        units = units[units["tier"].isin([1, 2])]
        met = pd.read_parquet(tdir / "metrics_unit_year.parquet")
        met = met[met["unit_id"].isin(units["unit_id"])]
        sp = station_scores_precip(met, thr, ty, S["min_years_station"])
        tfile = tdir / "temperature_unit_year.csv"
        if tfile.exists():
            tst = pd.read_csv(tfile, dtype={"unit_id": str})
            tun = pd.read_csv(tdir / "temperature_units.csv", dtype={"unit_id": str})
            st_ = station_scores_temp(tst, tun, ty, S["min_years_station"])
            tmeta = tun[["unit_id", "name", "lon", "lat", "region"]]
        else:
            st_, tmeta = pd.DataFrame(columns=sp.columns), pd.DataFrame(columns=["unit_id", "name", "lon", "lat", "region"])
        self.scores["short"] = pd.concat([sp, st_], ignore_index=True)
        meta = pd.concat([units[["unit_id", "name", "lon", "lat", "region"]], tmeta]).drop_duplicates("unit_id")
        meta["region"] = meta["region"].astype(str)
        self.meta["short"] = meta
        isl = set(meta.loc[meta["region"].isin(VI_REGIONS), "unit_id"])
        allu = set(meta["unit_id"])
        self.series["short"] = pd.concat([
            regional(self.scores["short"], isl, S["min_units_short"]).assign(region="island"),
            regional(self.scores["short"], allu, S["min_units_short"]).assign(region="all")], ignore_index=True)
        # long record
        lp = pdir / "long_precip.parquet"
        if lp.exists():
            lmet = pd.read_parquet(lp)
            lun = pd.read_parquet(pdir / "long_units.parquet")
            s1 = station_scores_precip(lmet, thr, ty, S["min_years_long"])
            lt = pdir / "long_temp.parquet"
            s2 = station_scores_temp(pd.read_parquet(lt), None, ty, S["min_years_long"]) if lt.exists() else s1.iloc[:0]
            self.scores["long"] = pd.concat([s1, s2], ignore_index=True)
            lun["region"] = lun["region"].astype(str)
            self.meta["long"] = lun[["unit_id", "name", "lon", "lat", "region"]]
            isl = set(lun.loc[lun["region"].isin(VI_REGIONS), "unit_id"])
            self.series["long"] = pd.concat([
                regional(self.scores["long"], isl, S["min_units_long"]).assign(region="island"),
                regional(self.scores["long"], set(lun["unit_id"]), S["min_units_long"]).assign(region="all")],
                ignore_index=True)
        # rivers
        hn = pdir / "hydro_natural_annual.parquet"
        if hn.exists():
            nat = pd.read_parquet(hn)
            if len(nat):
                self.scores["rivers"] = station_scores_river(nat, ty, S["min_years_long"])
                m = nat.drop_duplicates("station")[["station", "name", "lon", "lat"]].rename(columns={"station": "unit_id"})
                m["region"] = np.where(m["lat"] <= 50.9, "south", "north")  # rough split for display only
                self.meta["rivers"] = m
                self.series["rivers"] = regional(self.scores["rivers"], set(m["unit_id"]),
                                                 S["min_units_river"]).assign(region="coastal")

    # ---------- tests
    def _corr(self, y: pd.Series, name: str, season: str, detrended=True) -> dict:
        x = self.index_series(name, season)
        yrs = np.array(sorted(set(y.index) & set(x.index)))
        yrs = yrs[yrs < self.ty]
        # SOI is inverse to the Nino indices; its sign is reported as-is
        return perm_spearman(x.reindex(yrs).to_numpy(float), y.reindex(yrs).to_numpy(float), yrs, self.rs, detrended)

    def regional_y(self, record: str, region: str, metric: str) -> pd.Series:
        r = self.series.get(record)
        if r is None:
            return pd.Series(dtype=float)
        g = r[(r["region"] == region) & (r["metric"] == metric)]
        return g.set_index("year")["z"]

    def primary(self) -> dict:
        S = self.S
        out = {}
        for record in ("short", "long"):
            y = self.regional_y(record, S["primary_region"], S["primary_metric"])
            if y.empty:
                continue
            r = self._corr(y, S["primary_index"], S["primary_season"])
            r_raw = self._corr(y, S["primary_index"], S["primary_season"], detrended=False)
            out[record] = {**r, "p_one_sided": r["p_neg"], "rho_not_detrended": r_raw["rho"],
                           "p_not_detrended": r_raw["p_neg"]}
        out["spec"] = {k: S[k] for k in ("primary_index", "primary_season", "primary_metric", "primary_region")}
        out["spec"]["direction"] = "rho < 0 (El Niño → drier)"
        return out

    def grid(self) -> pd.DataFrame:
        rows = []
        for record, reg in self.series.items():
            for (region, metric), g in reg.groupby(["region", "metric"]):
                y = g.set_index("year")["z"]
                for name in GRID_INDICES:
                    for season in idx_mod.SEASONS:
                        if (name, season) not in self.idx.columns:
                            continue
                        r = self._corr(y, name, season)
                        rows.append({"record": record, "region": region, "metric": metric, "index": name,
                                     "season": season, **r})
        df = pd.DataFrame(rows)
        if len(df):
            df["q_bh"] = bh(df["p_two"].to_numpy())
            df["family"] = np.where(df["metric"].str.startswith(("tmax", "tmin", "dtr")), "temperature",
                                    np.where(df["metric"].str.startswith("flow"), "rivers", "precipitation"))
        return df

    def regressions(self) -> pd.DataFrame:
        S, ty = self.S, self.ty
        seas = S["primary_season"]
        enso = self.index_series(S["primary_index"], seas)
        pdo = self.index_series("pdo", seas)
        pna = self.index_series("pna", seas)
        rows = []
        for record, reg in self.series.items():
            for (region, metric), g in reg.groupby(["region", "metric"]):
                y = g.set_index("year")["z"]
                yrs = np.array(sorted(set(y.index) & set(enso.index)))
                fit_yrs = yrs[yrs < ty]
                if len(fit_yrs) < 10:
                    continue
                e = enso.reindex(fit_yrs).to_numpy(float)
                # PDO orthogonalised on ENSO over the fit years
                pd_ = pdo.reindex(fit_yrs).to_numpy(float)
                okp = np.isfinite(pd_) & np.isfinite(e)
                if okp.sum() > 5:
                    bp = np.polyfit(e[okp], pd_[okp], 1)
                    pdo_perp = pd_ - np.polyval(bp, e)
                else:
                    bp, pdo_perp = None, np.full_like(e, np.nan)
                pn = pna.reindex(fit_yrs).to_numpy(float)
                yy = y.reindex(fit_yrs).to_numpy(float)
                models = {
                    "enso": (np.column_stack([e]), ["enso"]),
                    "enso+pdo_perp": (np.column_stack([e, pdo_perp]), ["enso", "pdo_perp"]),
                    "pna": (np.column_stack([pn]), ["pna"]),
                    "enso+pna": (np.column_stack([e, pn]), ["enso", "pna"]),
                }
                x_new = {"enso": enso.get(ty, np.nan), "pna": pna.get(ty, np.nan),
                         "pdo_perp": (pdo.get(ty, np.nan) - np.polyval(bp, enso.get(ty, np.nan))) if bp is not None else np.nan}
                y_obs = y.get(ty, np.nan)
                for mname, (X, names) in models.items():
                    if np.isfinite(X).all(axis=1).sum() < 10:
                        continue
                    f = ols(yy, X, names)
                    row = {"record": record, "region": region, "metric": metric, "model": mname, "n": f["n"],
                           "r2": f["r2"], "resid_sd": float(np.sqrt(f["s2"]))}
                    for nm, c in f["coef"].items():
                        row |= {f"b_{nm}": c["b"], f"se_{nm}": c["se"], f"p_{nm}": c["p"]}
                    xn = np.array([x_new[nm] for nm in names], float)
                    if np.isfinite(xn).all():
                        pr = predict(f, xn)
                        row |= {"target_expected": pr["mean"], "target_lo90": pr["lo"], "target_hi90": pr["hi"],
                                "target_observed": y_obs}
                        if np.isfinite(y_obs):
                            p_cond = float(stats.t.cdf((y_obs - pr["mean"]) / pr["se"], f["dof"]))
                            p_unc = float(stats.norm.cdf(y_obs))
                            row |= {"target_p_cond": p_cond, "target_p_uncond": p_unc,
                                    "target_rp_cond": 1 / p_cond if p_cond > 0 else np.inf,
                                    "target_rp_uncond": 1 / p_unc if p_unc > 0 else np.inf}
                    rows.append(row)
        return pd.DataFrame(rows)

    def classes(self) -> pd.Series:
        S = self.S
        x = self.index_series(S["class_index"], S["class_season"])
        t = S["class_threshold"]
        return pd.Series(np.where(x >= t, "El Niño", np.where(x <= -t, "La Niña", "Neutral")), index=x.index)

    def composites(self) -> pd.DataFrame:
        cls = self.classes()
        rows = []
        for record, reg in self.series.items():
            for (region, metric), g in reg.groupby(["region", "metric"]):
                y = g.set_index("year")["z"]
                yrs = [v for v in y.index if v < self.ty and v in cls.index]
                yy, cc = y.reindex(yrs), cls.reindex(yrs)
                lo_terc = yy.quantile(1 / 3)
                obs = (yy[cc == "El Niño"].mean() - yy[cc != "El Niño"].mean())
                arr, lab = yy.to_numpy(), (cc == "El Niño").to_numpy()
                nperm = 4999
                null = np.array([arr[p_][lab].mean() - arr[p_][~lab].mean()
                                 for p_ in (self.rng.permutation(len(arr)) for _ in range(nperm))]) if lab.any() and (~lab).any() else np.array([])
                p = (1 + (null <= obs).sum()) / (nperm + 1) if len(null) else np.nan
                for c in ("El Niño", "Neutral", "La Niña"):
                    v = yy[cc == c]
                    rows.append({"record": record, "region": region, "metric": metric, "class": c, "n": len(v),
                                 "mean_z": float(v.mean()) if len(v) else np.nan,
                                 "median_z": float(v.median()) if len(v) else np.nan,
                                 "frac_low_tercile": float((v <= lo_terc).mean()) if len(v) else np.nan,
                                 "years": " ".join(map(str, v.index.tolist())),
                                 "p_elnino_lower": p})
        return pd.DataFrame(rows)

    def station_maps(self) -> pd.DataFrame:
        S = self.S
        rows = []
        for record, sc in self.scores.items():
            meta = self.meta[record].set_index("unit_id")
            for (uid, metric), g in sc.groupby(["unit_id", "metric"]):
                y = g.set_index("year")["z"]
                r = self._corr(y, S["primary_index"], S["primary_season"])
                if not np.isfinite(r.get("rho", np.nan)):
                    continue
                mrow = meta.loc[uid] if uid in meta.index else None
                rows.append({"record": record, "unit_id": uid, "metric": metric,
                             "name": None if mrow is None else mrow["name"],
                             "lon": None if mrow is None else mrow["lon"], "lat": None if mrow is None else mrow["lat"],
                             "z_target": float(y.get(self.ty, np.nan)), **r})
        df = pd.DataFrame(rows)
        if len(df):
            df["fdr_sig"] = False
            df["p_fdr_threshold"] = np.nan
            for key, g in df.groupby(["record", "metric"]):
                sig, thr = wilks_fdr(g["p_two"].to_numpy(), S["alpha"])
                df.loc[g.index, "fdr_sig"] = sig
                df.loc[g.index, "p_fdr_threshold"] = thr
                df.loc[g.index, "field_significant"] = bool(sig.any())
        return df

    def ahccd_check(self) -> pd.DataFrame:
        pdir = self.cfg.data_dir / "processed"
        a, lp = pdir / "long_ahccd.parquet", pdir / "long_precip.parquet"
        if not (a.exists() and lp.exists()):
            return pd.DataFrame()
        ah = pd.read_parquet(a)
        if ah.empty:
            return pd.DataFrame()
        lm = pd.read_parquet(lp)
        rows = []
        x = self.index_series(self.S["primary_index"], self.S["primary_season"])
        for uid, g in ah[ah["month"] == 10].groupby("unit_id"):
            adj = g.set_index("year")["precip"].dropna()
            raw = lm[(lm["unit_id"] == uid) & lm["valid_total_oct"].astype(bool)].drop_duplicates("year").set_index("year")["total_oct"]
            common = sorted(set(adj.index) & set(raw.index))
            r_adj = self._corr(normal_scores(adj), self.S["primary_index"], self.S["primary_season"])
            # raw Oct 1-end vs AHCCD full October: same years only
            r_raw = self._corr(normal_scores(raw.reindex(common)), self.S["primary_index"], self.S["primary_season"]) \
                if len(common) >= 8 else {"rho": np.nan, "n": len(common)}
            rows.append({"unit_id": uid, "ahccd_id": g["ahccd_id"].iloc[0], "ahccd_name": g["ahccd_name"].iloc[0],
                         "km": g["ahccd_km"].iloc[0], "n_ahccd": r_adj["n"], "rho_ahccd_oct": r_adj["rho"],
                         "p_ahccd": r_adj.get("p_two"), "n_common": len(common), "rho_raw_oct1_end": r_raw["rho"],
                         "corr_raw_vs_ahccd": float(np.corrcoef(adj.reindex(common), raw.reindex(common))[0, 1])
                         if len(common) >= 5 else np.nan,
                         "ahccd_last_year": int(adj.index.max()) if len(adj) else None})
        _ = x
        return pd.DataFrame(rows)

    def analogs(self, k: int = 8) -> pd.DataFrame:
        """Years with the JAS primary-index value closest to (and on the same side as) the target year."""
        x = self.index_series(self.S["primary_index"], self.S["primary_season"])
        if self.ty not in x.index:
            return pd.DataFrame()
        x0 = x[self.ty]
        cand = x[(x.index < self.ty) & (np.sign(x) == np.sign(x0))]
        near = (cand - x0).abs().sort_values().index[:k]
        rows = []
        for y in sorted(near):
            r = {"year": int(y), "index_value": float(x[y])}
            for record in ("short", "long"):
                for metric in ("total_window", "total_oct"):
                    s = self.regional_y(record, self.S["primary_region"], metric)
                    r[f"{record}_{metric}_z"] = float(s.get(y, np.nan))
            rows.append(r)
        return pd.DataFrame(rows)


def run(cfg: Config) -> dict[str, Path]:
    A = Analysis(cfg)
    A.load()
    out = cfg.data_dir / "processed" / "enso"
    out.mkdir(parents=True, exist_ok=True)
    prim = A.primary()
    res = {"grid": A.grid(), "regressions": A.regressions(), "composites": A.composites(),
           "station_maps": A.station_maps(), "ahccd_check": A.ahccd_check(), "analogs": A.analogs()}
    reg = pd.concat([r.assign(record=k) for k, r in A.series.items()], ignore_index=True)
    res["regional_series"] = reg
    paths = {}
    for k, df in res.items():
        p = out / f"{k}.parquet"
        df.to_parquet(p, index=False)
        df.to_csv(cfg.tables_dir / f"enso_{k}.csv", index=False)
        paths[f"enso_{k}"] = p
    cls = A.classes()
    x = A.index_series(A.S["primary_index"], A.S["primary_season"])
    summary = {"primary": prim, "target_year": A.ty,
               "target_index": {"name": A.S["primary_index"], "season": A.S["primary_season"],
                                "value": float(x.get(A.ty, np.nan)) if len(x) else None,
                                "class": str(cls.get(A.ty, "unknown"))},
               "n_grid_tests": int(len(res["grid"])),
               "n_grid_q10": int((res["grid"]["q_bh"] <= 0.10).sum()) if len(res["grid"]) else 0}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    paths["enso_summary"] = out / "summary.json"
    for rec, r in prim.items():
        if rec == "spec":
            continue
        print(f"Primary ENSO test ({rec} record, {r.get('year_first')}–{r.get('year_last')}, n={r['n']}): "
              f"rho = {r['rho']:+.2f} (90% {r.get('rho_lo', np.nan):+.2f}..{r.get('rho_hi', np.nan):+.2f}), "
              f"one-sided p = {r['p_one_sided']:.3f}")
    print(f"Secondary grid: {summary['n_grid_tests']} tests, {summary['n_grid_q10']} with BH q ≤ 0.10")
    return paths
