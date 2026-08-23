import pandas as pd
import numpy as np
from model_engine import (
    calculate_market_probabilities, get_team_form, get_defensive_metrics,
    get_home_advantage, get_league_baseline, calculate_elo_ratings,
    assign_risk_bucket, select_top_pick
)

def check_pick_won(top_pick, actual_home, actual_away):
    """Returns '✅', '❌', or '➖' (push/void)"""
    total = actual_home + actual_away
    diff = actual_home - actual_away
    
    if top_pick == "Home Win":
        return "✅" if diff > 0 else "❌"
    if top_pick == "Away Win":
        return "✅" if diff < 0 else "❌"
    if top_pick == "1X (Home or Draw)":
        return "✅" if diff >= 0 else "❌"
    if top_pick == "X2 (Away or Draw)":
        return "✅" if diff <= 0 else "❌"
    if top_pick == "Home DNB":
        return "✅" if diff > 0 else ("➖" if diff == 0 else "❌")
    if top_pick == "Away DNB":
        return "✅" if diff < 0 else ("➖" if diff == 0 else "❌")
    if top_pick == "Over 1.5":
        return "✅" if total > 1.5 else "❌"
    if top_pick == "Over 2.5":
        return "✅" if total > 2.5 else "❌"
    if top_pick == "Over 3.5":
        return "✅" if total > 3.5 else "❌"
    if top_pick == "Under 2.5":
        return "✅" if total < 2.5 else "❌"
    if top_pick == "Under 3.5":
        return "✅" if total < 3.5 else "❌"
    if top_pick == "BTTS Yes":
        return "✅" if actual_home > 0 and actual_away > 0 else "❌"
    if top_pick == "BTTS No":
        return "✅" if actual_home == 0 or actual_away == 0 else "❌"
    if top_pick == "Home -1.5 (win by 2+)":
        return "✅" if diff >= 2 else "❌"
    if top_pick == "Away -1.5 (win by 2+)":
        return "✅" if diff <= -2 else "❌"
    if top_pick == "Home Win to Nil":
        return "✅" if diff > 0 and actual_away == 0 else "❌"
    if top_pick == "Away Win to Nil":
        return "✅" if diff < 0 and actual_home == 0 else "❌"
    return "❌"

def run_backtest(df):
    print(f"Starting backtest. Total rows: {len(df)}")
    
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['date'])
    df = df.sort_values(by='date', ascending=True)
    
    if df['date'].notna().sum() == 0:
        return pd.DataFrame()
    
    latest_season = df['season'].max()
    test_matches = df[df['season'] == latest_season].tail(50)
    print(f"Testing on {len(test_matches)} matches from {latest_season}")
    
    results = []
    
    for index, match in test_matches.iterrows():
        match_date = match['date']
        home_team = match['home_team']
        away_team = match['away_team']
        league = match.get('league', 'all')
        
        historical_data = df[df['date'] < match_date]
        
        home_history = historical_data[
            (historical_data['home_team'] == home_team) | (historical_data['away_team'] == home_team)
        ]
        away_history = historical_data[
            (historical_data['home_team'] == away_team) | (historical_data['away_team'] == away_team)
        ]
        
        if len(home_history) < 3 or len(away_history) < 3:
            continue
        
        try:
            elo = calculate_elo_ratings(historical_data)
            
            home_goals_for = get_team_form(historical_data, home_team, window=5, is_home=True, elo=elo)
            away_goals_for = get_team_form(historical_data, away_team, window=5, is_home=False, elo=elo)
            
            home_def = get_defensive_metrics(historical_data, home_team, window=5, is_home=True, elo=elo)
            away_def = get_defensive_metrics(historical_data, away_team, window=5, is_home=False, elo=elo)
            
            away_def_weakness = (
                away_def['goals_against'] * 0.5 +
                away_def['recent_goals_against'] * 0.3 +
                (1.5 - away_def['clean_sheet_pct']) * 0.2
            )
            home_def_weakness = (
                home_def['goals_against'] * 0.5 +
                home_def['recent_goals_against'] * 0.3 +
                (1.5 - home_def['clean_sheet_pct']) * 0.2
            )
            
            exp_home = max(0.3, (home_goals_for + away_def_weakness) / 2.0)
            exp_away = max(0.3, (away_goals_for + home_def_weakness) / 2.0)
            exp_home += get_home_advantage(historical_data, league)
            
            league_baseline = get_league_baseline(historical_data, league)
            if league_baseline > 0:
                scale = 2.7 / league_baseline
                exp_home *= scale
                exp_away *= scale
            
            probs = calculate_market_probabilities(exp_home, exp_away)
            top_pick = select_top_pick(probs, exp_home, exp_away)
            max_prob = probs[top_pick]
            risk = assign_risk_bucket(max_prob)
            
            actual_home = match['home_goals']
            actual_away = match['away_goals']
            
            if actual_home > actual_away:
                actual_result = "Home Win"
            elif actual_away > actual_home:
                actual_result = "Away Win"
            else:
                actual_result = "Draw"
            
            won = check_pick_won(top_pick, actual_home, actual_away)
            
            results.append({
                "Date": match_date.strftime('%Y-%m-%d'),
                "Fixture": f"{home_team} vs {away_team}",
                "Top Pick": top_pick,
                "Confidence": f"{max_prob}%",
                "Risk": risk,
                "Actual Result": actual_result,
                "Score": f"{actual_home}-{actual_away}",
                "Won?": won
            })
        except Exception as e:
            print(f"Error on {home_team} vs {away_team}: {e}")
            continue
    
    if not results:
        return pd.DataFrame()
    
    results_df = pd.DataFrame(results)
    
    # Stats excluding pushes
    wins = len(results_df[results_df['Won?'] == "✅"])
    losses = len(results_df[results_df['Won?'] == "❌"])
    pushes = len(results_df[results_df['Won?'] == "➖"])
    settled = wins + losses
    win_rate = (wins / settled * 100) if settled > 0 else 0
    
    print(f"\n=== BACKTEST RESULTS ===")
    print(f"Wins: {wins} | Losses: {losses} | Pushes: {pushes}")
    print(f"Win Rate (settled bets): {win_rate:.1f}%")
    
    for level in ["🟢 LOW", "🟡 MED", "🔴 HIGH"]:
        subset = results_df[results_df['Risk'].str.contains(level.split()[1])]
        s_wins = len(subset[subset['Won?'] == "✅"])
        s_losses = len(subset[subset['Won?'] == "❌"])
        if s_wins + s_losses > 0:
            print(f"{level}: {s_wins}W-{s_losses}L ({s_wins/(s_wins+s_losses)*100:.1f}%)")
    
    return results_df