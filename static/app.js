/* QuantPro frontend */
const $ = (s) => document.querySelector(s);

const state = { window: "48h", league: "all", search: "", data: null };

// ---------- status pill ----------
async function pollStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    const pill = $("#status-pill");
    if (s.db_ready && !s.backtest_running) {
      pill.textContent = "Engine ready";
      pill.className = "status-pill ok";
    } else if (s.backtest_running) {
      pill.textContent = "Evaluating…";
      pill.className = "status-pill loading";
    } else {
      pill.textContent = s.message || "Preparing engine…";
      pill.className = "status-pill loading";
    }
    return s;
  } catch { return null; }
}

// ---------- navigation ----------
document.querySelectorAll(".nav-link").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    btn.classList.add("active");
    $(`#view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "backtest") loadBacktest(false);
  });
});

// ---------- predictions ----------
function riskClass(risk) {
  if (risk.includes("LOW")) return "low";
  if (risk.includes("MED")) return "med";
  return "high";
}

function cardHTML(p) {
  const [hg, ag] = p.oneXtwo["Home Win"], dg = p.oneXtwo["Draw"], aw = p.oneXtwo["Away Win"];
  const rc = riskClass(p.risk);
  const ev = (p.ev && p.ev.length)
    ? `<span class="ev-chip">+EV ${p.ev[0].market} @ ${p.ev[0].odds} (${p.ev[0].ev_pct > 0 ? "+" : ""}${p.ev[0].ev_pct}%)</span>`
    : "";
  const markets = Object.entries(p.markets)
    .filter(([k]) => !["Home Win", "Draw", "Away Win", "Exp Goals"].includes(k))
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}%</td></tr>`).join("");
  return `
  <article class="card risk-${rc}">
    <div class="card-top">
      <span class="card-league">${p.league}</span>
      <span class="card-date">${p.date}</span>
    </div>
    <div class="teams">
      <span class="team">${p.fixture.split(" vs ")[0]}</span>
      <span class="vs">VS</span>
      <span class="team">${p.fixture.split(" vs ")[1]}</span>
    </div>
    <div class="probbar">
      <div class="h" style="width:${hg}%"></div>
      <div class="d" style="width:${dg}%"></div>
      <div class="a" style="width:${aw}%"></div>
    </div>
    <div class="onextwo">
      <span>Home <b>${hg}%</b></span><span>Draw <b>${dg}%</b></span><span>Away <b>${aw}%</b></span>
    </div>
    <div class="pick-row">
      <span class="pick-chip">${p.pick} · ${p.pick_prob}%</span>
      <span class="risk risk-${rc}">${p.risk}</span>
    </div>
    ${p.banker ? `<div class="banker-row"><span class="banker-chip">🏦 Banker: ${p.banker.market} · ${p.banker.prob}%</span></div>` : ""}
    <div class="meta-row">
      <span>xG proj <b>${p.exp_goals[0]} – ${p.exp_goals[1]}</b></span>
      <span>O2.5 <b>${p.over25}%</b></span>
      <span>BTTS <b>${p.btts_yes}%</b></span>
    </div>
    <div class="cs-chips">
      ${p.correct_scores.map(c => `<span class="cs-chip">${c.score} · ${c.prob}%</span>`).join("")}
    </div>
    ${ev}
    <details><summary>All markets</summary><table class="market-table">${markets}</table></details>
  </article>`;
}

function renderPredictions() {
  let preds = state.data?.predictions || [];
  if (state.search) {
    const q = state.search.toLowerCase();
    preds = preds.filter(p => p.fixture.toLowerCase().includes(q));
  }
  $("#pred-grid").innerHTML = preds.map(cardHTML).join("");
  $("#pred-empty").classList.toggle("hidden", preds.length > 0);
}

async function loadPredictions() {
  const grid = $("#pred-grid");
  grid.innerHTML = `<div class="empty">Crunching distributions…</div>`;
  try {
    const r = await fetch(`/api/predictions?window=${state.window}&league=${encodeURIComponent(state.league)}`);
    const j = await r.json();
    if (!j.ready) { grid.innerHTML = `<div class="empty">${j.message}</div>`; return; }
    if (!j.has_key) { grid.innerHTML = `<div class="empty">${j.message}</div>`; return; }

    // populate league filter once
    const sel = $("#league-select");
    if (j.leagues && sel.options.length <= 1) {
      j.leagues.forEach(l => l && sel.add(new Option(l, l)));
    }
    // move +EV rows into scanner
    renderValue(j.predictions);
    state.data = j;
    renderPredictions();
  } catch {
    grid.innerHTML = `<div class="empty">Failed to reach the engine.</div>`;
  }
}

$("#window-seg").addEventListener("click", e => {
  if (e.target.dataset.window) {
    document.querySelectorAll("#window-seg button").forEach(b => b.classList.remove("active"));
    e.target.classList.add("active");
    state.window = e.target.dataset.window;
    loadPredictions();
  }
});
$("#league-select").addEventListener("change", e => { state.league = e.target.value; loadPredictions(); });
$("#search").addEventListener("input", e => { state.search = e.target.value; renderPredictions(); });
$("#refresh").addEventListener("click", loadPredictions);

// ---------- value scanner ----------
function renderValue(preds) {
  const rows = [];
  preds.forEach(p => (p.ev || []).forEach(ev =>
    rows.push(`<tr>
      <td>${p.fixture}</td><td>${ev.market}</td>
      <td class="odds-mono">${ev.prob.toFixed(1)}%</td>
      <td class="odds-mono">${ev.odds}</td>
      <td class="edge-pos">+${ev.ev_pct}%</td></tr>`)));
  const tb = $("#value-table tbody");
  tb.innerHTML = rows.join("") ||
    `<tr><td colspan="5" style="color:var(--text-dim)">No live odds source connected — set SMART_API_KEY to enable real-price comparison. The scanner never fabricates odds.</td></tr>`;
  $("#value-note").innerHTML = "";
}

// ---------- backtest ----------
async function loadBacktest(force) {
  const box = $("#bt-metrics"), verdict = $("#bt-verdict"), btn = $("#bt-run");
  try {
    let j = await (await fetch("/api/backtest")).json();
    if ((force || !j.summary) && !j.running) {
      btn.disabled = true; btn.textContent = "Running evaluation…";
      await fetch("/api/backtest", { method: "POST" });
    }
    const poll = setInterval(async () => {
      j = await (await fetch("/api/backtest")).json();
      pollStatus();
      if (!j.running && (j.summary || j.error)) {
        clearInterval(poll);
        btn.disabled = false; btn.textContent = "Re-run full evaluation (~2 min)";
        drawBacktest(j);
      }
    }, 5000);
    if (!force && j.summary) drawBacktest(j);
  } catch {}
}

function drawBacktest(j) {
  if (j.error) { $("#bt-metrics").innerHTML = `<div class="empty">Evaluation failed: ${j.error}</div>`; return; }
  const s = j.summary; if (!s) return;
  const gap = s.bookmaker_logloss ? (s.logloss - s.bookmaker_logloss).toFixed(4) : null;
  $("#bt-metrics").innerHTML = `
    <div class="metric"><div class="label">Test matches</div><div class="value">${s.matches}</div></div>
    <div class="metric"><div class="label">Brier score</div><div class="value">${s.brier}</div></div>
    <div class="metric"><div class="label">Log-loss (model)</div><div class="value">${s.logloss}</div></div>
    <div class="metric"><div class="label">Log-loss (bookmaker)</div><div class="value">${s.bookmaker_logloss ?? "—"}</div></div>
    <div class="metric"><div class="label">1X2 accuracy</div><div class="value">${s.accuracy_1x2}%</div></div>
    <div class="metric"><div class="label">+EV bets ROI</div><div class="value ${s.roi > 0 ? "good" : "bad"}">${s.roi !== null ? s.roi + "%" : "—"}</div></div>`;
  verdict.innerHTML = gap
    ? `Model finished <strong>${gap}</strong> log-loss points ${gap < 0 ? "<span style='color:var(--accent)'>ahead of</span>" : "behind"} the bookmaker on identical matches.
       ${gap < 0 ? "That is elite territory." : "Typical for a goals-only model using free data — the value scanner is where disagreement becomes opportunity."}`
    : "";
}

$("#bt-run").addEventListener("click", () => loadBacktest(true));

// ---------- boot ----------
(async function init() {
  for (let i = 0; i < 60; i++) {           // wait for first-time DB build
    const s = await pollStatus();
    if (s?.db_ready) break;
    await new Promise(r => setTimeout(r, 4000));
  }
  loadPredictions();
})();
