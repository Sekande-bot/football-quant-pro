import requests
import pandas as pd
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# Cloud platforms inject env vars; locally we read .env
API_TOKEN = os.getenv("FOOTBALL_DATA_API_KEY")

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

def _get_mls_fixtures():
    """MLS isn't covered by football-data.org; use fixturedownload.com feed."""
    try:
        year = datetime.now().year
        r = requests.get(f"https://fixturedownload.com/feed/json/mls-{year}",
                         timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        out = []
        for g in r.json():
            if g.get("HomeTeamScore") is not None:
                continue  # already played
            out.append({
                'home': g["HomeTeam"].strip(),
                'away': g["AwayTeam"].strip(),
                'date': g["DateUtc"][:10],
                'league': "MLS",
            })
        return out
    except Exception:
        return []


def get_upcoming_fixtures():
    if not API_TOKEN:
        print("No API Token found!")
        return pd.DataFrame()
    
    url = "https://api.football-data.org/v4/matches"
    headers = {'X-Auth-Token': API_TOKEN}
    
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

            fixtures.append({
                'home': NAME_MAPPING.get(home_api, home_api),
                'away': NAME_MAPPING.get(away_api, away_api),
                'date': match['utcDate'].split('T')[0],
                'league': match.get('competition', {}).get('name', 'Unknown League')
            })

        fixtures += _get_mls_fixtures()

        return pd.DataFrame(fixtures)

    except Exception as e:
        print(f"API Error: {e}")
        return pd.DataFrame()