"""
MLS backfill from fixturedownload.com (free JSON feeds).
Seasons 2023-present, ~500 matches each. No odds/shots available at this
source - goals only, which is all the model needs.
"""
import requests
from database import save_match_data

FEED = "https://fixturedownload.com/feed/json/mls-{year}"
SEASONS = [2023, 2024, 2025, 2026]
LEAGUE = "MLS"


def backfill_mls():
    rows = []
    for year in SEASONS:
        try:
            r = requests.get(FEED.format(year=year), timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                print(f"  MLS {year}: HTTP {r.status_code}")
                continue
            games = r.json()
            n = 0
            for g in games:
                hg, ag = g.get("HomeTeamScore"), g.get("AwayTeamScore")
                if hg is None or ag is None:
                    continue
                date = (g.get("DateUtc") or "")[:10]
                if not date:
                    continue
                rows.append({
                    "date": date,
                    "home_team": g["HomeTeam"].strip(),
                    "away_team": g["AwayTeam"].strip(),
                    "home_goals": int(hg),
                    "away_goals": int(ag),
                    "season": f"{str(year)[-2:]}-{str(year + 1)[-2:]}",
                    "league": LEAGUE,
                })
                n += 1
            print(f"  MLS {year}: {n} finished matches")
        except Exception as e:
            print(f"  MLS {year}: {e}")

    if rows:
        save_match_data(rows)
        print(f"Backfilled {len(rows)} MLS matches.")


if __name__ == "__main__":
    backfill_mls()
