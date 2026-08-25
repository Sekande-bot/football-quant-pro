"""
QuantPro - Flask backend serving the prediction API and frontend.
"""
import os
import re
import threading
import time
import unicodedata
from difflib import get_close_matches
from datetime import datetime, timedelta

import pandas as pd
from flask import Flask, jsonify, render_template, request

from database import get_historical_data, init_db
from model_engine import fit_all_models, predict_match_probs, blend_with_market, \
    select_top_pick, assign_risk_bucket, best_ev_bets, TeamResolver

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
    print(f"[quantpro] {msg}", flush=True)


def ensure_database():
    """Build DB from scratch on first boot (cloud deploys start empty)."""
    init_db()
    df = get_historical_data()
    if len(df) < 1000:
        set_status("Database empty - downloading historical data (~2 min)...")
        from scraper import scrape_football_data
        try:
            scrape_football_data()
        except Exception as e:
            set_status(f"Scrape failed: {e}")
            return
    STATE["db_ready"] = True
    set_status("Database ready.")


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
    })


@app.route("/api/predictions")
def api_predictions():
    if not STATE["db_ready"]:
        return jsonify({"ready": False, "message": STATE["status_msg"], "predictions": []})

    league_filter = request.args.get("league") or None
    args_window = request.args.get("window") or "48h"

    fixtures = get_fixtures()
    if fixtures is None or fixtures.empty:
        return jsonify({"ready": True, "has_key": False, "message":
                        "No fixtures available. Set FOOTBALL_DATA_API_KEY to enable upcoming matches.",
                        "predictions": []})

    fdf = fixtures.copy()
    fdf['date'] = pd.to_datetime(fdf['date'], errors='coerce')
    now = pd.Timestamp.now().normalize()
    if args_window == "today":
        fdf = fdf[fdf['date'].dt.date == now.date()]
    elif args_window == "tomorrow":
        fdf = fdf[fdf['date'].dt.date == (now + timedelta(days=1)).date()]
    else:
        fdf = fdf[fdf['date'] <= now + timedelta(days=2)]

    if league_filter and league_filter != "all":
        fdf = fdf[fdf['league'].str.contains(league_filter, case=False, na=False)]

    if fdf.empty:
        return jsonify({"ready": True, "has_key": True, "predictions": []})

    models = get_models()
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

        core = ["Home Win", "Draw", "Away Win"]
        out.append({
            "fixture": key,
            "league": lg,
            "date": str(fx.get('date', '')),
            "exp_goals": [round(float(lam), 2), round(float(mu), 2)],
            "pick": pick,
            "pick_prob": round(float(pick_p), 1),
            "risk": assign_risk_bucket(pick_p),
            "oneXtwo": {m: round(float(disp.get(m, 0)), 1) for m in core},
            "over25": round(float(disp.get("Over 2.5", 0)), 1),
            "btts_yes": round(float(disp.get("BTTS Yes", 0)), 1),
            "correct_scores": [{"score": s, "prob": p} for s, p in probs["_top_correct_scores"]],
            "markets": {k: round(float(v), 1) for k, v in disp.items()
                        if not k.startswith("_") and isinstance(v, (int, float))},
            "ev": [{"market": b["market"], "odds": b["odds"], "ev_pct": b["ev_pct"],
                    "prob": b["prob"]} for b in evs],
        })

    out.sort(key=lambda x: x["pick_prob"], reverse=True)
    leagues = sorted(set(fx.get('league', '') for _, fx in fixtures.iterrows()))
    return jsonify({"ready": True, "has_key": True, "count": len(out),
                    "leagues": leagues, "blended": bool(live), "predictions": out})


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
