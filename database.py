import os
import sqlite3
import pandas as pd

# Anchor to this file's directory so every process finds the same DB
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'football_quant_v2.db')
DB_NAME = DB_PATH

MATCH_COLUMNS = '''
    CREATE TABLE IF NOT EXISTS matches (
        match_id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_goals INTEGER,
        away_goals INTEGER,
        home_shots INTEGER,
        away_shots INTEGER,
        home_sot INTEGER,
        away_sot INTEGER,
        season TEXT,
        league TEXT,
        home_odds REAL,
        draw_odds REAL,
        away_odds REAL,
        over25_odds REAL,
        under25_odds REAL
    )
'''

BETS_TABLE = '''
    CREATE TABLE IF NOT EXISTS user_bets (
        bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        fixture TEXT NOT NULL,
        market TEXT NOT NULL,
        odds REAL NOT NULL,
        stake REAL NOT NULL,
        status TEXT DEFAULT 'Pending',
        payout REAL DEFAULT 0.0
    )
'''

# Columns added in v3 that may be missing from older databases
V3_COLUMNS = {
    "home_odds": "REAL",
    "draw_odds": "REAL",
    "away_odds": "REAL",
    "over25_odds": "REAL",
    "under25_odds": "REAL",
}

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(MATCH_COLUMNS)
    cursor.execute(BETS_TABLE)

    # Non-destructive migration: add any missing columns
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(matches)").fetchall()}
    for col, col_type in V3_COLUMNS.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE matches ADD COLUMN {col} {col_type}")

    conn.commit()
    conn.close()
    print("Database initialized (v3, non-destructive).")

def save_match_data(match_list):
    if not match_list:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    fields = ["date", "home_team", "away_team", "home_goals", "away_goals",
              "home_shots", "away_shots", "home_sot", "away_sot", "season", "league",
              "home_odds", "draw_odds", "away_odds", "over25_odds", "under25_odds"]
    placeholders = ", ".join(f":{f}" for f in fields)
    update_clause = ", ".join(f"{f} = excluded.{f}" for f in fields[2:])

    # Unique index so upsert works across repeated scrapes
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_match_unique
        ON matches (date, home_team, away_team, league)
    ''')

    cursor.executemany(f'''
    INSERT INTO matches ({", ".join(fields)})
    VALUES ({placeholders})
    ON CONFLICT(date, home_team, away_team, league) DO UPDATE SET
    {update_clause}
    WHERE matches.home_goals IS NULL OR matches.home_goals != excluded.home_goals
       OR excluded.home_odds IS NOT NULL AND (
           matches.home_odds IS NULL OR matches.home_odds != excluded.home_odds)
    ''', [{f: m.get(f) for f in fields} for m in match_list])

    conn.commit()
    conn.close()
    print(f"Saved/updated {len(match_list)} rows.")

def get_historical_data():
    conn = sqlite3.connect(DB_NAME)
    try:
        df = pd.read_sql_query("SELECT * FROM matches", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df
