"""
Football prediction engine v3.

Core: Dixon-Coles bivariate Poisson fitted by maximum likelihood per league,
with exponential time-decay weighting (recent form matters more) and L2
shrinkage of team ratings toward league average (protects small samples).

Home advantage and the low-score correlation rho are FITTED from data,
not hardcoded. Strength of schedule is handled implicitly by the regression
(each team's rating depends on the opponents it actually faced).

Public API:
    fit_all_models(df)                     -> {league: LeagueModel}
    predict_match_probs(model, home, away) -> market probability dict (%)
    select_top_pick(probs)                 -> honest most-probable core market
    best_ev_bets(probs, odds_dict, min_edge) -> positive-EV opportunities
    predict_next_matches(df, fixtures_df)  -> DataFrame for the Streamlit app
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson
from scipy.optimize import minimize
from datetime import datetime

MAX_GOALS = 10
HALF_LIFE_DAYS = 450.0         # tuned via backtest sweep (90-730); ~1.5yr memory is optimal
SHRINKAGE = 0.003             # L2 pull of ratings toward 0 (league average)
RHO_BOUNDS = (-0.20, 0.05)

# ---------------------------------------------------------------- fitting ---

def _decay_weights(dates, ref_date):
    days = (ref_date - dates).dt.days.astype(float)
    return np.exp(-np.log(2.0) * np.maximum(days, 0) / HALF_LIFE_DAYS)


def _tau(h, a, lam, mu, rho):
    t = np.ones_like(lam)
    t = np.where((h == 0) & (a == 0), 1 - lam * mu * rho, t)
    t = np.where((h == 0) & (a == 1), 1 + lam * rho, t)
    t = np.where((h == 1) & (a == 0), 1 + mu * rho, t)
    t = np.where((h == 1) & (a == 1), 1 - rho, t)
    return t


def _neg_log_likelihood(params, h_idx, a_idx, gh, ga, w, n_teams):
    atk = params[:n_teams]
    dfn = params[n_teams:2 * n_teams]
    adv = params[2 * n_teams]
    rho = params[2 * n_teams + 1]

    lam = np.exp(atk[h_idx] + dfn[a_idx] + adv)
    mu = np.exp(atk[a_idx] + dfn[h_idx])

    # guard against overflow
    lam = np.clip(lam, 1e-6, 15.0)
    mu = np.clip(mu, 1e-6, 15.0)

    tau = _tau(gh, ga, lam, mu, rho)
    ll = w * (poisson.logpmf(gh, lam) + poisson.logpmf(ga, mu) + np.log(np.maximum(tau, 1e-10)))

    penalty = SHRINKAGE * (np.sum(atk ** 2) + np.sum(dfn ** 2)) + 10.0 * (np.sum(atk)) ** 2
    return -ll.sum() + penalty


def _gradient(params, h_idx, a_idx, gh, ga, w, n_teams):
    atk = params[:n_teams]
    dfn = params[n_teams:2 * n_teams]
    adv = params[2 * n_teams]
    rho = params[2 * n_teams + 1]

    lam = np.clip(np.exp(atk[h_idx] + dfn[a_idx] + adv), 1e-6, 15.0)
    mu = np.clip(np.exp(atk[a_idx] + dfn[h_idx]), 1e-6, 15.0)

    tau = _tau(gh, ga, lam, mu, rho)
    safe_tau = np.maximum(tau, 1e-10)

    dtau_dlam = np.zeros_like(lam)
    dtau_dmu = np.zeros_like(mu)
    m00 = (gh == 0) & (ga == 0); m01 = (gh == 0) & (ga == 1); m10 = (gh == 1) & (ga == 0)
    dtau_dlam[m00] = -mu[m00] * rho
    dtau_dlam[m01] = rho
    dtau_dmu[m00] = -lam[m00] * rho
    dtau_dmu[m10] = rho

    base_lam = gh / lam - 1.0
    base_mu = ga / mu - 1.0
    # chain rule into minimizing NEGATIVE log-likelihood:
    # dNLL/d(lam) = -(gh/lam - 1 + dtau/dlam / tau); then * lam by exponential chain rule
    grad_lam = w * -(base_lam + dtau_dlam / safe_tau) * lam
    grad_mu = w * -(base_mu + dtau_dmu / safe_tau) * mu

    grad_atk = np.zeros(n_teams)
    grad_dfn = np.zeros(n_teams)
    np.add.at(grad_atk, h_idx, grad_lam)
    np.add.at(grad_atk, a_idx, grad_mu)
    np.add.at(grad_dfn, a_idx, grad_lam)
    np.add.at(grad_dfn, h_idx, grad_mu)
    grad_atk += 2 * SHRINKAGE * atk + 20.0 * atk.sum()
    grad_dfn += 2 * SHRINKAGE * dfn

    grad_adv = grad_lam.sum()
    # d(tau)/d(rho): tau00=-lam*mu, tau01=+lam, tau10=+mu, tau11=-1
    m11 = (gh == 1) & (ga == 1)
    dtau_drho = (-lam * mu * m00.astype(float)
                 + lam * m01.astype(float)
                 + mu * m10.astype(float)
                 - 1.0 * m11.astype(float))
    grad_rho = float((-w * dtau_drho / safe_tau).sum())

    return np.concatenate([grad_atk, grad_dfn, [grad_adv], [grad_rho]])


class LeagueModel:
    """Fitted Dixon-Coles model for one league."""

    def __init__(self, league, teams, attack, defense, home_adv, rho, avg_goals,
                 last_date, shot_quality=None):
        self.league = league
        self.teams = teams
        self.attack = attack          # dict team -> attack rating
        self.defense = defense        # dict team -> defence rating
        self.home_adv = home_adv      # additive on log scale
        self.rho = rho
        self.avg_goals = avg_goals
        self.last_date = last_date
        self.shot_quality = shot_quality or {}

    def expected_goals(self, home_team, away_team):
        if home_team not in self.attack or away_team not in self.attack:
            return None
        atk_h, def_a = self.attack[home_team], self.defense[away_team]
        atk_a, def_h = self.attack[away_team], self.defense[home_team]
        lam = np.exp(atk_h + def_a + self.home_adv)
        mu = np.exp(atk_a + def_h)
        lam = float(np.clip(lam, 0.2, 4.5))
        mu = float(np.clip(mu, 0.15, 4.5))

        # Finishing-regression: blend slightly toward shot-based expectation so
        # teams converting way above/below their shot volume get pulled back.
        sq = self.shot_quality
        key = f"{home_team}|{away_team}"
        if key in sq:
            h_adj, a_adj = sq[key]
            blend = 0.15
            lam = (1 - blend) * lam + blend * max(0.2, lam * h_adj)
            mu = (1 - blend) * mu + blend * max(0.15, mu * a_adj)
        return lam, mu


def fit_league_model(df_league, league_name, ref_date=None):
    df = df_league.copy()
    df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['date', 'home_goals', 'away_goals'])
    df = df.sort_values('date')
    if len(df) < 50:
        return None

    ref_date = ref_date or df['date'].max()

    teams = sorted(set(df['home_team']) | set(df['away_team']))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    h_idx = df['home_team'].map(idx).values
    a_idx = df['away_team'].map(idx).values
    gh = df['home_goals'].values.astype(float)
    ga = df['away_goals'].values.astype(float)
    w = _decay_weights(pd.to_datetime(df['date']), pd.Timestamp(ref_date))
    w = w.values if hasattr(w, 'values') else w

    home_mean = np.average(gh, weights=w)
    away_mean = np.average(ga, weights=w)
    adv0 = np.log(max(home_mean, 0.3) / max(away_mean, 0.3))

    x0 = np.concatenate([np.zeros(n), np.zeros(n),
                         [adv0], [-0.08]])
    bounds = ([(None, None)] * (2 * n) + [(0.0, np.log(3.0)), list(RHO_BOUNDS)])

    res = minimize(
        _neg_log_likelihood, x0,
        args=(h_idx, a_idx, gh, ga, w, n),
        jac=_gradient, method='L-BFGS-B', bounds=bounds,
        options={'maxiter': 300}
    )

    p = res.x
    attack = {teams[i]: float(p[i]) for i in range(n)}
    defense = {teams[i]: float(p[n + i]) for i in range(n)}
    home_adv = float(p[2 * n])
    rho = float(p[2 * n + 1])
    avg_goals = float(home_mean + away_mean)

    # Shot-quality regression factors (goals scored relative to shots generated)
    shot_quality = _shot_quality_factors(df, w)

    return LeagueModel(league_name, teams, attack, defense, home_adv, rho,
                       avg_goals, ref_date, shot_quality)


def _shot_quality_factors(df, weights):
    """
    For each team compute goals-per-SOT vs league average, shrunk heavily.
    Returns dict "home|away" -> (mult_home, mult_away) for a specific fixture.
    Teams that finish hot get a <1 multiplier (expected regression) and vice versa.
    """
    tot_sot = (df['home_sot'].fillna(0) + df['away_sot'].fillna(0)).sum()
    tot_goals = df['home_goals'].sum() + df['away_goals'].sum()
    lg_per_sot = tot_goals / max(tot_sot, 1)

    gf = {}
    sot_for = {}
    for pos_h, pos_a, gcol, scol in [(True, False, 'home_goals', 'home_sot'),
                                     (False, True, 'away_goals', 'away_sot')]:
        mask_h = df['home_team'] if pos_h else df['away_team']
        mask_o = df['away_team'] if pos_h else df['home_team']
        for team, g, s, wt in zip(mask_h, df[gcol].fillna(0), df[scol].fillna(0), weights):
            gf[team] = gf.get(team, 0.0) + g * wt
            sot_for[team] = sot_for.get(team, 0.0) + s * wt

    quality = {}
    for team in gf:
        exp_g = sot_for[team] * lg_per_sot
        if exp_g > 0.5:
            raw = gf[team] / exp_g
            # shrink 70% toward 1.0 (finishing is noisy)
            quality[team] = 1.0 + (min(max(raw, 0.6), 1.4) - 1.0) * 0.3
        else:
            quality[team] = 1.0

    result = {}
    for _, r in df.iterrows():
        result[f"{r['home_team']}|{r['away_team']}"] = (
            quality.get(r['home_team'], 1.0),
            quality.get(r['away_team'], 1.0),
        )
    return result


def fit_all_models(df):
    models = {}
    for league, sub in df.groupby('league'):
        m = fit_league_model(sub, league)
        if m:
            models[league] = m
    return models


# ------------------------------------------------------------- probabilities -

def build_score_matrix(lam, mu, rho):
    g = np.arange(MAX_GOALS + 1)
    P_h = poisson.pmf(g[:, None], lam)
    P_a = poisson.pmf(g[None, :], mu)
    M = P_h * P_a * _tau(g[:, None], g[None, :], lam, mu, rho)
    return M / M.sum()


def calculate_market_probabilities(home_xg, away_xg, rho=None):
    """All market probabilities (%) from expected goals."""
    if rho is None:
        rho = -0.08
    M = build_score_matrix(home_xg, away_xg, rho)
    idx = np.arange(MAX_GOALS + 1)
    total = idx[:, None] + idx[None, :]
    diff = idx[:, None] - idx[None, :]

    p_home = float(M[diff > 0].sum())
    p_draw = float(np.trace(M))
    p_away = float(M[diff < 0].sum())

    g = np.arange(MAX_GOALS + 1)
    totals_dist = np.array([M[total == k].sum() for k in range(2 * MAX_GOALS + 1)])
    home_goals_dist = M.sum(axis=1)
    away_goals_dist = M.sum(axis=0)

    # Half-time approximation: ~44% of goals before HT
    FH = build_score_matrix(home_xg * 0.44, away_xg * 0.44, rho)
    fh_total = idx[:, None] + idx[None, :]
    fh_home_cs = float(FH[:, 0].sum())
    fh_away_cs = float(FH[0, :].sum())

    cs_flat = [(f"{h}-{a}", float(M[h, a])) for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)]
    top_scores = sorted(cs_flat, key=lambda x: -x[1])[:5]

    markets = {
        "Home Win": p_home * 100,
        "Draw": p_draw * 100,
        "Away Win": p_away * 100,
        "1X (Home or Draw)": (p_home + p_draw) * 100,
        "X2 (Away or Draw)": (p_away + p_draw) * 100,
        "12 (Either Team Wins)": (p_home + p_away) * 100,
        "Home DNB": (p_home / max(p_home + p_away, 1e-9)) * 100,
        "Away DNB": (p_away / max(p_home + p_away, 1e-9)) * 100,
        "Over 0.5": totals_dist[1:].sum() * 100,
        "Over 1.5": totals_dist[2:].sum() * 100,
        "Over 2.5": totals_dist[3:].sum() * 100,
        "Over 3.5": totals_dist[4:].sum() * 100,
        "Over 4.5": totals_dist[5:].sum() * 100,
        "Under 1.5": totals_dist[:2].sum() * 100,
        "Under 2.5": totals_dist[:3].sum() * 100,
        "Under 3.5": totals_dist[:4].sum() * 100,
        "BTTS Yes": float(M[1:, 1:].sum()) * 100,
        "BTTS No": float(1 - M[1:, 1:].sum()) * 100,
        "Home Over 0.5": home_goals_dist[1:].sum() * 100,
        "Home Over 1.5": home_goals_dist[2:].sum() * 100,
        "Away Over 0.5": away_goals_dist[1:].sum() * 100,
        "Away Over 1.5": away_goals_dist[2:].sum() * 100,
        "Home Win to Nil": float(np.tril(M, -1)[:, 0].sum()) * 100,
        "Away Win to Nil": float(M[0, 1:].sum()) * 100,
        "Home -1.5 (win by 2+)": float(M[diff >= 2].sum()) * 100,
        "Away -1.5 (win by 2+)": float(M[diff <= -2].sum()) * 100,
        "FH Over 0.5": float(1 - FH[0, 0]) * 100,
        "FH Over 1.5": float(FH[fh_total >= 2].sum()) * 100,
        "FH Under 1.5": float(FH[fh_total <= 1].sum()) * 100,
        "Home FH Clean Sheet": fh_home_cs * 100,
        "Away FH Clean Sheet": fh_away_cs * 100,
        "_exact_total_goals": {int(k): float(totals_dist[k] * 100) for k in range(0, 7)},
        "_top_correct_scores": [(s, round(p * 100, 1)) for s, p in top_scores],
        "_score_matrix_shape": (home_xg, away_xg),
    }
    markets["Exp Goals"] = f"{home_xg:.2f} - {away_xg:.2f}"
    return markets


CORE_MARKETS = ["Home Win", "Draw", "Away Win", "Over 2.5", "Under 2.5", "BTTS Yes", "BTTS No"]

def select_top_pick(probs):
    """Most probable market among core, honestly-ranked markets."""
    available = [m for m in CORE_MARKETS if m in probs]
    best = max(available, key=lambda m: probs[m])
    return best, probs[best]


def assign_risk_bucket(confidence_score):
    if confidence_score >= 60:
        return "🟢 LOW"
    elif confidence_score >= 45:
        return "🟡 MED"
    else:
        return "🔴 HIGH"


def calculate_ev(prob_pct, odds):
    """EV% = prob*odds - 1, accounting for nothing else. Odds must be decimal."""
    if not odds or odds <= 1.0:
        return None
    return round((prob_pct / 100.0 * odds - 1) * 100, 1)


def best_ev_bets(probs, odds_dict, min_edge=3.0):
    """Compare model probabilities to real bookmaker odds; return +EV bets."""
    bets = []
    if not odds_dict:
        return bets
    for market, odds in odds_dict.items():
        if market.startswith("_") or market not in probs:
            continue
        ev = calculate_ev(probs[market], odds)
        if ev is not None and ev >= min_edge:
            bets.append({"market": market, "odds": odds, "prob": probs[market], "ev_pct": ev})
    return sorted(bets, key=lambda b: -b["ev_pct"])


# ------------------------------------------------------------ predictions API

def predict_match_probs(models, league, home_team, away_team):
    """Returns (markets_dict, lam, mu) or (None, None, None) if unmodellable."""
    model = models.get(league)
    if model is None:
        # fall back to any model that knows both teams
        for m in models.values():
            if home_team in m.attack and away_team in m.attack:
                model = m
                break
    if model is None:
        return None, None, None

    xg = model.expected_goals(home_team, away_team)
    if xg is None:
        return None, None, None
    lam, mu = xg
    probs = calculate_market_probabilities(lam, mu, rho=model.rho)
    return probs, lam, mu


# ------------------------------------------------------------ market blending

BLEND_W = 0.65   # weight on our model when bookmaker odds are available

BLEND_TARGETS = {
    "1X2": (["Home Win", "Draw", "Away Win"], ["home_odds", "draw_odds", "away_odds"]),
    "OU25": (["Over 2.5", "Under 2.5"], ["over25_odds", "under25_odds"]),
}


def blend_with_market(probs, odds_row, w=BLEND_W):
    """
    Blend model probabilities with de-margined bookmaker probabilities.
    odds_row: dict keyed like DB columns ('home_odds', ...) OR market names.
    Only touches 1X2 and O/U 2.5; other markets stay pure-model.
    Returns a NEW dict; input untouched (EV must always use pure model probs).
    """
    def get_odd(key):
        if key in odds_row:
            return odds_row[key]
        alias = {"home_odds": "Home Win", "draw_odds": "Draw", "away_odds": "Away Win",
                 "over25_odds": "Over 2.5", "under25_odds": "Under 2.5"}
        return odds_row.get(alias.get(key))

    out = dict(probs)
    for _, (mkts, cols) in BLEND_TARGETS.items():
        odds = [get_odd(c) for c in cols]
        if any(o is None or pd.isna(o) or o <= 1 for o in odds):
            continue
        inv = np.array([1.0 / o for o in odds])
        fair = inv / inv.sum() * 100
        blended = [w * probs[m] + (1 - w) * f for m, f in zip(mkts, fair)]
        for m, b in zip(mkts, blended):
            out[m] = round(b, 1)
    return out


def predict_next_matches(df, fixtures_df, live_odds=None):
    """
    live_odds: optional {fixture_key: {market_name: decimal_odds}} from odds_api.
    Displayed probabilities are blended with the market when available;
    EV flags always come from PURE model probabilities vs those odds.
    """
    if fixtures_df is None or fixtures_df.empty:
        return pd.DataFrame()

    models = fit_all_models(df)
    results = []

    for _, fixture in fixtures_df.iterrows():
        home_team = fixture['home']
        away_team = fixture['away']
        league = fixture.get('league', 'Unknown League')

        probs, lam, mu = predict_match_probs(models, league, home_team, away_team)
        if probs is None:
            continue

        key = f"{home_team} vs {away_team}"
        bookie = (live_odds or {}).get(key, {})

        ev_bets = best_ev_bets(probs, bookie, min_edge=3.0)

        display_probs = blend_with_market(probs, bookie) if bookie else probs
        top_pick, top_prob = select_top_pick(display_probs)

        results.append({
            "Date": fixture.get('date', ''),
            "League": league,
            "Fixture": f"{home_team} vs {away_team}",
            "Exp Goals": f"{lam:.2f} - {mu:.2f}",
            "Top Pick": top_pick,
            "Prob %": round(float(top_prob), 1),
            "Risk": assign_risk_bucket(top_prob),
            "Correct Scores": ", ".join(f"{s} ({p}%)" for s, p in probs["_top_correct_scores"]),
            "Best EV": max(ev_bets, key=lambda b: b["ev_pct"]) if ev_bets else None,
            "All_Markets": display_probs,
            "Pure_Markets": probs,
        })

    return pd.DataFrame(results)
