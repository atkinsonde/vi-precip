"""Shared figure styling. Colours follow one rule per job:
ordinal tiers -> one blue ramp (dark = better); magnitude -> sequential blue;
identity -> categorical slots in fixed order. Grids and axes stay recessive."""

import matplotlib as mpl

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
MUTED = "#8a8985"
GRID = "#e4e3df"
LAND = "#ecebe7"
COAST = "#b5b3ac"
WATER = "#f6f8fb"

SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
TIER_COLOR = {1: "#184f95", 2: "#5598e7", 3: "#ffffff"}
TIER_EDGE = {1: "#104281", 2: "#2a78d6", 3: MUTED}
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def apply():
    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_2,
            "axes.titlecolor": TEXT,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 8,
            "xtick.color": TEXT_2,
            "ytick.color": TEXT_2,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "font.size": 8,
            "legend.fontsize": 7,
            "legend.frameon": False,
            "font.family": "DejaVu Sans",
        }
    )
