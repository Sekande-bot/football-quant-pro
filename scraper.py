import pandas as pd
import requests
from io import StringIO
from database import save_match_data, init_db

LEAGUES = {
    "EPL": "E0",
    "Championship": "E1",
    "La Liga": "SP1",
    "Bundesliga": "D1",
    "Serie A": "I1",
    "Ligue 1": "F1",
    "Eredivisie": "N1",
    "Turkish Super Lig": "T1"
}

SEASONS = ["2122", "2223", "2324", "2425", "2526"]

# Candidate column names across football-data.co.uk format revisions
ODD_COLS = {
    "home_odds":   ["B365H", "BbMxH", "PSH", "MaxH"],
    "draw_odds":   ["B365D", "BbMxD", "PSD", "MaxD"],
    "away_odds":   ["B365A", "BbMxA", "PSA", "MaxA"],
    "over25_odds": ["B365>2.5", "Over 2.5", "BbMx>2.5", "Max>2.5"],
    "under25_odds": ["B365<2.5", "Under 2.5", "BbMx<2.5", "Max<2.5"],
}

def pick_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

def to_float(val):
    try:
        v = float(val)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None

def scrape_football_data():
    all_matches = []

    print("Starting data download (v3 - goals + shots + real bookmaker odds)...")

    for league_name, league_code in LEAGUES.items():
        print(f"\n{'='*50}")
        print(f"Downloading {league_name}...")
        print(f"{'='*50}")

        for season in SEASONS:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"

            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()

                df = pd.read_csv(StringIO(response.text), encoding='latin1')

                col_map = {key: pick_col(df, cands) for key, cands in ODD_COLS.items()}

                for _, row in df.iterrows():
                    def gi(col):
                        try:
                            return int(row[col]) if pd.notna(row.get(col)) else 0
                        except (ValueError, TypeError):
                            return 0

                    match_entry = {
                        "date": row['Date'],
                        "home_team": row['HomeTeam'],
                        "away_team": row['AwayTeam'],
                        "home_goals": gi('FTHG'),
                        "away_goals": gi('FTAG'),
                        "home_shots": gi('HS'),
                        "away_shots": gi('AS'),
                        "home_sot": gi('HST'),
                        "away_sot": gi('AST'),
                        "season": f"{season[:2]}-{season[2:]}",
                        "league": league_name,
                    }
                    for key, col in col_map.items():
                        match_entry[key] = to_float(row.get(col)) if col else None

                    all_matches.append(match_entry)

                print(f"  âœ“ {season}: {len(df)} matches")

            except Exception as e:
                print(f"  âœ— {season}: {e}")

    if all_matches:
        save_match_data(all_matches)
        print(f"\n{'='*50}")
        print(f"Success! Saved {len(all_matches)} matches.")
    else:
        print("\nNo matches saved.")

if __name__ == "__main__":
    init_db()
    scrape_football_data()

