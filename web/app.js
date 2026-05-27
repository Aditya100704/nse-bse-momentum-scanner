(() => {
  const DATA_URL = "../data/scanner_output.json";

  /* ---------- color tokens (synced with style.css) ---------- */
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

  /* ---------- state ---------- */
  const state = {
    rows: [],
    meta: null,
    sortKey: "rs_rating",
    sortDir: "desc",
    filters: { q: "", minRs: 0, minLiq: 0, ttOnly: false, near52w: false },
  };

  const $ = (id) => document.getElementById(id);

  /* ---------- formatters ---------- */
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
  const tvLink = (sym, exch) => {
    const code = exch === "NSE" ? `NSE:${sym}` : `BSE:${sym}`;
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(code)}`;
  };

  /* ---------- table ---------- */
  function renderTable() {
    const tbody = $("resultsBody");
    const f = state.filters;
    const q = f.q.trim().toLowerCase();

    let rows = state.rows.filter((r) => {
      if (r.rs_rating < f.minRs) return false;
      if (r.turnover_cr < f.minLiq) return false;
      if (f.ttOnly && !r.trend_template) return false;
      if (f.near52w && (r.pct_off_high == null || r.pct_off_high > 10)) return false;
      if (q) {
        const hay = (r.symbol + " " + (r.name || "")).toLowerCase();
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
          `<tr><td colspan="12" class="muted center" style="padding:14px">Showing first 800 of ${rows.length}. Tighten filters to see more.</td></tr>`);
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

  /* ---------- KPIs ---------- */
  function median(arr) {
    const xs = arr.filter((v) => v != null && !Number.isNaN(v)).sort((a, b) => a - b);
    if (!xs.length) return null;
    const m = Math.floor(xs.length / 2);
    return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
  }

  function renderKpis() {
    const m = state.meta;
    const rows = state.rows;
    if (!m) return;

    $("statUniverse").textContent = m.universe_size?.toLocaleString("en-IN") ?? "—";
    $("statQualifiers").textContent = m.qualifiers?.toLocaleString("en-IN") ?? "—";

    // Breadth: rows with price_gt_sma200 / with_data — but rows only contain those passing both gates.
    // So we infer: qualifiers/with_data is the floor of breadth. Show qualifiers/with_data ratio.
    const breadth = m.with_data ? m.qualifiers / m.with_data : 0;
    $("statBreadth").textContent = m.qualifiers?.toLocaleString("en-IN") ?? "—";
    $("statBreadthPct").textContent = `${(breadth * 100).toFixed(0)}%`;

    const ttCount = rows.filter((r) => r.trend_template).length;
    $("statTT").textContent = ttCount.toLocaleString("en-IN");

    const medMom = median(rows.map((r) => r.momentum));
    $("statMedMom").textContent = medMom != null ? `${medMom > 0 ? "+" : ""}${medMom.toFixed(1)}` : "—";

    // Market temp: based on the qualifier/with_data breadth + median 3M
    const med3m = median(rows.map((r) => r.r3m)) ?? 0;
    let label, klass;
    if (breadth >= 0.55 && med3m >= 10)      { label = "Risk On";   klass = "temp-hot"; }
    else if (breadth >= 0.40 || med3m >= 5)  { label = "Constructive"; klass = "temp-warm"; }
    else if (breadth >= 0.20)                { label = "Mixed";     klass = "temp-cool"; }
    else                                     { label = "Defensive"; klass = "temp-cold"; }
    const tempEl = $("statTemp");
    tempEl.innerHTML = `<span class="temp-pill ${klass}">${label}</span>`;
    $("statTempLabel").textContent = `breadth ${(breadth * 100).toFixed(0)}% · 3M med ${med3m.toFixed(1)}%`;

    // Last scan stamp
    const t = new Date(m.generated_at);
    const ageHrs = (Date.now() - t.getTime()) / 3.6e6;
    const badge = $("freshBadge");
    badge.classList.remove("stale", "bad");
    if (ageHrs > 48) badge.classList.add("bad");
    else if (ageHrs > 24) badge.classList.add("stale");
    $("lastScan").textContent = t.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
    $("duration").textContent = m.duration_s != null ? `${m.duration_s}s scan` : "";
  }

  /* ---------- Charts ---------- */
  let charts = {};

  function commonOpts() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      color: COLOR.whisper,
      font: { family: "Inter" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "rgba(5, 6, 15, 0.95)",
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
        x: {
          grid: { color: COLOR.grid, drawBorder: false },
          ticks: { color: COLOR.whisper, font: { size: 11, family: "Inter" } },
        },
        y: {
          grid: { color: COLOR.grid, drawBorder: false },
          ticks: { color: COLOR.whisper, font: { size: 11, family: "Inter" } },
        },
      },
    };
  }

  function gradient(ctx, top, bottom) {
    const g = ctx.createLinearGradient(0, 0, 0, 360);
    g.addColorStop(0, top);
    g.addColorStop(1, bottom);
    return g;
  }

  function renderTop() {
    const top = state.rows.slice(0, 15);
    const labels = top.map((r) => r.symbol);
    const data = top.map((r) => r.r3m ?? 0);
    const ctx = $("chartTop").getContext("2d");

    charts.top?.destroy();
    charts.top = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: data.map((v) => v >= 0 ? gradient(ctx, "rgba(111, 231, 179, 0.95)", "rgba(111, 231, 179, 0.25)")
                                                  : gradient(ctx, "rgba(255, 122, 138, 0.95)", "rgba(255, 122, 138, 0.25)")),
          borderRadius: 4,
          barThickness: 16,
        }],
      },
      options: {
        ...commonOpts(),
        indexAxis: "y",
        scales: {
          x: {
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` },
          },
          y: {
            grid: { display: false },
            ticks: { color: COLOR.arctic, font: { size: 11, family: "IBM Plex Mono" } },
          },
        },
        plugins: {
          ...commonOpts().plugins,
          tooltip: {
            ...commonOpts().plugins.tooltip,
            callbacks: {
              label: (ctx) => {
                const r = top[ctx.dataIndex];
                return [`${r.name || r.symbol}`, `3M: ${r.r3m?.toFixed(1)}%`, `RS: ${r.rs_rating}`];
              },
            },
          },
        },
      },
    });
  }

  function renderDist() {
    const vals = state.rows.map((r) => r.r1m).filter((v) => v != null);
    if (!vals.length) return;
    const min = Math.floor(Math.min(...vals) / 5) * 5;
    const max = Math.ceil(Math.max(...vals) / 5) * 5;
    const bins = [];
    for (let b = min; b < max; b += 5) {
      bins.push({ lo: b, hi: b + 5, count: 0 });
    }
    vals.forEach((v) => {
      const i = Math.min(bins.length - 1, Math.floor((v - min) / 5));
      if (i >= 0) bins[i].count++;
    });

    const labels = bins.map((b) => `${b.lo}…${b.hi}%`);
    const data = bins.map((b) => b.count);
    const colors = bins.map((b) => b.lo >= 0 ? "rgba(111, 231, 179, 0.55)" : "rgba(255, 122, 138, 0.55)");

    const ctx = $("chartDist").getContext("2d");
    charts.dist?.destroy();
    charts.dist = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{ data, backgroundColor: colors, borderRadius: 3 }],
      },
      options: {
        ...commonOpts(),
        plugins: {
          ...commonOpts().plugins,
          tooltip: {
            ...commonOpts().plugins.tooltip,
            callbacks: { label: (c) => `${c.parsed.y} stocks` },
          },
        },
      },
    });
  }

  function renderScatter() {
    const pts = state.rows.map((r) => ({
      x: r.turnover_cr,
      y: r.momentum,
      sym: r.symbol,
      rs: r.rs_rating,
      tt: r.trend_template,
    })).filter((p) => p.x > 0 && p.y != null);

    const ctx = $("chartScatter").getContext("2d");
    charts.scatter?.destroy();
    charts.scatter = new Chart(ctx, {
      type: "scatter",
      data: {
        datasets: [{
          data: pts,
          backgroundColor: pts.map((p) => p.tt ? "rgba(182, 217, 252, 0.75)" : "rgba(102, 58, 243, 0.45)"),
          borderColor: pts.map((p) => p.tt ? COLOR.celestial : COLOR.violetSoft),
          borderWidth: 1,
          pointRadius: pts.map((p) => Math.min(8, 2 + p.rs / 18)),
          pointHoverRadius: 8,
        }],
      },
      options: {
        ...commonOpts(),
        scales: {
          x: {
            type: "logarithmic",
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `₹${v}cr` },
            title: { display: true, text: "Daily liquidity (₹ cr, log)", color: COLOR.whisper, font: { size: 11 } },
          },
          y: {
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` },
            title: { display: true, text: "Momentum composite", color: COLOR.whisper, font: { size: 11 } },
          },
        },
        plugins: {
          ...commonOpts().plugins,
          tooltip: {
            ...commonOpts().plugins.tooltip,
            callbacks: {
              label: (c) => {
                const p = pts[c.dataIndex];
                return [p.sym, `RS ${p.rs}`, `Liq ₹${p.x.toFixed(1)}cr`, `Mom ${p.y.toFixed(1)}%`];
              },
            },
          },
        },
      },
    });
  }

  function renderHorizon() {
    const horizons = [
      { key: "r1m",  label: "1M"  },
      { key: "r3m",  label: "3M"  },
      { key: "r6m",  label: "6M"  },
      { key: "r12m", label: "12M" },
    ];
    const labels = horizons.map((h) => h.label);
    const medianData = horizons.map((h) => median(state.rows.map((r) => r[h.key])) ?? 0);
    const p75 = horizons.map((h) => {
      const xs = state.rows.map((r) => r[h.key]).filter((v) => v != null).sort((a, b) => a - b);
      return xs.length ? xs[Math.floor(xs.length * 0.75)] : 0;
    });
    const p25 = horizons.map((h) => {
      const xs = state.rows.map((r) => r[h.key]).filter((v) => v != null).sort((a, b) => a - b);
      return xs.length ? xs[Math.floor(xs.length * 0.25)] : 0;
    });

    const ctx = $("chartHorizon").getContext("2d");
    charts.horizon?.destroy();
    charts.horizon = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "P75",
            data: p75,
            borderColor: "rgba(111, 231, 179, 0.4)",
            backgroundColor: "rgba(111, 231, 179, 0.08)",
            fill: "+1",
            tension: 0.35,
            borderDash: [4, 4],
            borderWidth: 1.5,
            pointRadius: 0,
          },
          {
            label: "Median",
            data: medianData,
            borderColor: COLOR.celestial,
            backgroundColor: gradient(ctx, "rgba(182, 217, 252, 0.25)", "rgba(182, 217, 252, 0.02)"),
            fill: true,
            tension: 0.35,
            borderWidth: 2.5,
            pointRadius: 4,
            pointBackgroundColor: COLOR.ghost,
            pointBorderColor: COLOR.celestial,
            pointBorderWidth: 2,
          },
          {
            label: "P25",
            data: p25,
            borderColor: "rgba(186, 215, 247, 0.4)",
            fill: false,
            tension: 0.35,
            borderDash: [4, 4],
            borderWidth: 1.5,
            pointRadius: 0,
          },
        ],
      },
      options: {
        ...commonOpts(),
        scales: {
          x: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper } },
          y: {
            grid: { color: COLOR.grid },
            ticks: { color: COLOR.whisper, callback: (v) => `${v}%` },
          },
        },
        plugins: {
          ...commonOpts().plugins,
          legend: {
            display: true,
            position: "top",
            align: "end",
            labels: { color: COLOR.whisper, font: { size: 11 }, boxWidth: 10, boxHeight: 2, padding: 12 },
          },
        },
      },
    });
  }

  function renderCharts() {
    if (!state.rows.length) return;
    renderTop();
    renderDist();
    renderScatter();
    renderHorizon();
  }

  /* ---------- bindings ---------- */
  function bind() {
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
        if (state.sortKey === k) {
          state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        } else {
          state.sortKey = k;
          state.sortDir = ["symbol", "name"].includes(k) ? "asc" : "desc";
        }
        renderTable();
      });
    });

    $("exportCsv").addEventListener("click", () => {
      const headers = ["rs_rating", "symbol", "name", "exchange", "close", "r1m", "r3m", "r6m", "r12m",
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

  /* ---------- load ---------- */
  async function load() {
    try {
      const r = await fetch(DATA_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      state.meta = data.meta;
      state.rows = data.results || [];
      $("loadState").classList.add("hidden");
      renderKpis();
      renderCharts();
      renderTable();
    } catch (e) {
      $("loadState").innerHTML = `<span class="neg">Could not load scan output: ${e.message}.</span><br/><span class="muted" style="font-size:12px">Run <code>python scan.py</code> then refresh.</span>`;
    }
  }

  bind();
  load();
})();
