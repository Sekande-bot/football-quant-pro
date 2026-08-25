# Quant Pro Engine v3

Dixon-Coles football prediction engine with a Streamlit dashboard.

## The model

- Per-league **Dixon-Coles bivariate Poisson** fitted by maximum likelihood
  (analytic gradients) on 5 seasons of results per league (8 leagues, ~15k matches).
- **Exponential time-decay** weighting (half-life 450 days, tuned by sweep).
- **L2 shrinkage** of team ratings toward league average (protects small samples).
- Home advantage and the low-score correlation rho are **fitted, not hardcoded**.
- Shot-based finishing-regression blend (teams finishing hot vs their shot volume get pulled back).
- Probabilities shown in the app are **blended with de-margined bookmaker odds**
  (65% model / 35% market for 1X2 and O/U 2.5) when live odds are available.
  EV flags always use **pure** model probabilities.
- Backtest reports Brier score / log-loss **vs the bookmaker benchmark** and
  simulated ROI against real historical Bet365 prices.

Backtest summary (~2,970 test matches, all leagues): LogLoss 1.008 vs bookmaker 0.989,
50% 1X2 top-pick accuracy.

## Run locally

```bash
pip install -r requirements.txt
python server.py          # http://localhost:5000
```

On first boot the app downloads historical data automatically (~2 min).

## Deploy (Render â€” free tier)

1. Push this repo to GitHub (already at `Sekande-bot/football-quant-pro`).
2. On https://render.com -> **New +** -> **Blueprint** -> select the repo.
   Render reads `render.yaml` and creates the service.
3. When prompted, set env vars:
   - `FOOTBALL_DATA_API_KEY` â€” your football-data.org key (required for fixtures)
   - `SMART_API_KEY` â€” optional, enables live odds blending + EV scanner
4. Deploy. First boot builds the database automatically; free tier sleeps after
   15 min idle, then wakes on first request (~30 s cold start).

Alternative: any host that runs `gunicorn server:app` works (Railway, Fly.io, VPS).

## Files

| File | Role |
|---|---|
| `model_engine.py` | Dixon-Coles fitting, market probabilities, blending, EV |
| `backtest.py` | Probability quality + ROI evaluation |
| `scraper.py` | football-data.co.uk results + real bookmaker odds |
| `fixtures.py` | Upcoming fixtures via football-data.org API |
| `odds_api.py` | Live odds fetch + caching |
| `database.py` | SQLite storage (non-destructive migrations) |
| `team_news.py` | Injury/suspension hooks (inactive until a real feed is connected) |

