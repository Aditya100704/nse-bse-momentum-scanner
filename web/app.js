(() => {
  const DATA_URL = "../data/scanner_output.json";

  const COLOR = {
    ghost: "#ffffff",
    comet: "#d8ecf8",
    arctic: "#d1e4fa",
    celestial: "#b6d9fc",
    azure: "#c7d3ea",
    whisper: "#9da7ba",
    interstellar: "#81899b",
    violet: "#663af3",
    violetSoft: "rgba(102, 58, 243, 0.55)",
    pos: "#6fe7b3",
    neg: "#ff7a8a",
    warn: "#fbbf24",
    grid: "rgba(186, 215, 247, 0.08)",
    border: "rgba(186, 215, 247, 0.18)",
  };

  const state = {
    rows: [],
    breadth: {},
    sectors: [],
    meta: null,
    sortKey: "rs_rating",
    sortDir: "desc",
    filters: { q: "", minRs: 0, minLiq: 0, ttOnly: false, near52w: false, sector: "" },
    activeTab: "scanner",
  };

  const $ = (id) => document.getElementById(id);

  /* ============================================================ formatters */
  const fmtPct = (v) => {
    if (v == null || Number.isNaN(v)) return "—";
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
    const sign = v > 0 ? "+" : "";
    return `<span class="${cls}">${sign}${v.toFixed(1)}%</span>`;
  };
  const fmtNum = (v, d = 2) => {
    if (v == null || Number.isNaN(v)) return "—";
    return v.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
  };
  const fmtX = (v) => {
    if (v == null || Number.isNaN(v)) return "—";
    const cls = v >= 1.5 ? "pos" : v <= 0.8 ? "neg" : "muted";
    return `<span class="${cls}">${v.toFixed(2)}×</span>`;
  };
  const rsBadge = (v) => {
    const k = v >= 80 ? "rs-hi" : v >= 50 ? "rs-md" : "rs-lo";
    return `<span class="rs ${k}">${v}</span>`;
  };
  const tvLink = (sym, exch) =>
    `https://www.tradingview.com/chart/?symbol=${encodeURIComponent((exch === "NSE" ? "NSE:" : "BSE:") + sym)}`;

  const median = (arr) => {
    const xs = arr.filter((v) => v != null && !Number.isNaN(v)).sort((a, b) => a - b);
    if (!xs.length) return null;
    const m = Math.floor(xs.length / 2);
    return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
  };

  /* ============================================================ tabs */
  function bindTabs() {
    document.querySelectorAll(".tab").forEach((t) => {
      t.addEventListener("click", () => {
        const name = t.dataset.tab;
        state.activeTab = name;
        document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("is-active", x === t));
        document.querySelectorAll(".page").forEach((p) =>
          p.classList.toggle("is-active", p.dataset.page === name)
        );
        // Charts need a resize nudge when first revealed
        Object.values(charts).forEach((c) => c && c.resize && c.resize());
      });
    });
  }

  /* ============================================================ table */
  function renderTable() {
    const tbody = $("resultsBody");
    const f = state.filters;
    const q = f.q.trim().toLowerCase();

    let rows = state.rows.filter((r) => {
      if (r.rs_rating < f.minRs) return false;
      if (r.turnover_cr < f.minLiq) return false;
      if (f.ttOnly && !r.trend_template) return false;
      if (f.near52w && (r.pct_off_high == null || r.pct_off_high > 10)) return false;
      if (f.sector && r.sector !== f.sector) return false;
      if (q) {
        const hay = (r.symbol + " " + (r.name || "") + " " + (r.sector || "")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const dir = state.sortDir === "desc" ? -1 : 1;
    const key = state.sortKey;
    rows.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });

    if (!rows.length) {
      tbody.innerHTML = "";
      $("emptyState").classList.remove("hidden");
    } else {
      $("emptyState").classList.add("hidden");
      const view = rows.slice(0, 800);
      tbody.innerHTML = view.map((r) => `
        <tr>
          <td class="num">${rsBadge(r.rs_rating)}</td>
          <td class="sym"><a href="${tvLink(r.symbol, r.exchange)}" target="_blank" rel="noopener">${r.symbol}</a></td>
          <td><span class="name" title="${(r.name || "").replace(/"/g, "&quot;")}">${r.name || ""}</span></td>
          <td><span class="muted">${r.sector || "—"}</span></td>
          <td class="num">${fmtNum(r.close, 2)}</td>
          <td class="num">${fmtPct(r.r1m)}</td>
          <td class="num">${fmtPct(r.r3m)}</td>
          <td class="num">${fmtPct(r.r6m)}</td>
          <td class="num">${fmtPct(r.r12m)}</td>
          <td class="num"><span class="muted">-${(r.pct_off_high ?? 0).toFixed(1)}%</span></td>
          <td class="num">${fmtNum(r.turnover_cr, 1)}</td>
          <td class="num">${fmtX(r.vol_surge)}</td>
          <td class="center">${r.trend_template ? '<span class="tt-yes">✓</span>' : '<span class="tt-no">·</span>'}</td>
        </tr>`).join("");
      if (rows.length > 800) {
        tbody.insertAdjacentHTML("beforeend",
          `<tr><td colspan="13" class="muted center" style="padding:14px">Showing first 800 of ${rows.length}. Tighten filters to see more.</td></tr>`);
      }
    }

    document.querySelectorAll("th[data-key]").forEach((th) => {
      th.classList.remove("sort-active", "asc");
      if (th.dataset.key === state.sortKey) {
        th.classList.add("sort-active");
        if (state.sortDir === "asc") th.classList.add("asc");
      }
    });
  }

  /* ============================================================ Scanner KPIs */
  function renderScannerKpis() {
    const m = state.meta;
    const rows = state.rows;
    if (!m) return;

    $("statUniverse").textContent = (m.universe_size ?? 0).toLocaleString("en-IN");
    $("statWithData").textContent = (m.with_data ?? 0).toLocaleString("en-IN");
    $("statQualifiers").textContent = (m.qualifiers ?? 0).toLocaleString("en-IN");
    $("statTT").textContent = rows.filter((r) => r.trend_template).length.toLocaleString("en-IN");
    const medMom = median(rows.map((r) => r.momentum));
    $("statMedMom").textContent = medMom != null ? `${medMom > 0 ? "+" : ""}${medMom.toFixed(1)}` : "—";

    const b = state.breadth || {};
    const score = b.regime_score;
    let klass = "temp-cool", label = b.regime_label || "—";
    if (score >= 7)      klass = "temp-hot";
    else if (score >= 4) klass = "temp-warm";
    else                 klass = "temp-cold";
    $("statTemp").innerHTML = `<span class="temp-pill ${klass}">${label}</span>`;
    $("statTempLabel").textContent = score != null ? `score ${score.toFixed(1)} / 10` : "";

    const t = new Date(m.generated_at);
    const ageHrs = (Date.now() - t.getTime()) / 3.6e6;
    const badge = $("freshBadge");
    badge.classList.remove("stale", "bad");
    if (ageHrs > 48) badge.classList.add("bad");
    else if (ageHrs > 24) badge.classList.add("stale");
    $("lastScan").textContent = t.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
    $("duration").textContent = m.duration_s != null ? `${m.duration_s}s scan` : "";
  }

  /* ============================================================ Charts */
  const charts = {};

  const baseOpts = () => ({
    responsive: true,
    maintainAspectRatio: false,
    color: COLOR.whisper,
    font: { family: "Inter" },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "rgba(5, 6, 15, 0.96)",
        borderColor: COLOR.border,
        borderWidth: 1,
        titleColor: COLOR.ghost,
        bodyColor: COLOR.comet,
        padding: 10,
        cornerRadius: 8,
        titleFont: { family: "Inter", weight: 600, size: 12 },
        bodyFont:  { family: "IBM Plex Mono", size: 12 },
      },
    },
    scales: {
      x: { grid: { color: COLOR.grid, drawBorder: false }, ticks: { color: COLOR.whisper, font: { size: 11 } } },
      y: { grid: { color: COLOR.grid, drawBorder: false }, ticks: { color: COLOR.whisper, font: { size: 11 } } },
    },
  });

  const gradient = (ctx, top, bottom, h = 360) => {
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, top); g.addColorStop(1, bottom);
    return g;
  };

  /* ---------- Scanner charts ---------- */
  function renderTop() {
    const top = state.rows.slice(0, 15);
    const labels = top.map((r) => r.symbol);
    const data = top.map((r) => r.r3m ?? 0);
    const ctx = $("chartTop").getContext("2d");
    charts.top?.destroy();
    charts.top = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{
        data,
        backgroundColor: data.map((v) => v >= 0
          ? gradient(ctx, "rgba(111, 231, 179, 0.95)", "rgba(111, 231, 179, 0.25)")
          : gradient(ctx, "rgba(255, 122, 138, 0.95)", "rgba(255, 122, 138, 0.25)")),
        borderRadius: 4, barThickness: 16,
      }]},
      options: {
        ...baseOpts(),
        indexAxis: "y",
        scales: {
          x: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` } },
          y: { grid: { display: false }, ticks: { color: COLOR.arctic, font: { size: 11, family: "IBM Plex Mono" } } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => {
            const r = top[c.dataIndex];
            return [r.name || r.symbol, `3M: ${r.r3m?.toFixed(1)}%`, `RS: ${r.rs_rating}`, `Sector: ${r.sector || "—"}`];
          } } } },
      },
    });
  }

  function renderDist() {
    const vals = state.rows.map((r) => r.r1m).filter((v) => v != null);
    if (!vals.length) return;
    const min = Math.floor(Math.min(...vals) / 5) * 5;
    const max = Math.ceil(Math.max(...vals) / 5) * 5;
    const bins = [];
    for (let b = min; b < max; b += 5) bins.push({ lo: b, hi: b + 5, count: 0 });
    vals.forEach((v) => { const i = Math.min(bins.length - 1, Math.floor((v - min) / 5)); if (i >= 0) bins[i].count++; });
    const ctx = $("chartDist").getContext("2d");
    charts.dist?.destroy();
    charts.dist = new Chart(ctx, {
      type: "bar",
      data: { labels: bins.map((b) => `${b.lo}…${b.hi}%`),
        datasets: [{ data: bins.map((b) => b.count),
          backgroundColor: bins.map((b) => b.lo >= 0 ? "rgba(111, 231, 179, 0.55)" : "rgba(255, 122, 138, 0.55)"),
          borderRadius: 3 }] },
      options: { ...baseOpts(), plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
        callbacks: { label: (c) => `${c.parsed.y} stocks` } } } },
    });
  }

  function renderScatter() {
    const pts = state.rows.map((r) => ({
      x: r.turnover_cr, y: r.momentum, sym: r.symbol, rs: r.rs_rating, tt: r.trend_template,
    })).filter((p) => p.x > 0 && p.y != null);
    const ctx = $("chartScatter").getContext("2d");
    charts.scatter?.destroy();
    charts.scatter = new Chart(ctx, {
      type: "scatter",
      data: { datasets: [{
        data: pts,
        backgroundColor: pts.map((p) => p.tt ? "rgba(182, 217, 252, 0.75)" : "rgba(102, 58, 243, 0.45)"),
        borderColor: pts.map((p) => p.tt ? COLOR.celestial : COLOR.violetSoft),
        borderWidth: 1,
        pointRadius: pts.map((p) => Math.min(8, 2 + p.rs / 18)),
        pointHoverRadius: 8,
      }]},
      options: {
        ...baseOpts(),
        scales: {
          x: { type: "logarithmic", grid: { color: COLOR.grid },
               ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `₹${v}cr` },
               title: { display: true, text: "Daily liquidity (₹ cr, log)", color: COLOR.whisper, font: { size: 11 } } },
          y: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` },
               title: { display: true, text: "Momentum composite", color: COLOR.whisper, font: { size: 11 } } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => {
            const p = pts[c.dataIndex];
            return [p.sym, `RS ${p.rs}`, `Liq ₹${p.x.toFixed(1)}cr`, `Mom ${p.y.toFixed(1)}%`];
          } } } },
      },
    });
  }

  function renderHorizon() {
    const H = [{k:"r1m",l:"1M"},{k:"r3m",l:"3M"},{k:"r6m",l:"6M"},{k:"r12m",l:"12M"}];
    const labels = H.map((h) => h.l);
    const med = H.map((h) => median(state.rows.map((r) => r[h.k])) ?? 0);
    const sorted = (k) => state.rows.map((r) => r[k]).filter((v) => v != null).sort((a, b) => a - b);
    const p75 = H.map((h) => { const x = sorted(h.k); return x.length ? x[Math.floor(x.length * 0.75)] : 0; });
    const p25 = H.map((h) => { const x = sorted(h.k); return x.length ? x[Math.floor(x.length * 0.25)] : 0; });
    const ctx = $("chartHorizon").getContext("2d");
    charts.horizon?.destroy();
    charts.horizon = new Chart(ctx, {
      type: "line",
      data: { labels, datasets: [
        { label: "P75", data: p75, borderColor: "rgba(111, 231, 179, 0.45)",
          backgroundColor: "rgba(111, 231, 179, 0.08)", fill: "+1", tension: 0.35,
          borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0 },
        { label: "Median", data: med, borderColor: COLOR.celestial,
          backgroundColor: gradient(ctx, "rgba(182, 217, 252, 0.25)", "rgba(182, 217, 252, 0.02)"),
          fill: true, tension: 0.35, borderWidth: 2.5, pointRadius: 4,
          pointBackgroundColor: COLOR.ghost, pointBorderColor: COLOR.celestial, pointBorderWidth: 2 },
        { label: "P25", data: p25, borderColor: "rgba(186, 215, 247, 0.4)", fill: false,
          tension: 0.35, borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0 },
      ]},
      options: {
        ...baseOpts(),
        scales: { x: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper } },
                  y: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, callback: (v) => `${v}%` } } },
        plugins: { ...baseOpts().plugins,
          legend: { display: true, position: "top", align: "end",
            labels: { color: COLOR.whisper, font: { size: 11 }, boxWidth: 10, boxHeight: 2, padding: 12 } } },
      },
    });
  }

  /* ---------- Breadth page ---------- */
  function renderRegimeGauge() {
    const cv = $("gaugeRegime");
    const score = state.breadth?.regime_score;
    if (score == null) return;
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);

    const cx = W / 2, cy = H * 0.92, radius = Math.min(W * 0.42, H * 0.78);
    const start = Math.PI, end = 2 * Math.PI;       // semicircle, left -> right
    const arcSweep = end - start;
    const ticks = 50;

    // Background segments (red → amber → green)
    for (let i = 0; i < ticks; i++) {
      const t0 = i / ticks, t1 = (i + 1) / ticks;
      const a0 = start + t0 * arcSweep;
      const a1 = start + t1 * arcSweep;
      const mid = (t0 + t1) / 2;
      let color;
      if (mid < 0.4)      color = `rgba(255, 122, 138, ${0.18 + 0.18 * (1 - mid / 0.4)})`;
      else if (mid < 0.6) color = "rgba(251, 191, 36, 0.32)";
      else                color = `rgba(111, 231, 179, ${0.18 + 0.30 * ((mid - 0.6) / 0.4)})`;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, a0, a1);
      ctx.lineWidth = 18;
      ctx.strokeStyle = color;
      ctx.lineCap = "butt";
      ctx.stroke();
    }

    // Outline ring
    ctx.beginPath();
    ctx.arc(cx, cy, radius, start, end);
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(186, 215, 247, 0.20)";
    ctx.stroke();

    // Tick marks at 0, 2, 4, 5, 6, 8, 10
    const ticks_lbl = [0, 2, 4, 5, 6, 8, 10];
    ctx.fillStyle = COLOR.whisper;
    ctx.font = "10px Inter";
    ctx.textAlign = "center";
    ticks_lbl.forEach((v) => {
      const a = start + (v / 10) * arcSweep;
      const rx = cx + (radius + 14) * Math.cos(a);
      const ry = cy + (radius + 14) * Math.sin(a);
      ctx.fillText(String(v), rx, ry + 3);
    });

    // Needle
    const ang = start + (Math.min(10, Math.max(0, score)) / 10) * arcSweep;
    const tipX = cx + (radius - 8) * Math.cos(ang);
    const tipY = cy + (radius - 8) * Math.sin(ang);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(tipX, tipY);
    ctx.lineWidth = 3;
    ctx.strokeStyle = COLOR.ghost;
    ctx.lineCap = "round";
    ctx.stroke();
    // hub
    ctx.beginPath();
    ctx.arc(cx, cy, 7, 0, 2 * Math.PI);
    ctx.fillStyle = COLOR.violet;
    ctx.fill();
    ctx.strokeStyle = COLOR.ghost;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Readout
    $("regimeScore").textContent = score.toFixed(1);
    const label = state.breadth.regime_label || "—";
    const lbl = $("regimeLabel");
    lbl.textContent = label.toUpperCase();
    lbl.className = "gauge-state " + (label.toLowerCase());
  }

  function renderBreadthBars() {
    const b = state.breadth || {};
    const labels = ["10-day", "20-day", "50-day", "200-day"];
    const data = [b.pct_above_sma10, b.pct_above_sma20, b.pct_above_sma50, b.pct_above_sma200];
    const ctx = $("chartBreadth").getContext("2d");
    charts.breadth?.destroy();
    charts.breadth = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{
        label: "% above SMA",
        data,
        backgroundColor: data.map((v) => {
          if (v == null) return "rgba(108, 119, 135, 0.5)";
          if (v >= 60) return gradient(ctx, "rgba(111, 231, 179, 0.95)", "rgba(111, 231, 179, 0.20)");
          if (v >= 40) return gradient(ctx, "rgba(251, 191, 36, 0.95)", "rgba(251, 191, 36, 0.18)");
          return gradient(ctx, "rgba(255, 122, 138, 0.95)", "rgba(255, 122, 138, 0.20)");
        }),
        borderRadius: 6,
        barThickness: 64,
      }]},
      options: {
        ...baseOpts(),
        scales: {
          x: { grid: { display: false }, ticks: { color: COLOR.comet, font: { size: 12, weight: 600 } } },
          y: { beginAtZero: true, max: 100,
               grid: { color: COLOR.grid },
               ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => `${c.parsed.y.toFixed(1)}% above SMA${labels[c.dataIndex].split("-")[0]}` } } } },
    });
  }

  function renderRegimeComponents() {
    const cs = state.breadth?.component_scores || {};
    const labels = ["% > SMA200", "% > SMA50", "% > SMA20", "Median 1M", "Median 3M", "Near 52w high"];
    const keys = ["pct200", "pct50", "pct20", "median_r1m", "median_r3m", "near_high"];
    const data = keys.map((k) => cs[k] ?? 0);
    const ctx = $("chartRegimeComponents").getContext("2d");
    charts.regimeComp?.destroy();
    charts.regimeComp = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ data,
        backgroundColor: data.map((v) =>
          v >= 7 ? "rgba(111, 231, 179, 0.75)" :
          v >= 4 ? "rgba(251, 191, 36, 0.75)" :
                   "rgba(255, 122, 138, 0.75)"),
        borderRadius: 4, barThickness: 22 }]},
      options: {
        ...baseOpts(),
        indexAxis: "y",
        scales: {
          x: { beginAtZero: true, max: 10, grid: { color: COLOR.grid },
               ticks: { color: COLOR.whisper, font: { size: 11 } } },
          y: { grid: { display: false }, ticks: { color: COLOR.arctic, font: { size: 11 } } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => `${c.parsed.x.toFixed(2)} / 10` } } },
      },
    });
  }

  function renderMedRet() {
    const b = state.breadth || {};
    const labels = ["1M", "3M", "6M"];
    const data = [b.median_r1m, b.median_r3m, b.median_r6m];
    const ctx = $("chartMedRet").getContext("2d");
    charts.medRet?.destroy();
    charts.medRet = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ data,
        backgroundColor: data.map((v) => v == null ? "#555" : v >= 0
          ? gradient(ctx, "rgba(111, 231, 179, 0.85)", "rgba(111, 231, 179, 0.20)")
          : gradient(ctx, "rgba(255, 122, 138, 0.85)", "rgba(255, 122, 138, 0.20)")),
        borderRadius: 6, barThickness: 56 }]},
      options: {
        ...baseOpts(),
        scales: {
          x: { grid: { display: false }, ticks: { color: COLOR.comet, font: { size: 12, weight: 600 } } },
          y: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => `${(c.parsed.y ?? 0).toFixed(2)}% median` } } },
      },
    });
  }

  function renderBreadthSignals() {
    const b = state.breadth || {};
    const cells = [
      { label: "Stocks analyzed", value: (b.universe_with_data ?? 0).toLocaleString("en-IN"), foot: "across all NSE + BSE" },
      { label: "% above SMA200", value: b.pct_above_sma200 != null ? `${b.pct_above_sma200.toFixed(1)}%` : "—",
        klass: b.pct_above_sma200 >= 50 ? "pos" : "neg", foot: "long-term participation" },
      { label: "% above SMA50",  value: b.pct_above_sma50  != null ? `${b.pct_above_sma50.toFixed(1)}%` : "—",
        klass: b.pct_above_sma50 >= 50 ? "pos" : "neg", foot: "medium-term momentum" },
      { label: "% above SMA20",  value: b.pct_above_sma20  != null ? `${b.pct_above_sma20.toFixed(1)}%` : "—",
        klass: b.pct_above_sma20 >= 50 ? "pos" : "neg", foot: "short-term momentum" },
      { label: "Within 10% of 52w high", value: b.pct_within_10_of_high != null ? `${b.pct_within_10_of_high.toFixed(1)}%` : "—",
        foot: "leadership concentration" },
      { label: "Trend Template setups", value: (b.trend_template_pass ?? 0).toLocaleString("en-IN"),
        foot: "strict Minervini 8/8 across universe" },
    ];
    $("signalGrid").innerHTML = cells.map((c) => `
      <article class="signal-card">
        <div class="signal-label">${c.label}</div>
        <div class="signal-value ${c.klass || ""}">${c.value}</div>
        <div class="signal-foot">${c.foot}</div>
      </article>`).join("");
  }

  /* ---------- Sectors page ---------- */
  function renderSectorMom() {
    const ss = state.sectors || [];
    if (!ss.length) return;
    const labels = ss.map((s) => `${s.sector} (${s.count})`);
    const data = ss.map((s) => s.avg_momentum ?? 0);
    const ctx = $("chartSectorMom").getContext("2d");
    charts.sectorMom?.destroy();
    charts.sectorMom = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ data,
        backgroundColor: data.map((v) => v >= 0
          ? gradient(ctx, "rgba(111, 231, 179, 0.90)", "rgba(111, 231, 179, 0.20)")
          : gradient(ctx, "rgba(255, 122, 138, 0.90)", "rgba(255, 122, 138, 0.20)")),
        borderRadius: 4, barThickness: 16 }]},
      options: {
        ...baseOpts(),
        indexAxis: "y",
        scales: {
          x: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` } },
          y: { grid: { display: false }, ticks: { color: COLOR.arctic, font: { size: 11 } } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => {
            const s = ss[c.dataIndex];
            return [`${s.sector}`, `Momentum: ${(s.avg_momentum ?? 0).toFixed(1)}%`, `Qualifiers: ${s.count}`, `TT pass: ${s.tt_pass}`];
          } } } },
      },
    });
  }

  function renderSectorHorizons() {
    const ss = state.sectors || [];
    if (!ss.length) return;
    const labels = ss.map((s) => s.sector);
    const ds = ["avg_r1m", "avg_r3m", "avg_r6m", "avg_r12m"];
    const dsLabels = ["1M", "3M", "6M", "12M"];
    const dsColors = [
      "rgba(182, 217, 252, 0.85)",
      "rgba(102, 58, 243, 0.80)",
      "rgba(111, 231, 179, 0.85)",
      "rgba(251, 191, 36, 0.85)",
    ];
    const ctx = $("chartSectorHorizons").getContext("2d");
    charts.sectorHorizons?.destroy();
    charts.sectorHorizons = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: ds.map((k, i) => ({
        label: dsLabels[i],
        data: ss.map((s) => s[k] ?? 0),
        backgroundColor: dsColors[i],
        borderRadius: 3,
      })) },
      options: {
        ...baseOpts(),
        scales: {
          x: { grid: { display: false }, ticks: { color: COLOR.arctic, font: { size: 10 }, maxRotation: 45, minRotation: 45 } },
          y: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` } },
        },
        plugins: { ...baseOpts().plugins,
          legend: { display: true, position: "top", align: "end",
            labels: { color: COLOR.whisper, font: { size: 11 }, boxWidth: 10, boxHeight: 10, padding: 12 } },
        },
      },
    });
  }

  function renderSectorTable() {
    const ss = state.sectors || [];
    const tbody = $("sectorBody");
    tbody.innerHTML = ss.map((s) => {
      const top = (s.top_symbols || []).map((t) => `${t.symbol}<span class="muted"> ${t.rs_rating}</span>`).join(" · ");
      return `<tr>
        <td class="sector-name"><a href="#" data-sector="${s.sector}" class="sector-link">${s.sector}</a></td>
        <td class="num">${s.count}</td>
        <td class="num">${s.tt_pass}</td>
        <td class="num">${fmtPct(s.avg_r1m)}</td>
        <td class="num">${fmtPct(s.avg_r3m)}</td>
        <td class="num">${fmtPct(s.avg_r6m)}</td>
        <td class="num">${fmtPct(s.avg_r12m)}</td>
        <td class="num">${fmtPct(s.avg_momentum)}</td>
        <td class="top-syms">${top}</td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll(".sector-link").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        state.filters.sector = a.dataset.sector;
        document.querySelector('.tab[data-tab="scanner"]').click();
        $("searchBox").value = "";
        renderTable();
      });
    });
  }

  function renderAll() {
    renderScannerKpis();
    renderTop();
    renderDist();
    renderScatter();
    renderHorizon();
    renderTable();

    renderRegimeGauge();
    renderBreadthBars();
    renderRegimeComponents();
    renderMedRet();
    renderBreadthSignals();

    renderSectorMom();
    renderSectorHorizons();
    renderSectorTable();
  }

  /* ============================================================ bindings */
  function bindFilters() {
    $("searchBox").addEventListener("input", (e) => { state.filters.q = e.target.value; renderTable(); });
    $("rsSlider").addEventListener("input", (e) => {
      state.filters.minRs = +e.target.value;
      $("rsValue").textContent = e.target.value;
      renderTable();
    });
    $("liqInput").addEventListener("input", (e) => { state.filters.minLiq = +e.target.value || 0; renderTable(); });
    $("ttOnly").addEventListener("change", (e) => { state.filters.ttOnly = e.target.checked; renderTable(); });
    $("near52w").addEventListener("change", (e) => { state.filters.near52w = e.target.checked; renderTable(); });

    document.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const k = th.dataset.key;
        if (state.sortKey === k) state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        else { state.sortKey = k; state.sortDir = ["symbol", "name", "sector"].includes(k) ? "asc" : "desc"; }
        renderTable();
      });
    });

    $("exportCsv").addEventListener("click", () => {
      const headers = ["rs_rating", "symbol", "name", "exchange", "sector", "close", "r1m", "r3m", "r6m", "r12m",
                       "pct_off_high", "turnover_cr", "vol_surge", "trend_template"];
      const csv = [headers.join(",")]
        .concat(state.rows.map((r) => headers.map((h) => {
          const v = r[h];
          if (v == null) return "";
          if (typeof v === "string" && (v.includes(",") || v.includes('"'))) return `"${v.replace(/"/g, '""')}"`;
          return v;
        }).join(",")))
        .join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = `phenom_scan_${(state.meta?.generated_at || "").slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  }

  /* ============================================================ load */
  async function load() {
    try {
      const r = await fetch(DATA_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      state.meta = data.meta;
      state.rows = data.results || [];
      state.breadth = data.breadth || {};
      state.sectors = data.sectors || [];
      $("loadState").classList.add("hidden");
      renderAll();
    } catch (e) {
      $("loadState").innerHTML = `<span class="neg">Could not load scan output: ${e.message}.</span><br/><span class="muted" style="font-size:12px">Run <code>python scan.py</code> then refresh.</span>`;
    }
  }

  bindTabs();
  bindFilters();
  load();
})();
