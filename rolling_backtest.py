"""
Rolling within-season evaluation — the "fair fight" the user asked for:

- Test on the latest season's matches.
- At every point, the model may ONLY use matches from THAT season that were
  played before the match being predicted (no prior-season knowledge).
- Models refit continuously as the season progresses.

Pick quality is measured two ways:
1. 1X2 top-pick accuracy (the hard, honest number).
2. CONFIDENT PICKS: engine picks its single best market per match (any market),
   but only speaks when probability >= CONF_THRESHOLD. Reported as
   hit-rate + coverage. This is the >=70% accuracy product.
"""

import sys
import numpy as np
import pandas as pd

from database import get_historical_data
from model_engine import fit_league_model, calculate_market_probabilities

CONF_THRESHOLD = 70.0
REFIT_EVERY = 10          # refit after this many newly played matches
MIN_TRAIN = 30            # need at least this many in-season matches to fit


def outcome(hg, ag):
    return "H" if hg > ag else ("A" if hg < ag else "D")


def check_market(market, hg, ag):
    total, diff = hg + ag, hg - ag
    table = {
        "Home Win": diff > 0, "Away Win": diff < 0, "Draw": diff == 0,
        "1X (Home or Draw)": diff >= 0, "X2 (Away or Draw)": diff <= 0,
        "12 (Either Team Wins)": diff != 0,
        "Home DNB": (diff > 0) or (diff == 0),   # push counts separately? keep simple: DNB win-or-push
        "Away DNB": (diff < 0) or (diff == 0),
        "Over 0.5": total >= 1, "Over 1.5": total >= 2, "Over 2.5": total >= 3,
        "Over 3.5": total >= 4, "Under 1.5": total <= 1, "Under 2.5": total <= 2,
        "Under 3.5": total <= 3, "Under 4.5": total <= 4,
        "BTTS Yes": hg > 0 and ag > 0, "BTTS No": hg == 0 or ag == 0,
        "Home Over 0.5": hg >= 1, "Away Over 0.5": ag >= 1,
    }
    if market not in table:
        return None
    return bool(table[market])


def confident_pick(probs, threshold=CONF_THRESHOLD):
    """Best market overall; only return it if >= threshold."""
    best, best_p = None, -1.0
    for m, p in probs.items():
        if m.startswith("_") or not isinstance(p, (int, float)):
            continue
        if check_market(m, -1, -1) is None:      # not an evaluable market
            continue
        if m in ("Home DNB", "Away DNB"):        # push semantics muddy hit-rate
            continue
        if p > best_p:
            best, best_p = m, p
    if best and best_p >= threshold:
        return best, best_p
    return None, best_p


def run_rolling(league_name=None, df=None):
    df = (df if df is not None else get_historical_data()).copy()
    df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['date', 'home_goals', 'away_goals'])
    df = df.sort_values('date')

    leagues = [league_name] if league_name else sorted(df['league'].unique())

    all_1x2, all_conf, all_cov = [], [], []
    per_league = {}

    for lg in leagues:
        dfl = df[df['league'] == lg]
        latest_season = dfl['season'].max()
        season = dfl[dfl['season'] == latest_season].sort_values('date').reset_index(drop=True)
        if len(season) < MIN_TRAIN + 20:
            continue

        model = None
        fitted_at = -1
        res_1x2, res_conf, res_cov = [], [], []
        pick_counts = {}

        for i, m in season.iterrows():
            hist = season.iloc[:i]           # ONLY current season, strictly before this match
            if len(hist) < MIN_TRAIN:
                continue
            if model is None or (i - fitted_at) >= REFIT_EVERY:
                model = fit_league_model(hist, lg, ref_date=m['date'])
                fitted_at = i
                if model is None:
                    continue
            xg = model.expected_goals(m['home_team'], m['away_team'])
            if xg is None:
                continue
            lam, mu = xg
            probs = calculate_market_probabilities(lam, mu, rho=model.rho)

            hg, ag = int(m['home_goals']), int(m['away_goals'])
            oc = outcome(hg, ag)

            # 1X2 top pick
            p1x2 = {"H": probs["Home Win"], "D": probs["Draw"], "A": probs["Away Win"]}
            pick = max(p1x2, key=p1x2.get)
            res_1x2.append((pick == oc, p1x2[pick]))

            # confident pick across ALL markets
            cp, cp_p = confident_pick(probs)
            if cp is not None:
                won = check_market(cp, hg, ag)
                res_conf.append((won, cp_p))
                pick_counts[cp] = pick_counts.get(cp, 0) + 1
            res_cov.append(cp is not None)

        if res_1x2:
            acc = np.mean([r[0] for r in res_1x2]) * 100
            conf_acc = np.mean([r[0] for r in res_conf]) * 100 if res_conf else float('nan')
            cov = np.mean(res_cov) * 100
            avg_conf = np.mean([r[1] for r in res_conf]) if res_conf else float('nan')
            per_league[lg] = {
                "n": len(res_1x2), "acc_1x2": acc,
                "conf_n": len(res_conf), "conf_acc": conf_acc,
                "conf_avg_prob": avg_conf, "coverage": cov,
                "pick_mix": pick_counts,
            }
            all_1x2 += [r[0] for r in res_1x2]
            all_conf += [r[0] for r in res_conf]
            all_cov += res_cov

    print("=" * 66)
    title = f"ROLLING IN-SEASON EVALUATION — {league_name or 'ALL LEAGUES'}"
    print(f"{title:^66}")
    print(f"(train on current season only, continuous refits, no prior seasons)")
    print("=" * 66)

    print(f"\n{'League':<18}{'N':>5}{'1X2 acc':>9}{'BankerN':>9}{'BankerAcc':>11}{'AvgConf':>9}{'Cover':>7}")
    for lg, s in sorted(per_league.items(), key=lambda x: -x[1]['n']):
        print(f"{lg:<18}{s['n']:>5}{s['acc_1x2']:>8.1f}%{s['conf_n']:>9}"
              f"{s['conf_acc']:>10.1f}%{s['conf_avg_prob']:>8.1f}%{s['coverage']:>6.0f}%")

    if all_1x2:
        print("-" * 66)
        print(f"OVERALL 1X2 accuracy : {np.mean(all_1x2)*100:.1f}%  ({len(all_1x2)} matches)")
        if all_conf:
            print(f"BANKER PICKS         : {np.mean(all_conf)*100:.1f}% hit "
                  f"({len(all_conf)} picks, coverage {np.mean(all_cov)*100:.0f}% of matches)")
        mix = {}
        for s in per_league.values():
            for m, c in s["pick_mix"].items():
                mix[m] = mix.get(m, 0) + c
        print("\nWhat the banker picks were:")
        for m, c in sorted(mix.items(), key=lambda x: -x[1]):
            print(f"  {m:<22} {c:>4}")
    return per_league


if __name__ == "__main__":
    lg = sys.argv[1] if len(sys.argv) > 1 else None
    run_rolling(lg)
