/* GoalPredict frontend */
const $ = (s) => document.querySelector(s);
const slip = [];   // {fixture, market, odds, prob, est}

// ---------- navigation ----------
document.querySelectorAll(".nav-link").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    btn.classList.add("active");
    $(`#view-${btn.dataset.view}`).classList.add("active");
  });
});

// ---------- slip drawer ----------
$("#slip-btn").addEventListener("click", openSlip);
$("#slip-close").addEventListener("click", closeSlip);
$("#slip-overlay").addEventListener("click", closeSlip);
function openSlip() {
  $("#slip-drawer").classList.remove("hidden");
  $("#slip-overlay").classList.remove("hidden");
  renderSlip(); loadBets();
}
function closeSlip() {
  $("#slip-drawer").classList.add("hidden");
  $("#slip-overlay").classList.add("hidden");
}
function updateSlipCount() { $("#slip-count").textContent = slip.length; }

function addToSlip(sel) {
  if (slip.some(s => s.fixture === sel.fixture && s.market === sel.market)) return;
  slip.push(sel); updateSlipCount();
}

$("#slip-clear").addEventListener("click", () => { slip.length = 0; updateSlipCount(); renderSlip(); });
$("#slip-log").addEventListener("click", async () => {
  const stake = parseFloat($("#slip-stake").value) || 100;
  if (!slip.length) return;
  await fetch("/api/slip/log", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selections: slip.map(s => ({ ...s, stake })) }),
  });
  slip.length = 0; updateSlipCount(); renderSlip(); loadBets();
});

function renderSlip() {
  const box = $("#slip-items");
  box.innerHTML = slip.length ? slip.map((s, i) => `
    <div class="slip-item">
      <div>
        <div class="si-fixture">${s.fixture}</div>
        <div class="si-market">${s.market} · <span class="odds-mono">${s.odds}</span>${s.est ? ' <em class="est">est</em>' : ''}</div>
      </div>
      <button class="btn ghost x" data-i="${i}">×</button>
    </div>`).join("")
    : `<div class="empty-sm">Slip is empty. Add picks from any tab.</div>`;
  box.querySelectorAll(".x").forEach(b =>
    b.addEventListener("click", () => { slip.splice(+b.dataset.i, 1); updateSlipCount(); renderSlip(); }));
}

async function loadBets() {
  try {
    const j = await (await fetch("/api/bets")).json();
    const s = j.summary;
    $("#perf-metrics").innerHTML = `
      <div class="metric"><div class="label">Staked</div><div class="value">₦${s.staked.toLocaleString()}</div></div>
      <div class="metric"><div class="label">Returned</div><div class="value">₦${s.returned.toLocaleString()}</div></div>
      <div class="metric"><div class="label">ROI</div><div class="value ${s.roi > 0 ? "good" : s.roi < 0 ? "bad" : ""}">${s.roi}%</div></div>
      <div class="metric"><div class="label">Win rate</div><div class="value">${s.win_rate}%</div></div>
      <div class="metric"><div class="label">Open</div><div class="value">${s.open}</div></div>`;
    $("#bets-history").innerHTML = j.bets.length ? j.bets.map(b => `
      <div class="bet-row">
        <div>
          <div class="si-fixture">${b.fixture}</div>
          <div class="si-market">${b.market} @ ${b.odds} · ₦${b.stake} · ${b.date}</div>
        </div>
        ${b.status === "Pending"
          ? `<span><button class="btn tiny good" data-id="${b.bet_id}" data-s="Won">W</button>
             <button class="btn tiny bad" data-id="${b.bet_id}" data-s="Lost">L</button></span>`
          : `<span class="status ${b.status.toLowerCase()}">${b.status}</span>`}
      </div>`).join("")
      : `<div class="empty-sm">No logged bets yet.</div>`;
    $("#bets-history").querySelectorAll("button[data-id]").forEach(btn =>
      btn.addEventListener("click", async () => {
        await fetch(`/api/bets/${btn.dataset.id}/settle`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: btn.dataset.s }),
        });
        loadBets();
      }));
  } catch {}
}

// ---------- today's picks ----------
function pickRowHTML(c) {
  const odds = c.odds || c.odds_est;
  return `
  <article class="pick-row-card risk-${c.risk.toLowerCase()}">
    <div class="pr-left">
      <div class="pr-fixture">${c.fixture}</div>
      <div class="pr-meta">${c.league} · ${c.date} · <span class="risk risk-${c.risk.toLowerCase()}">${c.risk} RISK</span></div>
    </div>
    <div class="pr-mid">
      <span class="pick-chip">${c.market}</span>
      <span class="pr-prob">${c.prob}%</span>
    </div>
    <div class="pr-right">
      <div class="pr-odds">@ <b>${odds}</b>${c.has_real_odds ? "" : ' <em class="est">est</em>'}</div>
      <button class="btn small" data-add='${JSON.stringify({ fixture: c.fixture, market: c.market, odds, prob: c.prob, est: !c.has_real_odds })}'>+ Slip</button>
    </div>
  </article>`;
}

async function loadPicks() {
  $("#picks-status").innerHTML = "";
  $("#picks-list").innerHTML = `<div class="empty">Scanning today's fixtures…</div>`;
  try {
    const j = await (await fetch("/api/picks/today?window=today")).json();
    if (!j.ready) { $("#picks-list").innerHTML = `<div class="empty">${j.message}</div>`; return; }
    if (!j.picks.length) {
      $("#picks-list").innerHTML = `<div class="empty">No qualifying picks today${j.n_games ? ` (${j.n_games} games scanned)` : ""}. Check All Games for the full slate.</div>`;
      return;
    }
    $("#picks-status").innerHTML =
      `<div class="note">${j.picks.length} recommended picks from ${j.n_games} games
       · ${j.blended ? "live odds blended ✓" : "no live odds — probabilities are pure model, prices estimated"}</div>`;
    const el = $("#picks-list");
    el.innerHTML = j.picks.map(pickRowHTML).join("");
    bindAddButtons(el);
  } catch {
    $("#picks-list").innerHTML = `<div class="empty">Engine unreachable.</div>`;
  }
}

function bindAddButtons(root) {
  root.querySelectorAll("[data-add]").forEach(btn =>
    btn.addEventListener("click", () => {
      addToSlip(JSON.parse(btn.dataset.add));
      renderSlip();
      btn.textContent = "✓ Added"; btn.disabled = true;
      setTimeout(() => { btn.textContent = "+ Slip"; btn.disabled = false; }, 1500);
    }));
}

// ---------- bet builder ----------
$("#bb-go").addEventListener("click", async () => {
  const btn = $("#bb-go");
  btn.disabled = true; btn.textContent = "Building…";
  $("#bb-result").innerHTML = "";
  try {
    const r = await fetch("/api/build-acca", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_odds: parseFloat($("#bb-target").value),
        num_games: parseInt($("#bb-games").value),
        risk: $("#bb-risk").value,
        window: "today",
      }),
    });
    const j = await r.json();
    if (!j.ok) { $("#bb-result").innerHTML = `<div class="empty">${j.error}</div>`; return; }
    $("#bb-result").innerHTML = `
      <div class="acca-summary">
        <div><span class="label">Total odds</span><b>${j.total_odds}</b> (target ${$("#bb-target").value})</div>
        <div><span class="label">Combined chance</span><b>${j.combined_prob}%</b></div>
        <div><span class="label">Prices</span><b>${j.uses_real_odds ? "real bookmaker odds" : "estimated (no live odds)"}</b></div>
      </div>
      <div class="stack">${j.legs.map(pickRowHTML).join("")}</div>`;
    bindAddButtons($("#bb-result"));
  } catch {
    $("#bb-result").innerHTML = `<div class="empty">Build failed.</div>`;
  }
  btn.disabled = false; btn.textContent = "Build Accumulator";
});

// ---------- value play ----------
let valuePoll = null;
async function loadValue() {
  const tb = $("#value-table tbody");
  if (valuePoll) clearInterval(valuePoll);
  tb.innerHTML = `<tr><td colspan="6" style="color:var(--text-dim)">Scanning…</td></tr>`;
  try {
    const j = await (await fetch("/api/value?window=48h")).json();
    if (!j.ready) {
      tb.innerHTML = `<tr><td colspan="6" style="color:var(--text-dim)">Engine warming up: ${j.message || "building data…"} — retrying automatically…</td></tr>`;
      valuePoll = setTimeout(loadValue, 8000);
      return;
    }
    if (!j.blended) {
      tb.innerHTML = `<tr><td colspan="6" style="color:var(--text-dim)">No real odds source connected — set SMART_API_KEY. The scanner never fabricates prices.</td></tr>`;
      return;
    }
    tb.innerHTML = j.rows.map(r => `
      <tr>
        <td>${r.fixture}</td><td>${r.market}</td>
        <td class="odds-mono">${r.prob.toFixed(1)}%</td>
        <td class="odds-mono">${r.odds}</td>
        <td class="edge-pos">+${r.ev_pct}%</td>
        <td><button class="btn small" data-add='${JSON.stringify({ fixture: r.fixture, market: r.market, odds: r.odds, prob: r.prob, est: false })}'>+ Slip</button></td>
      </tr>`).join("") || `<tr><td colspan="6" style="color:var(--text-dim)">No qualifying edge found.</td></tr>`;
    bindAddButtons(tb);
  } catch {
    tb.innerHTML = `<tr><td colspan="6">Engine unreachable — retrying in 10s…</td></tr>`;
    valuePoll = setTimeout(loadValue, 10000);
  }
}

document.querySelector('[data-view="value"]').addEventListener("click", loadValue);

// ---------- all games ----------
const state = { window: "today", league: "all", search: "" };

function riskClass(risk) { return risk.includes("LOW") ? "low" : risk.includes("MED") ? "med" : "high"; }

function cardHTML(p) {
  const hg = p.oneXtwo["Home Win"], dg = p.oneXtwo["Draw"], aw = p.oneXtwo["Away Win"];
  const rc = riskClass(p.risk);
  const markets = Object.entries(p.markets)
    .filter(([k]) => !["Home Win", "Draw", "Away Win"].includes(k))
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}%</td></tr>`).join("");
  const topOdds = Math.round((100 / p.pick_prob) * 100) / 100;
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
      <div class="h" style="width:${hg}%"></div><div class="d" style="width:${dg}%"></div><div class="a" style="width:${aw}%"></div>
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
      <span>xG <b>${p.exp_goals[0]} – ${p.exp_goals[1]}</b></span>
      <span>O2.5 <b>${p.over25}%</b></span>
      <span>BTTS <b>${p.btts_yes}%</b></span>
    </div>
    <div class="cs-chips">
      ${p.correct_scores.map(c => `<span class="cs-chip">${c.score} · ${c.prob}%</span>`).join("")}
    </div>
    <details><summary>All markets & add pick</summary>
      <table class="market-table">${markets}</table>
      <div class="quick-add">
        <select class="qa-select">
          ${Object.entries(p.markets).filter(([k]) => !k.startsWith("_")).map(([k]) => `<option>${k}</option>`).join("")}
        </select>
        <button class="btn small" data-qa='${JSON.stringify({ fixture: p.fixture, est_odds_mult: topOdds })}'>Add to slip</button>
      </div>
    </details>
  </article>`;
}

function renderGames() {
  let preds = window.__games?.predictions || [];
  if (state.search) {
    const q = state.search.toLowerCase();
    preds = preds.filter(p => p.fixture.toLowerCase().includes(q));
  }
  $("#pred-grid").innerHTML = preds.map(cardHTML).join("");
  $("#pred-empty").classList.toggle("hidden", preds.length > 0);

  // quick-add uses selected market + fair-ish odds estimate from blended prob
  document.querySelectorAll("[data-qa]").forEach(btn =>
    btn.addEventListener("click", () => {
      const d = JSON.parse(btn.dataset.qa);
      const card = btn.closest("details");
      const market = card.querySelector(".qa-select").value;
      const prob = window.__games.predictions.find(x => x.fixture === d.fixture)?.markets[market] || 50;
      const odds = Math.max(1.01, Math.round(100 / prob * 100) / 100);
      addToSlip({ fixture: d.fixture, market, odds, prob, est: true });
      renderSlip();
      btn.textContent = "✓"; setTimeout(() => btn.textContent = "Add to slip", 1200);
    }));
}

async function loadGames() {
  $("#pred-grid").innerHTML = `<div class="empty">Crunching distributions…</div>`;
  try {
    const j = await (await fetch(`/api/predictions?window=${state.window}&league=${encodeURIComponent(state.league)}`));
    const data = await j.json();
    if (!data.ready) {
      $("#pred-grid").innerHTML = `<div class="empty">Engine warming up: ${data.message}<br>Retrying in 8s…</div>`;
      setTimeout(loadGames, 8000);
      return;
    }
    window.__games = data;
    const sel = $("#league-select");
    if (data.leagues && sel.options.length <= 1) data.leagues.forEach(l => l && sel.add(new Option(l, l)));
    renderGames();
    // explain empty results
    if (!data.predictions.length) {
      const d = data.diag || {};
      $("#pred-empty").textContent = d.fixtures_in_window
        ? `${d.fixtures_in_window} fixtures in this window, but none could be modelled (teams outside our covered leagues). Try "Next 48h".`
        : `No scheduled matches in this window from the fixtures API. Try "Next 48h" — the free API covers limited competitions.`;
      $("#pred-empty").classList.remove("hidden");
    }
  } catch {
    $("#pred-grid").innerHTML = `<div class="empty">Engine unreachable.</div>`;
  }
}

$("#window-seg").addEventListener("click", e => {
  if (e.target.dataset.window) {
    document.querySelectorAll("#window-seg button").forEach(b => b.classList.remove("active"));
    e.target.classList.add("active");
    state.window = e.target.dataset.window;
    loadGames();
  }
});
$("#league-select").addEventListener("change", e => { state.league = e.target.value; loadGames(); });
$("#search").addEventListener("input", e => { state.search = e.target.value; renderGames(); });

// ---------- boot ----------
(async function init() {
  for (let i = 0; i < 60; i++) {
    try {
      const s = await (await fetch("/api/status")).json();
      if (s.db_ready) break;
    } catch {}
    await new Promise(r => setTimeout(r, 4000));
  }
  loadPicks();
  loadGames();
})();
