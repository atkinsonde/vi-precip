/* vi-precip dashboard. Plain JavaScript + Plotly; all data precomputed in window.VIP. */
(function () {
  "use strict";
  const V = window.VIP || {};
  const M = V.meta || {};
  const $ = (id) => document.getElementById(id);
  const fin = (x) => x !== null && x !== undefined && Number.isFinite(x);
  const fmt = (x, d = 0) => (fin(x) ? x.toFixed(d) : "–");
  const sgn = (x, d = 1) => (fin(x) ? (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d) : "–");
  const ord = (n) => { n = Math.round(n); const s = (n % 100 >= 11 && n % 100 <= 13) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"); return n + s; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const median = (a) => { a = a.filter(fin).sort((x, y) => x - y); if (!a.length) return NaN; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const rendered = {};

  // ------------------------------------------------------------ theme
  const root = document.documentElement;
  try { const t = localStorage.getItem("vip-theme"); if (t) root.dataset.theme = t; } catch (e) { /* storage blocked */ }
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("vip-theme", root.dataset.theme); } catch (e) { /* ignore */ }
    for (const k in rendered) rendered[k] = false;
    show(current);
  });

  function C() {
    return {
      ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), grid: css("--grid"), axis: css("--axis"),
      surface: css("--surface"), page: css("--page"), accent: css("--accent"), target: css("--target"),
      band: css("--band"), band2: css("--band-2"),
      div: [css("--dry-3"), css("--dry-2"), css("--dry-1"), css("--mid"), css("--wet-1"), css("--wet-2"), css("--wet-3")],
    };
  }
  function divScale(rev) {
    const d = C().div.slice();
    if (rev) d.reverse();
    return d.map((c, i) => [i / (d.length - 1), c]);
  }
  function baseLayout(extra) {
    const c = C();
    return Object.assign({
      paper_bgcolor: c.surface, plot_bgcolor: c.surface, font: { family: "system-ui, -apple-system, Segoe UI, sans-serif", size: 12, color: c.ink2 },
      margin: { l: 52, r: 14, t: 10, b: 40 }, hoverlabel: { bgcolor: c.surface, bordercolor: c.axis, font: { color: c.ink } },
      xaxis: { gridcolor: c.grid, linecolor: c.axis, zerolinecolor: c.axis, tickcolor: c.axis },
      yaxis: { gridcolor: c.grid, linecolor: c.axis, zerolinecolor: c.axis, tickcolor: c.axis },
      showlegend: false,
    }, extra || {});
  }
  const cfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };
  function plot(id, data, layout) { Plotly.react($(id), data, layout, cfg); }

  // ------------------------------------------------------------ maps (cartesian lon/lat with Natural Earth land)
  function basemapTraces(bm) {
    const c = C();
    const tr = [];
    if (!bm) return tr;
    const join = (rings) => { const x = [], y = []; for (const r of rings) { for (const p of r) { x.push(p[0]); y.push(p[1]); } x.push(null); y.push(null); } return { x, y }; };
    if (bm.land && bm.land.length) {
      const j = join(bm.land);
      tr.push({ x: j.x, y: j.y, type: "scatter", mode: "lines", fill: "toself", fillcolor: c.page === "#0d0d0d" ? "#2a2a28" : "#ecebe7",
        line: { width: 0 }, hoverinfo: "skip" });
    }
    if (bm.coast && bm.coast.length) {
      const j = join(bm.coast);
      tr.push({ x: j.x, y: j.y, type: "scatter", mode: "lines", line: { width: 0.6, color: c.axis }, hoverinfo: "skip" });
    }
    return tr;
  }
  function mapLayout(bm, lons, lats) {
    let x0, x1, y0, y1;
    const lo = lons.filter(fin), la = lats.filter(fin);
    if (lo.length) { x0 = Math.min(...lo) - 0.25; x1 = Math.max(...lo) + 0.25; y0 = Math.min(...la) - 0.15; y1 = Math.max(...la) + 0.15; }
    else if (bm && bm.bbox) { [x0, y0, x1, y1] = bm.bbox; }
    const lat0 = ((y0 || 49) + (y1 || 50)) / 2;
    const c = C();
    return baseLayout({
      margin: { l: 4, r: 4, t: 4, b: 4 }, plot_bgcolor: c.page === "#0d0d0d" ? "#141e2a" : "#eef3f8",
      xaxis: { range: [x0, x1], visible: false, constrain: "domain" },
      yaxis: { range: [y0, y1], visible: false, scaleanchor: "x", scaleratio: 1 / Math.cos(lat0 * Math.PI / 180) },
      dragmode: "pan",
    });
  }

  // ------------------------------------------------------------ helpers for data
  const P = V.precip, T = V.temp, W = V.water, E = V.enso;
  const thrs = (M.thresholds || [1, 0.2]).map((t) => String(+t));
  const thr0 = thrs[0];
  function pv(metric, thr, field) {
    const v = P && P.values[metric + "|" + thr];
    return v ? v[field] : null;
  }
  function metricInfo(key) { return (P.metrics || []).find((m) => m.key === key) || { key, label: key, unit: "", dry: "low" }; }
  function rankText(r, n, word) {
    if (!fin(r)) return "–";
    if (r <= 1) return word + " of " + n;
    return ord(r) + " " + word + " of " + n;
  }
  function fillSelect(sel, opts, value) {
    sel.innerHTML = opts.map((o) => `<option value="${esc(o[0])}">${esc(o[1])}</option>`).join("");
    if (value !== undefined && opts.some((o) => String(o[0]) === String(value))) sel.value = value;
  }
  function table(el, cols, rows) {
    const head = "<tr>" + cols.map((c, i) => `<th data-i="${i}" class="${c.n ? "n" : ""}">${esc(c.t)}</th>`).join("") + "</tr>";
    const body = rows.map((r) => "<tr>" + cols.map((c) => `<td class="${c.n ? "n" : ""}">${c.f ? c.f(r) : esc(r[c.k])}</td>`).join("") + "</tr>").join("");
    el.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
    let dir = 1, last = -1;
    el.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
      const i = +th.dataset.i, c = cols[i];
      dir = last === i ? -dir : 1; last = i;
      const key = c.s || c.k;
      const sorted = rows.slice().sort((a, b) => {
        const x = typeof key === "function" ? key(a) : a[key], y = typeof key === "function" ? key(b) : b[key];
        if (x == null) return 1; if (y == null) return -1;
        return (x > y ? 1 : x < y ? -1 : 0) * dir;
      });
      table(el, cols, sorted);
    }));
  }
  function loadStation(uid) {
    return new Promise((res) => {
      const key = "st_" + uid;
      if (V[key]) return res(V[key]);
      const s = document.createElement("script");
      s.src = "data/stations/" + encodeURIComponent(uid) + ".js";
      s.onload = () => res(window.VIP[key]);
      s.onerror = () => res(null);
      document.head.appendChild(s);
    });
  }
  function unitRows() {
    const u = P.units;
    return u.unit_id.map((id, i) => ({ i, id, name: u.name[i], region: u.region[i], lon: u.lon[i], lat: u.lat[i], elev: u.elev_m[i],
      tier: u.tier[i], status: u.status[i], kind: u.kind[i], members: u.members[i], first: u.first_valid_year[i] }));
  }

  // ------------------------------------------------------------ header
  const B = M.baseline || [2000, 2025];
  $("vintage").textContent = `${M.window_label || ""}, ${M.target_year || ""} · normal = ${B[0]}–${B[1]} · data through ${M.end_date || "?"}` +
    (M.vintage && M.vintage.built ? ` · downloaded ${M.vintage.built.slice(0, 16).replace("T", " ")}` : "");
  $("foot-built").textContent = "Page built " + (M.built || "").replace("T", " ") + ".";
  if (M.synthetic) $("synthetic").innerHTML = '<div class="banner"><b>Synthetic test data.</b> This build was made from simulated services for testing the page; none of the numbers are real.</div>';

  // ------------------------------------------------------------ SUMMARY
  function pctColour(p) { // % of median -> diverging colour value (log2 ratio)
    return fin(p) && p > 0 ? Math.max(-2, Math.min(2, Math.log2(p / 100))) : null;
  }
  function renderSummary() {
    const h = M.headline || {};
    const tiles = [];
    const hp = h.precip, ht = h.temp, hw = h.water, he = h.enso;
    if (hp) {
      tiles.push({ k: `Precipitation, ${M.oct_label}`, v: fmt(hp.oct_pct_median) + "%", s: `of normal (median of ${hp.n} stations). ${hp.oct_top3} of ${hp.n} had one of their 3 driest since ${B[0]}.` });
      tiles.push({ k: `Precipitation, ${M.window_label}`, v: fmt(hp.window_pct_median) + "%", s: `of normal; typically ${fin(hp.window_rank_median) ? ord(hp.window_rank_median) : "–"} driest of ${hp.n_years} years` + (fin(hp.window_rp_median) ? (hp.window_rp_median > 100 ? ", rarer than 1-in-100 years by a fitted distribution." : `, roughly a 1-in-${fmt(hp.window_rp_median)}-year event.`) : ".") });
      if (fin(hp.wet_days)) tiles.push({ k: `Wet days (≥ ${thr0} mm), ${M.window_label}`, v: fmt(hp.wet_days), s: `against a normal of ${fmt(hp.wet_days_normal)} (station median).` });
    }
    if (ht) tiles.push({ k: `Daytime highs, ${M.oct_label}`, v: sgn(ht.tmax_oct_anom) + " °C", s: `compared with normal (station median); above normal at ${ht.tmax_oct_warmer} of ${ht.n} stations. Nights: ${sgn(ht.tmin_oct_anom)} °C.` });
    if (hw) tiles.push({ k: `River flow, ${M.oct_label}`, v: fmt(hw.flow_pct_median) + "%", s: `of normal (median gauge). ${hw.n_lowest} lowest and ${hw.n_top3 - hw.n_lowest} 2nd–3rd lowest since ${hw.baseline_start}, of ${hw.n} sites.` });
    if (he && he.index) tiles.push({ k: "Tropical Pacific", v: esc(he.index.class), s: `${(E && E.index_labels[he.index.name]) || he.index.name}, ${he.index.season}: ${sgn(he.index.value, 2)} °C` });
    $("sum-tiles").innerHTML = tiles.map((t) => `<div class="card tile"><div class="k">${esc(t.k)}</div><div class="v">${t.v}</div><div class="s">${esc(t.s)}</div></div>`).join("");
    if (hp) {
      $("sum-lead").innerHTML = `From October 1 to ${M.end_label}, ${M.target_year}, the South Coast received about <b>${fmt(hp.oct_pct_median)}% of its normal precipitation</b>` +
        (fin(hp.oct_pct_median_vi) ? ` (Vancouver Island stations: ${fmt(hp.oct_pct_median_vi)}%)` : "") +
        `. ` + (fin(hp.sep_pct_median) ? (hp.sep_pct_median >= 85 ? `September was close to normal (${fmt(hp.sep_pct_median)}%), so the dryness is mainly an October story. ` : `September was also drier than normal (${fmt(hp.sep_pct_median)}%). `) : "") +
        `“Normal” here means the median of ${B[0]}–${B[1]}.`;
    } else $("sum-lead").textContent = "Precipitation results are not available in this build.";
    // map
    if (P) {
      const u = unitRows(), pct = pv("total_oct", thr0, "pct_of_median"), rk = pv("total_oct", thr0, "rank_dry"), n = pv("total_oct", thr0, "n_years"), val = pv("total_oct", thr0, "value");
      const keep = u.filter((r) => fin(pct[r.i]));
      const bm = M.basemaps && M.basemaps.precip;
      const tr = basemapTraces(bm);
      tr.push({
        x: keep.map((r) => r.lon), y: keep.map((r) => r.lat), type: "scatter", mode: "markers",
        marker: { size: 13, color: keep.map((r) => pctColour(pct[r.i])), colorscale: divScale(false), cmin: -2, cmax: 2,
          line: { width: 1, color: C().surface } },
        customdata: keep.map((r) => [r.name, fmt(pct[r.i]), rankText(rk[r.i], n[r.i], "driest"), fmt(val[r.i]), r.id]),
        hovertemplate: "<b>%{customdata[0]}</b><br>%{customdata[1]}% of normal (%{customdata[3]} mm)<br>%{customdata[2]} since " + B[0] + "<extra></extra>",
      });
      $("sum-map-title").textContent = `Precipitation, ${M.oct_label}, ${M.target_year}: % of normal`;
      $("sum-map-legend").innerHTML = pctLegend();
      plot("sum-map", tr, mapLayout(bm, keep.map((r) => r.lon), keep.map((r) => r.lat)));
      if ($("sum-map").removeAllListeners) $("sum-map").removeAllListeners("plotly_click");
      $("sum-map").on("plotly_click", (ev) => { const p = ev.points.find((q) => q.customdata); if (p) gotoStation(p.customdata[4]); });
      // station selector
      const opts = u.filter((r) => r.tier === 1 && r.status === "final").map((r) => [r.id, `${r.name} (${r.region})`]);
      const pref = ["C_1018620", "C_1108447", "1021830"].find((id) => opts.some((o) => o[0] === id)) || (opts[0] || [])[0];
      fillSelect($("sum-st"), opts, pref);
      $("sum-st").onchange = () => cumChart("sum-cum", $("sum-st").value, "sum-cum-note");
      cumChart("sum-cum", $("sum-st").value, "sum-cum-note");
    }
    // ENSO plain language
    const se = $("sum-enso");
    if (!he) { se.innerHTML = '<p class="note">The ENSO analysis is not part of this build.</p>'; return; }
    const pr = he.primary || {}, s = pr.short, l = pr.long;
    const ex = (he.expected || {}).short || (he.expected || {}).long;
    let t = `<p>The tropical Pacific is in <b>${esc(he.index.class)}</b> conditions (${esc((E && E.index_labels[he.index.name]) || he.index.name)}, Jul–Sep: ${sgn(he.index.value, 1)} °C). `;
    const verdict = (r) => !r || !fin(r.rho) ? "could not be tested" :
      (r.p_one_sided <= 0.05 ? `shows a clear tendency for drier autumns in El Niño years (ρ = ${sgn(r.rho, 2)}, p = ${fmt(r.p_one_sided, 3)})` :
        r.p_one_sided <= 0.15 ? `shows a weak, uncertain tendency for drier autumns in El Niño years (ρ = ${sgn(r.rho, 2)}, p = ${fmt(r.p_one_sided, 2)})` :
          `shows no reliable link between El Niño and Sep–Oct precipitation (ρ = ${sgn(r.rho, 2)}, p = ${fmt(r.p_one_sided, 2)})`);
    t += `Over ${s ? s.year_first + "–" + s.year_last : "the reference years"}, the island record ${verdict(s)}`;
    if (l && fin(l.rho)) t += `; the longer record (${l.year_first}–${l.year_last}) ${verdict(l)}`;
    t += ".</p>";
    if (ex && fin(ex.target_expected) && fin(ex.target_observed)) {
      const zq = (z) => z < -1.3 ? "much drier than normal" : z < -0.5 ? "drier than normal" : z <= 0.5 ? "near normal" : "wetter than normal";
      t += `<p>Given this year's ENSO state alone, the expected autumn was <b>${zq(ex.target_expected)}</b>; what happened was <b>${zq(ex.target_observed)}</b>. ` +
        (fin(ex.target_rp_cond) && fin(ex.target_rp_uncond) ? `Taking ENSO into account, a season this dry is ${rpTxt(ex.target_rp_cond)}, compared with ${rpTxt(ex.target_rp_uncond)} without it (normal-distribution estimates, rough in the tails). ` : "") +
        `ENSO explains about ${fmt(100 * (ex.r2 || 0))}% of the year-to-year variation in this measure, so most of what happens in any one autumn comes from weather patterns that ENSO does not control.</p>`;
    }
    t += '<p class="note">Details, other indices and seasons, and station-by-station results are in the ENSO tab.</p>';
    se.innerHTML = t;
  }
  function rpTxt(r) { return !fin(r) ? "–" : r > 200 ? "rarer than 1-in-200" : r < 2 ? "common (about 1-in-" + fmt(r, 1) + ")" : "about a 1-in-" + fmt(r) + " event"; }
  function pctLegend() {
    const c = C().div;
    const labs = ["≤ 25%", "50%", "70%", "100%", "140%", "200%", "≥ 400%"];
    return labs.map((l, i) => `<span><span class="sw" style="background:${c[i]}"></span>${l}</span>`).join("");
  }

  async function cumChart(div, uid, noteId) {
    const s = await loadStation(uid);
    const c = C();
    if (!s || !s.cumulative || !s.cumulative.p50) { $(div).innerHTML = '<div class="empty">No daily series for this station.</div>'; return; }
    const q = s.cumulative, n = q.ndays;
    const x = Array.from({ length: n }, (_, i) => new Date(Date.UTC(M.target_year, 8, 1 + i)).toISOString().slice(0, 10));
    const tr = [
      { x, y: q.max, mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { x, y: q.min, mode: "lines", fill: "tonexty", fillcolor: c.band2, line: { width: 0 }, hoverinfo: "skip", name: "driest to wettest year" },
      { x, y: q.p90, mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { x, y: q.p10, mode: "lines", fill: "tonexty", fillcolor: c.band, line: { width: 0 }, name: "middle 80% of years", hoverinfo: "skip" },
      { x, y: q.p50, mode: "lines", line: { width: 1.5, color: c.ink2, dash: "dash" }, name: "median", hovertemplate: "median %{y:.0f} mm<extra></extra>" },
    ];
    if (q.target) tr.push({ x: x.slice(0, q.target.length), y: q.target, mode: "lines", line: { width: 2.6, color: c.target }, name: String(M.target_year), hovertemplate: M.target_year + ": %{y:.0f} mm<extra></extra>" });
    const lay = baseLayout({ hovermode: "x unified", yaxis: Object.assign(baseLayout().yaxis, { title: { text: "mm since Sep 1" }, rangemode: "tozero" }),
      xaxis: Object.assign(baseLayout().xaxis, { tickformat: "%b %-d", tickvals: [0, 14, 30, 44, 60].filter((k) => k < n).map((k) => x[k]) }), showlegend: true, legend: { orientation: "h", y: 1.08, x: 0, font: { size: 11 } } });
    const last = q.target ? q.target[q.target.length - 1] : null, med = q.p50[n - 1];
    lay.annotations = [];
    if (fin(last)) lay.annotations.push({ x: x[q.target.length - 1], y: last, text: `${fmt(last)} mm (${fmt(100 * last / med)}% of median)`, showarrow: false, xanchor: "right", yanchor: "bottom", font: { color: c.target, size: 12 } });
    plot(div, tr, lay);
    if (noteId) $(noteId).textContent = `Grey: range and middle 80% of ${q.n_years} reference years (${B[0]}–${B[1]}); dashed: median.` + (q.target_missing ? ` ${q.target_missing} day(s) missing this year.` : "");
  }

  // ------------------------------------------------------------ MAPS
  function initMaps() {
    const vars = [];
    if (P) vars.push(["precip", "Precipitation"]);
    if (T) vars.push(["temp", "Temperature"]);
    fillSelect($("m-var"), vars);
    fillSelect($("m-thr"), thrs.map((t) => [t, t + " mm"]));
    $("m-var").onchange = () => { mapMetricOptions(); renderMaps(); };
    ["m-metric", "m-thr", "m-stat"].forEach((id) => ($(id).onchange = renderMaps));
    mapMetricOptions();
  }
  function mapMetricOptions() {
    const v = $("m-var").value;
    if (v === "precip") {
      fillSelect($("m-metric"), P.metrics.map((m) => [m.key, m.label]), "total_oct");
      fillSelect($("m-stat"), [["pct", "% of normal"], ["rank", "Rank (driest = 1)"], ["spi", "SPI"], ["diff", "Difference from normal"], ["value", "Value"]]);
      $("m-thr-wrap").style.display = "";
    } else {
      const opts = [];
      for (const k of ["tmax", "tmin", "dtr"]) for (const [p, pl] of [["oct", M.oct_label], ["window", M.window_label], ["sep", "September"]]) opts.push([k + "|" + p, `${T.vars[k]}, ${pl}`]);
      fillSelect($("m-metric"), opts, "tmax|oct");
      fillSelect($("m-stat"), [["anom", "Anomaly (°C)"], ["rank", "Rank (warmest = 1)"], ["z", "Standardised anomaly"]]);
      $("m-thr-wrap").style.display = "none";
    }
  }
  function mapData() {
    const v = $("m-var").value, metric = $("m-metric").value, stat = $("m-stat").value, thr = $("m-thr").value;
    const out = [];
    if (v === "precip") {
      const mi = metricInfo(metric), g = P.values[metric + "|" + thr];
      if (!g) return { rows: [], mi };
      const u = unitRows();
      const flip = mi.dry === "high"; // for spells etc., "dry" = high value
      for (const r of u) {
        const val = g.value[r.i]; if (!fin(val)) continue;
        let c, label;
        if (stat === "pct") { c = pctColour(g.pct_of_median[r.i]); if (flip && c !== null) c = -c; label = fmt(g.pct_of_median[r.i]) + "%"; }
        else if (stat === "rank") { const n = g.n_years[r.i], rk = g.rank_dry[r.i]; c = fin(rk) ? -2 + 4 * (rk - 1) / Math.max(1, n - 1) : null; label = rankText(rk, n, "driest"); }
        else if (stat === "spi") { c = fin(g.spi[r.i]) ? Math.max(-2, Math.min(2, g.spi[r.i])) : null; if (flip && c !== null) c = -c; label = sgn(g.spi[r.i], 2); }
        else if (stat === "diff") { const d = g.diff_from_median[r.i]; const sd = Math.max(1e-9, Math.abs(g.baseline_median[r.i] || 1)); c = fin(d) ? Math.max(-2, Math.min(2, 2 * d / sd)) : null; if (flip && c !== null) c = -c; label = sgn(d, 1) + " " + mi.unit; }
        else { c = pctColour(g.pct_of_median[r.i]); if (flip && c !== null) c = -c; label = fmt(val, 1) + " " + mi.unit; }
        out.push({ ...r, c, label, val, med: g.baseline_median[r.i], rank: g.rank_dry[r.i], n: g.n_years[r.i], pct: g.pct_of_median[r.i], spi: g.spi[r.i], rp: g.rp_fit[r.i], rec: g.record_dry[r.i] });
      }
      return { rows: out, mi, rev: false };
    }
    const [k, per] = metric.split("|"), g = T.values[k + "|" + per];
    if (!g) return { rows: [] };
    const u = T.units;
    u.unit_id.forEach((id, i) => {
      if (!fin(g.value[i])) return;
      let c, label;
      if (stat === "anom") { c = Math.max(-2, Math.min(2, g.anom[i] / 1.5)); label = sgn(g.anom[i]) + " °C"; }
      else if (stat === "rank") { c = 2 - 4 * (g.rank_high[i] - 1) / Math.max(1, g.n_years[i] - 1); label = rankText(g.rank_high[i], g.n_years[i], "warmest"); }
      else { c = Math.max(-2, Math.min(2, g.z[i])); label = sgn(g.z[i], 2); }
      out.push({ id, name: u.name[i], region: u.region[i], lon: u.lon[i], lat: u.lat[i], c, label, val: g.value[i], med: g.base_mean[i], rank: g.rank_high[i], n: g.n_years[i], anom: g.anom[i], z: g.z[i] });
    });
    return { rows: out, mi: { label: $("m-metric").selectedOptions[0].text, unit: "°C" }, rev: true };
  }
  function renderMaps() {
    const d = mapData(), rows = d.rows, v = $("m-var").value;
    const bm = M.basemaps && M.basemaps.precip;
    const tr = basemapTraces(bm);
    tr.push({ x: rows.map((r) => r.lon), y: rows.map((r) => r.lat), type: "scatter", mode: "markers",
      marker: { size: 13, color: rows.map((r) => r.c), colorscale: divScale(d.rev), cmin: -2, cmax: 2, line: { width: 1, color: C().surface } },
      customdata: rows.map((r) => [r.name, r.label, r.region, r.id]), hovertemplate: "<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{customdata[2]}<extra></extra>" });
    plot("m-map", tr, mapLayout(bm, rows.map((r) => r.lon), rows.map((r) => r.lat)));
    if ($("m-map").removeAllListeners) $("m-map").removeAllListeners("plotly_click");
    $("m-map").on("plotly_click", (ev) => { const p = ev.points.find((q) => q.customdata); if (p) gotoStation(p.customdata[3]); });
    $("m-legend").innerHTML = v === "precip" ? (($("m-stat").value === "rank") ? "<span>Colour: rank (red = driest end, blue = wettest end)</span>" : pctLegend()) :
      "<span>Colour: blue = cooler, red = warmer than normal</span>";
    // ranked bars
    const sorted = rows.slice().sort((a, b) => (a.c ?? 0) - (b.c ?? 0));
    if (d.rev) sorted.reverse();
    const c = C();
    const sc = divScale(d.rev);
    const colourOf = (cv) => sc[Math.round(((Math.max(-2, Math.min(2, cv ?? 0)) + 2) / 4) * (sc.length - 1))][1];
    const barVal = (r) => v === "precip" ? ($("m-stat").value === "rank" ? r.rank : $("m-stat").value === "spi" ? r.spi : r.pct) : ($("m-stat").value === "rank" ? r.rank : $("m-stat").value === "z" ? r.z : r.anom);
    plot("m-bars", [{ type: "bar", orientation: "h", y: sorted.map((r) => r.name), x: sorted.map(barVal), marker: { color: sorted.map((r) => colourOf(r.c)), line: { width: 0 } },
      customdata: sorted.map((r) => r.label), hovertemplate: "%{y}: %{customdata}<extra></extra>" }],
      baseLayout({ margin: { l: 150, r: 10, t: 4, b: 40 }, height: Math.max(420, 15 * sorted.length + 50),
        xaxis: Object.assign(baseLayout().xaxis, { title: { text: $("m-stat").selectedOptions[0].text } }), yaxis: Object.assign(baseLayout().yaxis, { autorange: "reversed", tickfont: { size: 10.5 } }) }));
    $("m-list-title").textContent = d.mi ? d.mi.label : "";
    // table
    const cols = v === "precip" ? [
      { t: "Station", k: "name" }, { t: "Region", k: "region" },
      { t: `${M.target_year}`, k: "val", n: 1, f: (r) => fmt(r.val, 1) }, { t: "Normal", k: "med", n: 1, f: (r) => fmt(r.med, 1) },
      { t: "% of normal", k: "pct", n: 1, f: (r) => fmt(r.pct) }, { t: "Rank (driest = 1)", k: "rank", n: 1, f: (r) => rankText(r.rank, r.n, "driest") },
      { t: "SPI", k: "spi", n: 1, f: (r) => sgn(r.spi, 2) }, { t: "Return period (yr)", k: "rp", n: 1, f: (r) => !fin(r.rp) ? "–" : r.rp > 10 * (r.n || 27) ? "> " + 10 * (r.n || 27) + " (beyond data)" : fmt(r.rp, 1) },
    ] : [
      { t: "Station", k: "name" }, { t: "Region", k: "region" }, { t: `${M.target_year} (°C)`, k: "val", n: 1, f: (r) => fmt(r.val, 1) },
      { t: "Normal", k: "med", n: 1, f: (r) => fmt(r.med, 1) }, { t: "Anomaly", k: "anom", n: 1, f: (r) => sgn(r.anom) },
      { t: "Rank (warmest = 1)", k: "rank", n: 1, f: (r) => rankText(r.rank, r.n, "warmest") },
    ];
    table($("m-table"), cols, sorted);
  }

  // ------------------------------------------------------------ STATIONS
  function initStations() {
    if (!P) { $("tab-stations").innerHTML = '<div class="empty">No station data in this build.</div>'; return; }
    const u = unitRows();
    const byReg = {};
    for (const r of u) (byReg[r.region] = byReg[r.region] || []).push(r);
    $("s-st").innerHTML = Object.keys(byReg).map((g) => `<optgroup label="${esc(g)}">` + byReg[g].map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("") + "</optgroup>").join("");
    const series = [["total_window", M.window_label + " total"], ["total_oct", M.oct_label + " total"], ["total_sep", "September total"], ["driest30_mm", "Driest 30 days"],
      ["n_wet_scaled", "Wet days"], ["sdii", "Wet-day intensity"], ["spell_max_win", "Longest dry spell"]];
    fillSelect($("s-metric"), series);
    fillSelect($("s-thr"), thrs.map((t) => [t, t + " mm"]));
    ["s-st", "s-metric", "s-thr"].forEach((id) => ($(id).onchange = renderStation));
  }
  function gotoStation(uid) {
    if (!inited.stations) { initStations(); inited.stations = true; }
    $("s-st").value = uid;
    rendered.stations = false;
    if (current === "stations") show("stations"); else location.hash = "#stations";
  }
  async function renderStation() {
    const uid = $("s-st").value, u = unitRows().find((r) => r.id === uid);
    if (!u) return;
    $("s-info").textContent = `${u.name} · ${u.region} · ${fmt(u.elev)} m · Tier ${u.tier}` + (u.kind === "composite" ? ` · joined record (${u.members})` : ` · ID ${u.id}`) + (u.status === "provisional" ? " · provisional (some days not yet reported)" : "");
    cumChart("s-cum", uid);
    const s = await loadStation(uid), c = C();
    if (!s) return;
    const thr = $("s-thr").value, m = $("s-metric").value, ser = s.series[thr] || s.series[thr0];
    if (ser && ser[m]) {
      const y = ser.years, v = ser[m];
      const base = y.map((yy, i) => (yy >= B[0] && yy <= B[1] ? v[i] : null));
      const med = median(base);
      plot("s-series", [{ type: "bar", x: y, y: v, marker: { color: y.map((yy) => (yy === M.target_year ? c.target : c.axis)) }, hovertemplate: "%{x}: %{y:.1f}<extra></extra>" }],
        baseLayout({ shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: med, y1: med, line: { color: c.ink2, width: 1.2, dash: "dash" } }],
          annotations: [{ xref: "paper", x: 1, y: med, text: "median", showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 11, color: c.ink2 } }],
          yaxis: Object.assign(baseLayout().yaxis, { rangemode: "tozero" }) }));
      $("s-series-title").textContent = $("s-metric").selectedOptions[0].text + ", year by year";
    }
    const q = s.cumulative;
    if (q && q.daily) {
      const x = q.daily.map((_, i) => new Date(Date.UTC(M.target_year, 8, 1 + i)).toISOString().slice(0, 10));
      const acc = q.daily_status.map((st) => st === "accumulated" || st === "uncertain");
      plot("s-daily", [{ type: "bar", x, y: q.daily, marker: { color: acc.map((a) => (a ? c.muted : c.accent)) }, hovertemplate: "%{x|%b %-d}: %{y:.1f} mm<extra></extra>" }],
        baseLayout({ xaxis: Object.assign(baseLayout().xaxis, { tickformat: "%b %-d", tickvals: [0, 14, 30, 44, 60].filter((k) => k < x.length).map((k) => x[k]) }), yaxis: Object.assign(baseLayout().yaxis, { title: { text: "mm" } }) }));
    } else $("s-daily").innerHTML = '<div class="empty">–</div>';
    if (s.temp && s.temp.oct) {
      const t = s.temp.oct;
      plot("s-temp", [{ x: t.years, y: t.tmax, mode: "lines+markers", line: { color: c.target, width: 2 }, marker: { size: 6 }, name: "Tmax", hovertemplate: "%{x}: %{y:.1f} °C<extra>Tmax</extra>" },
        { x: t.years, y: t.tmin, mode: "lines+markers", line: { color: c.accent, width: 2 }, marker: { size: 6 }, name: "Tmin", hovertemplate: "%{x}: %{y:.1f} °C<extra>Tmin</extra>" }],
        baseLayout({ showlegend: true, legend: { orientation: "h", y: 1.12 }, yaxis: Object.assign(baseLayout().yaxis, { title: { text: `°C, ${M.oct_label}` } }) }));
    } else $("s-temp").innerHTML = '<div class="empty">No temperature series for this station (too incomplete, or a joined record whose instrument change shifted temperatures).</div>';
    // all-measures table
    const rows = P.metrics.map((mi) => {
      const g = P.values[mi.key + "|" + thr]; const i = u.i;
      return g ? { label: mi.label, unit: mi.unit, val: g.value[i], med: g.baseline_median[i], pct: g.pct_of_median[i], rank: g.rank_dry[i], n: g.n_years[i], dry: mi.dry } : null;
    }).filter((r) => r && fin(r.val));
    table($("s-table"), [{ t: "Measure", k: "label" }, { t: `${M.target_year}`, k: "val", n: 1, f: (r) => fmt(r.val, 1) + " " + esc(r.unit) },
      { t: "Normal", k: "med", n: 1, f: (r) => fmt(r.med, 1) }, { t: "% of normal", k: "pct", n: 1, f: (r) => fmt(r.pct) },
      { t: "Rank (driest end = 1)", k: "rank", n: 1, f: (r) => rankText(r.rank, r.n, "driest") }], rows);
  }

  // ------------------------------------------------------------ WATER
  const WCOL = ["#8f2a2a", "#e34948", "#f6a8a2", "#f0efec", "#6da7ec"];
  function initWater() {
    if (!W) { $("tab-water").innerHTML = '<div class="empty">No river or lake data in this build.</div>'; return; }
    fillSelect($("w-per"), [["oct", M.oct_label], ["sepoct", M.window_label], ["last7", "Last 7 days"]]);
    fillSelect($("w-base"), [["base", String(W.baseline_start)], ["rec", "start of record"]]);
    ["w-per", "w-base", "w-reg"].forEach((id) => ($(id).onchange = renderWater));
  }
  function wcat(rank, n) { if (!fin(rank)) return null; if (rank <= 1) return 0; if (rank <= 3.5) return 1; const p = (rank - 0.5) / n; return p <= 0.25 ? 2 : p < 0.75 ? 3 : 4; }
  function renderWater() {
    const R = W.rows, per = $("w-per").value, base = $("w-base").value, inclReg = $("w-reg").checked;
    const rows = R.station.map((id, i) => ({ id, i, name: R.short[i], lon: R.lon[i], lat: R.lat[i], param: R.param[i], reg: R.regulated[i], regtxt: R.regulation[i],
      rank: R[`${per}_rank_${base}`][i], n: R[`${per}_n_${base}`][i], pct: R[`${per}_pct_median_base`][i], diff: R[`${per}_diff_median_base`][i],
      val: R[`${per}_value`][i], lower: R[`${per}_last_lower_year`][i], first: R.first_year[i], prov: R.oct_provisional_days[i] }))
      .filter((r) => fin(r.rank) && (inclReg || !r.reg));
    rows.forEach((r) => (r.cat = wcat(r.rank, r.n)));
    const sinceTxt = base === "base" ? `since ${W.baseline_start}` : "in the full record";
    const cats = W.cats.slice(); if (base !== "base") cats[0] = "Lowest on record";
    $("w-legend").innerHTML = cats.map((t, k) => `<span><span class="sw" style="background:${WCOL[k]}"></span>${esc(t)}</span>`).join("");
    const bm = M.basemaps && (M.basemaps.water || M.basemaps.precip);
    const tr = basemapTraces(bm), c = C();
    for (const par of ["flow", "level"]) {
      const g = rows.filter((r) => r.param === par);
      tr.push({ x: g.map((r) => r.lon), y: g.map((r) => r.lat), type: "scatter", mode: "markers",
        marker: { symbol: par === "flow" ? "circle" : "square", size: 15, color: g.map((r) => WCOL[r.cat]), line: { width: g.map((r) => (r.reg ? 2.4 : 0.7)), color: c.ink } },
        customdata: g.map((r) => [r.name, rankText(r.rank, r.n, "lowest").replace("1st lowest", "lowest"), par === "flow" ? fmt(r.pct) + "% of normal flow" : sgn(r.diff, 2) + " m from normal level", r.regtxt, r.lower ? "last lower: " + r.lower : "no lower year"]),
        hovertemplate: "<b>%{customdata[0]}</b><br>%{customdata[1]} " + sinceTxt + "<br>%{customdata[2]}<br>%{customdata[4]}<br>%{customdata[3]}<extra></extra>" });
    }
    plot("w-map", tr, mapLayout(bm, rows.map((r) => r.lon), rows.map((r) => r.lat)));
    const sorted = rows.slice().sort((a, b) => a.rank / a.n - b.rank / b.n);
    const rx = [], ry = [];
    for (const r of sorted) { rx.push(1, r.n, null); ry.push(r.name, r.name, null); }
    plot("w-bars", [{ x: rx, y: ry, mode: "lines", line: { color: c.grid, width: 6 }, hoverinfo: "skip" },
      { x: sorted.map((r) => r.rank), y: sorted.map((r) => r.name), mode: "markers", marker: { size: 13, color: sorted.map((r) => WCOL[r.cat]), symbol: sorted.map((r) => (r.param === "flow" ? "circle" : "square")),
        line: { color: c.ink, width: sorted.map((r) => (r.reg ? 2.2 : 0.6)) } },
        customdata: sorted.map((r) => rankText(r.rank, r.n, "lowest")), hovertemplate: "%{y}: %{customdata}<extra></extra>" }],
      baseLayout({ margin: { l: 190, r: 10, t: 4, b: 40 }, height: Math.max(420, 18 * sorted.length + 60), xaxis: Object.assign(baseLayout().xaxis, { title: { text: "rank (1 = lowest); grey = years of record" }, rangemode: "tozero" }),
        yaxis: Object.assign(baseLayout().yaxis, { autorange: "reversed", tickfont: { size: 10.5 } }) }));
    table($("w-table"), [{ t: "Gauge", k: "name" }, { t: "Measures", k: "param", f: (r) => (r.param === "flow" ? "flow" : "level") },
      { t: "Rank " + sinceTxt, k: "rank", n: 1, f: (r) => rankText(r.rank, r.n, "lowest") }, { t: "% of normal flow", k: "pct", n: 1, f: (r) => (r.param === "flow" ? fmt(r.pct) : "–") },
      { t: "Level vs normal (m)", k: "diff", n: 1, f: (r) => (r.param === "level" ? sgn(r.diff, 2) : "–") }, { t: "Last lower year", k: "lower", n: 1, f: (r) => r.lower || "–" },
      { t: "Record since", k: "first", n: 1 }, { t: "Regulation", k: "regtxt" }, { t: "Provisional days", k: "prov", n: 1 }], sorted);
    const h = (M.headline || {}).water;
    if (h) $("w-lead").innerHTML = `Of ${h.n} river and lake gauges on south and central Vancouver Island, <b>${h.n_lowest}</b> had their lowest ${M.oct_label} since ${h.baseline_start}, ` +
      `<b>${h.n_top3 - h.n_lowest}</b> their 2nd or 3rd lowest, and in all <b>${h.n_low_quarter}</b> were in the lowest quarter of years. The median river carried ${fmt(h.flow_pct_median)}% of its normal flow.`;
    const pngs = (M.figures || []).filter((f) => f.startsWith("water_ranks"));
    $("w-fig").innerHTML = pngs.length ? "Print-ready map: " + pngs.map((f) => `<a href="figures/${esc(f)}">${esc(f)}</a>`).join(", ") : "";
  }

  // ------------------------------------------------------------ COHERENCE
  function initCoherence() {
    if (!P) return;
    fillSelect($("c-metric"), [["total_oct", M.oct_label + " total"], ["total_window", M.window_label + " total"], ["total_sep", "September total"], ["driest30_mm", "Driest 30 days"], ["n_wet_scaled", "Wet days"], ["sdii", "Wet-day intensity"]]);
    fillSelect($("c-thr"), thrs.map((t) => [t, t + " mm"]));
    ["c-metric", "c-thr"].forEach((id) => ($(id).onchange = renderCoherence));
  }
  function renderCoherence() {
    const m = $("c-metric").value, thr = $("c-thr").value, g = P.values[m + "|" + thr], u = unitRows(), c = C();
    if (!g) return;
    const regs = (M.regions || []).filter((r) => u.some((x) => x.region === r));
    const tr = regs.map((reg) => {
      const rr = u.filter((x) => x.region === reg && fin(g.pct_of_median[x.i]));
      return { type: "box", name: reg, y: rr.map((x) => g.pct_of_median[x.i]), boxpoints: "all", jitter: 0.4, pointpos: 0, marker: { color: c.accent, size: 7 }, line: { color: c.ink2, width: 1 }, fillcolor: "rgba(0,0,0,0)",
        text: rr.map((x) => x.name), hovertemplate: "%{text}: %{y:.0f}%<extra></extra>" };
    });
    plot("c-region", tr, baseLayout({ shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: c.ink2, dash: "dash", width: 1 } }],
      yaxis: Object.assign(baseLayout().yaxis, { title: { text: "% of normal" }, range: [0, Math.max(115, 1.08 * Math.max(...u.map((x) => g.pct_of_median[x.i]).filter(fin)))] }), margin: { l: 52, r: 10, t: 10, b: 70 } }));
    const ranks = u.map((x) => g.rank_dry[x.i]).filter(fin).map(Math.round);
    const nmax = Math.max(...u.map((x) => g.n_years[x.i]).filter(fin), 1);
    const counts = Array.from({ length: nmax }, (_, k) => ranks.filter((r) => r === k + 1).length);
    plot("c-rank", [{ type: "bar", x: counts.map((_, k) => k + 1), y: counts, marker: { color: counts.map((_, k) => (k < 3 ? c.div[1] : c.axis)) }, hovertemplate: "%{x}: %{y} stations<extra></extra>" }],
      baseLayout({ xaxis: Object.assign(baseLayout().xaxis, { title: { text: "rank (1 = driest)" }, dtick: 2 }), yaxis: Object.assign(baseLayout().yaxis, { title: { text: "stations" }, tickformat: "d", dtick: Math.max(...counts) <= 6 ? 1 : null }),
        annotations: [{ xref: "paper", yref: "paper", x: 1, y: 1, xanchor: "right", showarrow: false, text: `Expected if random: ${fmt(ranks.length / nmax, 1)} per rank`, font: { size: 11, color: c.ink2 } }] }));
    const aN = P.values["a_N|" + thr], aI = P.values["a_I|" + thr];
    if (aN && aI) {
      const rr = u.filter((x) => fin(aN.value[x.i]) && fin(aI.value[x.i]));
      const lim = Math.max(0.5, ...rr.map((x) => Math.abs(aN.value[x.i])), ...rr.map((x) => Math.abs(aI.value[x.i]))) * 1.1;
      const traces = regs.map((reg, k) => {
        const q = rr.filter((x) => x.region === reg);
        return { x: q.map((x) => aN.value[x.i]), y: q.map((x) => aI.value[x.i]), mode: "markers", name: reg, marker: { size: 9, line: { width: 1, color: c.surface } }, text: q.map((x) => x.name), hovertemplate: "%{text}<br>frequency %{x:+.2f}, intensity %{y:+.2f}<extra>" + esc(reg) + "</extra>" };
      });
      plot("c-ni", traces, baseLayout({ showlegend: true, legend: { orientation: "h", y: -0.18, font: { size: 11 } }, colorway: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"],
        xaxis: Object.assign(baseLayout().xaxis, { title: { text: "ln(wet days / normal)" }, range: [-lim, lim], zeroline: true }), yaxis: Object.assign(baseLayout().yaxis, { title: { text: "ln(intensity / normal)" }, range: [-lim, lim], zeroline: true }),
        shapes: [-1, -0.5, 0, 0.5].map((k) => ({ type: "line", x0: -lim, x1: lim, y0: k + lim, y1: k - lim, line: { color: c.grid, width: 1, dash: "dot" } })),
        margin: { l: 56, r: 10, t: 10, b: 90 } }));
    }
    const reg = regs.map((r) => {
      const q = u.filter((x) => x.region === r && fin(g.pct_of_median[x.i]));
      return { r, n: q.length, pct: median(q.map((x) => g.pct_of_median[x.i])), top3: q.filter((x) => g.rank_dry[x.i] <= 3).length, rank: median(q.map((x) => g.rank_dry[x.i])) };
    });
    table($("c-table"), [{ t: "Region", k: "r" }, { t: "Stations", k: "n", n: 1 }, { t: "Median % of normal", k: "pct", n: 1, f: (x) => fmt(x.pct) }, { t: "Top-3 driest", k: "top3", n: 1 }, { t: "Median rank", k: "rank", n: 1, f: (x) => fmt(x.rank, 1) }], reg);
  }

  // ------------------------------------------------------------ ENSO
  const REC_LAB = { short: `Short record (${B[0]}–${B[1]})`, long: "Long record (1950 onward)", rivers: "Natural rivers, coastal BC" };
  const REG_LAB = { island: "Vancouver Island", all: "Whole domain", coastal: "Coastal BC" };
  let gridIdx = null;
  function initEnso() {
    if (!E) { $("tab-enso").innerHTML = '<div class="empty">The ENSO analysis is not part of this build (run <code>python run.py enso</code>).</div>'; return; }
    const recs = [...new Set(Object.keys(E.regional).map((k) => k.split("|")[0]))];
    fillSelect($("e-rec"), recs.map((r) => [r, REC_LAB[r] || r]), "short");
    fillSelect($("e-index"), Object.keys(E.index_labels).map((k) => [k, E.index_labels[k]]), E.summary.primary.spec.primary_index);
    fillSelect($("e-season"), E.seasons.map((s) => [s, s]), E.summary.primary.spec.primary_season);
    $("e-rec").onchange = () => { ensoOptions(); renderEnso(); };
    $("e-region").onchange = () => { ensoMetrics(); renderEnso(); };
    ["e-metric", "e-index", "e-season"].forEach((id) => ($(id).onchange = renderEnso));
    ensoOptions();
    gridIdx = {};
    const G = E.grid;
    if (G) G.record.forEach((r, i) => { gridIdx[[r, G.region[i], G.metric[i], G.index[i], G.season[i]].join("|")] = i; });
    $("e-ntests").textContent = G ? G.record.length : 0;
    $("class-rule").textContent = E.class_rule;
    renderPrimary();
  }
  function ensoOptions() {
    const rec = $("e-rec").value;
    const regs = [...new Set(Object.keys(E.regional).filter((k) => k.startsWith(rec + "|")).map((k) => k.split("|")[1]))];
    fillSelect($("e-region"), regs.map((r) => [r, REG_LAB[r] || r]), $("e-region").value || "island");
    ensoMetrics();
  }
  function ensoMetrics() {
    const rec = $("e-rec").value, reg = $("e-region").value;
    const ms = Object.keys(E.regional).filter((k) => k.startsWith(rec + "|" + reg + "|")).map((k) => k.split("|")[2]);
    const prev = $("e-metric").value;
    fillSelect($("e-metric"), ms.map((m) => [m, E.metric_labels[m] || m]), ms.includes(prev) ? prev : ms.includes("total_window") ? "total_window" : ms[0]);
  }
  function renderPrimary() {
    const pr = E.summary.primary, sp = pr.spec, ti = E.summary.target_index;
    const line = (rec, r) => !r ? "" : `<div><b>${esc(REC_LAB[rec])}</b>: ρ = <b>${sgn(r.rho, 2)}</b> (90% interval ${sgn(r.rho_lo, 2)} to ${sgn(r.rho_hi, 2)}), one-sided p = <b>${fmt(r.p_one_sided, 3)}</b>, n = ${r.n} years (${r.year_first}–${r.year_last}). Without detrending: ρ = ${sgn(r.rho_not_detrended, 2)}.</div>`;
    $("e-primary").innerHTML = `<h3 style="margin-top:0">Pre-registered test</h3><p class="note">${esc(REG_LAB[sp.primary_region] || sp.primary_region)} ${esc(E.metric_labels[sp.primary_metric] || sp.primary_metric)} against ${esc(E.index_labels[sp.primary_index])} (${sp.primary_season}); hypothesis ${esc(sp.direction)}; linear trends removed; permutation p-value.</p>` +
      line("short", pr.short) + line("long", pr.long) +
      `<p class="note" style="margin-bottom:0">${M.target_year}: ${esc(E.index_labels[ti.name] || ti.name)} ${ti.season} = ${sgn(ti.value, 2)} °C (${esc(ti.class)}).</p>`;
  }
  function ols1(x, y) {
    const n = x.length, mx = x.reduce((a, b) => a + b, 0) / n, my = y.reduce((a, b) => a + b, 0) / n;
    let sxx = 0, sxy = 0; for (let i = 0; i < n; i++) { sxx += (x[i] - mx) ** 2; sxy += (x[i] - mx) * (y[i] - my); }
    const b = sxy / sxx, a = my - b * mx; let sse = 0; for (let i = 0; i < n; i++) sse += (y[i] - a - b * x[i]) ** 2;
    return { a, b, s: Math.sqrt(sse / (n - 2)), mx, sxx, n };
  }
  function renderEnso() {
    const rec = $("e-rec").value, reg = $("e-region").value, m = $("e-metric").value, ix = $("e-index").value, se = $("e-season").value, c = C();
    const ys = E.regional[[rec, reg, m].join("|")], xs = E.index_series[ix + "|" + se];
    const ty = M.target_year;
    if (ys && xs) {
      const xmap = {}; xs.years.forEach((y, i) => (xmap[y] = xs.values[i]));
      const pts = ys.years.map((y, i) => ({ y, z: ys.z[i], x: xmap[y] })).filter((p) => fin(p.x) && fin(p.z));
      const past = pts.filter((p) => p.y < ty), cur = pts.find((p) => p.y === ty);
      const tr = [{ x: past.map((p) => p.x), y: past.map((p) => p.z), mode: "markers", marker: { size: 8, color: c.accent, opacity: 0.8, line: { width: 1, color: c.surface } }, text: past.map((p) => p.y), hovertemplate: "%{text}: index %{x:.2f}, score %{y:.2f}<extra></extra>" }];
      if (past.length > 4) {
        const f = ols1(past.map((p) => p.x), past.map((p) => p.z));
        const xr = [Math.min(...pts.map((p) => p.x)), Math.max(...pts.map((p) => p.x))];
        const xx = Array.from({ length: 30 }, (_, k) => xr[0] + (xr[1] - xr[0]) * k / 29);
        const half = xx.map((x) => 1.68 * f.s * Math.sqrt(1 + 1 / f.n + (x - f.mx) ** 2 / f.sxx));
        tr.unshift({ x: xx.concat(xx.slice().reverse()), y: xx.map((x, k) => f.a + f.b * x + half[k]).concat(xx.slice().reverse().map((x, k) => f.a + f.b * x - half[29 - k])), fill: "toself", fillcolor: c.band2, line: { width: 0 }, hoverinfo: "skip", mode: "lines" });
        tr.push({ x: xx, y: xx.map((x) => f.a + f.b * x), mode: "lines", line: { color: c.ink2, width: 1.5 }, hoverinfo: "skip" });
      }
      if (cur) tr.push({ x: [cur.x], y: [cur.z], mode: "markers+text", marker: { size: 15, color: c.target, line: { width: 2, color: c.surface } }, text: [String(ty)], textposition: "top center", textfont: { color: c.target, size: 12 }, hovertemplate: ty + ": index %{x:.2f}, score %{y:.2f}<extra></extra>" });
      plot("e-scatter", tr, baseLayout({ xaxis: Object.assign(baseLayout().xaxis, { title: { text: `${E.index_labels[ix]} (${se})` } }), yaxis: Object.assign(baseLayout().yaxis, { title: { text: "score (− = drier / cooler / lower)" } }) }));
      $("e-sc-title").textContent = `${E.metric_labels[m] || m}: ${REG_LAB[reg] || reg}`;
      const gi = gridIdx[[rec, reg, m, ix, se].join("|")];
      $("e-sc-note").innerHTML = gi !== undefined ? `Detrended Spearman ρ = <b>${sgn(E.grid.rho[gi], 2)}</b>, two-sided p = ${fmt(E.grid.p_two[gi], 3)}, BH q = ${fmt(E.grid.q_bh[gi], 2)}, n = ${E.grid.n[gi]}. Line: least squares on years before ${ty}, with 90% prediction band. Red: ${ty} (not used in the fit).` : "";
      // time series (two stacked charts, shared years; no dual axis)
      const yrs = ys.years;
      plot("e-ts-y", [{ type: "bar", x: yrs, y: ys.z, marker: { color: ys.z.map((z, i) => (yrs[i] === ty ? c.target : z < 0 ? c.div[1] : c.div[5])) }, hovertemplate: "%{x}: %{y:.2f}<extra></extra>" }],
        baseLayout({ margin: { l: 52, r: 14, t: 6, b: 20 }, yaxis: Object.assign(baseLayout().yaxis, { title: { text: "score" } }), xaxis: Object.assign(baseLayout().xaxis, { range: [Math.min(...yrs) - 0.5, ty + 0.5] }) }));
      const xi = xs.years.map((y, i) => [y, xs.values[i]]).filter((p) => p[0] >= Math.min(...yrs));
      plot("e-ts-x", [{ type: "bar", x: xi.map((p) => p[0]), y: xi.map((p) => p[1]), marker: { color: xi.map((p) => (p[1] >= 0 ? c.div[1] : c.div[5])), opacity: 0.75 }, hovertemplate: "%{x}: %{y:.2f}<extra></extra>" }],
        baseLayout({ margin: { l: 52, r: 14, t: 6, b: 30 }, yaxis: Object.assign(baseLayout().yaxis, { title: { text: `${ix} ${se}` } }), xaxis: Object.assign(baseLayout().xaxis, { range: [Math.min(...yrs) - 0.5, ty + 0.5] }) }));
    }
    // heatmap
    const idxs = Object.keys(E.index_labels), seas = E.seasons;
    const z = idxs.map((i) => seas.map((s) => { const k = gridIdx[[rec, reg, m, i, s].join("|")]; return k === undefined ? null : E.grid.rho[k]; }));
    const txt = idxs.map((i) => seas.map((s) => { const k = gridIdx[[rec, reg, m, i, s].join("|")]; if (k === undefined) return ""; const q = E.grid.q_bh[k], p = E.grid.p_two[k]; return sgn(E.grid.rho[k], 2) + (q <= 0.1 ? "✱" : p <= 0.05 ? "·" : ""); }));
    plot("e-heat", [{ type: "heatmap", z, x: seas, y: idxs.map((i) => E.index_labels[i]), text: txt, texttemplate: "%{text}", zmin: -0.6, zmax: 0.6, colorscale: divScale(false), showscale: true,
      colorbar: { thickness: 10, len: 0.8, title: { text: "ρ", side: "right" } }, hovertemplate: "%{y}, %{x}: ρ = %{z:.2f}<extra></extra>", xgap: 2, ygap: 2 }],
      baseLayout({ margin: { l: 170, r: 10, t: 6, b: 34 }, yaxis: Object.assign(baseLayout().yaxis, { autorange: "reversed", gridcolor: "rgba(0,0,0,0)" }), xaxis: Object.assign(baseLayout().xaxis, { gridcolor: "rgba(0,0,0,0)" }) }));
    // composites
    const comp = E.composites.filter((r) => r.record === rec && r.region === reg && r.metric === m);
    if (comp.length) {
      const order = ["La Niña", "Neutral", "El Niño"], cc = order.map((k) => comp.find((r) => r.class === k)).filter(Boolean);
      plot("e-comp", [{ type: "bar", x: cc.map((r) => `${r.class} (n=${r.n})`), y: cc.map((r) => r.mean_z), marker: { color: cc.map((r) => (r.class === "El Niño" ? c.div[1] : r.class === "La Niña" ? c.div[5] : c.axis)) },
        customdata: cc.map((r) => [fmt(100 * r.frac_low_tercile), r.years]), hovertemplate: "%{x}<br>mean score %{y:.2f}<br>%{customdata[0]}% of years in the lowest third<br>%{customdata[1]}<extra></extra>" }],
        baseLayout({ yaxis: Object.assign(baseLayout().yaxis, { title: { text: "mean score" }, zeroline: true }) }));
      $("e-comp-note").textContent = `Classes by ${E.class_rule}. Permutation p (El Niño lower than other years) = ${fmt(comp[0].p_elnino_lower, 3)}. Hover for the share of years in the lowest third and the years.`;
    }
    // regressions table
    const rg = E.regressions.filter((r) => r.record === rec && r.region === reg && r.metric === m);
    const coef = (r, k) => (fin(r["b_" + k]) ? `${sgn(r["b_" + k], 2)} (p ${fmt(r["p_" + k], 3)})` : "–");
    table($("e-reg"), [{ t: "Model", k: "model" }, { t: "ENSO", k: "b_enso", n: 1, f: (r) => coef(r, "enso") }, { t: "PDO⊥", k: "b_pdo_perp", n: 1, f: (r) => coef(r, "pdo_perp") },
      { t: "PNA", k: "b_pna", n: 1, f: (r) => coef(r, "pna") }, { t: "R²", k: "r2", n: 1, f: (r) => fmt(r.r2, 2) },
      { t: `${ty} expected [90%]`, k: "target_expected", n: 1, f: (r) => fin(r.target_expected) ? `${sgn(r.target_expected, 2)} [${sgn(r.target_lo90, 1)}, ${sgn(r.target_hi90, 1)}]` : "–" },
      { t: "observed", k: "target_observed", n: 1, f: (r) => sgn(r.target_observed, 2) },
      { t: "1 in N (given model / none)", k: "target_rp_cond", n: 1, f: (r) => fin(r.target_rp_cond) ? `${fmt(r.target_rp_cond, 1)} / ${fmt(r.target_rp_uncond, 1)}` : "–" }], rg);
    // station map
    const S = E.station_maps;
    if (S) {
      const ii = S.record.map((r, i) => i).filter((i) => S.record[i] === rec && S.metric[i] === m && fin(S.lon[i]));
      const bm = M.basemaps && (rec === "rivers" ? M.basemaps.enso : M.basemaps.precip);
      const tr = basemapTraces(bm);
      for (const sig of [false, true]) {
        const q = ii.filter((i) => !!S.fdr_sig[i] === sig);
        tr.push({ x: q.map((i) => S.lon[i]), y: q.map((i) => S.lat[i]), mode: "markers", type: "scatter",
          marker: { size: sig ? 15 : 12, color: q.map((i) => S.rho[i]), cmin: -0.6, cmax: 0.6, colorscale: divScale(false), symbol: sig ? "circle" : "circle", line: { width: sig ? 2.5 : 1, color: sig ? c.ink : c.surface } },
          customdata: q.map((i) => [S.name[i] || S.unit_id[i], sgn(S.rho[i], 2), fmt(S.p_two[i], 3), S.n[i], sgn(S.z_target[i], 2)]),
          hovertemplate: "<b>%{customdata[0]}</b><br>ρ = %{customdata[1]} (p %{customdata[2]}, n %{customdata[3]})<br>" + ty + " score %{customdata[4]}<extra></extra>" });
      }
      plot("e-map", tr, mapLayout(bm, ii.map((i) => S.lon[i]), ii.map((i) => S.lat[i])));
      const nsig = ii.filter((i) => S.fdr_sig[i]).length;
      $("e-map-legend").innerHTML = "<span>Colour: ρ with " + esc(E.index_labels[E.summary.primary.spec.primary_index]) + " (" + E.summary.primary.spec.primary_season + "); red = drier/lower in El Niño years. Thick outline: significant after FDR control.</span>";
      $("e-map-note").textContent = ii.length ? `${nsig} of ${ii.length} stations significant at the Wilks FDR level (α_global = 0.05). ` + (nsig ? "The field is significant." : "No station passes; the pattern is not distinguishable from chance.") : "No stations for this record and measure.";
    }
    // analogs + ahccd
    table($("e-analog"), [{ t: "Year", k: "year" }, { t: E.index_labels[E.summary.primary.spec.primary_index], k: "index_value", n: 1, f: (r) => sgn(r.index_value, 2) },
      { t: "Island Sep–Oct score (short)", k: "short_total_window_z", n: 1, f: (r) => sgn(r.short_total_window_z, 2) },
      { t: "(long)", k: "long_total_window_z", n: 1, f: (r) => sgn(r.long_total_window_z, 2) }], E.analogs);
    table($("e-ahccd"), [{ t: "Station", k: "unit_id" }, { t: "AHCCD", k: "ahccd_name" }, { t: "km", k: "km", n: 1 }, { t: "ρ AHCCD Oct", k: "rho_ahccd_oct", n: 1, f: (r) => sgn(r.rho_ahccd_oct, 2) },
      { t: "ρ raw Oct 1–end", k: "rho_raw_oct1_end", n: 1, f: (r) => sgn(r.rho_raw_oct1_end, 2) }, { t: "r raw vs AHCCD", k: "corr_raw_vs_ahccd", n: 1, f: (r) => fmt(r.corr_raw_vs_ahccd, 2) },
      { t: "AHCCD to", k: "ahccd_last_year", n: 1 }], E.ahccd);
  }

  // ------------------------------------------------------------ METHODS
  function renderMethods() {
    $("downloads").innerHTML = (M.csv || []).length ? "<ul>" + M.csv.map((f) => `<li><a href="data/csv/${esc(f)}" download>${esc(f)}</a></li>`).join("") + "</ul>" : '<p class="note">No tables.</p>';
    const kv = [["Target period", `${M.window_label}, ${M.target_year}`], ["Reference years", `${B[0]}–${B[1]}`], ["Data source", (M.vintage && M.vintage.source) || "–"],
      ["Data downloaded", (M.vintage && M.vintage.built) || "–"], ["Page built", M.built || "–"], ["Components", Object.keys(M.have || {}).filter((k) => M.have[k]).join(", ")]];
    $("buildinfo").innerHTML = kv.map((x) => `<div>${esc(x[0])}</div><div>${esc(x[1])}</div>`).join("");
  }

  // ------------------------------------------------------------ routing
  const tabs = ["summary", "maps", "stations", "water", "coherence", "enso", "methods"];
  const init = { maps: initMaps, stations: initStations, water: initWater, coherence: initCoherence, enso: initEnso };
  const draw = { summary: renderSummary, maps: () => P && renderMaps(), stations: () => P && renderStation(), water: () => W && renderWater(), coherence: () => P && renderCoherence(), enso: () => E && renderEnso(), methods: renderMethods };
  const inited = {};
  let current = "summary";
  function show(tab) {
    if (!tabs.includes(tab)) tab = "summary";
    current = tab;
    document.querySelectorAll("section.tab").forEach((s) => s.classList.toggle("on", s.id === "tab-" + tab));
    document.querySelectorAll("nav.tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
    if (!inited[tab] && init[tab]) { init[tab](); }
    inited[tab] = true;
    if (!rendered[tab]) { try { draw[tab](); } catch (e) { console.error(e); } rendered[tab] = true; }
    else document.querySelectorAll("#tab-" + tab + " .js-plotly-plot").forEach((d) => Plotly.Plots.resize(d));
  }
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  if (!window.Plotly) document.querySelector("main").insertAdjacentHTML("afterbegin", '<div class="banner">assets/plotly.min.js is missing; charts cannot be drawn.</div>');
  show(location.hash.slice(1) || "summary");
})();
