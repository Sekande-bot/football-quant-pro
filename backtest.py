"""
Backtest v3.

Evaluates the fitted Dixon-Coles engine two ways:

1. PROBABILITY QUALITY (all test matches, no betting needed)
   - Multi-class Brier score and log-loss on 1X2 (lower = better).
   - Benchmarked against (a) always predicting league-average probabilities
     and (b) the bookmaker's own de-margined odds when available.
     If we beat the bookmaker benchmark on log-loss, the model has real signal.

2. BETTING ROI (only matches with real historical bookmaker odds)
   - Bet every market where model prob exceeds de-margined implied prob by > EDGE,
     level stake of 1 unit, settled at the actual bookmaker price.
   - Reported overall ROI plus per-market breakdown.

The model is refit periodically (every REFIT_EVERY test matches) using only
data available before each match date -> no lookahead leakage beyond that.
"""

import numpy as np
import pandas as pd
from model_engine import fit_league_model, calculate_market_probabilities

EDGE = 0.05          # bet when model prob > fair prob by 5+ points
REFIT_EVERY = 50     # refit models after this many test matches processed


def demargin(h, d, a):
    inv = np.array([1.0 / h, 1.0 / d, 1.0 / a])
    total = inv.sum()
    if total <= 1.0:
        return None
    return inv / total * 100


def score_1x2(probs, result_idx):
    """Brier (multi-class) and log-loss for one match. probs in %, order H,D,A."""
    p = np.array([probs["Home Win"], probs["Draw"], probs["Away Win"]]) / 100.0
    p = np.clip(p, 1e-6, 1 - 1e-6)
    y = np.zeros(3)
    y[result_idx] = 1
    brier = float(((p - y) ** 2).sum())
    logloss = float(-np.log(p[result_idx]))
    return brier, logloss


def run_backtest(df, min_history=30):
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['date', 'home_goals', 'away_goals'])
    df = df.sort_values('date').reset_index(drop=True)

    cutoff = df['date'].quantile(0.8)
    print(f"Training data before: {cutoff.date()} | Test after.")

    records = []
    since_refit = {lg: REFIT_EVERY for lg in df['league'].unique()}
    models = {}

    test = df[df['date'] >= cutoff].reset_index(drop=True)

    for i, match in test.iterrows():
        lg = match['league']
        # refit lazily/periodically per league, strictly on past data
        if lg not in models or since_refit[lg] >= REFIT_EVERY:
            hist = df[(df['league'] == lg) & (df['date'] < match['date'])]
            m = fit_league_model(hist, lg, ref_date=match['date'])
            models[lg] = m
            since_refit[lg] = 0
        since_refit[lg] += 1

        model = models[lg]
        home, away = match['home_team'], match['away_team']
        xg = model.expected_goals(home, away) if model else None
        if xg is None:
            continue
        lam, mu = xg
        probs = calculate_market_probabilities(lam, mu, rho=model.rho)

        hg, ag = int(match['home_goals']), int(match['away_goals'])
        if hg > ag:
            res_idx, outcome = 0, "H"
        elif hg < ag:
            res_idx, outcome = 2, "A"
        else:
            res_idx, outcome = 1, "D"

        brier, ll = score_1x2(probs, res_idx)

        # bookmaker benchmark (de-margined odds as probabilities)
        oh_, od_, oa_ = match.get('home_odds'), match.get('draw_odds'), match.get('away_odds')
        if all(pd.notna(x) and x and x > 1 for x in [oh_, od_, oa_]):
            fair_bm = demargin(oh_, od_, oa_)
            p = np.clip(fair_bm / 100.0, 1e-6, 1 - 1e-6)
            bm_ll = float(-np.log(p[res_idx]))
        else:
            bm_ll = None

        rec = {
            "Date": match['date'], "League": lg,
            "Fixture": f"{home} vs {away}", "Score": f"{hg}-{ag}",
            "Brier": brier, "LogLoss": ll, "BM_LogLoss": bm_ll,
        }

        # --- betting vs real odds ---
        bets_placed = []
        oh, od, oa = match.get('home_odds'), match.get('draw_odds'), match.get('away_odds')
        if all(pd.notna(x) and x and x > 1 for x in [oh, od, oa]):
            fair = demargin(oh, od, oa)   # bookmaker's own opinion, margin removed
            model_p = np.array([probs["Home Win"], probs["Draw"], probs["Away Win"]])
            odds_arr = np.array([oh, od, oa])
            names = ["Home Win", "Draw", "Away Win"]
            for k in range(3):
                if model_p[k] / 100.0 > fair[k] / 100.0 + EDGE:
                    won = (res_idx == k)
                    ret = (odds_arr[k] - 1.0) if won else -1.0
                    bets_placed.append({
                        "market": names[k], "odds": float(odds_arr[k]),
                        "model_prob": float(model_p[k]), "fair_prob": float(fair[k] * 100),
                        "won": bool(won), "return": ret,
                    })

        # O/U 2.5 and BTTS where odds exist
        o25, u25 = match.get('over25_odds'), match.get('under25_odds')
        if pd.notna(o25) and o25 and o25 > 1:
            p_over = probs["Over 2.5"] / 100.0
            fair_o = (1.0 / o25) / ((1.0 / o25) + (1.0 / u25)) if (pd.notna(u25) and u25 and u25 > 1) else 1.0 / o25
            if p_over > fair_o + EDGE:
                won = (hg + ag) > 2
                bets_placed.append({"market": "Over 2.5", "odds": float(o25),
                                    "model_prob": p_over * 100, "fair_prob": fair_o * 100,
                                    "won": bool(won),
                                    "return": (o25 - 1.0) if won else -1.0})
        if pd.notna(u25) and u25 and u25 > 1:
            p_under = probs["Under 2.5"] / 100.0
            fair_u = (1.0 / u25) / ((1.0 / u25) + (1.0 / o25)) if (pd.notna(o25) and o25 and o25 > 1) else 1.0 / u25
            if p_under > fair_u + EDGE:
                won = (hg + ag) < 3
                bets_placed.append({"market": "Under 2.5", "odds": float(u25),
                                    "model_prob": p_under * 100, "fair_prob": fair_u * 100,
                                    "won": bool(won),
                                    "return": (u25 - 1.0) if won else -1.0})

        rec["bets"] = bets_placed
        rec["Top Pick"] = max(["Home Win", "Draw", "Away Win"], key=lambda m: probs[m])
        rec["Correct?"] = rec["Top Pick"] == {"H": "Home Win", "D": "Draw", "A": "Away Win"}[outcome]
        records.append(rec)

    results_df = pd.DataFrame(records)
    if results_df.empty:
        print("No testable matches.")
        return results_df

    n = len(results_df)
    brier = results_df['Brier'].mean()
    ll = results_df['LogLoss'].mean()
    top_pick_acc = results_df['Correct?'].mean() * 100

    # baseline: always pick most common class proxy -> uniform probs
    base_brier = 2/3
    base_ll = -np.log(1/3)

    print("\n" + "=" * 60)
    print(f"PROBABILITY QUALITY ({n} matches)")
    print("=" * 60)
    print(f"Model      : Brier {brier:.4f} | LogLoss {ll:.4f}")
    print(f"Uniform    : Brier {base_brier:.4f} | LogLoss {base_ll:.4f}")
    bm = results_df['BM_LogLoss'].dropna()
    if len(bm):
        print(f"Bookmaker  : LogLoss {bm.mean():.4f}  ({len(bm)} matches with odds)")
        verdict = "BEATS the bookmaker" if ll < bm.mean() else f"behind the bookmaker by {ll - bm.mean():.4f}"
        print(f"--> Model {verdict}")
    print(f"Top-pick accuracy (1X2): {top_pick_acc:.1f}%")

    # ROI
    all_bets = [b for recs in results_df['bets'] for b in recs]
    print("\n" + "=" * 60)
    print(f"BETTING SIMULATION ({len(all_bets)} bets, edge >= {EDGE*100:.0f} pts, level stakes)")
    print("=" * 60)
    if all_bets:
        bets_df = pd.DataFrame(all_bets)
        roi = bets_df['return'].mean() * 100
        wins = bets_df['won'].sum()
        print(f"Wins: {wins}/{len(bets_df)} ({wins/len(bets_df)*100:.1f}%) | ROI: {roi:+.2f}%")
        print("\nPer market:")
        for mkt, sub in bets_df.groupby('market'):
            r = sub['return'].mean() * 100
            print(f"  {mkt:<10}: {len(sub):>4} bets | hit {(sub['won'].mean()*100):5.1f}% | ROI {r:+7.2f}%")
    else:
        print("No qualifying bets found.")

    return results_df


if __name__ == "__main__":
    from database import get_historical_data
    df = get_historical_data()
    if df.empty:
        print("No data! Run scraper.py first.")
    else:
        run_backtest(df)
