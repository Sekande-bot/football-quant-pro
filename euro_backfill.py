"""
Backfill UEFA Champions League & Europa League results from the
football-data.org API (free tier covers both). Team names are normalized
to match the domestic-league names already in our database, so European
matches reinforce the same team ratings.
"""
import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv

from database import save_match_data, get_historical_data
from model_engine import _normalize

load_dotenv()
API_TOKEN = os.getenv("FOOTBALL_DATA_API_KEY")

COMPETITIONS = {
    "CL": "UEFA Champions League",
    "BSA": "Brazil Serie A",
    "CLI": "Copa Libertadores",
}

SEASONS = ["2022", "2023", "2024", "2025"]

NAME_OVERRIDES = {
    # API name -> football-data.co.uk DB name where fuzzy match struggles
    "club atlético de madrid": "Atletico Madrid",
    "atletico madrid": "Atletico Madrid",
    "paris saint-germain": "Paris SG",
    "paris saint germain": "Paris SG",
    "manchester united fc": "Man United",
    "manchester city fc": "Man City",
    "newcastle united fc": "Newcastle",
    "wolverhampton wanderers fc": "Wolves",
    "tottenham hotspur fc": "Tottenham",
    "west ham united fc": "West Ham",
    "nottingham forest fc": "Nott'm Forest",
    "sheffield united fc": "Sheffield Utd",
    "athletic club": "Ath Bilbao",
    "real sociedad de fútbol": "Sociedad",
    "real sociedad": "Sociedad",
    "celta vigo": "Celta",
    "rc celta de vigo": "Celta",
    "sporting cp": "Sporting CP",
    "fc porto": "Porto",
    "sl benfica": "Benfica",
    "sc braga": "Braga",
}


class NameMapper:
    def __init__(self, known_teams):
        self.by_norm = {}
        for t in known_teams:
            self.by_norm.setdefault(_normalize(t), t)
        self.cache = {}

    def resolve(self, api_name):
        if api_name in self.cache:
            return self.cache[api_name]
        low = api_name.lower().strip()
        if low in NAME_OVERRIDES:
            out = NAME_OVERRIDES[low]
        else:
            norm = _normalize(api_name)
            out = self.by_norm.get(norm)          # exact normalized hit
            if out is None:
                # try containment: "borussia dortmund" vs "Dortmund"
                hits = [orig for n, orig in self.by_norm.items()
                        if n and (n in norm or norm in n)]
                out = hits[0] if len(hits) == 1 else None
        self.cache[api_name] = out
        return out


def fetch_competition(comp_code, season):
    url = f"https://api.football-data.org/v4/competitions/{comp_code}/matches"
    r = requests.get(url, headers={"X-Auth-Token": API_TOKEN},
                     params={"season": season}, timeout=30)
    if r.status_code != 200:
        print(f"  {comp_code} {season}: HTTP {r.status_code} - {r.text[:120]}")
        return []
    return r.json().get("matches", [])


def backfill():
    if not API_TOKEN:
        print("No FOOTBALL_DATA_API_KEY - cannot backfill Europe.")
        return

    df = get_historical_data()
    known = set(df['home_team']) | set(df['away_team']) if not df.empty else set()
    mapper = NameMapper(known)

    rows = []
    for comp_code, comp_name in COMPETITIONS.items():
        for season in SEASONS:
            matches = fetch_competition(comp_code, season)
            time.sleep(7)   # free tier: 10 req/min
            n = 0
            for m in matches:
                if m.get('status') != 'FINISHED':
                    continue
                score = m.get('score', {}).get('fullTime', {})
                hg, ag = score.get('home'), score.get('away')
                if hg is None or ag is None:
                    continue
                home = mapper.resolve(m['homeTeam']['name'])
                away = mapper.resolve(m['awayTeam']['name'])
                # For brand-new competitions (Brazil etc.) there is no existing
                # DB name to map to - keep the API name so the league exists.
                home = home or m['homeTeam']['name'].strip()
                away = away or m['awayTeam']['name'].strip()
                rows.append({
                    "date": m['utcDate'][:10],
                    "home_team": home,
                    "away_team": away,
                    "home_goals": hg,
                    "away_goals": ag,
                    "season": f"{season[-2:]}-{str(int(season)+1)[-2:]}",
                    "league": comp_name,
                })
                n += 1
            print(f"  {comp_name} {season}: {n} finished matches")

    if rows:
        save_match_data(rows)
        print(f"Backfilled {len(rows)} European matches.")


if __name__ == "__main__":
    backfill()
