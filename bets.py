import sqlite3
import pandas as pd
from database import DB_NAME

def ensure_bets_table_exists():
    """Create the user_bets table if it doesn't exist"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
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

def add_bet(date, fixture, market, odds, stake):
    ensure_bets_table_exists()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_bets (date, fixture, market, odds, stake)
        VALUES (?, ?, ?, ?, ?)
    ''', (date, fixture, market, odds, stake))
    conn.commit()
    conn.close()

def get_my_bets():
    ensure_bets_table_exists()
    conn = sqlite3.connect(DB_NAME)
    try:
        df = pd.read_sql_query("SELECT * FROM user_bets ORDER BY date DESC", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

def update_bet_status(bet_id, status):
    ensure_bets_table_exists()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if status == "Won":
        cursor.execute("SELECT stake, odds FROM user_bets WHERE bet_id = ?", (bet_id,))
        row = cursor.fetchone()
        if row:
            payout = row[0] * row[1]
            cursor.execute("UPDATE user_bets SET status = ?, payout = ? WHERE bet_id = ?", (status, payout, bet_id))
    else:
        cursor.execute("UPDATE user_bets SET status = ? WHERE bet_id = ?", (status, bet_id))
        
    conn.commit()
    conn.close()