"""Public map: ranks of October flow (rivers) and level (lakes), south Vancouver Island.

Numbered markers (north to south) with a key below, so labels never collide. Markers that would
overlap are nudged apart, with a thin leader line back to the gauge location.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon

INK, INK2, LAND, WATER, COAST = "#0b0b0b", "#52514e", "#ecebe7", "#f6f8fb", "#b5b3ac"


def short_name(n: str) -> str:
    s = n.title()
    for a, b in ((" River", " R."), (" Creek", " Cr."), (" Near ", " near "), (" At ", " at "), (" Above ", " above "),
                 (" Below ", " below "), (" Outlet", " outlet"), (" The ", " the "), (" Mouth", " mouth"), ("Bchp", "BCHP")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def rank_words(rank: float, n: float) -> str:
    if rank is None or not np.isfinite(rank):
        return ""
    r = int(round(rank))
    if r == 1:
        return f"lowest of {int(n)} years"
    suf = "th" if 10 <= r % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(r % 10, "th")
    return f"{r}{suf} lowest of {int(n)}"


def cats(b0: int):
    return [  # (label, fill, number colour)
        (f"Lowest since {b0}", "#8f2a2a", "white"),
        ("2nd or 3rd lowest", "#e34948", "white"),
        ("Among the lowest quarter", "#f6a8a2", INK),
        ("Near typical (middle half)", "#f0efec", INK),
        ("Higher than usual", "#6da7ec", INK),
    ]


def category(rank: float, n: float) -> int:
    if rank <= 1:
        return 0
    if rank <= 3.5:
        return 1
    p = (rank - 0.5) / n
    if p <= 0.25:
        return 2
    if p < 0.75:
        return 3
    return 4


def _repel(px: np.ndarray, min_d: float, box, iters: int = 400) -> np.ndarray:
    """Push points apart until all pairs are at least min_d apart (display pixels), staying in box."""
    p = px.astype(float).copy()
    x0, y0, x1, y1 = box
    for _ in range(iters):
        moved = False
        for i in range(len(p)):
            d = p[i] - p
            dist = np.hypot(d[:, 0], d[:, 1])
            dist[i] = np.inf
            close = np.flatnonzero(dist < min_d)
            for j in close:
                v = d[j] if dist[j] > 1e-6 else np.array([1.0, 0.3])
                v = v / np.linalg.norm(v)
                step = (min_d - dist[j]) / 2 + 0.3
                p[i] += v * step
                p[j] -= v * step
                moved = True
        p[:, 0] = np.clip(p[:, 0], x0 + min_d / 2, x1 - min_d / 2)
        p[:, 1] = np.clip(p[:, 1], y0 + min_d / 2, y1 - min_d / 2)
        if not moved:
            break
    return p


def make_map(r: pd.DataFrame, out: Path, end: dt.date, S: dict, basemap=None) -> dict:
    if basemap is None:
        try:
            from vi_precip.config import load_config
            from vi_precip.geo import load_basemap

            basemap = load_basemap(load_config())
        except Exception:
            basemap = None
    ty, b0 = S["target_year"], S["baseline_start"]
    C = cats(b0)
    r = r.copy()
    r["cat"] = [category(a, b) for a, b in zip(r["oct_rank_base"], r["oct_n_base"])]
    r["reg"] = ~r["regulation"].astype(str).str.startswith("natural")
    r = r.sort_values("lat", ascending=False).reset_index(drop=True)
    r["num"] = np.arange(1, len(r) + 1)

    lx0, ly0, lx1, ly1 = S["map_extent"]
    x0 = max(lx0, min(r["lon"].min(), -123.37) - 0.12)
    x1 = min(lx1, max(r["lon"].max(), -123.37) + 0.12)
    y0 = max(ly0, min(r["lat"].min(), 48.42) - 0.08)
    y1 = min(ly1, r["lat"].max() + 0.08)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
    W, axw = 8.2, 7.7
    axh = axw * (y1 - y0) / ((x1 - x0) * np.cos(np.radians((y0 + y1) / 2)))
    nrow = int(np.ceil(len(r) / 2))
    key_h = 0.20 * nrow + 0.35
    top, legend_h, foot = 1.0, 0.75, 0.45
    H = top + axh + 0.15 + key_h + legend_h + foot
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([(W - axw) / 2 / W, (foot + legend_h + key_h + 0.15) / H, axw / W, axh / H])
    ax.set_facecolor(WATER)
    if basemap:
        for ring in basemap["land"]:
            ax.add_patch(Polygon(np.asarray(ring), closed=True, fc=LAND, ec="none", zorder=0))
        for line in basemap["coast"]:
            a = np.asarray(line)
            ax.plot(a[:, 0], a[:, 1], color=COAST, lw=0.5, zorder=1)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect(1 / np.cos(np.radians((y0 + y1) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color("#d8d7d2")

    towns = {"Victoria": (-123.365, 48.428), "Duncan": (-123.708, 48.778), "Nanaimo": (-123.94, 49.166),
             "Parksville": (-124.31, 49.32), "Port Alberni": (-124.80, 49.235), "Courtenay": (-124.99, 49.69),
             "Port Renfrew": (-124.42, 48.555), "Lake Cowichan": (-124.055, 48.825), "Tofino": (-125.905, 49.153),
             "Ucluelet": (-125.546, 48.942), "Sooke": (-123.73, 48.375)}
    fig.canvas.draw()
    true_px = ax.transData.transform(np.column_stack([r["lon"], r["lat"]]))
    bb = ax.get_window_extent()
    marker_px = 15.5 * fig.dpi / 72  # marker diameter in pixels (~15.5 pt)
    new_px = _repel(true_px, marker_px * 1.08, (bb.x0, bb.y0, bb.x1, bb.y1))
    new_xy = ax.transData.inverted().transform(new_px)
    for i, s in r.iterrows():
        tx, ty_ = s["lon"], s["lat"]
        nx, ny = new_xy[i]
        if np.hypot(*(new_px[i] - true_px[i])) > 2:
            ax.plot([tx, nx], [ty_, ny], color=INK2, lw=0.5, zorder=4)
            ax.plot(tx, ty_, marker="o", ms=1.8, color=INK2, zorder=4)
        _, fill, tc = C[s["cat"]]
        ax.scatter(nx, ny, s=(15.5) ** 2, marker="o" if s["param"] == "flow" else "s", c=fill,
                   edgecolors=INK if s["reg"] else INK2, linewidths=1.8 if s["reg"] else 0.6, zorder=6)
        ax.text(nx, ny, str(s["num"]), ha="center", va="center", fontsize=6.3, color=tc, fontweight="bold", zorder=7)

    # ---- towns, placed clear of the station markers
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    rad = marker_px * 0.55
    offsets = [(3, -3, "left", "top"), (-3, -3, "right", "top"), (3, 3, "left", "bottom"), (-3, 3, "right", "bottom"),
               (0, -6, "center", "top"), (0, 6, "center", "bottom"), (12, -4, "left", "top"), (-12, -4, "right", "top"),
               (12, 4, "left", "bottom"), (-12, 4, "right", "bottom"), (0, -14, "center", "top"),
               (0, 14, "center", "bottom")]
    for t, (tx, ty_) in towns.items():
        if not (x0 < tx < x1 and y0 < ty_ < y1):
            continue
        ax.plot(tx, ty_, marker="s", ms=2.4, color=INK2, zorder=3)
        best = None
        for dx, dy, ha, va in offsets:
            a = ax.annotate(t, (tx, ty_), xytext=(dx, dy), textcoords="offset points", fontsize=6.0, color=INK2,
                            style="italic", ha=ha, va=va, zorder=3)
            bx = a.get_window_extent(rend)
            cx = np.clip(new_px[:, 0], bx.x0, bx.x1)
            cy = np.clip(new_px[:, 1], bx.y0, bx.y1)
            dmin = float(np.hypot(new_px[:, 0] - cx, new_px[:, 1] - cy).min())
            a.remove()
            if best is None or dmin > best[0]:
                best = (dmin, dx, dy, ha, va)
            if dmin > rad:
                break
        _, dx, dy, ha, va = best
        ax.annotate(t, (tx, ty_), xytext=(dx, dy), textcoords="offset points", fontsize=6.0, color=INK2,
                    style="italic", ha=ha, va=va, zorder=3)

    # ---- title
    fig.text(0.03, 1 - 0.22 / H, f"River flows on southern Vancouver Island, October 1–{end.day}, {ty}",
             fontsize=14, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.03, 1 - 0.58 / H,
             f"Average flow (circles) or lake level (squares) over October 1–{end.day}, ranked against the same dates\n"
             f"in every year since {b0} at each station. Numbers refer to the list below.",
             fontsize=8.4, color=INK2, ha="left", va="top")

    # ---- key (two columns)
    kax = fig.add_axes([0.03, (foot + legend_h) / H, 0.94, key_h / H])
    kax.axis("off")
    kax.set_xlim(0, 2)
    kax.set_ylim(nrow + 0.2, -0.6)
    for i, s in r.iterrows():
        col, row = (0, i) if i < nrow else (1, i - nrow)
        _, fill, tc = C[s["cat"]]
        kax.scatter(col + 0.02, row, s=70, marker="o" if s["param"] == "flow" else "s", c=fill,
                    edgecolors=INK if s["reg"] else INK2, linewidths=1.4 if s["reg"] else 0.5, clip_on=False)
        kax.text(col + 0.02, row, str(s["num"]), ha="center", va="center", fontsize=5.0, color=tc, fontweight="bold")
        name = short_name(s["name"])
        kax.text(col + 0.06, row, name, ha="left", va="center", fontsize=6.9, color=INK)
        kax.text(col + 0.96, row, rank_words(s["oct_rank_base"], s["oct_n_base"]), ha="right", va="center",
                 fontsize=6.9, color=INK, fontweight="bold" if s["cat"] <= 1 else "normal")
    kax.text(0.06, -0.85, "Station", fontsize=6.6, color=INK2, ha="left", va="center")
    kax.text(0.96, -0.85, f"Oct 1–{end.day} rank since {b0}", fontsize=6.6, color=INK2, ha="right", va="center")
    if nrow < len(r):
        kax.text(1.06, -0.85, "Station", fontsize=6.6, color=INK2, ha="left", va="center")
        kax.text(1.96, -0.85, f"Oct 1–{end.day} rank since {b0}", fontsize=6.6, color=INK2, ha="right", va="center")

    # ---- legend
    counts = r["cat"].value_counts()
    handles = [Line2D([], [], ls="", marker="o", ms=8, mfc=fill, mec=INK2, mew=0.6,
                      label=f"{lab} ({int(counts.get(i, 0))})") for i, (lab, fill, _) in enumerate(C)]
    handles += [Line2D([], [], ls="", marker="o", ms=8, mfc="white", mec=INK, mew=1.8,
                       label="Thick black ring: flow or level partly set by a dam or weir")]
    fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.03, (foot - 0.05) / H), ncol=3, frameon=False,
               fontsize=7.0, handletextpad=0.3, columnspacing=1.2)
    fig.text(0.03, 0.06 / H,
             f"Data: Environment and Climate Change Canada, Water Survey of Canada. {ty} values are provisional and may "
             f"be revised.\nEach station is ranked only against its own record since {b0}; stations with shorter "
             f"records are ranked among fewer years.\nOther gauge sites might be influenced by the presence of a dam "
             f"or weir.",
             fontsize=6.2, color=INK2, ha="left", va="bottom")
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for ext in ("png", "pdf", "svg"):
        p = out / f"water_ranks_{ty}.{ext}"
        fig.savefig(p, dpi=300 if ext == "png" else None)
        paths[ext] = p
    plt.close(fig)
    r[["num", "station", "name", "param", "regulation", "cat", "oct_rank_base", "oct_n_base"]].to_csv(
        out / f"water_map_key_{ty}.csv", index=False)
    return paths
