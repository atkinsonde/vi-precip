"""Composite stations: candidate detection, proposals, accepted-join loading and series building.

Nothing is joined unless listed in config/composites.csv. Proposals are written for review.
See DESIGN.md §4.5.
"""

from __future__ import annotations

import datetime as dt
import difflib
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger(__name__)

_NAME_NOISE = {
    "A", "AUT", "AUTO", "CS", "CLIMATE", "INTL", "INT'L", "INTERNATIONAL", "AIRPORT", "RCS",
    "CDA", "MARINE", "CAPMON", "HEIGHTS", "STN", "STATION", "THE", "OF",
}


def _norm_name(s: str) -> str:
    toks = re.split(r"[^A-Z0-9']+", str(s).upper())
    return " ".join(t for t in toks if t and t not in _NAME_NOISE and not t.isdigit())


def name_similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio(), 3)


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def overlap_stats(a: pd.DataFrame, b: pd.DataFrame, thresholds: list[float], min_days: int) -> dict:
    """Agreement statistics over days where both annotated series are daily-valid."""
    both = pd.concat(
        {"a": a["precip"].where(a["daily_valid"]), "b": b["precip"].where(b["daily_valid"])}, axis=1
    ).dropna()
    out: dict = {"n_overlap_valid_days": int(len(both))}
    if len(both) < min_days:
        return out
    x, y = both["a"], both["b"]
    full = pd.concat(
        {"a": a["precip"].where(a["daily_valid"]), "b": b["precip"].where(b["daily_valid"])}, axis=1
    )
    for lag in (-1, 0, 1):
        pair = pd.concat([full["a"], full["b"].shift(lag)], axis=1).dropna()
        r = pair.corr().iloc[0, 1] if len(pair) >= min_days and pair.std().min() > 0 else np.nan
        out[f"corr_lag{lag:+d}"] = round(float(r), 3) if pd.notna(r) else np.nan
    lags = {k: v for k, v in out.items() if k.startswith("corr_lag") and pd.notna(v)}
    out["best_lag"] = int(max(lags, key=lags.get).replace("corr_lag", "")) if lags else np.nan
    out["ratio_total_b_over_a"] = round(float(y.sum() / x.sum()), 3) if x.sum() > 0 else np.nan
    for t in thresholds:
        out[f"wetfreq_a_ge{t:g}"] = round(float((x >= t).mean()), 3)
        out[f"wetfreq_b_ge{t:g}"] = round(float((y >= t).mean()), 3)
    return out


def find_candidates(
    stations: pd.DataFrame,
    spans: pd.DataFrame,
    annotated: dict[str, pd.DataFrame],
    cfg: Config,
) -> pd.DataFrame:
    """Candidate pairs for joining. `spans` has climate_id, data_first, data_last (may be NaT)."""
    c = cfg.composites
    st = stations.merge(spans, on="climate_id", how="left")
    st["first"] = st["data_first"].fillna(st["dly_first"])
    st["last"] = st["data_last"].fillna(st["dly_last"])
    st["span_source"] = np.where(st["data_first"].notna(), "data", "metadata")
    st = st.dropna(subset=["first", "last"]).reset_index(drop=True)
    n = len(st)
    if n < 2:
        return pd.DataFrame()
    lon, lat, el = st["lon"].to_numpy(), st["lat"].to_numpy(), st["elev_m"].to_numpy(dtype=float)
    rows = []
    max_ov = pd.Timedelta(days=365.25 * float(c["max_overlap_years"]))
    max_gap = pd.Timedelta(days=365.25 * float(c["max_gap_years"]))
    for i in range(n - 1):
        d = haversine_km(lon[i], lat[i], lon[i + 1 :], lat[i + 1 :])
        for k in np.flatnonzero(d <= float(c["review_max_km"])):
            j = i + 1 + k
            dz = abs(el[i] - el[j]) if np.isfinite(el[i]) and np.isfinite(el[j]) else np.nan
            if np.isfinite(dz) and dz > float(c["review_max_dz_m"]):
                continue
            A, B = (st.iloc[i], st.iloc[j]) if st.at[i, "first"] <= st.at[j, "first"] else (st.iloc[j], st.iloc[i])
            overlap = min(A["last"], B["last"]) - B["first"] + pd.Timedelta(days=1)
            overlap = max(overlap, pd.Timedelta(0))
            gap = B["first"] - A["last"] - pd.Timedelta(days=1) if B["first"] > A["last"] else pd.Timedelta(0)
            if B["last"] <= A["last"]:
                temporal = "contained"  # B lies inside A's span: a side record, not a continuation
            elif overlap > max_ov:
                temporal = "parallel"
            elif gap > max_gap:
                temporal = "disjoint"
            else:
                temporal = "complementary"
            strict = d[k] <= float(c["join_max_km"]) and (not np.isfinite(dz) or dz <= float(c["join_max_dz_m"]))
            if strict and temporal == "complementary":
                suggestion = "join"
            elif strict or temporal == "complementary":
                suggestion = "review"
            else:
                continue
            row = {
                "id_a": A["climate_id"], "name_a": A["name"], "id_b": B["climate_id"], "name_b": B["name"],
                "region": A["region"], "dist_km": round(float(d[k]), 3),
                "dz_m": round(float(dz), 1) if np.isfinite(dz) else np.nan,
                "first_a": A["first"].date(), "last_a": A["last"].date(),
                "first_b": B["first"].date(), "last_b": B["last"].date(),
                "span_source": "data" if A["span_source"] == B["span_source"] == "data" else "metadata",
                "overlap_days": int(overlap.days), "gap_days": int(gap.days),
                "temporal": temporal, "spatial": "strict" if strict else "loose",
                "name_similarity": name_similarity(A["name"], B["name"]),
                "suggestion": suggestion,
            }
            if A["climate_id"] in annotated and B["climate_id"] in annotated:
                row.update(
                    overlap_stats(annotated[A["climate_id"]], annotated[B["climate_id"]],
                                  cfg.wet_thresholds, int(c["min_overlap_days_for_stats"]))
                )
            # Disagreement over a real overlap demotes an automatic join.
            r0, ratio = row.get("corr_lag+0"), row.get("ratio_total_b_over_a")
            row["overlap_disagrees"] = bool(
                row.get("n_overlap_valid_days", 0) >= int(c["min_overlap_days_for_stats"])
                and ((pd.notna(r0) and r0 < float(c.get("min_overlap_corr", 0.8)))
                     or (pd.notna(ratio) and abs(ratio - 1) > float(c.get("max_overlap_ratio_dev", 0.1))))
            )
            if row["suggestion"] == "join" and row["overlap_disagrees"]:
                row["suggestion"] = "review"
            rows.append(row)
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["suggestion", "region", "dist_km"]).reset_index(drop=True)
    return out


def propose(candidates: pd.DataFrame, spans: pd.DataFrame) -> pd.DataFrame:
    """Group 'join' pairs into chains; default switch rule: newer member takes over from its first valid day."""
    cols = ["composite_id", "order", "climate_id", "name", "use_from", "use_to", "note"]
    if candidates.empty:
        return pd.DataFrame(columns=cols)
    j = candidates[candidates["suggestion"] == "join"]
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(j["id_a"], j["id_b"]):
        parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    names = dict(zip(candidates["id_a"], candidates["name_a"])) | dict(zip(candidates["id_b"], candidates["name_b"]))
    firsts = dict(zip(candidates["id_a"], candidates["first_a"])) | dict(zip(candidates["id_b"], candidates["first_b"]))
    lasts = dict(zip(candidates["id_a"], candidates["last_a"])) | dict(zip(candidates["id_b"], candidates["last_b"]))
    rows = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda m: firsts[m])
        cid = f"C_{members[0]}"
        for k, m in enumerate(members):
            use_from = firsts[m] if k > 0 else None
            use_to = firsts[members[k + 1]] - dt.timedelta(days=1) if k + 1 < len(members) else None
            if use_to is not None and lasts[m] < use_to:
                use_to = lasts[m]
            rows.append({
                "composite_id": cid, "order": k + 1, "climate_id": m, "name": names[m],
                "use_from": use_from, "use_to": use_to,
                "note": "auto-proposed; copy accepted rows to config/composites.csv",
            })
    return pd.DataFrame(rows, columns=cols)


def load_accepted(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["composite_id", "order", "climate_id", "use_from", "use_to"])
    df = pd.read_csv(path, dtype=str).fillna("")
    df = df[df["composite_id"].str.strip() != ""].copy()
    if df.empty:
        return df
    df["order"] = df["order"].astype(int)
    for c in ("use_from", "use_to"):
        df[c] = pd.to_datetime(df[c].replace("", None), errors="coerce")
    return df.sort_values(["composite_id", "order"]).reset_index(drop=True)


def build_series(members: pd.DataFrame, daily: dict[str, pd.DataFrame], composite_id: str) -> pd.DataFrame:
    """Splice standardized daily frames according to member use_from/use_to (inclusive bounds)."""
    parts = []
    for _, m in members.sort_values("order").iterrows():
        d = daily.get(m["climate_id"])
        if d is None or d.empty:
            log.warning("%s: member %s has no data", composite_id, m["climate_id"])
            continue
        sel = pd.Series(True, index=d.index)
        if pd.notna(m.get("use_from")):
            sel &= d["date"] >= pd.Timestamp(m["use_from"])
        if pd.notna(m.get("use_to")):
            sel &= d["date"] <= pd.Timestamp(m["use_to"])
        parts.append(d[sel].assign(source_id=m["climate_id"]))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True).sort_values("date")
    # Later members win on any residual overlap (consistent with the 'newer' rule).
    out = out.drop_duplicates("date", keep="last").reset_index(drop=True)
    out["climate_id"] = composite_id
    return out
