"""Quick-look anomaly map: station symbols coloured by where 2026 falls in each station's baseline."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D

from . import style
from .inventory import ZOOM, _draw_base, _short

# Diverging: red (dry) <- neutral gray -> blue (wet); equal steps per arm.
DRY = ["#8f2a2a", "#c23a39", "#e34948", "#f08a85", "#fbd3d0"]
WET = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
NEUTRAL = "#f0efec"
PCT_BOUNDS = [0, 5, 10, 20, 35, 45, 55, 65, 80, 90, 95, 100]


def percentile_map(wide: pd.DataFrame, metric: str, basemap, bbox, path: Path, title: str, label_col: str | None = None,
                   label_fmt: str = "{:.0f}%") -> Path:
    """wide must hold unit rows with lon, lat, status and f'{metric}__pctl' (0-100, low = dry)."""
    style.apply()
    cmap = ListedColormap(DRY + [NEUTRAL] + WET)
    norm = BoundaryNorm(PCT_BOUNDS, cmap.N)
    col = f"{metric}__pctl"
    d = wide.dropna(subset=[col])
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.6), gridspec_kw={"width_ratios": [1.45, 1]})
    x0, y0, x1, y1 = bbox
    for ax, ext, zoom in ((axes[0], (x0, y0 + 0.05, x1, y1 - 0.15), False), (axes[1], ZOOM, True)):
        _draw_base(ax, basemap, ext, polygons=False)
        sub = d if not zoom else d[d["lon"].between(ZOOM[0], ZOOM[2]) & d["lat"].between(ZOOM[1], ZOOM[3])]
        final = sub["status"] == "final"
        ax.scatter(sub.loc[final, "lon"], sub.loc[final, "lat"], c=sub.loc[final, col], cmap=cmap, norm=norm,
                   s=46, edgecolors=style.TEXT_2, linewidths=0.5, zorder=4)
        ax.scatter(sub.loc[~final, "lon"], sub.loc[~final, "lat"], c=sub.loc[~final, col], cmap=cmap, norm=norm,
                   s=46, marker="D", edgecolors=style.TEXT_2, linewidths=0.5, zorder=4)
        if label_col:
            for _, r in sub.iterrows():
                v = r.get(label_col)
                if pd.notna(v):
                    ax.annotate(label_fmt.format(v), (r["lon"], r["lat"]), xytext=(4, 2), textcoords="offset points",
                                fontsize=5.2, color=style.TEXT_2, zorder=6)
    axes[0].set_title("Domain", loc="left")
    axes[1].set_title("Greater Victoria · Gulf Islands · Metro Vancouver", loc="left")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    fig.subplots_adjust(left=0.04, right=0.92, top=0.88, bottom=0.1, wspace=0.08)
    cax = fig.add_axes([0.94, 0.16, 0.011, 0.66])
    cb = fig.colorbar(sm, cax=cax, ticks=PCT_BOUNDS)
    cb.set_label("Percentile of 2026 within 2000–2025 (low = dry)", fontsize=7)
    cb.ax.tick_params(labelsize=6)
    handles = [Line2D([], [], ls="", marker="o", ms=6, mfc=NEUTRAL, mec=style.TEXT_2, label="final (Tier 1–2)"),
               Line2D([], [], ls="", marker="D", ms=5.5, mfc=NEUTRAL, mec=style.TEXT_2,
                      label="provisional (late reports; total is a lower bound)")]
    fig.legend(handles=handles, loc="lower left", ncol=2, bbox_to_anchor=(0.01, 0.0))
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path
