"""Inventory figures: candidate-station map, region-check small multiples, data timeline."""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle

from .. import domain
from . import style

log = logging.getLogger(__name__)

ZOOM = (-124.25, 48.28, -122.15, 49.48)  # Greater Victoria, Gulf Islands, Metro Vancouver


def _short(name: str, n: int = 22) -> str:
    s = str(name).title().replace(" Intl A", " Intl").replace(" Int'L A", " Intl")
    return s if len(s) <= n else s[: n - 1] + "…"


def _draw_base(ax, basemap, extent, polygons: bool = False):
    x0, y0, x1, y1 = extent
    ax.set_facecolor(style.WATER)
    if basemap:
        for ring in basemap["land"]:
            ax.add_patch(Polygon(np.asarray(ring), closed=True, fc=style.LAND, ec="none", zorder=0))
        for line in basemap["coast"]:
            a = np.asarray(line)
            ax.plot(a[:, 0], a[:, 1], color=style.COAST, lw=0.4, zorder=1)
    if polygons:
        for ring in (domain.VANCOUVER_ISLAND, domain.LOWER_MAINLAND, domain.SEA_TO_SKY, domain.SUNSHINE_COAST):
            a = np.asarray(ring + [ring[0]])
            ax.plot(a[:, 0], a[:, 1], color=style.MUTED, lw=0.6, ls=(0, (3, 2)), zorder=1)
        s = np.asarray(domain.VI_SPINE)
        s = s[s[:, 1] <= domain.NORTH_VI_LAT + 0.1]
        ax.plot(s[:, 0], s[:, 1], color=style.MUTED, lw=0.6, ls=(0, (1, 1.5)), zorder=1)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect(1 / np.cos(np.radians((y0 + y1) / 2)))
    ax.tick_params(length=2)
    for sp in ax.spines.values():
        sp.set_color(style.GRID)


def _plot_stations(ax, inv: pd.DataFrame, composite_ids: set[str], label: pd.Series | None):
    for tier in (3, 2, 1):
        s = inv[inv["tier"] == tier]
        ax.scatter(
            s["lon"], s["lat"], s=22 if tier < 3 else 12, c=style.TIER_COLOR[tier],
            edgecolors=style.TIER_EDGE[tier], linewidths=0.6 if tier < 3 else 0.5, zorder=3 + (3 - tier),
        )
    c = inv[inv["climate_id"].isin(composite_ids)]
    ax.scatter(c["lon"], c["lat"], s=70, facecolors="none", edgecolors=style.CAT[1], linewidths=1.0, zorder=7)
    if label is not None:
        for _, r in inv[label].iterrows():
            ax.annotate(
                _short(r["name"]), (r["lon"], r["lat"]), xytext=(3, 2), textcoords="offset points",
                fontsize=4.6, color=style.TEXT_2, zorder=8,
            )


def station_map(inv: pd.DataFrame, proposals: pd.DataFrame, basemap, bbox, path: Path, title: str) -> Path:
    style.apply()
    comp_ids = set(proposals["climate_id"]) if len(proposals) else set()
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.3), gridspec_kw={"width_ratios": [1.45, 1]})
    x0, y0, x1, y1 = bbox
    ax = axes[0]
    _draw_base(ax, basemap, (x0, y0 + 0.05, x1, y1 - 0.15), polygons=True)
    zx0, zy0, zx1, zy1 = ZOOM
    in_zoom = inv["lon"].between(zx0, zx1) & inv["lat"].between(zy0, zy1)
    _plot_stations(ax, inv, comp_ids, (inv["tier"] <= 2) & ~in_zoom)
    ax.add_patch(Rectangle((zx0, zy0), zx1 - zx0, zy1 - zy0, fill=False, ec=style.TEXT_2, lw=0.7, zorder=9))
    ax.set_title("Domain", loc="left")

    ax = axes[1]
    _draw_base(ax, basemap, ZOOM, polygons=True)
    _plot_stations(ax, inv[in_zoom], comp_ids, inv[in_zoom]["tier"] <= 2)
    ax.set_title("Greater Victoria · Gulf Islands · Metro Vancouver", loc="left")

    counts = inv["tier"].value_counts().reindex([1, 2, 3], fill_value=0)
    handles = [
        Line2D([], [], ls="", marker="o", ms=6, mfc=style.TIER_COLOR[1], mec=style.TIER_EDGE[1],
               label=f"Tier 1 — near-complete baseline + 2026 (n={counts[1]})"),
        Line2D([], [], ls="", marker="o", ms=6, mfc=style.TIER_COLOR[2], mec=style.TIER_EDGE[2],
               label=f"Tier 2 — partial baseline + 2026 (n={counts[2]})"),
        Line2D([], [], ls="", marker="o", ms=4.5, mfc=style.TIER_COLOR[3], mec=style.TIER_EDGE[3],
               label=f"Tier 3 — insufficient or no valid 2026 (n={counts[3]})"),
        Line2D([], [], ls="", marker="o", ms=10, mfc="none", mec=style.CAT[1], label="Proposed composite member"),
        Line2D([], [], color=style.MUTED, lw=0.8, ls=(0, (3, 2)), label="Region polygons / VI spine (dotted)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.005))
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold", color=style.TEXT)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def region_check(inv: pd.DataFrame, basemap, bbox, path: Path) -> Path:
    """Small multiples: one panel per region, its stations highlighted. For checking assignment."""
    style.apply()
    regs = [r for r in (list(inv["region"].cat.categories) if hasattr(inv["region"], "cat") else domain.REGIONS)
            if (inv["region"] == r).any()]
    ncol = 4
    nrow = int(np.ceil(len(regs) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(16, 2.6 * nrow + 0.5), squeeze=False)
    x0, y0, x1, y1 = bbox
    for ax, r in zip(axes.flat, regs):
        _draw_base(ax, basemap, (x0, y0 + 0.05, x1, y1 - 0.15), polygons=True)
        other = inv[inv["region"] != r]
        mine = inv[inv["region"] == r]
        ax.scatter(other["lon"], other["lat"], s=6, c=style.MUTED, alpha=0.5, lw=0, zorder=3)
        ov = mine["region_source"] == "override"
        ax.scatter(mine.loc[~ov, "lon"], mine.loc[~ov, "lat"], s=14, c=style.CAT[0], ec="white", lw=0.4, zorder=4)
        ax.scatter(mine.loc[ov, "lon"], mine.loc[ov, "lat"], s=26, marker="D", c=style.CAT[0], ec="white", lw=0.4, zorder=4)
        ax.set_title(f"{r} (n={len(mine)})", loc="left", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in list(axes.flat)[len(regs):]:
        ax.axis("off")
    fig.suptitle("Region assignment check — diamonds are manual overrides", x=0.01, ha="left",
                 fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def timeline(comp: pd.DataFrame, inv: pd.DataFrame, target_year: int, path: Path) -> Path:
    """Rows = stations, columns = years; cell = fraction of window days daily-valid;
    dot = window total valid (≤ allowed missing days)."""
    style.apply()
    w = comp[comp["period"] == "window"].copy()
    w["frac"] = w["n_valid_daily"] / w["n_days"]
    has = w.groupby("climate_id")["n_valid_daily"].sum()
    ids = has[has > 0].index
    order = (
        inv[inv["climate_id"].isin(ids)]
        .assign(region_order=lambda d: d["region"].cat.codes)
        .sort_values(["region_order", "tier", "lat"], ascending=[True, True, False])
    )
    if order.empty:
        log.warning("No station has data in the analysis window; timeline skipped")
        return path
    years = sorted(w["year"].unique())
    frac = w.pivot(index="climate_id", columns="year", values="frac").reindex(index=order["climate_id"], columns=years)
    valid = w.pivot(index="climate_id", columns="year", values="valid_total").reindex(index=order["climate_id"], columns=years)
    n = len(order)
    fig_h = max(4.0, 0.16 * n + 1.6)
    fig, ax = plt.subplots(figsize=(11, fig_h))
    cmap = ListedColormap(["#f1f0ec"] + style.SEQ_BLUE[1:])
    bounds = [0, 0.001, 0.5, 0.8, 0.9, 0.95, 0.98, 0.999, 1.0001]
    from matplotlib.colors import BoundaryNorm

    norm = BoundaryNorm(bounds, cmap.N)
    im = ax.imshow(frac.fillna(0).to_numpy(), aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
    yy, xx = np.nonzero(valid.fillna(False).to_numpy(dtype=bool))
    ax.scatter(xx, yy, s=4, c="white", lw=0, zorder=3)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) if (y % 5 == 0 or y == target_year) else "" for y in years])
    labels = [f"{_short(r['name'], 26)}  {r['climate_id']}  T{int(r['tier'])}" for _, r in order.iterrows()]
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=5.2)
    ti = years.index(target_year) if target_year in years else None
    if ti is not None:
        ax.add_patch(Rectangle((ti - 0.5, -0.5), 1, n, fill=False, ec=style.TEXT, lw=1.0, zorder=4))
    # region separators and labels
    reg = order["region"].astype(str).to_numpy()
    edges = [0] + [i for i in range(1, n) if reg[i] != reg[i - 1]] + [n]
    for a, b in zip(edges[:-1], edges[1:]):
        if a:
            ax.axhline(a - 0.5, color=style.TEXT_2, lw=0.6)
        ax.text(len(years) - 0.3, (a + b - 1) / 2, reg[a], va="center", ha="left", fontsize=7, color=style.TEXT_2)
    ax.tick_params(length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.12, ticks=[0, 0.5, 0.8, 0.9, 0.95, 0.98, 1.0])
    cb.set_label("Fraction of window days with valid daily precipitation", fontsize=7)
    cb.ax.tick_params(labelsize=6)
    ax.set_title(
        "Data coverage in the Sep 1 – end-date window (white dot = window total valid; outlined = target year)",
        loc="left", fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def smoke_plot(ann_by_year: dict[int, pd.DataFrame], cid: str, name: str, path: Path) -> Path:
    style.apply()
    yrs = sorted(ann_by_year)
    fig, axes = plt.subplots(len(yrs), 1, figsize=(10, 2.2 * len(yrs)), sharey=True, squeeze=False)
    for ax, y in zip(axes[:, 0], yrs):
        d = ann_by_year[y]
        ok = d["daily_valid"]
        ax.bar(d.index[ok], d["precip"][ok], width=0.8, color=style.SEQ_BLUE[4])
        miss = d.index[~ok & ~(d["acc_block"] >= 0)]
        acc = d.index[d["acc_block"] >= 0]
        ax.scatter(miss, np.zeros(len(miss)), marker="x", s=10, c=style.CAT[7], label="missing", zorder=3)
        ax.scatter(acc, np.zeros(len(acc)), marker="s", s=10, c=style.CAT[3], label="accumulation block", zorder=3)
        ax.set_title(f"{y}: total {d['precip'].where(ok).sum():.1f} mm on daily-valid days", loc="left", fontsize=8)
        ax.grid(axis="y", color=style.GRID, lw=0.5)
        ax.set_ylabel("mm")
    axes[0, 0].legend(loc="upper right")
    fig.suptitle(f"Smoke test — {name} ({cid}), extended window", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
