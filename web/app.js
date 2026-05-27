(() => {
  const DATA_URL = "../data/scanner_output.json";

  const state = {
    rows: [],
    meta: null,
    sortKey: "rs_rating",
    sortDir: "desc",
    filters: {
      q: "",
      minRs: 0,
      minLiq: 0,
      ttOnly: false,
      near52w: false,
      exchange: "",
    },
  };

  const $ = (id) => document.getElementById(id);

  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
    const sign = v > 0 ? "+" : "";
    return `<span class="${cls}">${sign}${v.toFixed(1)}%</span>`;
  }
  function fmtNum(v, digits = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }
  function fmtX(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const cls = v >= 1.5 ? "pos" : v <= 0.8 ? "neg" : "muted";
    return `<span class="${cls}">${v.toFixed(2)}×</span>`;
  }
  function rsBadge(v) {
    const klass = v >= 80 ? "rs-hi" : v >= 50 ? "rs-md" : "rs-lo";
    return `<span class="rs ${klass}">${v}</span>`;
  }
  function exchTag(e) {
    return `<span class="exch-tag ${e.toLowerCase()}">${e}</span>`;
  }
  function tvLink(yf, sym, exch) {
    const code = exch === "NSE" ? `NSE:${sym}` : `BSE:${sym}`;
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(code)}`;
  }

  function render() {
    const tbody = $("resultsBody");
    const f = state.filters;
    const q = f.q.trim().toLowerCase();

    let filtered = state.rows.filter((r) => {
      if (r.rs_rating < f.minRs) return false;
      if (r.turnover_cr < f.minLiq) return false;
      if (f.ttOnly && !r.trend_template) return false;
      if (f.near52w && (r.pct_off_high === null || r.pct_off_high > 10)) return false;
      if (f.exchange && r.exchange !== f.exchange) return false;
      if (q) {
        const hay = (r.symbol + " " + (r.name || "")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const dir = state.sortDir === "desc" ? -1 : 1;
    const key = state.sortKey;
    filtered.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });

    if (filtered.length === 0) {
      tbody.innerHTML = "";
      $("emptyState").classList.remove("hidden");
    } else {
      $("emptyState").classList.add("hidden");
      const rows = filtered.slice(0, 800).map((r) => `
        <tr>
          <td class="num">${rsBadge(r.rs_rating)}</td>
          <td class="sym"><a href="${tvLink(r.yf_ticker, r.symbol, r.exchange)}" target="_blank" rel="noopener">${r.symbol}</a></td>
          <td><span class="name" title="${r.name}">${r.name}</span></td>
          <td class="center">${exchTag(r.exchange)}</td>
          <td class="num">${fmtNum(r.close, 2)}</td>
          <td class="num">${fmtPct(r.r1m)}</td>
          <td class="num">${fmtPct(r.r3m)}</td>
          <td class="num">${fmtPct(r.r6m)}</td>
          <td class="num">${fmtPct(r.r12m)}</td>
          <td class="num"><span class="muted">-${(r.pct_off_high ?? 0).toFixed(1)}%</span></td>
          <td class="num">${fmtNum(r.turnover_cr, 2)}</td>
          <td class="num">${fmtX(r.vol_surge)}</td>
          <td class="center">${r.trend_template ? '<span class="tt-yes">✓</span>' : '<span class="tt-no">·</span>'}</td>
        </tr>`).join("");
      tbody.innerHTML = rows;
      if (filtered.length > 800) {
        tbody.innerHTML += `<tr><td colspan="13" class="muted center" style="padding:14px">Showing first 800 of ${filtered.length}. Tighten filters to see more.</td></tr>`;
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

  function bindControls() {
    $("searchBox").addEventListener("input", (e) => { state.filters.q = e.target.value; render(); });
    $("rsSlider").addEventListener("input", (e) => {
      state.filters.minRs = Number(e.target.value);
      $("rsValue").textContent = e.target.value;
      render();
    });
    $("liqInput").addEventListener("input", (e) => {
      state.filters.minLiq = Number(e.target.value) || 0;
      render();
    });
    $("ttOnly").addEventListener("change", (e) => { state.filters.ttOnly = e.target.checked; render(); });
    $("near52w").addEventListener("change", (e) => { state.filters.near52w = e.target.checked; render(); });
    $("exchSelect").addEventListener("change", (e) => { state.filters.exchange = e.target.value; render(); });

    document.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const k = th.dataset.key;
        if (state.sortKey === k) {
          state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        } else {
          state.sortKey = k;
          state.sortDir = ["symbol", "name", "exchange"].includes(k) ? "asc" : "desc";
        }
        render();
      });
    });

    $("exportCsv").addEventListener("click", () => {
      const headers = ["rs_rating", "symbol", "name", "exchange", "close", "r1m", "r3m", "r6m", "r12m", "pct_off_high", "turnover_cr", "vol_surge", "trend_template"];
      const lines = [headers.join(",")];
      for (const r of state.rows) {
        lines.push(headers.map((h) => {
          const v = r[h];
          if (v === null || v === undefined) return "";
          if (typeof v === "string" && v.includes(",")) return `"${v.replace(/"/g, '""')}"`;
          return v;
        }).join(","));
      }
      const blob = new Blob([lines.join("\n")], { type: "text/csv" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "scanner_output.csv";
      a.click();
      URL.revokeObjectURL(a.href);
    });
  }

  function renderMeta() {
    const m = state.meta;
    if (!m) return;
    $("statUniverse").textContent = (m.universe_size ?? 0).toLocaleString("en-IN");
    $("statWithData").textContent = (m.with_data ?? 0).toLocaleString("en-IN");
    $("statQualifiers").textContent = (m.qualifiers ?? 0).toLocaleString("en-IN");
    $("statLiq").textContent = `≥ ₹${(m.filters?.min_liquidity_cr ?? 0).toFixed(1)} cr`;
    const ttPass = state.rows.filter((r) => r.trend_template).length;
    $("statTT").textContent = ttPass.toLocaleString("en-IN");

    const t = new Date(m.generated_at);
    const ageHrs = (Date.now() - t.getTime()) / 3.6e6;
    const dot = $("freshDot");
    dot.className = "dot";
    if (ageHrs > 48) dot.classList.add("bad");
    else if (ageHrs > 24) dot.classList.add("stale");
    $("lastScan").textContent = t.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
    $("duration").textContent = `Scan duration: ${m.duration_s ?? "?"}s`;
  }

  async function load() {
    try {
      const r = await fetch(DATA_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      state.meta = data.meta;
      state.rows = data.results || [];
      $("loadState").classList.add("hidden");
      renderMeta();
      render();
    } catch (e) {
      $("loadState").innerHTML = `<p style="color:var(--red)">Could not load scanner output: ${e.message}.<br/>Run <code>python scan.py</code> first.</p>`;
    }
  }

  bindControls();
  load();
})();
