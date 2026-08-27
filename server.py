import os
import re
import threading
import time
import traceback
import unicodedata
from difflib import get_close_matches
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

from database import get_historical_data, init_db
from model_engine import fit_all_models, predict_match_probs, blend_with_market, \
    select_top_pick, assign_risk_bucket, best_ev_bets, TeamResolver
from rolling_backtest import confident_pick

# Global boot state tracker
BOOT_STATE = {
    "thread_alive": False,
    "started": None,
    "error": None,
    "traceback": None,
}

app = Flask(__name__, static_folder="static", template_folder="templates")

# ------------------------------------------------------------------ caches --
_cache_lock = threading.Lock()
STATE = {
    "models": None,
    "models_ts": 0,
    "fixtures": None,
    "fixtures_ts": 0,
    "backtest": None,
    "backtest_running": False,
    "db_ready": False,
    "status_msg": "Starting...",
}

MODEL_TTL = 6 * 3600       # refit models every 6h
FIXTURE_TTL = 15 * 60      # refresh fixtures every 15 min


def set_status(msg):
    STATE["status_msg"] = msg
    print(f"[goalpredict] {msg}", flush=True)


def _ensure_database_inner():
    """Inner function that does the actual database setup work."""
    init_db()
    df = get_historical_data()
    set_status(f"Data loaded ({len(df)} rows).")
    if len(df) < 1000:
        set_status("Database empty - downloading historical data (~2 min)...")
        from scraper import scrape_football_data
        scrape_football_data()
    # top up European competitions + MLS (safe to skip on failure)
    try:
        import euro_backfill
        if os.getenv("FOOTBALL_DATA_API_KEY"):
            set_status("Backfilling Champions League / Brazil results...")
            euro_backfill.backfill()
    except Exception as e:
        print(f"euro backfill skipped: {e}")
    try:
        import mls_backfill
        set_status("Backfilling MLS results...")
        mls_backfill.backfill_mls()
    except Exception as e:
        print(f"MLS backfill skipped: {e}")

    # Pre-fit models in the BACKGROUND so no web request ever blocks on
    # multi-minute computation (Render/browser kill slow connections).
    set_status("Fitting league models...")
    t0 = time.time()
    df = get_historical_data()
    STATE["models"] = fit_all_models(df)
    STATE["models_ts"] = time.time()
    set_status(f"Ready - {len(STATE['models'])} leagues fitted "
               f"in {time.time() - t0:.0f}s.")

    STATE["db_ready"] = True
    set_status("Database ready.")


def boot_worker():
    """Wrapper that captures thread lifecycle and any exceptions."""
    BOOT_STATE["thread_alive"] = True
    BOOT_STATE["started"] = time.time()
    try:
        _ensure_database_inner()
        BOOT_STATE["thread_alive"] = False
    except Exception as e:
        tb = traceback.format_exc()
        BOOT_STATE["error"] = str(e)
        BOOT_STATE["traceback"] = tb
        BOOT_STATE["thread_alive"] = False
        set_status(f"Boot error: {e}")
        print("[goalpredict] BOOT ERROR:\n" + tb, flush=True)


def ensure_database():
    """Build DB from scratch on first boot (cloud deploys start empty)."""
    t = threading.Thread(target=boot_worker, daemon=True)
    t.start()


# Start the database initialization on module import
ensure_database()


def get_models(force=False):
    with _cache_lock:
        now = time.time()
        if force or STATE["models"] is None or now - STATE["models_ts"] > MODEL_TTL:
            set_status("Fitting league models...")
            df = get_historical_data()
            STATE["models"] = fit_all_models(df)
            STATE["models_ts"] = now
            set_status(f"Models fitted ({len(STATE['models'])} leagues).")
        return STATE["models"]


def get_fixtures():
    with _cache_lock:
        now = time.time()
        if STATE["fixtures"] is None or now - STATE["fixtures_ts"] > FIXTURE_TTL:
            from fixtures import get_upcoming_fixtures
            try:
                STATE["fixtures"] = get_upcoming_fixtures()
            except Exception as e:
                print(f"fixtures error: {e}")
                STATE["fixtures"] = pd.DataFrame() if STATE["fixtures"] is None else STATE["fixtures"]
            STATE["fixtures_ts"] = now
        return STATE["fixtures"]


def get_live_odds_safe():
    try:
        from odds_api import get_live_odds
        return get_live_odds() or {}
    except Exception:
        return {}



# ------------------------------------------------------------------- views --

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    return jsonify({
        "db_ready": STATE["db_ready"],
        "message": STATE["status_msg"],
        "backtest_running": STATE["backtest_running"],
        "boot_error": STATE.get("boot_error"),
    })


@app.route("/api/predictions")
def api_predictions():
    if not STATE["db_ready"]:
        return jsonify({"ready": False, "message": STATE["status_msg"], "predictions": []})
    data = compute_predictions(request.args.get("window") or "48h",
                               request.args.get("league"))
    data["predictions"] = [_public_prediction(r) for r in data["predictions"]]
    return jsonify(data)


def compute_predictions(window="48h", league_filter=None):
    """Core computation shared by all pick endpoints. Cached briefly."""
    if STATE["models"] is None:
        return {"ready": False, "message": STATE["status_msg"], "predictions": []}
    cache_key = f"{window}|{league_filter}|{STATE['models_ts']}"
    now = time.time()
    if STATE.get("preds_cache_key") == cache_key and now - STATE.get("preds_cache_ts", 0) < 120:
        return STATE["preds_cache"]

    fixtures = get_fixtures()
    if fixtures is None or fixtures.empty:
        return {"ready": True, "has_key": False, "message":
                "No fixtures available. Set FOOTBALL_DATA_API_KEY to enable upcoming matches.",
                "predictions": []}

    fdf = fixtures.copy()
    fdf['date'] = pd.to_datetime(fdf['date'], errors='coerce')
    now_ts = pd.Timestamp.now().normalize()
    if window == "today":
        fdf = fdf[fdf['date'].dt.date == now_ts.date()]
    elif window == "tomorrow":
        fdf = fdf[fdf['date'].dt.date == (now_ts + timedelta(days=1)).date()]
    else:
        fdf = fdf[fdf['date'] <= now_ts + timedelta(days=2)]

    if league_filter and league_filter != "all":
        fdf = fdf[fdf['league'].str.contains(league_filter, case=False, na=False)]

    if fdf.empty:
        return {"ready": True, "has_key": True, "predictions": [], "leagues": []}

    models = STATE["models"]   # pre-fitted at boot; never fit inside a request
    live = get_live_odds_safe()

    known_teams = set()
    for m in models.values():
        known_teams.update(m.teams)
    resolver = TeamResolver(known_teams)

    out = []
    for _, fx in fdf.iterrows():
        home, away, lg = fx['home'], fx['away'], fx.get('league', 'Unknown')
        r_home, r_away = resolver.resolve(home), resolver.resolve(away)
        if not r_home or not r_away:
            continue
        probs, lam, mu = predict_match_probs(models, lg, r_home, r_away)
        if probs is None:
            continue
        key = f"{r_home} vs {r_away}"
        bookie = live.get(key, {})
        evs = best_ev_bets(probs, bookie, min_edge=3.0)
        disp = blend_with_market(probs, bookie) if bookie else probs
        pick, pick_p = select_top_pick(disp)
        banker, banker_p = confident_pick(probs)

        core = ["Home Win", "Draw", "Away Win"]
        rec = {
            "fixture": key,
            "league": lg,
            "date": str(fx.get('date', '')),
            "exp_goals": [round(float(lam), 2), round(float(mu), 2)],
            "pick": pick,
            "pick_prob": round(float(pick_p), 1),
            "risk": assign_risk_bucket(pick_p),
            "banker": {"market": banker, "prob": round(float(banker_p), 1)} if banker else None,
            "oneXtwo": {m: round(float(disp.get(m, 0)), 1) for m in core},
            "over25": round(float(disp.get("Over 2.5", 0)), 1),
            "btts_yes": round(float(disp.get("BTTS Yes", 0)), 1),
            "correct_scores": [{"score": s, "prob": p} for s, p in probs["_top_correct_scores"]],
            "markets": {k: round(float(v), 1) for k, v in disp.items()
                        if not k.startswith("_") and isinstance(v, (int, float))},
            "ev": [{"market": b["market"], "odds": b["odds"], "ev_pct": b["ev_pct"],
                    "prob": b["prob"]} for b in evs],
            "_pure": probs,
            "_bookie": bookie,
        }
        out.append(rec)

    out.sort(key=lambda x: x["pick_prob"], reverse=True)
    leagues = sorted(set(fx.get('league', '') for _, fx in fixtures.iterrows()))
    result = {"ready": True, "has_key": True, "count": len(out),
              "leagues": leagues, "blended": bool(live), "predictions": out,
              "diag": {
                  "fixtures_in_window": int(len(fdf)),
                  "resolved_and_predicted": len(out),
                  "total_fixtures_fetched": int(len(fixtures)),
              }}

    STATE["preds_cache"] = result
    STATE["preds_cache_key"] = cache_key
    STATE["preds_cache_ts"] = now
    return result


def _public_prediction(rec):
    """Strip private keys for JSON."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}


# --------------------------------------------------------- picks & builder --

BOOKIE_MARGIN_EST = 1.10   # used ONLY when no real odds; clearly labelled est


def est_odds(prob_pct):
    p = max(min(prob_pct / 100.0, 0.97), 0.02)
    return round(max(1.01, BOOKIE_MARGIN_EST / p), 2)


def risk_of(p):
    if p >= 75:
        return "LOW"
    if p >= 60:
        return "MED"
    return "HIGH"


def collect_candidates(preds):
    """Candidate singles per fixture: top core pick, banker, double chance,
    best of O/U 2.5, BTTS side. Deduped, with prob + odds (real or est)."""
    cands = []
    seen = set()
    for rec in preds:
        pure = rec["_pure"]
        bookie = rec["_bookie"]
        fx_market_probs = []

        def add(market, source=None):
            p = pure.get(market)
            if p is None or market in seen:
                return
            odds = bookie.get(source or market)
            odds = float(odds) if odds and odds > 1 else None
            cands.append({
                "fixture": rec["fixture"], "league": rec["league"],
                "date": rec["date"], "market": market,
                "prob": round(float(p), 1),
                "odds": odds, "odds_est": est_odds(p) if odds is None else None,
                "risk": risk_of(float(p)),
                "has_real_odds": odds is not None,
            })
            seen.add((rec["fixture"], market))

        # top core pick by pure probability
        core_best = max(["Home Win", "Draw", "Away Win"], key=lambda m: pure.get(m, 0))
        add(core_best)
        # banker pick
        if rec["banker"]:
            add(rec["banker"]["market"])
        # double chance on the favoured side
        h, a = pure.get("Home Win", 0), pure.get("Away Win", 0)
        add("1X (Home or Draw)" if h >= a else "X2 (Away or Draw)")
        # totals side
        add("Over 2.5" if pure.get("Over 2.5", 0) >= pure.get("Under 2.5", 0) else "Under 2.5")
        # BTTS side
        add("BTTS Yes" if pure.get("BTTS Yes", 0) >= pure.get("BTTS No", 0) else "BTTS No")
        _ = fx_market_probs

    return cands


def score_pick(c):
    """Ranking score: probability first, EV bonus when real odds exist."""
    ev_bonus = 0.0
    if c["odds"]:
        ev_bonus = max(-15.0, min(25.0, (c["prob"] / 100 * c["odds"] - 1) * 100))
    return c["prob"] + ev_bonus


@app.route("/api/picks/today")
def api_picks_today():
    if not STATE["db_ready"]:
        return jsonify({"ready": False, "message": STATE["status_msg"], "picks": []})
    data = compute_predictions(request.args.get("window") or "today",
                               request.args.get("league"))
    preds = [r for r in data["predictions"] if r["date"].startswith(str(pd.Timestamp.now().date()))] \
        or data["predictions"]
    cands = [c for c in collect_candidates(preds) if c["risk"] != "HIGH"]
    cands.sort(key=score_pick, reverse=True)

    # max one pick per fixture so the shortlist spreads across games
    shortlist, used = [], set()
    for c in cands:
        if c["fixture"] in used:
            continue
        used.add(c["fixture"])
        shortlist.append(c)
        if len(shortlist) >= 8:
            break

    return jsonify({"ready": True, "blended": data.get("blended", False),
                    "n_games": len(preds), "picks": shortlist})


@app.route("/api/build-acca", methods=["POST"])
def api_build_acca():
    body = request.get_json(force=True, silent=True) or {}
    target = float(body.get("target_odds", 5.0))
    n_legs = int(body.get("num_games", 3))
    risk = body.get("risk", "MED")

    data = compute_predictions(body.get("window") or "today", request.args.get("league"))
    pool = [c for c in collect_candidates(data["predictions"])
            if (c["risk"] == risk if risk != "ALL" else True)]
    if len(pool) < n_legs:
        return jsonify({"ok": False, "error": f"Only {len(pool)} eligible picks found."})

    req_log = np.log(target) / n_legs
    legs, used_fx = [], set()
    remaining = n_legs
    acc_log = 0.0
    for step in range(n_legs):
        need = (np.log(target) - acc_log) / (remaining)
        ranked = sorted(
            [c for c in pool if c["fixture"] not in used_fx],
            key=lambda c: abs(np.log(c["odds"] or c["odds_est"]) - need)
                          - (c["prob"] / 10000.0))          # prefer higher prob on ties
        if not ranked:
            break
        chosen = ranked[0]
        legs.append(chosen)
        used_fx.add(chosen["fixture"])
        acc_log += np.log(chosen["odds"] or chosen["odds_est"])
        remaining -= 1

    total_odds = float(np.exp(sum(np.log(l["odds"] or l["odds_est"]) for l in legs)))
    combined_p = float(np.prod([l["prob"] / 100.0 for l in legs])) * 100
    est_book_total = float(np.prod([(l["odds"] or l["odds_est"]) for l in legs]))

    return jsonify({
        "ok": True,
        "legs": [{**l, "display_odds": l["odds"] or l["odds_est"]} for l in legs],
        "total_odds": round(total_odds, 2),
        "combined_prob": round(combined_p, 1),
        "target_hit": bool(abs(total_odds - target) / target <= 0.25),
        "uses_real_odds": any(l["has_real_odds"] for l in legs),
    })


@app.route("/api/value")
def api_value():
    if not STATE["db_ready"]:
        return jsonify({"ready": False, "message": STATE["status_msg"], "rows": []})
    data = compute_predictions(request.args.get("window") or "48h",
                               request.args.get("league"))
    rows = []
    for rec in data["predictions"]:
        for b in rec["ev"]:
            rows.append({"fixture": rec["fixture"], "league": rec["league"],
                         **b})
    rows.sort(key=lambda r: -r["ev_pct"])
    return jsonify({"ready": True, "blended": data.get("blended", False), "rows": rows})


# ------------------------------------------------------------------ slip ----

from bets import add_bet, get_my_bets, update_bet_status


@app.route("/api/slip/log", methods=["POST"])
def api_slip_log():
    body = request.get_json(force=True, silent=True) or {}
    sels = body.get("selections", [])
    if not sels:
        return jsonify({"ok": False, "error": "No selections provided."})
    today = datetime.now().strftime('%Y-%m-%d')
    for s in sels:
        add_bet(today, s["fixture"], s["market"], float(s["odds"]), float(s.get("stake", 100)))
    return jsonify({"ok": True, "logged": len(sels)})


@app.route("/api/bets")
def api_bets():
    dfb = get_my_bets()
    if dfb.empty:
        return jsonify({"ok": True, "summary": {"staked": 0, "returned": 0, "roi": 0,
                                                "win_rate": 0, "open": 0}, "bets": []})
    settled = dfb[dfb['status'] != 'Pending']
    staked = settled['stake'].sum()
    returned = settled[settled['status'] == 'Won']['payout'].sum()
    wins = len(settled[settled['status'] == 'Won'])
    losses = len(settled[settled['status'] == 'Lost'])
    summary = {
        "staked": round(float(staked), 0),
        "returned": round(float(returned), 0),
        "roi": round(float((returned - staked) / staked * 100), 1) if staked else 0,
        "win_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else 0,
        "open": int(len(dfb[dfb['status'] == 'Pending'])),
    }
    return jsonify({"ok": True, "summary": summary,
                    "bets": dfb.to_dict(orient="records")})


@app.route("/api/bets/<int:bet_id>/settle", methods=["POST"])
def api_settle(bet_id):
    status = (request.get_json(force=True, silent=True) or {}).get("status")
    if status not in ("Won", "Lost"):
        return jsonify({"ok": False, "error": "status must be Won or Lost"}), 400
    update_bet_status(bet_id, status)
    return jsonify({"ok": True})


@app.route("/api/admin/resync", methods=["POST"])
def api_admin_resync():
    """Refresh historical data + refit models. Requires ADMIN_KEY env var."""
    key = request.args.get("key") or (request.get_json(silent=True) or {}).get("key")
    if not os.getenv("ADMIN_KEY") or key != os.getenv("ADMIN_KEY"):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    if STATE.get("resync_running"):
        return jsonify({"ok": False, "running": True})

    def job():
        STATE["resync_running"] = True
        try:
            from scraper import scrape_football_data
            scrape_football_data()
            import euro_backfill
            euro_backfill.backfill()
            import mls_backfill
            mls_backfill.backfill_mls()
            get_models(force=True)
            set_status("Resync complete.")
        except Exception as e:
            set_status(f"Resync failed: {e}")
        finally:
            STATE["resync_running"] = False

    threading.Thread(target=job, daemon=True).start()
    return jsonify({"ok": True, "started": True})


def run_backtest_job():
    try:
        from backtest import run_backtest
        df = get_historical_data()
        res = run_backtest(df)
        summary = {}
        if not res.empty:
            allb = [b for recs in res['bets'] for b in recs]
            bm = res['BM_LogLoss'].dropna()
            summary = {
                "matches": int(len(res)),
                "brier": round(float(res['Brier'].mean()), 4),
                "logloss": round(float(res['LogLoss'].mean()), 4),
                "bookmaker_logloss": round(float(bm.mean()), 4) if len(bm) else None,
                "accuracy_1x2": round(float(res['Correct?'].mean() * 100), 1),
                "bets": int(len(allb)),
                "roi": round(float(sum(b['return'] for b in allb) / len(allb) * 100), 2) if allb else None,
            }
        STATE["backtest"] = {"finished": datetime.now().isoformat(timespec='seconds'),
                             "summary": summary}
    except Exception as e:
        STATE["backtest"] = {"error": str(e)}
    finally:
        STATE["backtest_running"] = False


@app.route("/api/backtest", methods=["GET", "POST"])
def api_backtest():
    if request.method == "POST":
        if not STATE["backtest_running"]:
            STATE["backtest_running"] = True
            threading.Thread(target=run_backtest_job, daemon=True).start()
            return jsonify({"started": True})
        return jsonify({"started": False, "running": True})
    return jsonify({"running": STATE["backtest_running"], **(STATE["backtest"] or {})})


# ------------------------------------------------------------------- boot ---

threading.Thread(target=ensure_database, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)