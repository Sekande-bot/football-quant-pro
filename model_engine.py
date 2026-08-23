import numpy as np
import pandas as pd
from scipy.stats import poisson
from datetime import datetime
from team_news import calculate_pwis_impact, get_team_news

class EloRatings:
    def __init__(self, k_factor=20, home_advantage=65):
        self.ratings = {}
        self.k_factor = k_factor
        self.home_advantage = home_advantage
    
    def get_rating(self, team):
        if team not in self.ratings:
            self.ratings[team] = 1500
        return self.ratings[team]
    
    def update_ratings(self, home_team, away_team, home_goals, away_goals):
        home_elo = self.get_rating(home_team)
        away_elo = self.get_rating(away_team)
        home_adj = home_elo + self.home_advantage
        
        exp_home = 1 / (1 + 10 ** ((away_elo - home_adj) / 400))
        exp_away = 1 - exp_home
        
        if home_goals > away_goals:
            actual_home, actual_away = 1, 0
        elif home_goals < away_goals:
            actual_home, actual_away = 0, 1
        else:
            actual_home, actual_away = 0.5, 0.5
        
        self.ratings[home_team] = home_elo + self.k_factor * (actual_home - exp_home)
        self.ratings[away_team] = away_elo + self.k_factor * (actual_away - exp_away)

def calculate_elo_ratings(df):
    elo = EloRatings()
    df_sorted = df.copy()
    df_sorted['date'] = pd.to_datetime(df_sorted['date'], dayfirst=True, errors='coerce')
    df_sorted = df_sorted.dropna(subset=['date'])
    df_sorted = df_sorted.sort_values('date')
    
    for _, match in df_sorted.iterrows():
        elo.update_ratings(match['home_team'], match['away_team'], match['home_goals'], match['away_goals'])
    
    return elo

def dixon_coles_correction(x, y, lambda_h, lambda_a, rho=-0.13):
    tau = 1.0
    if x == 0 and y == 0:
        tau = 1 - lambda_h * lambda_a * rho
    elif x == 0 and y == 1:
        tau = 1 + lambda_h * rho
    elif x == 1 and y == 0:
        tau = 1 + lambda_a * rho
    elif x == 1 and y == 1:
        tau = 1 - rho
    return tau

def calculate_market_probabilities(home_xg, away_xg, rho=-0.13):
    max_goals = 8
    M = np.zeros((max_goals + 1, max_goals + 1))
    
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            prob = poisson.pmf(h, home_xg) * poisson.pmf(a, away_xg)
            M[h, a] = prob * dixon_coles_correction(h, a, home_xg, away_xg, rho)
    
    M = M / np.sum(M)
    
    FH = np.zeros((max_goals + 1, max_goals + 1))
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            FH[h, a] = poisson.pmf(h, home_xg * 0.45) * poisson.pmf(a, away_xg * 0.45)
    FH = FH / np.sum(FH)
    
    total = np.add.outer(np.arange(max_goals + 1), np.arange(max_goals + 1))
    
    p_home = np.sum(np.tril(M, -1))
    p_draw = np.sum(np.diag(M))
    p_away = np.sum(np.triu(M, 1))
    p_btts = np.sum(M[1:, 1:])
    
    markets = {
        "Home Win": p_home * 100,
        "Away Win": p_away * 100,
        "Draw": p_draw * 100,
        "1X (Home or Draw)": (p_home + p_draw) * 100,
        "X2 (Away or Draw)": (p_away + p_draw) * 100,
        "Over 1.5": np.sum(M[total > 1.5]) * 100,
        "Over 2.5": np.sum(M[total > 2.5]) * 100,
        "Over 3.5": np.sum(M[total > 3.5]) * 100,
        "Under 2.5": np.sum(M[total <= 2.5]) * 100,
        "Under 3.5": np.sum(M[total <= 3.5]) * 100,
        "BTTS Yes": p_btts * 100,
        "BTTS No": (1 - p_btts) * 100,
        "Home DNB": (p_home + 0.5 * p_draw) * 100,
        "Away DNB": (p_away + 0.5 * p_draw) * 100,
        "Home Win to Nil": np.sum(M[1:, 0]) * 100,
        "Away Win to Nil": np.sum(M[0, 1:]) * 100,
        "Home -1.5 (win by 2+)": np.sum(np.tril(M, -2)) * 100,
        "Away -1.5 (win by 2+)": np.sum(np.triu(M, 2)) * 100,
        "FH Over 0.5": (1 - FH[0, 0]) * 100,
        "FH Over 1.5": np.sum(FH[total > 1.5]) * 100,
        "Home FH Clean Sheet": np.sum(FH[:, 0]) * 100,
        "Away FH Clean Sheet": np.sum(FH[0, :]) * 100,
    }
    
    return {k: round(v, 1) for k, v in markets.items()}

def assign_risk_bucket(confidence_score):
    if confidence_score >= 75:
        return "🟢 LOW"
    elif confidence_score >= 55:
        return "🟡 MED"
    else:
        return "🔴 HIGH"

def select_top_pick(probs, exp_home, exp_away):
    """
    Scores every candidate market by: probability + profile fit.
    One-sided games boost handicaps & win-to-nil.
    Open games boost Over 2.5 & BTTS Yes.
    Tight games boost Under 2.5 & BTTS No.
    Balanced games boost double chance.
    """
    total = exp_home + exp_away
    diff = exp_home - exp_away
    side = "Home" if exp_home >= exp_away else "Away"
    
    fav = abs(diff)              # how one-sided the game is
    gr = total - 2.6             # positive = goal-fest, negative = tight game
    
    over_boost = max(0, gr)
    under_boost = max(0, -gr)
    
    dc = "1X (Home or Draw)" if side == "Home" else "X2 (Away or Draw)"
    
    scores = {
        f"{side} Win": probs[f"{side} Win"] + (fav * 12),
        f"{side} DNB": probs[f"{side} DNB"] + (fav * 6),
        f"{side} -1.5 (win by 2+)": probs[f"{side} -1.5 (win by 2+)"] + (fav * 30),
        f"{side} Win to Nil": probs[f"{side} Win to Nil"] + (fav * 18),
        "Over 2.5": probs["Over 2.5"] + (over_boost * 35) - (under_boost * 15),
        "Under 2.5": probs["Under 2.5"] + (under_boost * 30) - (over_boost * 15),
        "BTTS Yes": probs["BTTS Yes"] + (over_boost * 30) - (under_boost * 10),
        "BTTS No": probs["BTTS No"] + (under_boost * 25) - (over_boost * 10),
        "Over 1.5": probs["Over 1.5"] - 12,  # stop it from dominating
        dc: probs[dc] + (8 if (fav < 0.3 and -0.3 <= gr <= 0.3) else 0) - (10 if fav >= 0.5 else 0),
    }
    
    best_market = max(scores, key=scores.get)
    return best_market

def get_exponential_weights(dates, decay=0.02):
    today = datetime.now()
    weights = []
    for date in dates:
        if pd.isna(date):
            weights.append(0)
        else:
            days_ago = (today - date).days
            weights.append(np.exp(-decay * days_ago))
    return np.array(weights)

def get_team_form(df, team_name, window=5, is_home=True, elo=None):
    if is_home:
        venue_matches = df[df['home_team'] == team_name].copy()
    else:
        venue_matches = df[df['away_team'] == team_name].copy()
    
    venue_matches['date'] = pd.to_datetime(venue_matches['date'], dayfirst=True, errors='coerce')
    venue_matches = venue_matches.dropna(subset=['date'])
    venue_matches = venue_matches.sort_values(by='date', ascending=False).head(window)
    
    if len(venue_matches) < 2:
        venue_matches = df[(df['home_team'] == team_name) | (df['away_team'] == team_name)].copy()
        venue_matches['date'] = pd.to_datetime(venue_matches['date'], dayfirst=True, errors='coerce')
        venue_matches = venue_matches.dropna(subset=['date'])
        venue_matches = venue_matches.sort_values(by='date', ascending=False).head(window)
    
    if venue_matches.empty:
        return 1.2
    
    goals_for_list = []
    for _, match in venue_matches.iterrows():
        goals_for_list.append(match['home_goals'] if is_home else match['away_goals'])
    
    weights = get_exponential_weights(venue_matches['date'].values)
    weighted_goals = np.average(goals_for_list, weights=weights) if np.sum(weights) > 0 else np.mean(goals_for_list)
    
    if elo:
        opponents = [match['away_team'] if is_home else match['home_team'] for _, match in venue_matches.iterrows()]
        avg_opponent_elo = np.mean([elo.get_rating(opp) for opp in opponents])
        elo_diff = (avg_opponent_elo - 1500) / 400
        sos_adjustment = max(0.85, min(1.15, 1.0 + (elo_diff * 0.1)))
        weighted_goals *= sos_adjustment
    
    return weighted_goals

def get_defensive_metrics(df, team_name, window=5, is_home=True, elo=None):
    if is_home:
        venue_matches = df[df['home_team'] == team_name].copy()
    else:
        venue_matches = df[df['away_team'] == team_name].copy()
    
    venue_matches['date'] = pd.to_datetime(venue_matches['date'], dayfirst=True, errors='coerce')
    venue_matches = venue_matches.dropna(subset=['date'])
    venue_matches = venue_matches.sort_values(by='date', ascending=False).head(window)
    
    if venue_matches.empty:
        return {'goals_against': 1.2, 'recent_goals_against': 1.2, 'clean_sheet_pct': 0.2}
    
    goals_against_list = venue_matches['away_goals'].tolist() if is_home else venue_matches['home_goals'].tolist()
    
    weights = get_exponential_weights(venue_matches['date'].values)
    weighted_against = np.average(goals_against_list, weights=weights) if np.sum(weights) > 0 else np.mean(goals_against_list)
    
    recent_against = np.mean(goals_against_list[:2]) if len(goals_against_list) >= 2 else weighted_against
    clean_sheets = sum(1 for g in goals_against_list if g == 0)
    clean_sheet_pct = clean_sheets / len(goals_against_list) if goals_against_list else 0.2
    
    if elo:
        opponents = [match['away_team'] if is_home else match['home_team'] for _, match in venue_matches.iterrows()]
        avg_opponent_elo = np.mean([elo.get_rating(opp) for opp in opponents])
        elo_diff = (avg_opponent_elo - 1500) / 400
        sos_adjustment = max(0.85, min(1.15, 1.0 - (elo_diff * 0.1)))
        weighted_against *= sos_adjustment
    
    return {
        'goals_against': weighted_against,
        'recent_goals_against': recent_against,
        'clean_sheet_pct': clean_sheet_pct
    }

def get_home_advantage(df, league='all'):
    league_data = df if league == 'all' else df[df['league'] == league]
    if league_data.empty:
        return 0.3
    home_adv = league_data['home_goals'].mean() - league_data['away_goals'].mean()
    return max(0.0, min(0.5, home_adv))

def get_league_baseline(df, league):
    league_data = df[df['league'] == league]
    if league_data.empty:
        return 2.7
    return (league_data['home_goals'].mean() + league_data['away_goals'].mean()) / 2

def predict_next_matches(df, fixtures_df):
    if fixtures_df.empty:
        return pd.DataFrame()
    
    elo = calculate_elo_ratings(df)
    results = []
    team_news = get_team_news()
    
    for _, fixture in fixtures_df.iterrows():
        home_team = fixture['home']
        away_team = fixture['away']
        league = fixture.get('league', 'all')
        
        home_goals_for = get_team_form(df, home_team, window=5, is_home=True, elo=elo)
        away_goals_for = get_team_form(df, away_team, window=5, is_home=False, elo=elo)
        
        home_def = get_defensive_metrics(df, home_team, window=5, is_home=True, elo=elo)
        away_def = get_defensive_metrics(df, away_team, window=5, is_home=False, elo=elo)
        
        home_xg_mult, home_xga_mult = calculate_pwis_impact(home_team, team_news.get(home_team, []))
        away_xg_mult, away_xga_mult = calculate_pwis_impact(away_team, team_news.get(away_team, []))
        
        home_goals_for *= home_xg_mult
        away_goals_for *= away_xg_mult
        
        away_def_weakness = (
            away_def['goals_against'] * 0.5 +
            away_def['recent_goals_against'] * 0.3 +
            (1.5 - away_def['clean_sheet_pct']) * 0.2
        ) * home_xga_mult
        
        home_def_weakness = (
            home_def['goals_against'] * 0.5 +
            home_def['recent_goals_against'] * 0.3 +
            (1.5 - home_def['clean_sheet_pct']) * 0.2
        ) * away_xga_mult
        
        exp_home = max(0.3, (home_goals_for + away_def_weakness) / 2.0)
        exp_away = max(0.3, (away_goals_for + home_def_weakness) / 2.0)
        
        exp_home += get_home_advantage(df, league)
        
        league_baseline = get_league_baseline(df, league)
        if league_baseline > 0:
            scale = 2.7 / league_baseline
            exp_home *= scale
            exp_away *= scale
        
        probs = calculate_market_probabilities(exp_home, exp_away)
        
        top_pick = select_top_pick(probs, exp_home, exp_away)
        max_prob = probs[top_pick]
        
        results.append({
            "Date": fixture['date'],
            "League": league,
            "Fixture": f"{home_team} vs {away_team}",
            "Exp Goals": f"{exp_home:.2f} - {exp_away:.2f}",
            "Top Pick": top_pick,
            "Prob %": f"{max_prob}%",
            "Risk": assign_risk_bucket(max_prob),
            "All_Markets": probs
        })
    
    return pd.DataFrame(results)