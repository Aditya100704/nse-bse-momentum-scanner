/* ============================================================================
   Phenom hero animation — a live, self-generating candlestick stream.
   Pure canvas, no deps. Candles form + scroll, a neon price line tracks the
   closes, the last price pulses with a dashed guide + label, and green candles
   throw off rising "spark" particles. A ticker tape scrolls underneath.
   Pauses when the tab is hidden or the Scanner page isn't visible.
   ========================================================================== */
(() => {
  "use strict";
  const canvas = document.getElementById("heroAnim");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const UP = "#5fd39b", DOWN = "#ff5566", LINE = "#ff3b46";
  const GRID = "rgba(236,222,226,0.045)";
  const RED = (a) => `rgba(225,29,42,${a})`;

  const MAXC = 48;                 // candles kept
  let W = 0, H = 0, DPR = 1;
  let candles = [];                // {o,h,l,c}
  let price = 100;
  let forming = null, formT = 0, formLen = 22;
  let sparks = [];
  let labelPrice = 100;            // eased display price
  let t = 0, raf = null;

  /* ---------- sizing ---------- */
  function resize() {
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  /* ---------- model ---------- */
  function tickPrice() {
    const vol = 0.55 + Math.random() * 1.7;
    const drift = 0.05;                       // gentle bullish bias
    price += (Math.random() - 0.5 + drift) * vol * 1.7;
    if (price < 18) price = 18 + Math.random() * 4;
  }
  function startCandle() { forming = { o: price, h: price, l: price, c: price }; formT = 0; formLen = 16 + (Math.random() * 22 | 0); }
  function finalizeCandle() {
    candles.push(forming);
    if (candles.length > MAXC) candles.shift();
    if (forming.c >= forming.o) spawnSparks(forming);
    startCandle();
  }
  function seed() {
    candles = []; price = 100; startCandle();
    for (let i = 0; i < MAXC; i++) {
      for (let k = 0; k < 6; k++) { tickPrice(); forming.c = price; forming.h = Math.max(forming.h, price); forming.l = Math.min(forming.l, price); }
      candles.push(forming); startCandle();
    }
    labelPrice = price;
  }

  function spawnSparks(c) {
    const n = 3 + (Math.random() * 4 | 0);
    for (let i = 0; i < n; i++) sparks.push({ cx: candles.length - 1, dx: (Math.random() - 0.5) * 6, y: 0, vy: 0.5 + Math.random() * 1.1, life: 1, r: 0.8 + Math.random() * 1.6 });
    if (sparks.length > 60) sparks.splice(0, sparks.length - 60);
  }

  /* ---------- geometry ---------- */
  function bounds() {
    let lo = Infinity, hi = -Infinity;
    for (const c of candles) { if (c.l < lo) lo = c.l; if (c.h > hi) hi = c.h; }
    if (forming) { lo = Math.min(lo, forming.l); hi = Math.max(hi, forming.h); }
    const pad = (hi - lo) * 0.12 + 1;
    return { lo: lo - pad, hi: hi + pad };
  }

  /* ---------- render ---------- */
  function draw() {
    const padL = 8, padR = 64, padT = 14, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const all = forming ? candles.concat(forming) : candles;
    const n = Math.max(all.length, 1);
    const step = plotW / (MAXC + 1);
    const cw = Math.max(2, step * 0.62);
    const { lo, hi } = bounds();
    const yOf = (p) => padT + plotH - ((p - lo) / (hi - lo || 1)) * plotH;
    const xOf = (i) => padL + (i + 0.5) * step;

    ctx.clearRect(0, 0, W, H);

    // grid
    ctx.strokeStyle = GRID; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) { const y = padT + (plotH * g) / 4; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke(); }

    // candles
    for (let i = 0; i < all.length; i++) {
      const c = all[i], x = xOf(i + (MAXC + 1 - n));
      const up = c.c >= c.o, col = up ? UP : DOWN;
      const isLast = i === all.length - 1;
      ctx.strokeStyle = col; ctx.fillStyle = col;
      ctx.globalAlpha = isLast ? 1 : 0.92;
      if (isLast) { ctx.shadowColor = col; ctx.shadowBlur = 12; } else ctx.shadowBlur = 0;
      // wick
      ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(x, yOf(c.h)); ctx.lineTo(x, yOf(c.l)); ctx.stroke();
      // body
      const yo = yOf(c.o), yc = yOf(c.c), top = Math.min(yo, yc), bh = Math.max(1.5, Math.abs(yc - yo));
      ctx.fillRect(x - cw / 2, top, cw, bh);
    }
    ctx.shadowBlur = 0; ctx.globalAlpha = 1;

    // neon close line
    ctx.beginPath();
    for (let i = 0; i < all.length; i++) { const x = xOf(i + (MAXC + 1 - n)), y = yOf(all[i].c); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
    const grad = ctx.createLinearGradient(0, 0, W, 0);
    grad.addColorStop(0, "rgba(255,59,70,0.15)"); grad.addColorStop(1, LINE);
    ctx.strokeStyle = grad; ctx.lineWidth = 2; ctx.shadowColor = RED(0.8); ctx.shadowBlur = 10;
    ctx.lineJoin = "round"; ctx.stroke(); ctx.shadowBlur = 0;

    // last price marker
    const last = all[all.length - 1];
    const lx = xOf(all.length - 1 + (MAXC + 1 - n)), ly = yOf(last.c);
    labelPrice += (last.c - labelPrice) * 0.2;
    // dashed guide
    ctx.strokeStyle = RED(0.35); ctx.setLineDash([4, 5]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, ly); ctx.lineTo(W - padR, ly); ctx.stroke(); ctx.setLineDash([]);
    // pulsing dot
    const pulse = 4 + Math.sin(t / 14) * 1.6;
    ctx.fillStyle = RED(0.18); ctx.beginPath(); ctx.arc(lx, ly, pulse + 6, 0, 7); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.shadowColor = LINE; ctx.shadowBlur = 14; ctx.beginPath(); ctx.arc(lx, ly, 3.2, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
    // price label
    const up = last.c >= last.o;
    ctx.fillStyle = up ? UP : DOWN;
    ctx.font = "600 13px 'IBM Plex Mono', monospace"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(labelPrice.toFixed(2), Math.min(lx + 10, W - padR + 6), ly);

    // sparks (rising bullish energy)
    for (const s of sparks) {
      const x = xOf(s.cx + (MAXC + 1 - n)) + s.dx;
      const y = yOf(candles[Math.min(s.cx, candles.length - 1)]?.c ?? price) - s.y;
      ctx.globalAlpha = Math.max(0, s.life);
      ctx.fillStyle = UP; ctx.shadowColor = UP; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.arc(x, y, s.r, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
  }

  function update() {
    // advance forming candle
    tickPrice();
    forming.c = price;
    forming.h = Math.max(forming.h, price);
    forming.l = Math.min(forming.l, price);
    if (++formT >= formLen) finalizeCandle();
    // sparks
    for (const s of sparks) { s.y += s.vy; s.vy *= 0.99; s.life -= 0.012; }
    sparks = sparks.filter((s) => s.life > 0);
  }

  /* ---------- loop (throttled ~30fps; pauses when hidden) ---------- */
  let acc = 0, prev = 0;
  function frame(ts) {
    raf = requestAnimationFrame(frame);
    const onScanner = document.querySelector('.page[data-page="scanner"]')?.classList.contains("is-active");
    if (document.hidden || !onScanner || !W) { prev = ts; return; }
    const dt = ts - prev; prev = ts; acc += dt; t++;
    if (acc < 33) return; acc = 0;            // ~30fps
    update(); draw();
  }

  /* ---------- ticker tape ---------- */
  function buildTape() {
    const tape = document.getElementById("heroTape");
    if (!tape) return;
    const syms = [["RELIANCE",2934],["TCS",4112],["HDFCBANK",1678],["CGPOWER",934],["INFY",1856],
      ["ICICIBANK",1244],["DIXON",14250],["TATAMOTORS",982],["SBIN",842],["BHARTIARTL",1620],
      ["LT",3680],["ADANIENT",2410],["DREDGECORP",1191],["IFCI",68],["SOUTHWEST",269]];
    const item = ([s, p]) => {
      const ch = (Math.random() * 6 - 2); const cls = ch >= 0 ? "pos" : "neg";
      const v = (p * (1 + ch / 100)).toFixed(p > 1000 ? 0 : 2);
      return `<span class="tk"><b>${s}</b><span>${v}</span><span class="${cls}">${ch >= 0 ? "▲" : "▼"}${Math.abs(ch).toFixed(2)}%</span></span>`;
    };
    const row = syms.concat(syms).map(item).join("");
    tape.innerHTML = `<div class="hero-tape-track">${row}</div>`;
  }

  /* ---------- init ---------- */
  function init() {
    resize(); seed(); buildTape();
    if (window.ResizeObserver) new ResizeObserver(() => { resize(); draw(); }).observe(canvas.parentElement);
    window.addEventListener("resize", () => { resize(); draw(); });
    draw();                                  // paint an immediate first frame (rAF is paused on hidden tabs)
    raf = requestAnimationFrame(frame);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
