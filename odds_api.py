import requests
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

SMART_API_KEY = os.getenv("SMART_API_KEY")
CACHE_FILE = "odds_cache.json"
CACHE_EXPIRY = 3600  # 1 hour

def get_live_odds():
    """
    Fetches odds from Smart API (Free API Live Football Data)
    """
    if not SMART_API_KEY:
        print("Warning: SMART_API_KEY not found in .env file")
        return {}

    # Check Cache
    if os.path.exists(CACHE_FILE):
        file_age = time.time() - os.path.getmtime(CACHE_FILE)
        if file_age < CACHE_EXPIRY:
            print("Loading odds from cache...")
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)

    print("Fetching fresh odds from Smart API...")
    
    # Placeholder URL - you'll need to update this based on Smart API docs
    url = "https://api.smart-api.com/football/odds"
    
    headers = {
        'Authorization': f'Bearer {SMART_API_KEY}',
        'Accept': 'application/json'
    }
    
    params = {
        'league': 'premier-league',
        'season': '2024-2025'
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        odds_dict = {}
        
        for match in data.get('matches', []):
            home_team = match.get('home_team', '')
            away_team = match.get('away_team', '')
            
            fixture_key = f"{home_team} vs {away_team}"
            
            odds = {}
            
            if 'odds' in match:
                match_odds = match['odds']
                
                if 'home_win' in match_odds:
                    odds['Home Win'] = float(match_odds['home_win'])
                if 'draw' in match_odds:
                    odds['Draw'] = float(match_odds['draw'])
                if 'away_win' in match_odds:
                    odds['Away Win'] = float(match_odds['away_win'])
                if 'over_2_5' in match_odds:
                    odds['Over 2.5'] = float(match_odds['over_2_5'])
                if 'under_2_5' in match_odds:
                    odds['Under 2.5'] = float(match_odds['under_2_5'])
                if 'btts_yes' in match_odds:
                    odds['BTTS Yes'] = float(match_odds['btts_yes'])
                if 'btts_no' in match_odds:
                    odds['BTTS No'] = float(match_odds['btts_no'])
            
            odds_dict[fixture_key] = odds

        # Save to Cache
        with open(CACHE_FILE, 'w') as f:
            json.dump(odds_dict, f)
            
        print(f"Successfully fetched odds for {len(odds_dict)} matches.")
        return odds_dict

    except Exception as e:
        print(f"Error fetching odds: {e}")
        return {}

def calculate_ev(our_probability_percent, bookmaker_odds):
    """
    Calculate Expected Value (EV).
    EV = (Probability × Odds) - 1
    """
    if not bookmaker_odds or bookmaker_odds <= 0:
        return 0
    probability = our_probability_percent / 100.0
    ev = (probability * bookmaker_odds) - 1
    return round(ev * 100, 1)

def simulate_bookmaker_odds(our_probs):
    """
    Generate realistic bookmaker odds by adding a margin.
    Bookmakers typically add 5-10% margin (overround).
    """
    odds = {}
    margin = 1.08  # 8% bookmaker margin
    
    for market, prob in our_probs.items():
        if prob > 0:
            adjusted_prob = min(prob * margin, 95)
            odds[market] = round(100 / adjusted_prob, 2)
    
    return odds