import sqlite3
import pandas as pd

DB_NAME = 'football_quant_v2.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('DROP TABLE IF EXISTS matches')
    cursor.execute('DROP TABLE IF EXISTS teams')
    cursor.execute('DROP TABLE IF EXISTS user_bets')
    
    cursor.execute('''
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
        league TEXT
    )
    ''')
    
    cursor.execute('''
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
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized (v2).")

def save_match_data(match_list):
    if not match_list:
        return
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.executemany('''
    INSERT OR IGNORE INTO matches 
    (date, home_team, away_team, home_goals, away_goals, home_shots, away_shots, home_sot, away_sot, season, league)
    VALUES (:date, :home_team, :away_team, :home_goals, :away_goals, :home_shots, :away_shots, :home_sot, :away_sot, :season, :league)
    ''', match_list)
    
    conn.commit()
    conn.close()
    print(f"Saved {len(match_list)} matches.")

def get_historical_data():
    conn = sqlite3.connect(DB_NAME)
    try:
        df = pd.read_sql_query("SELECT * FROM matches", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df