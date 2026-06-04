(() => {
  /* ---- Market selection (India ⇄ US). Sticky via localStorage, default last-used.
     MARKET is fixed per page load; the navbar toggle persists the choice and reloads,
     so every data fetch + render re-runs cleanly for the chosen market. ---- */
  const MARKET = (localStorage.getItem("phenom_market") === "us") ? "us" : "in";
  const IS_US = MARKET === "us";
  const dpath = (base) => `../data/${base}${IS_US ? "_us" : ""}.json`;
  const DATA_URL = dpath("scanner_output");
  const LOCALE = IS_US ? "en-US" : "en-IN";   // number grouping (lakh/cr vs thousands)
  const CUR = IS_US ? "$" : "₹";               // currency symbol
  const LIQ_UNIT = IS_US ? "M" : "cr";         // daily-liquidity unit ($M vs ₹cr)
  const LIQ_MIN = IS_US ? "$20M/day" : "₹10 cr/day";   // qualifier liquidity floor (display)
  const BREADTH_FOOT = IS_US ? "across all US listings" : "across all NSE + BSE";
  window.__PHENOM_MARKET = MARKET;             // shared with trade.js / news.js / hero.js

  const COLOR = {
    ghost: "#ffffff",
    comet: "#ece6e8",
    arctic: "#d8d1d4",
    celestial: "#f0455a",   /* red highlight */
    azure: "#b9b1b5",
    whisper: "#9b9196",
    interstellar: "#7c7378",
    violet: "#e11d2a",      /* primary red accent */
    violetSoft: "rgba(225, 29, 42, 0.55)",
    pos: "#5fd39b",
    neg: "#ff7a8a",
    warn: "#fbbf24",
    grid: "rgba(236, 222, 226, 0.08)",
    border: "rgba(236, 222, 226, 0.18)",
  };

  const state = {
    rows: [],
    ipos: [],
    eps: [],
    breadth: {},
    sectors: [],
    history: null,
    meta: null,
    sortKey: "rs_rating",
    sortDir: "desc",
    filters: { q: "", minRs: 0, minLiq: 0, sector: "" },
    activeTab: "scanner",
    activeScanner: "momentum",
  };

  /* ============================================================ Scanners
     Each scanner applies a filter on top of the base qualifier set
     (close > SMA200 AND turnover >= 2 cr — already enforced by scan.py). */
  const SCANNERS = {
    momentum: {
      label: "Momentum",
      title: "All qualifiers",
      sub: `Uptrend (above 50 & 200 SMA) · within 25% of 52w high · positive 3M & 6M · ≥ ${LIQ_MIN} · ranked by RS`,
      filter: (r) => true,   // base gate already enforces the full momentum set
    },
    trend_template: {
      label: "Trend Template",
      title: "Minervini Trend Template",
      sub: "Strict 8/8: above all SMAs, SMA cascade, rising 200‑SMA, within 25% of 52w high, 30%+ above 52w low",
      filter: (r) => r.trend_template === true,
    },
    fundamentals: {
      label: "Fundamental Leaders",
      title: "Fundamental Leaders — growth + trend",
      sub: "Technical leaders that ALSO have real growth: EPS up ≥ 25% and sales up ≥ 20% year‑on‑year (Minervini's growth test). Fundamentals via TradingView",
      filter: (r) => (r.eps_growth != null && r.eps_growth >= 25) && (r.sales_growth != null && r.sales_growth >= 20),
    },
    qm_breakout: {
      label: "Qullamaggie Breakout",
      title: "Qullamaggie Breakout setups",
      sub: "High‑ADR movers (ADR ≥ 4%) above their 10/20/50‑day SMAs that already ran 25%+ and are now coiling tight near the highs — the classic momentum‑burst‑then‑flag breakout",
      filter: (r) => r.qm_breakout === true,
    },
    vcp: {
      label: "VCP / Tight",
      title: "Minervini VCP — tight coils",
      sub: "Stage‑2 leaders within 15% of their high whose range is contracting while volume dries up — the pre‑breakout volatility contraction",
      filter: (r) => r.vcp_setup === true,
    },
    power_play: {
      label: "Power Play",
      title: "Minervini Power Play — high tight flag",
      sub: "Explosive +100% move in ≤ 8 weeks, then a tight (≤ 25%) 3–6 week consolidation still near the high — Minervini's 'velocity pattern', the one setup he'll buy without fundamentals",
      filter: (r) => r.power_play === true,
    },
    breakout52w: {
      label: "52w High Breakout",
      title: "52‑Week High Breakout",
      sub: "Within 2% of 52‑week high with a positive 1‑month return — fresh leadership",
      filter: (r) => r.pct_off_high != null && r.pct_off_high <= 2 && (r.r1m ?? 0) > 0,
    },
    vol_shocker: {
      label: "Volume Shocker",
      title: "Volume Shocker",
      sub: "Today's volume ≥ 2.5× the 50‑day average with a positive 1‑month return — likely institutional accumulation",
      filter: (r) => (r.vol_surge ?? 0) >= 2.5 && (r.r1m ?? 0) > 0,
    },
    episodic_pivot: {
      label: "Episodic Pivot",
      title: "Episodic Pivots — gap + volume off a base",
      sub: "Stocks that gapped up 5%+ on 3×+ volume out of a quiet base (not already extended) — the market repricing a surprise. Daily‑data read of Qullamaggie's EP setup",
      filter: (r) => true,            // applied via state.eps source instead
      source: "eps",                  // marker: use state.eps not state.rows
    },
    ipo: {
      label: "IPO Momentum",
      title: "Fresh IPOs near their highs",
      sub: "Recent listings (30 to 199 trading bars, ~6 weeks to ~10 months old) within 25% of their all‑time high, liquid, and trading above the 20‑bar SMA",
      filter: (r) => true,            // applied via state.ipos source instead
      source: "ipos",                 // marker: use state.ipos not state.rows
    },
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
    return v.toLocaleString(LOCALE, { maximumFractionDigits: d, minimumFractionDigits: d });
  };
  const fmtX = (v) => {
    if (v == null || Number.isNaN(v)) return "—";
    const cls = v >= 1.5 ? "pos" : v <= 0.8 ? "neg" : "muted";
    return `<span class="${cls}">${v.toFixed(2)}×</span>`;
  };
  const rsBadge = (v) => {
    if (v == null || Number.isNaN(v)) return `<span class="muted">—</span>`;
    const k = v >= 80 ? "rs-hi" : v >= 50 ? "rs-md" : "rs-lo";
    return `<span class="rs ${k}">${v}</span>`;
  };
  const fmtAdr = (v) => v == null || Number.isNaN(v) ? "—" : `${v.toFixed(1)}%`;
  // YoY growth %, color-coded; caps the display so a +900% doesn't blow the cell
  const fmtGrowth = (v) => {
    if (v == null || Number.isNaN(v)) return `<span class="muted">—</span>`;
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
    const s = v > 0 ? "+" : "";
    return `<span class="${cls}">${s}${v.toFixed(0)}%</span>`;
  };
  // Styled hover tooltip for the ticker (custom — not the native title). The
  // secondary line folds in sector/industry, liquidity and volume (those columns
  // were removed from the table so it fits without scroll).
  const attr = (s) => String(s == null ? "" : s).replace(/"/g, "&quot;");
  const symSub = (r) => {
    const bits = [];
    const ind = (r.industry && r.industry !== "Other") ? r.industry
              : (r.sector && r.sector !== "Other") ? r.sector : "";
    if (ind) bits.push(ind);
    if (r.turnover_cr != null && !Number.isNaN(r.turnover_cr)) bits.push(`${CUR}${fmtNum(r.turnover_cr, 1)} ${LIQ_UNIT}/day`);
    if (r.vol_surge != null && !Number.isNaN(r.vol_surge)) bits.push(`${r.vol_surge.toFixed(1)}× vol`);
    if (r.surfing_ma) bits.push(`surfing ${r.surfing_ma}D MA`);
    if (r.higher_lows >= 2) bits.push(`${r.higher_lows} higher lows`);
    if (r.power_play) bits.push("Power Play");
    if (r.pocket_pivot) bits.push("pocket pivot");
    if (r.vcp_footprint) bits.push(`VCP ${r.vcp_footprint}`);
    return bits.join("  ·  ");
  };
  const symbolCell = (r, badge) =>
    `<td class="sym"><a href="${tvLink(r.symbol, r.exchange)}" ${tvAnchorAttrs} data-nm="${attr(r.name || r.symbol)}" data-sub="${attr(symSub(r))}">${r.symbol}</a>${badge || ""}</td>`;

  // Earnings badge: shows the next-earnings date; amber if within 10 days (event risk)
  const earningsBadge = (r) => {
    if (!r.next_earnings) return `<span class="muted">—</span>`;
    const d = r.days_to_earnings;
    const risk = d != null && d >= 0 && d <= 3;   // Qullamaggie: never buy within ~3 days of earnings
    const soon = d != null && d >= 0 && d <= 10;
    const dt = new Date(r.next_earnings);
    const lbl = Number.isNaN(dt.getTime()) ? r.next_earnings
      : dt.toLocaleDateString(LOCALE, { day: "2-digit", month: "short" });
    const tail = d != null && d >= 0 ? ` <span class="muted">${d}d</span>` : "";
    const cls = risk ? "earn-risk" : soon ? "earn-soon" : "";
    const ttl = risk ? `earnings in ${d} days — avoid new entries (Qullamaggie rule)`
                     : `next earnings${d != null ? ` in ${d} days` : ""}`;
    return `<span class="${cls}" title="${ttl}">${lbl}${tail}</span>`;
  };
  // Mobile detection — used to choose between TV chart (desktop, new tab)
  // and the TV symbol page (mobile, universal link that opens the TV app).
  const IS_MOBILE = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);

  const tvLink = (sym, exch) => {
    const e = IS_US ? ((exch && ["NASDAQ", "NYSE", "AMEX"].includes(exch)) ? exch : "NASDAQ")
                    : (exch === "NSE" ? "NSE" : "BSE");
    if (IS_MOBILE) {
      // Universal link: iOS/Android route this to the TradingView app if
      // installed, otherwise it opens the mobile-friendly symbol page.
      return `https://www.tradingview.com/symbols/${e}-${encodeURIComponent(sym)}/`;
    }
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(e + ":" + sym)}`;
  };

  const tvAnchorAttrs = IS_MOBILE
    ? `rel="noopener"`
    : `target="_blank" rel="noopener"`;

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
        // Build this tab's charts now that it's visible (correct sizing).
        // setTimeout(0) lets the display:flex layout settle before measuring.
        setTimeout(() => renderTabCharts(name), 30);
      });
    });
  }

  /* ============================================================ market toggle */
  function applyMarketLabels() {
    const set = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
    if (IS_US) {
      set("heroSub", "Every US listing above its 200-day SMA with real liquidity, ranked Minervini-style.");
      set("statWithDataFoot", "tradeable · NASDAQ + NYSE + AMEX");
      set("liqLabel", "Min $M");
      set("footSource", "Data via Nasdaq Trader + Yahoo EOD · news via Google News. Educational, not investment advice.");
    } else {
      set("heroSub", "Every NSE + BSE listing above its 200-day SMA with real liquidity, ranked Minervini-style.");
      set("statWithDataFoot", "tradeable · NSE + BSE bhavcopy");
      set("liqLabel", "Min ₹cr");
      set("footSource", "Data via NSE + BSE bhavcopy · news via Google News. Educational, not investment advice.");
    }
  }

  function bindMarket() {
    document.querySelectorAll(".market-btn").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.market === MARKET);
      b.addEventListener("click", () => {
        if (b.dataset.market === MARKET) return;
        localStorage.setItem("phenom_market", b.dataset.market);
        location.reload();   // re-run load() + every render for the chosen market
      });
    });
  }

  /* ============================================================ subtabs */
  // Which data array a scanner reads from (qualifiers, IPOs, or episodic pivots)
  const srcArr = (def) => def?.source === "ipos" ? state.ipos
                        : def?.source === "eps" ? state.eps
                        : state.rows;
  function renderSubtabCounts() {
    Object.keys(SCANNERS).forEach((key) => {
      const def = SCANNERS[key];
      const src = srcArr(def);
      const n = src.filter(def.filter).length;
      const el = document.querySelector(`.subtab-count[data-count="${key}"]`);
      if (el) el.textContent = n.toLocaleString(LOCALE);
    });
  }
  function renderScannerHeader() {
    const s = SCANNERS[state.activeScanner];
    if (!s) return;
    $("scannerTitle").textContent = s.title;
    $("scannerSub").textContent = s.sub;
    document.querySelectorAll(".subtab").forEach((el) =>
      el.classList.toggle("is-active", el.dataset.scanner === state.activeScanner)
    );
    // IPO mode renames a few columns: RS -> Bars, 1M stays, 6M -> 1W, 12M -> Since IPO
    const isIpo = s.source === "ipos";
    const headerMap = {
      rs_rating:    isIpo ? "Bars"      : "RS",
      r1m:          isIpo ? "1M"        : "1M",
      r3m:          isIpo ? "3M"        : "3M",
      r6m:          isIpo ? "1W"        : "6M",
      r12m:         isIpo ? "Since IPO" : "12M",
      trend_template: isIpo ? "—"       : "TT",
    };
    document.querySelectorAll("th[data-key]").forEach((th) => {
      const k = th.dataset.key;
      if (headerMap[k] !== undefined) th.firstChild.textContent = headerMap[k];
    });
  }

  /* ============================================================ table */
  function renderTable() {
    const tbody = $("resultsBody");
    const f = state.filters;
    const q = f.q.trim().toLowerCase();
    const def = SCANNERS[state.activeScanner];
    const scannerFilter = def?.filter || (() => true);
    const isIpo = def?.source === "ipos";
    const sourceRows = srcArr(def);

    let rows = sourceRows.filter(scannerFilter).filter((r) => {
      if (!isIpo && r.rs_rating != null && r.rs_rating < f.minRs) return false;
      if (r.turnover_cr < f.minLiq) return false;
      if (f.sector && r.sector !== f.sector) return false;
      if (q) {
        const hay = (r.symbol + " " + (r.name || "") + " " + (r.sector || "")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    // In IPO mode, several normal column data-keys map to IPO-only fields:
    //   rs_rating header is relabeled "Bars" -> sort by `bars`
    //   r6m header is relabeled "1W"         -> sort by `r1w`
    //   r12m header is relabeled "Since IPO" -> sort by `since_listing_pct`
    const IPO_KEY_MAP = { rs_rating: "bars", r6m: "r1w", r12m: "since_listing_pct" };
    const dir = state.sortDir === "desc" ? -1 : 1;
    const key = isIpo ? (IPO_KEY_MAP[state.sortKey] || state.sortKey) : state.sortKey;
    rows.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });

    state.shownRows = rows;   // exactly what this scanner shows (filtered+sorted) — for per-scanner copy

    if (!rows.length) {
      tbody.innerHTML = "";
      $("emptyState").classList.remove("hidden");
    } else {
      $("emptyState").classList.add("hidden");
      const view = rows.slice(0, 800);
      if (isIpo) {
        // IPO row: bars-since-listing badge in the "RS" column, since-listing
        // return in the "12M" column, no Trend Template flag.
        tbody.innerHTML = view.map((r) => `
          <tr>
            <td class="num"><span class="rs rs-md" title="trading bars since listing">${r.bars}d</span></td>
            ${symbolCell(r)}
            <td class="num">${fmtNum(r.close, 2)}</td>
            <td class="num">${fmtPct(r.r1w)}</td>
            <td class="num">${fmtPct(r.r1m)}</td>
            <td class="num">${fmtPct(r.r3m)}</td>
            <td class="num">${fmtPct(r.since_listing_pct)}</td>
            <td class="num"><span class="muted">-${(r.pct_off_high ?? 0).toFixed(1)}%</span></td>
            <td class="num">${fmtAdr(r.adr_pct)}</td>
            <td class="num">${fmtGrowth(r.eps_growth)}</td>
            <td class="num">${fmtGrowth(r.sales_growth)}</td>
            <td class="num earn-cell">${earningsBadge(r)}</td>
            <td class="center"><span class="tt-no" title="not applicable for IPOs">·</span></td>
          </tr>`).join("");
      } else {
        tbody.innerHTML = view.map((r) => `
          <tr>
            <td class="num">${rsBadge(r.rs_rating)}</td>
            ${symbolCell(r)}
            <td class="num">${fmtNum(r.close, 2)}</td>
            <td class="num">${fmtPct(r.r1m)}</td>
            <td class="num">${fmtPct(r.r3m)}</td>
            <td class="num">${fmtPct(r.r6m)}</td>
            <td class="num">${fmtPct(r.r12m)}</td>
            <td class="num"><span class="muted">-${(r.pct_off_high ?? 0).toFixed(1)}%</span></td>
            <td class="num">${fmtAdr(r.adr_pct)}</td>
            <td class="num">${fmtGrowth(r.eps_growth)}</td>
            <td class="num">${fmtGrowth(r.sales_growth)}</td>
            <td class="num earn-cell">${earningsBadge(r)}</td>
            <td class="center">${r.trend_template ? '<span class="tt-yes">✓</span>' : '<span class="tt-no">·</span>'}</td>
          </tr>`).join("");
      }
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

    $("statWithData").textContent = (m.with_data ?? 0).toLocaleString(LOCALE);
    $("statQualifiers").textContent = (m.qualifiers ?? 0).toLocaleString(LOCALE);
    $("statTT").textContent = rows.filter((r) => r.trend_template).length.toLocaleString(LOCALE);

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
    $("lastScan").textContent = t.toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
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
               ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${CUR}${v}${LIQ_UNIT}` },
               title: { display: true, text: `Daily liquidity (${CUR} ${LIQ_UNIT}, log)`, color: COLOR.whisper, font: { size: 11 } } },
          y: { grid: { color: COLOR.grid }, ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` },
               title: { display: true, text: "Momentum composite", color: COLOR.whisper, font: { size: 11 } } },
        },
        plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip,
          callbacks: { label: (c) => {
            const p = pts[c.dataIndex];
            return [p.sym, `RS ${p.rs}`, `Liq ${CUR}${p.x.toFixed(1)}${LIQ_UNIT}`, `Mom ${p.y.toFixed(1)}%`];
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
      { label: "Suggested exposure", value: b.suggested_exposure_pct != null ? `${b.suggested_exposure_pct}%` : "—",
        klass: (b.suggested_exposure_pct ?? 0) >= 60 ? "pos" : (b.suggested_exposure_pct ?? 0) >= 40 ? "" : "neg",
        foot: b.exposure_note || "scale risk to market health (Minervini)" },
      { label: "Stocks analyzed", value: (b.universe_with_data ?? 0).toLocaleString(LOCALE), foot: BREADTH_FOOT },
      { label: "% above SMA200", value: b.pct_above_sma200 != null ? `${b.pct_above_sma200.toFixed(1)}%` : "—",
        klass: b.pct_above_sma200 >= 50 ? "pos" : "neg", foot: "long-term participation" },
      { label: "% above SMA50",  value: b.pct_above_sma50  != null ? `${b.pct_above_sma50.toFixed(1)}%` : "—",
        klass: b.pct_above_sma50 >= 50 ? "pos" : "neg", foot: "medium-term momentum" },
      { label: "% above SMA20",  value: b.pct_above_sma20  != null ? `${b.pct_above_sma20.toFixed(1)}%` : "—",
        klass: b.pct_above_sma20 >= 50 ? "pos" : "neg", foot: "short-term momentum" },
      { label: "Within 10% of 52w high", value: b.pct_within_10_of_high != null ? `${b.pct_within_10_of_high.toFixed(1)}%` : "—",
        foot: "leadership concentration" },
    ];
    $("signalGrid").innerHTML = cells.map((c) => `
      <article class="signal-card">
        <div class="signal-label">${c.label}</div>
        <div class="signal-value ${c.klass || ""}">${c.value}</div>
        <div class="signal-foot">${c.foot}</div>
      </article>`).join("");
  }

  /* ---------- Sectors page ---------- */
  // Rebuild the Sectors-tab aggregate from the (fundamentals-enriched) rows. Used for the
  // US market, whose scan-time sectors are all "Other" until TradingView sectors are merged.
  function computeSectors(rows) {
    const g = {};
    rows.forEach((r) => {
      const sec = (r.sector && r.sector !== "Other") ? r.sector : "Other";
      (g[sec] = g[sec] || []).push(r);
    });
    const avg = (arr, k) => {
      const v = arr.map((x) => x[k]).filter((x) => x != null && !Number.isNaN(x));
      return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0;
    };
    const multi = Object.keys(g).length > 1;
    return Object.entries(g)
      .filter(([sec]) => sec !== "Other" || !multi)
      .map(([sector, arr]) => ({
        sector, count: arr.length,
        tt_pass: arr.filter((x) => x.trend_template).length,
        avg_r1m: avg(arr, "r1m"), avg_r3m: avg(arr, "r3m"),
        avg_r6m: avg(arr, "r6m"), avg_r12m: avg(arr, "r12m"),
        avg_momentum: avg(arr, "momentum"),
        top_symbols: arr.slice().sort((a, b) => (b.rs_rating || 0) - (a.rs_rating || 0))
          .slice(0, 4).map((x) => ({ symbol: x.symbol, rs_rating: x.rs_rating })),
      }))
      .sort((a, b) => b.avg_momentum - a.avg_momentum);
  }

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
      "rgba(240, 69, 90, 0.85)",
      "rgba(225, 29, 42, 0.85)",
      "rgba(95, 211, 155, 0.85)",
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

  /* ============================================================ scanner history (per-subtab line chart) */
  function renderScannerHistory() {
    const card = document.querySelector(".scanner-history-card");
    if (!state.history?.dates) { if (card) card.style.display = "none"; return; }
    const def = SCANNERS[state.activeScanner];
    const data = state.history.scanners?.[state.activeScanner] || [];
    // Newer scanners (QM breakout, VCP, episodic pivot) have no per-day history —
    // hide the history card for them rather than showing a stale chart.
    if (!data.length) { if (card) card.style.display = "none"; return; }
    if (card) card.style.display = "";
    const labels = state.history.dates;
    const ctx = $("chartScannerHistory").getContext("2d");
    charts.scannerHistory?.destroy();
    charts.scannerHistory = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: def?.label || state.activeScanner,
          data,
          backgroundColor: gradient(ctx, "rgba(225, 29, 42, 0.95)", "rgba(225, 29, 42, 0.28)"),
          hoverBackgroundColor: COLOR.ghost,
          borderWidth: 0,
          borderRadius: 1,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
        }],
      },
      options: {
        ...baseOpts(),
        scales: {
          x: {
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: {
              color: COLOR.whisper, font: { size: 10 },
              maxTicksLimit: 12, autoSkip: true,
              callback: function (v) { const l = this.getLabelForValue(v); return l ? l.slice(0, 7) : ""; },
            },
          },
          y: {
            beginAtZero: true,
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: { color: COLOR.whisper, font: { size: 11 } },
            title: { display: true, text: "Matching stocks", color: COLOR.whisper, font: { size: 11 } },
          },
        },
        plugins: {
          ...baseOpts().plugins,
          tooltip: {
            ...baseOpts().plugins.tooltip,
            callbacks: {
              title: (items) => items[0]?.label || "",
              label: (c) => ` ${c.parsed.y.toLocaleString(LOCALE)} matches`,
            },
          },
        },
        interaction: { mode: "index", intersect: false },
      },
    });
    $("historyTitle").textContent = `1‑year scanner history: ${def?.label || state.activeScanner}`;
  }

  /* ============================================================ breadth history (4 MAs on Breadth tab) */
  function renderBreadthHistory() {
    if (!state.history?.dates || !state.history.breadth_pct) return;
    const labels = state.history.dates;
    const bp = state.history.breadth_pct;
    const MA_DEFS = [
      { key: "above_sma10",  label: "% > 10‑day SMA",  color: "rgba(150, 150, 162, 0.85)" }, // grey
      { key: "above_sma50",  label: "% > 50‑day SMA",  color: "rgba(255, 255, 255, 0.90)" }, // white
      { key: "above_sma200", label: "% > 200‑day SMA", color: "rgba(225, 29, 42, 0.95)" },   // red
    ];
    const ctx = $("chartBreadthHistory").getContext("2d");

    // Background plugin: paint the whole zone ABOVE the 50% line with a light
    // green gradient (bullish breadth) and below 50% a faint red (bearish).
    const zonePlugin = {
      id: "breadthZones",
      beforeDraw(chart) {
        const { ctx: c, chartArea: a, scales: { y } } = chart;
        if (!a || !y) return;
        const y50 = y.getPixelForValue(50);
        c.save();
        // Green above 50%
        const g = c.createLinearGradient(0, a.top, 0, y50);
        g.addColorStop(0, "rgba(111, 231, 179, 0.18)");
        g.addColorStop(1, "rgba(111, 231, 179, 0.02)");
        c.fillStyle = g;
        c.fillRect(a.left, a.top, a.right - a.left, y50 - a.top);
        // Faint red below 50%
        const r = c.createLinearGradient(0, y50, 0, a.bottom);
        r.addColorStop(0, "rgba(255, 122, 138, 0.02)");
        r.addColorStop(1, "rgba(255, 122, 138, 0.10)");
        c.fillStyle = r;
        c.fillRect(a.left, y50, a.right - a.left, a.bottom - y50);
        // 50% reference line (dimmed)
        c.strokeStyle = "rgba(120, 120, 130, 0.28)";
        c.lineWidth = 1;
        c.setLineDash([4, 4]);
        c.beginPath(); c.moveTo(a.left, y50); c.lineTo(a.right, y50); c.stroke();
        c.restore();
      },
    };

    charts.breadthHistory?.destroy();
    charts.breadthHistory = new Chart(ctx, {
      type: "line",
      plugins: [zonePlugin],
      data: {
        labels,
        datasets: [
          ...MA_DEFS.map((m) => ({
            label: m.label,
            data: bp[m.key] || [],
            borderColor: m.color,
            backgroundColor: "transparent",
            fill: false,
            tension: 0.25,
            borderWidth: m.key === "above_sma200" ? 2.5 : 1.6,
            pointRadius: 0,
            pointHoverRadius: 4,
          })),
        ],
      },
      options: {
        ...baseOpts(),
        scales: {
          x: {
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: {
              color: COLOR.whisper, font: { size: 10 },
              maxTicksLimit: 12, autoSkip: true,
              callback: function (v) { const l = this.getLabelForValue(v); return l ? l.slice(0, 7) : ""; },
            },
          },
          y: {
            min: 0, max: 100,
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: { color: COLOR.whisper, font: { size: 11 }, callback: (v) => `${v}%` },
          },
        },
        plugins: {
          ...baseOpts().plugins,
          legend: {
            display: true, position: "top", align: "end",
            labels: {
              color: COLOR.whisper, font: { size: 11 }, boxWidth: 10, boxHeight: 2, padding: 12,
              filter: (item) => item.text !== "Bullish zone",
            },
          },
          tooltip: {
            ...baseOpts().plugins.tooltip,
            filter: (item) => item.dataset.label !== "Bullish zone",
            callbacks: {
              title: (items) => items[0]?.label || "",
              label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? "—" : c.parsed.y.toFixed(1) + "%"}`,
            },
          },
        },
        interaction: { mode: "index", intersect: false },
      },
    });
  }

  /* ============================================================ net new highs (diverging bars) */
  function renderNetNewHighs() {
    const nnh = state.history?.net_new_highs;
    const labels = state.history?.dates;
    if (!nnh || !labels || !nnh.length) return;
    const ctx = $("chartNetNewHighs").getContext("2d");
    charts.netNewHighs?.destroy();
    charts.netNewHighs = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Net new highs",
          data: nnh,
          backgroundColor: nnh.map((v) => v >= 0 ? "rgba(111, 231, 179, 0.85)" : "rgba(255, 122, 138, 0.85)"),
          borderWidth: 0,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
        }],
      },
      options: {
        ...baseOpts(),
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: COLOR.whisper, font: { size: 10 },
              maxTicksLimit: 12, autoSkip: true,
              callback: function (v) { const l = this.getLabelForValue(v); return l ? l.slice(0, 7) : ""; },
            },
          },
          y: {
            grid: { color: COLOR.grid, drawBorder: false },
            ticks: { color: COLOR.whisper, font: { size: 11 } },
            title: { display: true, text: "New highs − new lows", color: COLOR.whisper, font: { size: 11 } },
          },
        },
        plugins: {
          ...baseOpts().plugins,
          tooltip: {
            ...baseOpts().plugins.tooltip,
            callbacks: {
              title: (items) => items[0]?.label || "",
              label: (c) => { const v = c.parsed.y; return ` ${v >= 0 ? "+" : ""}${v} net new ${v >= 0 ? "highs" : "lows"}`; },
            },
          },
        },
        interaction: { mode: "index", intersect: false },
      },
    });
  }

  // Render the charts for one tab. Only ~5 at a time (per tab), so no freeze.
  // Each chart is isolated in try/catch so one failure can't blank the rest.
  // Always re-renders on switch — guarantees correct canvas dimensions.
  function renderTabCharts(name) {
    const fns = {
      scanner: [renderTop, renderScannerHistory],
      breadth: [renderRegimeGauge, renderBreadthBars,
                renderBreadthSignals, renderBreadthHistory, renderNetNewHighs],
      sectors: [renderSectorMom, renderSectorHorizons, renderSectorTable],
    }[name] || [];
    for (const fn of fns) {
      try { fn(); }
      catch (e) { console.error(`chart render failed (${name}/${fn.name}):`, e); }
    }
  }

  function renderAll() {
    // Always-cheap parts
    renderScannerKpis();
    renderSubtabCounts();
    renderScannerHeader();
    renderTable();
    // Only the visible tab's charts up front; others render on first switch.
    renderTabCharts(state.activeTab);
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

    document.querySelectorAll(".subtab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const prev = state.activeScanner;
        state.activeScanner = btn.dataset.scanner;
        // Default sort when switching INTO IPO mode = since_listing_pct desc
        const enteringIpo  = SCANNERS[state.activeScanner]?.source === "ipos";
        const leavingIpo   = SCANNERS[prev]?.source === "ipos";
        if (enteringIpo && !leavingIpo) {
          state.sortKey = "r12m";     // mapped to since_listing_pct in IPO mode
          state.sortDir = "desc";
        } else if (leavingIpo && !enteringIpo) {
          state.sortKey = "rs_rating";
          state.sortDir = "desc";
        }
        renderScannerHeader();
        renderScannerHistory();
        renderTable();
      });
    });

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

    // TradingView import symbol for a row: NASDAQ:SYM / NYSE:SYM / NSE:SYM / BSE:SYM.
    // Prefer tv_exchange (the exchange TradingView actually resolves, set by
    // tv_validate.py) so a stock that only exists on the other exchange still works.
    const tvSym = (r) => {
      const ex = r.tv_exchange ? r.tv_exchange
               : IS_US ? ((r.exchange && ["NASDAQ", "NYSE", "AMEX"].includes(r.exchange)) ? r.exchange : "NASDAQ")
                       : (r.exchange === "NSE" ? "NSE" : "BSE");
      return `${ex}:${r.symbol}`;
    };
    // Copy a set of rows as a deduped TradingView watchlist string (EX:SYM,EX:SYM,…).
    const copyRowsToTV = async (rows, btn) => {
      const seen = new Set(), syms = [];
      for (const r of (rows || [])) {
        if (!r || !r.symbol) continue;
        if (r.tv_ok === false) continue;   // skip tickers TradingView can't resolve (dead/renamed/ETF gaps)
        const tv = tvSym(r);
        if (seen.has(tv)) continue;
        seen.add(tv); syms.push(tv);
      }
      const orig = btn.dataset.label || btn.textContent;
      btn.dataset.label = orig;
      const done = (msg) => { btn.textContent = msg; setTimeout(() => { btn.textContent = btn.dataset.label; }, 2000); };
      if (!syms.length) { done("Nothing to copy"); return; }
      const text = syms.join(",");
      try {
        await navigator.clipboard.writeText(text);
        done(`Copied ${syms.length} ✓`);
      } catch (e) {
        const ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(`Copied ${syms.length} ✓`); }
        catch (_) { done("Copy failed"); }
        document.body.removeChild(ta);
      }
    };
    // "Copy list" = THIS scanner only, exactly as currently filtered/sorted (per-scanner).
    $("copyTV").addEventListener("click", () => copyRowsToTV(state.shownRows, $("copyTV")));
    // "Copy all" = every stock across ALL scanners (qualifiers + IPOs + EPs), deduped.
    $("copyTVAll").addEventListener("click", () =>
      copyRowsToTV([...state.rows, ...state.ipos, ...state.eps], $("copyTVAll")));
  }

  /* ============================================================ ticker tooltip
     A single floating dark tooltip (styled like the chart tooltips) that follows
     the cursor when hovering any ticker with data-nm/data-sub. Replaces the
     native title popup. */
  function initTickerTooltip() {
    const tip = document.createElement("div");
    tip.className = "ph-tip";
    tip.innerHTML = '<div class="ph-tip-title"></div>' +
      '<div class="ph-tip-row"><span class="ph-tip-dot"></span><span class="ph-tip-sub"></span></div>';
    document.body.appendChild(tip);
    const titleEl = tip.querySelector(".ph-tip-title");
    const subEl = tip.querySelector(".ph-tip-sub");
    const rowEl = tip.querySelector(".ph-tip-row");
    let cur = null;
    const place = (x0, y0) => {
      const pad = 14, r = tip.getBoundingClientRect();
      let x = x0 + pad, y = y0 + pad;
      if (x + r.width > innerWidth - 8) x = x0 - r.width - pad;
      if (y + r.height > innerHeight - 8) y = y0 - r.height - pad;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    };
    // single mousemove handler — works for real and synthetic events
    document.addEventListener("mousemove", (e) => {
      const a = e.target.closest && e.target.closest("a[data-nm]");
      if (a) {
        if (a !== cur) {
          cur = a;
          titleEl.textContent = a.dataset.nm || "";
          const sub = a.dataset.sub || "";
          subEl.textContent = sub;
          rowEl.style.display = sub ? "flex" : "none";
          tip.classList.add("show");
        }
        place(e.clientX, e.clientY);
      } else if (cur) {
        cur = null;
        tip.classList.remove("show");
      }
    });
    // also hide on scroll (tooltip would otherwise stick)
    document.addEventListener("scroll", () => { if (cur) { cur = null; tip.classList.remove("show"); } }, true);
  }

  /* ============================================================ load */
  async function load() {
    try {
      const r = await fetch(DATA_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      state.meta = data.meta;
      state.rows = data.results || [];
      state.ipos = data.ipos || [];
      state.eps = data.episodic_pivots || [];
      state.breadth = data.breadth || {};
      state.sectors = data.sectors || [];
      state.history = data.history || null;
      // Fundamentals (TradingView) — separate file, non-fatal. Merge EPS/sales
      // growth, P/E, ROE, next earnings into every row by symbol.
      try {
        const fr = await fetch(dpath("fundamentals"), { cache: "no-store" });
        if (fr.ok) {
          const fd = (await fr.json()).data || {};
          const merge = (arr) => arr.forEach((r) => {
            const f = fd[(r.symbol || "").toUpperCase()];
            if (f) {
              Object.assign(r, {
                eps_growth: f.eps_growth, sales_growth: f.sales_growth,
                profit_growth: f.profit_growth, pe: f.pe, roe: f.roe,
                next_earnings: f.next_earnings, days_to_earnings: f.days_to_earnings,
                industry: f.industry,
              });
              // real sector from TradingView overrides the sparse "Other" map
              if (f.sector) r.sector = f.sector;
            }
          });
          merge(state.rows); merge(state.ipos); merge(state.eps);
          // US (sparse scan-time sectors) → rebuild the Sectors aggregate from the
          // now-enriched rows so the Sectors tab shows real sectors, not one "Other".
          if (state.sectors.length < 3 && state.rows.length) {
            state.sectors = computeSectors(state.rows);
          }
        }
      } catch (e) { /* fundamentals optional */ }
      $("loadState").classList.add("hidden");
      renderAll();
    } catch (e) {
      $("loadState").innerHTML = `<span class="neg">Could not load scan output: ${e.message}.</span><br/><span class="muted" style="font-size:12px">Run <code>python scan.py</code> then refresh.</span>`;
    }
  }

  bindTabs();
  bindMarket();
  applyMarketLabels();
  bindFilters();
  initTickerTooltip();
  load();
})();
