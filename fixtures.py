import requests
import pandas as pd
import os
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
try:
    API_TOKEN = st.secrets["FOOTBALL_DATA_API_KEY"]
except Exception:
    API_TOKEN = os.getenv("FOOTBALL_DATA_API_KEY")

# Map API names to our DB names
NAME_MAPPING = {
    "Manchester United": "Man United", "Wolverhampton Wanderers": "Wolves",
    "Sheffield United": "Sheffield Utd", "Nottingham Forest": "Nott'm Forest",
    "Brighton & Hove Albion": "Brighton", "West Ham United": "West Ham",
    "Newcastle United": "Newcastle", "Tottenham Hotspur": "Tottenham",
    "Aston Villa": "Aston Villa", "Crystal Palace": "Crystal Palace",
    "Everton": "Everton", "Fulham": "Fulham", "Brentford": "Brentford",
    "Chelsea": "Chelsea", "Liverpool": "Liverpool", "Arsenal": "Arsenal",
    "Hull City": "Hull City", "Ipswich Town": "Ipswich Town",
    "Sunderland": "Sunderland", "Leeds United": "Leeds",
    "Coventry City": "Coventry", "Burnley": "Burnley",
    "Manchester City": "Man City", "Leicester City": "Leicester",
    "Southampton": "Southampton", "Bournemouth": "Bournemouth",
    "Real Madrid": "Real Madrid", "Barcelona": "Barcelona",
    "Atletico Madrid": "Atletico Madrid", "Bayern Munich": "Bayern Munich",
    "Borussia Dortmund": "Dortmund", "Bayer Leverkusen": "Leverkusen",
    "Inter Milan": "Inter Milan", "Juventus": "Juventus",
    "AC Milan": "AC Milan", "Napoli": "Napoli", "PSG": "PSG"
}

def get_upcoming_fixtures():
    if not API_TOKEN:
        print("No API Token found!")
        return pd.DataFrame()
    
    url = "https://api.football-data.org/v4/matches"
    headers = {'X-Auth-Token': API_TOKEN}
    
    # Fetch matches from today to 7 days from now
    today = datetime.now().strftime('%Y-%m-%d')
    next_week = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    params = {'status': 'SCHEDULED', 'dateFrom': today, 'dateTo': next_week}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        fixtures = []
        for match in data.get('matches', []):
            home_api = match['homeTeam']['name']
            away_api = match['awayTeam']['name']
            
            home_db = NAME_MAPPING.get(home_api, home_api)
            away_db = NAME_MAPPING.get(away_api, away_api)
            
            # Extract just the YYYY-MM-DD part
            match_date = match['utcDate'].split('T')[0]
            league = match.get('competition', {}).get('name', 'Unknown League')
            
            fixtures.append({
                'home': home_db,
                'away': away_db,
                'date': match_date,
                'league': league
            })
            
        return pd.DataFrame(fixtures)
        
    except Exception as e:
        print(f"API Error: {e}")
        return pd.DataFrame()