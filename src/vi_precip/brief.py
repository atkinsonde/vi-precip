"""Briefing page: headline numbers, figures and station table in one self-contained HTML file.

Reads the tables written by the metrics stage; makes no downloads. Wording is deliberately
careful: ranks are 'since 2000' (the baseline), never 'on record'.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import domain
from .config import Config
from .geo import load_basemap
from .plots import anomaly as anomplots
from .plots import brief as briefplots

log = logging.getLogger(__name__)

REGION_LABEL = {
    "West VI": "West Vancouver Island", "East VI": "East Vancouver Island", "North VI": "North Vancouver Island",
    "Gulf Islands": "Gulf Islands", "Sunshine Coast": "Sunshine Coast", "Sea-to-Sky": "Sea-to-Sky",
    "Lower Mainland": "Lower Mainland",
}


def _ord(n: float) -> str:
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _rank_txt(rank: float, n: float) -> str:
    if not np.isfinite(rank):
        return ""
    if rank <= 1:
        return f"driest of {int(n)}"
    return f"{_ord(rank)} driest of {int(n)}"


def _fmt_date(s) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)) or s == "":
        return ""
    d = dt.date.fromisoformat(str(s)[:10])
    return f"{d:%b} {d.day}"


def _img(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def regional_table(fin: pd.DataFrame) -> pd.DataFrame:
    g = fin.groupby("region", observed=True)
    reg = pd.DataFrame({
        "n": g.size(),
        "sep": g["total_sep__pct_of_median"].median(),
        "oct": g["total_oct__pct_of_median"].median(),
        "window": g["total_window__pct_of_median"].median(),
        "driest30": g["driest30_mm__pct_of_median"].median() if "driest30_mm__pct_of_median" in fin else np.nan,
        "oct_top3": g["total_oct__rank_dry"].apply(lambda r: int((r <= 3).sum())),
    })
    order = [r for r in domain.REGIONS if r in reg.index]
    reg = reg.loc[order]
    reg.index = [REGION_LABEL.get(r, r) for r in reg.index]
    reg.index.name = "region"
    return reg


def run_brief(cfg: Config) -> Path:
    t = cfg.tables_dir
    y = cfg.target_year
    end = cfg.end_date
    wide = pd.read_csv(t / f"anomalies_{y}.csv", dtype={"unit_id": str})
    units = pd.read_csv(t / "units.csv", dtype={"unit_id": str})
    met = pd.read_csv(t / "metrics_unit_year.csv", dtype={"unit_id": str})
    t0 = cfg.wet_thresholds[0]
    tgt = met[(met["year"] == y) & (met["threshold"] == t0)].set_index("unit_id")
    for c in ("driest30_start", "driest30_end", "driest45_start", "driest45_end"):
        if c in tgt:
            wide[c] = wide["unit_id"].map(tgt[c])
    wide["region"] = pd.Categorical(wide["region"], categories=domain.REGIONS, ordered=True)
    fin = wide[wide["status"] == "final"].copy()
    prov = wide[wide["status"] == "provisional"].copy()
    out = cfg.out_dir / "brief"
    figs = out / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    endlab = f"{end:%b} {end.day}"

    # ---------------- figures
    basemap = load_basemap(cfg)
    bbox = tuple(cfg.domain["bbox"])
    maps = {}
    spec = [
        ("total_oct", f"Oct 1–{end.day}, {y}: precipitation compared with 2000–2025 (label: % of normal)"),
        ("total_window", f"Sep 1–{endlab}, {y}: precipitation compared with 2000–2025 (label: % of normal)"),
        ("total_sep", f"September {y}: precipitation compared with 2000–2025 (label: % of normal)"),
        ("driest30_mm", f"Driest 30 days between Sep 1 and {endlab}, {y}, compared with 2000–2025 (label: % of normal)"),
    ]
    mw = wide.copy()
    mw["region"] = mw["region"].astype(str)
    for metric, title in spec:
        if f"{metric}__pctl" in mw and mw[f"{metric}__pctl"].notna().any():
            maps[metric] = anomplots.percentile_map(mw, metric, basemap, bbox, figs / f"map_{metric}.png", title,
                                                    label_col=f"{metric}__pct_of_median")
    reg = regional_table(fin)
    bars = briefplots.regional_bars(reg, figs / "regional_bars.png", end)

    want = [str(s) for s in cfg.raw.get("brief", {}).get("stations", [])]
    avail = set(fin["unit_id"])
    chosen = [u for u in want if u in avail]
    if not chosen:
        best = fin[fin["tier"] == 1].sort_values("total_window__n_years", ascending=False)
        chosen = best.drop_duplicates("region")["unit_id"].tolist()[:8]
    proc = cfg.data_dir / "processed" / "daily"
    daily = {u: pd.read_parquet(proc / f"{u}.parquet") for u in chosen if (proc / f"{u}.parquet").exists()}
    names = dict(zip(wide["unit_id"], wide["name"].str.replace(" (composite)", "", regex=False).str.title()))
    cum = None
    if daily:
        cum = briefplots.cumulative_panels([(u, names[u]) for u in chosen if u in daily], daily,
                                           cfg.baseline_years, y, end, figs / "cumulative.png")

    # ---------------- headline numbers
    n = len(fin)
    o = fin["total_oct__pct_of_median"]
    orank = fin["total_oct__rank_dry"]
    w = fin["total_window__pct_of_median"]
    wrank = fin["total_window__rank_dry"]
    rec_oct = fin[orank <= 1]["name"].str.replace(" (composite)", "", regex=False).str.title().tolist()
    d30 = fin.get("driest30_mm__pct_of_median", pd.Series(dtype=float))
    d30r = fin.get("driest30_mm__rank_dry", pd.Series(dtype=float))
    wet26 = fin["n_wet_scaled__value"].median() if "n_wet_scaled__value" in fin else np.nan
    wetn = fin["n_wet_scaled__baseline_median"].median() if "n_wet_scaled__baseline_median" in fin else np.nan
    sp26 = fin["spell_max_win__value"].median()
    spn = fin["spell_max_win__baseline_median"].median()
    starts = pd.to_datetime(fin.get("driest30_start"), errors="coerce").dropna()
    head = {
        "n_final": n,
        "oct_median_pct": float(o.median()), "oct_min_pct": float(o.min()), "oct_max_pct": float(o.max()),
        "oct_n_rank1": int((orank <= 1).sum()), "oct_n_top3": int((orank <= 3).sum()), "oct_rank1_names": rec_oct,
        "sep_median_pct": float(fin["total_sep__pct_of_median"].median()),
        "window_median_pct": float(w.median()), "window_median_rank": float(wrank.median()),
        "window_median_nyears": float(fin["total_window__n_years"].median()),
        "window_rp_emp_median": float(fin["total_window__rp_emp"].median()),
        "driest30_median_pct": float(d30.median()) if len(d30) else np.nan,
        "driest30_n_rank1": int((d30r <= 1).sum()) if len(d30r) else 0,
        "driest30_n_top3": int((d30r <= 3).sum()) if len(d30r) else 0,
        "driest30_typical_start": (starts.dt.dayofyear.median() if len(starts) else np.nan),
        "wet_days_2026": float(wet26), "wet_days_normal": float(wetn),
        "spell_2026": float(sp26), "spell_normal": float(spn),
        "spell_n_top3": int((fin["spell_max_win__rank_dry"] <= 3).sum()),
        "n_provisional": len(prov),
        "a_N": float(fin["a_N__value"].median()) if "a_N__value" in fin else np.nan,
        "a_I": float(fin["a_I__value"].median()) if "a_I__value" in fin else np.nan,
        "driest_region": str(reg["oct"].idxmin()) if len(reg) else "",
        "driest_region_pct": float(reg["oct"].min()) if len(reg) else np.nan,
        "wettest_region": str(reg["oct"].idxmax()) if len(reg) else "",
        "wettest_region_pct": float(reg["oct"].max()) if len(reg) else np.nan,
    }
    (out / f"brief_{y}.json").write_text(json.dumps(head, indent=2, default=str))

    # ---------------- station table
    rows = []
    for _, r in fin.sort_values(["region", "total_oct__pct_of_median"]).iterrows():
        rows.append({
            "Station": r["name"].replace(" (composite)", "").title(), "Region": REGION_LABEL.get(str(r["region"]), str(r["region"])),
            f"Oct 1–{end.day} mm": r["total_oct__value"], "Oct % normal": r["total_oct__pct_of_median"],
            "Oct rank": _rank_txt(r["total_oct__rank_dry"], r["total_oct__n_years"]),
            f"Sep 1–{endlab} mm": r["total_window__value"], "Sep–Oct % normal": r["total_window__pct_of_median"],
            "Sep–Oct rank": _rank_txt(r["total_window__rank_dry"], r["total_window__n_years"]),
            "Driest 30 days mm": r.get("driest30_mm__value"),
            "Driest 30 days": f"{_fmt_date(r.get('driest30_start'))}–{_fmt_date(r.get('driest30_end'))}",
            "Driest-30 rank": _rank_txt(r.get("driest30_mm__rank_dry", np.nan), r.get("driest30_mm__n_years", np.nan)),
            "Longest dry spell (days)": r["spell_max_win__value"],
            "Dry-spell rank": _rank_txt(r["spell_max_win__rank_dry"], r["spell_max_win__n_years"]),
        })
    st = pd.DataFrame(rows)
    st.to_csv(out / f"brief_{y}_stations.csv", index=False)

    # ---------------- HTML
    vint = cfg.out_dir / "data_vintage.json"
    built = json.loads(vint.read_text()).get("built", "") if vint.exists() else ""
    page = _html(cfg, head, reg, st, prov, maps, bars, cum, built)
    path = out / f"brief_{y}.html"
    path.write_text(page, encoding="utf-8")
    log.info("Brief written to %s", path)
    return path


def _num(v, nd=0, pct=False, signed=False) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    s = f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"
    return s.replace("-", "−") + ("%" if pct else "")


def _html(cfg, h, reg, st, prov, maps, bars, cum, built) -> str:
    y, end = cfg.target_year, cfg.end_date
    endlab = f"{end:%B} {end.day}"
    e = html.escape
    rec = ", ".join(h["oct_rank1_names"]) if h["oct_rank1_names"] else "none"
    typical = ""
    if np.isfinite(h["driest30_typical_start"]):
        d0 = dt.date(y, 1, 1) + dt.timedelta(days=int(h["driest30_typical_start"]) - 1)
        typical = f" The driest 30-day stretch typically began around {d0:%B} {d0.day}."
    def adj(pct):
        if not np.isfinite(pct):
            return "unknown"
        return "very dry" if pct < 50 else "dry" if pct < 80 else "near normal" if pct <= 120 else "wet"

    rp = h["window_rp_emp_median"]
    nyw = h["window_median_nyears"]
    if np.isfinite(rp) and rp >= nyw:
        rarity = "the driest since 2000 at a typical station"
    elif np.isfinite(rp):
        rarity = f"roughly a 1-in-{rp:.0f}-year dry period at a typical station"
    else:
        rarity = ""
    if np.isfinite(h["a_N"]) and np.isfinite(h["a_I"]):
        if abs(h["a_N"]) >= abs(h["a_I"]):
            cause = (f"<b>Mostly fewer rainy days</b> rather than lighter rain: about {_num(h['wet_days_2026'])} days "
                     f"with at least 1 mm against a normal {_num(h['wet_days_normal'])} "
                     f"({_num(100 * (np.exp(h['a_N']) - 1), 0, True, True)} for rainy days, "
                     f"{_num(100 * (np.exp(h['a_I']) - 1), 0, True, True)} for rain per rainy day; medians across stations).")
        else:
            cause = (f"<b>Mostly lighter rain on rainy days</b> rather than fewer of them "
                     f"({_num(100 * (np.exp(h['a_I']) - 1), 0, True, True)} for rain per rainy day, "
                     f"{_num(100 * (np.exp(h['a_N']) - 1), 0, True, True)} for the number of rainy days; medians across stations).")
    else:
        cause = ""
    bullets = [
        f"<b>October 1–{end.day} was {adj(h['oct_median_pct'])}.</b> Across {h['n_final']} stations with complete records, "
        f"precipitation was a median <b>{_num(h['oct_median_pct'], 0, True)}</b> of normal (range "
        f"{_num(h['oct_min_pct'])}–{_num(h['oct_max_pct'], 0, True)}). It was among the three driest October 1–{end.day} "
        f"periods since 2000 at <b>{h['oct_n_top3']} of {h['n_final']}</b> stations, and the driest since 2000 at "
        f"{h['oct_n_rank1']} ({e(rec)}).",
        f"<b>September was {adj(h['sep_median_pct'])}</b> (median {_num(h['sep_median_pct'], 0, True)} of normal).",
        f"<b>September 1–{endlab} as a whole was {adj(h['window_median_pct'])}:</b> median "
        f"{_num(h['window_median_pct'], 0, True)} of normal, typically "
        f"{'the driest' if h['window_median_rank'] <= 1 else 'the ' + _ord(h['window_median_rank']) + ' driest'} of "
        f"{int(nyw)} years; {rarity}.",
        f"<b>The driest 30 days</b> between September 1 and {endlab} brought a median {_num(h['driest30_median_pct'], 0, True)} "
        f"of what each year’s driest 30 days normally bring, and ranked among the three driest since 2000 at "
        f"{h['driest30_n_top3']} of {h['n_final']} stations.{typical}",
        cause,
        f"<b>Dry spells:</b> the longest run of days with less than 1 mm was a median {_num(h['spell_2026'])} days against a "
        f"normal {_num(h['spell_normal'])}, among the three longest since 2000 at {h['spell_n_top3']} stations.",
        f"<b>Regionally</b>, October was driest in {e(h['driest_region'])} (median {_num(h['driest_region_pct'], 0, True)}) and "
        f"least dry in {e(h['wettest_region'])} (median {_num(h['wettest_region_pct'], 0, True)}).",
    ]
    bullets = [x for x in bullets if x]
    caveats = [
        "“Normal” here is each station’s 2000–2025 median for the same calendar dates, not Environment Canada’s 1991–2020 normals.",
        "Ranks and “driest” refer to the years 2000–2026 only. Several stations have records back to the 1890s–1940s that are not yet in this comparison, so these are not all-time records.",
        f"Recent data are preliminary. Staffed stations report late, so rain on the last days before {endlab} may be missing at some of them"
        + (f"; {h['n_provisional']} stations are held back as provisional for that reason and are shown separately." if h["n_provisional"] else "."),
        "This is precipitation only. Links to El Niño have not yet been analysed and should be described as context, not cause.",
    ]
    fig_html = []

    def fig(path, cap):
        if path:
            fig_html.append(f'<figure><img src="{_img(path)}" alt="{e(cap)}"><figcaption>{e(cap)}</figcaption></figure>')

    fig(maps.get("total_oct"), f"October 1–{end.day}: each dot is a station, coloured by how 2026 ranks among 2000–2025 (red = drier). Labels give % of normal. Diamonds are provisional.")
    fig(bars, "Median across stations in each region. 100% = the 2000–2025 median for the same dates.")
    fig(cum, "Grey lines are 2000–2025, the dashed line is their median, red is 2026. A flat red line means little or no rain.")
    fig(maps.get("total_window"), f"September 1–{endlab}, same layout as the October map.")
    fig(maps.get("driest30_mm"), "Driest 30-day stretch inside September 1 – " + endlab + ", compared with each year’s own driest 30 days.")
    fig(maps.get("total_sep"), "September alone, same layout.")

    def table(df: pd.DataFrame) -> str:
        cols = list(df.columns)
        head = "".join(f"<th>{e(c)}</th>" for c in cols)
        body = []
        for _, r in df.iterrows():
            cells = []
            for c in cols:
                v = r[c]
                if isinstance(v, (float, np.floating)):
                    v = _num(v, 0 if ("%" in c or "days" in c.lower()) else 1, "%" in c)
                cells.append(f"<td>{e(str(v))}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    regdf = reg.reset_index().rename(columns={
        "region": "Region", "n": "Stations", "sep": "September % normal", "oct": f"Oct 1–{end.day} % normal",
        "window": f"Sep 1–{end:%b} {end.day} % normal", "driest30": "Driest 30 days % normal",
        "oct_top3": "Stations with a top-3 driest October"})
    provdf = pd.DataFrame()
    if len(prov):
        provdf = pd.DataFrame({
            "Station": prov["name"].str.title(), "Region": prov["region"].astype(str).map(lambda r: REGION_LABEL.get(r, r)),
            f"Oct 1–{end.day} mm (at least)": prov["total_oct__value"],
            "Oct % normal (at least)": prov["total_oct__pct_of_median"],
        })
    css = """
    :root{--ink:#0b0b0b;--ink2:#52514e;--rule:#e4e3df;--bg:#fcfcfb;--accent:#c23a39}
    body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
    main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
    h1{font-size:26px;margin:0 0 4px} h2{font-size:18px;margin:32px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:4px}
    .meta{color:var(--ink2);font-size:13px} ul{padding-left:20px} li{margin:6px 0}
    figure{margin:20px 0} img{max-width:100%;height:auto;border:1px solid var(--rule)} figcaption{color:var(--ink2);font-size:13px}
    table{border-collapse:collapse;font-size:12.5px;width:100%;margin:8px 0}
    th,td{padding:4px 6px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
    th{background:#f4f3f0;position:sticky;top:0}
    .wrap{overflow-x:auto} .caveat{background:#f6f8fb;border-left:3px solid #86b6ef;padding:8px 12px;font-size:13.5px}
    @media print{h2{break-after:avoid} figure{break-inside:avoid}}
    """
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Dry Fall {y} Brief</title>
<style>{css}</style></head><body><main>
<h1>How unusual has the dry fall of {y} been?</h1>
<p class="meta">Vancouver Island, Gulf Islands, Sunshine Coast, Sea-to-Sky and Lower Mainland · Environment and Climate Change Canada daily observations · data through {endlab}, {y} · compared with 2000–2025 · built {e(built)}</p>
<h2>Main points</h2><ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul>
<div class="caveat"><b>Before quoting</b><ul>{''.join(f'<li>{e(c)}</li>' for c in caveats)}</ul></div>
<h2>By region</h2><div class="wrap">{table(regdf.round(0))}</div>
<h2>Figures</h2>{''.join(fig_html)}
<h2>Stations</h2><p class="meta">Final stations only (near-complete records for 2000–2025 and for {y}). Ranks count the years 2000–{y} with complete data at that station; “driest of 26” means no drier year in those 26.</p>
<div class="wrap">{table(st)}</div>
{f'<h2>Provisional stations</h2><p class="meta">These stations have good long-term records but have not yet reported every day of {y}; their totals are lower bounds.</p><div class="wrap">{table(provdf)}</div>' if len(provdf) else ''}
<p class="meta">Method: totals use days with valid observations; weekend accumulations at volunteer stations are handled as multi-day totals. A wet day has at least 1 mm. Percentages compare with the station’s own 2000–2025 median. Generated by vi-precip.</p>
</main></body></html>"""
