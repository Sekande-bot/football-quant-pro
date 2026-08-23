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

SEASONS = ["2122", "2223", "2324", "2425"]

def scrape_football_data():
    all_matches = []
    
    print("Starting data download (v2 - actual goals)...")
    
    for league_name, league_code in LEAGUES.items():
        print(f"\n{'='*50}")
        print(f"Downloading {league_name}...")
        print(f"{'='*50}")
        
        for season in SEASONS:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"
            
            try:
                response = requests.get(url)
                response.raise_for_status()
                
                df = pd.read_csv(StringIO(response.text), encoding='latin1')
                df['League'] = league_name
                
                for _, row in df.iterrows():
                    home_goals = int(row['FTHG']) if pd.notna(row['FTHG']) else 0
                    away_goals = int(row['FTAG']) if pd.notna(row['FTAG']) else 0
                    
                    home_shots = int(row['HS']) if pd.notna(row['HS']) else 0
                    away_shots = int(row['AS']) if pd.notna(row['AS']) else 0
                    home_sot = int(row['HST']) if pd.notna(row['HST']) else 0
                    away_sot = int(row['AST']) if pd.notna(row['AST']) else 0
                    
                    match_entry = {
                        "date": row['Date'],
                        "home_team": row['HomeTeam'],
                        "away_team": row['AwayTeam'],
                        "home_goals": home_goals,
                        "away_goals": away_goals,
                        "home_shots": home_shots,
                        "away_shots": away_shots,
                        "home_sot": home_sot,
                        "away_sot": away_sot,
                        "season": f"{season[:2]}-{season[2:]}",
                        "league": league_name
                    }
                    all_matches.append(match_entry)
                    
                print(f"  ✓ {season}: {len(df)} matches")
                    
            except Exception as e:
                print(f"  ✗ {season}: {e}")
                
    if all_matches:
        save_match_data(all_matches)
        print(f"\n{'='*50}")
        print(f"Success! Saved {len(all_matches)} matches.")
    else:
        print("\nNo matches saved.")

if __name__ == "__main__":
    init_db()
    scrape_football_data()