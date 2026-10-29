"""Figures for the briefing page: regional bars and cumulative-precipitation panels."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import style

SEP_C, WIN_C, OCT_C = "#b5b3ac", "#f08a85", "#c23a39"


def regional_bars(reg: pd.DataFrame, path: Path, end: dt.date) -> Path:
    """reg: index region, columns sep, oct, window (% of median), n."""
    style.apply()
    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = np.arange(len(reg))
    w = 0.26
    series = [("sep", "September", SEP_C), ("window", f"Sep 1–{end:%b} {end.day}", WIN_C),
              ("oct", f"Oct 1–{end.day}", OCT_C)]
    for k, (col, lab, c) in enumerate(series):
        vals = reg[col].to_numpy(dtype=float)
        bars = ax.bar(x + (k - 1) * w, vals, width=w - 0.03, color=c, label=lab, zorder=3)
        for b, v in zip(bars, vals):
            if np.isfinite(v):
                ax.annotate(f"{v:.0f}", (b.get_x() + b.get_width() / 2, v), xytext=(0, 2), textcoords="offset points",
                            ha="center", fontsize=6.5, color=style.TEXT_2)
    ax.axhline(100, color=style.TEXT_2, lw=0.8, ls=(0, (4, 3)), zorder=2)
    ax.annotate("reference period (2000–2025 median)", (len(reg) - 0.5, 100), xytext=(0, 3), textcoords="offset points",
                ha="right", fontsize=7, color=style.TEXT_2)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r}\n({int(n)} station{'s' if int(n) != 1 else ''})" for r, n in zip(reg.index, reg["n"])],
                       fontsize=7.5)
    ax.set_ylabel("% of reference-period median")
    ax.set_ylim(0, max(120, np.nanmax(reg[["sep", "oct", "window"]].to_numpy()) * 1.12))
    ax.grid(axis="y", color=style.GRID, lw=0.6, zorder=0)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(loc="upper left", ncol=3, frameon=False)
    ax.set_title("Precipitation as % of the reference-period median, median of stations in each region", loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def _cumulative(daily: pd.DataFrame, year: int, start_md: tuple[int, int], end_md: tuple[int, int]) -> tuple[np.ndarray, int]:
    lo, hi = pd.Timestamp(year, *start_md), pd.Timestamp(year, *end_md)
    seg = daily.loc[lo:hi]
    known = seg["daily_valid"].to_numpy(dtype=bool) | (seg["acc_block"].to_numpy() >= 0)
    v = np.where(known, np.nan_to_num(seg["precip"].to_numpy(dtype=float), nan=0.0), 0.0)
    return np.cumsum(v), int((~known).sum())


def cumulative_panels(units: list[tuple[str, str]], daily: dict[str, pd.DataFrame], years: list[int], target: int,
                      end: dt.date, path: Path, max_missing: int = 2) -> Path:
    """units: list of (unit_id, display name). Grey = 2000–2025, black dashed = median, red = target."""
    style.apply()
    n = len(units)
    ncol = 4 if n > 4 else n
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.4 * ncol, 2.7 * nrow), squeeze=False)
    start_md, end_md = (9, 1), (end.month, end.day)
    ndays = (dt.date(target, *end_md) - dt.date(target, *start_md)).days + 1
    ticks = [0, 14, 30, 44] + ([60] if ndays > 60 else [])
    ticklabels = ["Sep 1", "Sep 15", "Oct 1", "Oct 15", "Oct 31"][: len(ticks)]
    for ax, (uid, name) in zip(axes.flat, units):
        d = daily[uid]
        curves = []
        for y in years:
            c, miss = _cumulative(d, y, start_md, end_md)
            if len(c) == ndays and miss <= max_missing:
                curves.append(c)
                ax.plot(c, color=style.MUTED, lw=0.5, alpha=0.45, zorder=2)
        if curves:
            med = np.median(np.vstack(curves), axis=0)
            ax.plot(med, color=style.TEXT, lw=1.1, ls=(0, (4, 2)), zorder=3)
            ax.annotate("median", (ndays - 1, med[-1]), xytext=(-2, 3), textcoords="offset points", ha="right",
                        fontsize=6.5, color=style.TEXT)
        c, miss = _cumulative(d, target, start_md, end_md)
        if len(c):
            ax.plot(c, color=OCT_C, lw=2.0, zorder=4)
            pct = f" ({100 * c[-1] / med[-1]:.0f}% of median)" if curves and med[-1] > 0 else ""
            ax.text(0.02, 0.95, f"{target}: {c[-1]:.0f} mm{pct}", transform=ax.transAxes, ha="left", va="top",
                    fontsize=7, color=OCT_C, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.2", fc=style.SURFACE, ec="none", alpha=0.85), zorder=5)
        ax.axvline(30, color=style.GRID, lw=0.8, zorder=1)
        ax.set_xticks([t for t in ticks if t < ndays])
        ax.set_xticklabels(ticklabels[: len([t for t in ticks if t < ndays])], fontsize=6.5)
        ax.set_title(name, loc="left", fontsize=8.5)
        ax.grid(axis="y", color=style.GRID, lw=0.5)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.set_xlim(0, ndays - 1)
        ax.set_ylim(bottom=0)
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    for ax in axes[:, 0]:
        ax.set_ylabel("mm since Sep 1")
    fig.suptitle(f"Accumulated precipitation since September 1: {target} (red) against each year 2000–2025 (grey)",
                 x=0.01, ha="left", fontsize=10, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path
