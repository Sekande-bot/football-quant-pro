import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from database import init_db, get_historical_data
from scraper import scrape_football_data
from fixtures import get_upcoming_fixtures
from model_engine import predict_next_matches, best_ev_bets
from backtest import run_backtest
from odds_api import get_live_odds
from bets import add_bet, get_my_bets, update_bet_status

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Quant Pro Engine", 
    layout="wide", 
    page_icon="🤖",
    initial_sidebar_state="expanded"
)

# --- GLOBAL CSS (Sekande Cool - Institutional Dark Theme) ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@300;400;700;900&display=swap');

    .stApp { 
        background: radial-gradient(circle at 50% 0%, #0a0f18, #05080c 70%); 
        color: #F8FAFC; 
        font-family: 'Inter', sans-serif;
    }
    
    h1, h2, h3, h4, h5 { 
        color: #FFFFFF !important; 
        font-weight: 900 !important; 
        letter-spacing: -0.5px;
    }
    
    [data-testid="stSidebar"] {
        background-color: rgba(5, 8, 12, 0.85) !important;
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-right: 1px solid rgba(255,255,255,0.05);
    }
    
    /* Sleek Cards */
    .match-card { 
        background: rgba(255, 255, 255, 0.02);
        backdrop-filter: blur(10px);
        padding: 20px; 
        border-radius: 12px; 
        margin-bottom: 12px; 
        border: 1px solid rgba(255,255,255,0.05);
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        transition: all 0.3s ease;
    }
    .match-card:hover { transform: translateY(-2px); background: rgba(255, 255, 255, 0.04); }
    
    /* Confidence Borders */
    .card-low { border-left: 4px solid #10B981; }  /* Green */
    .card-med { border-left: 4px solid #F59E0B; }  /* Amber */
    .card-high { border-left: 4px solid #EF4444; } /* Red */
    
    .team-names { font-size: 1.25em; font-weight: 900; color: #FFFFFF; font-family: 'JetBrains Mono', monospace;}
    
    .odds-box { 
        background: rgba(16, 185, 129, 0.1); 
        color: #10B981; 
        border: 1px solid #10B981;
        padding: 6px 14px; 
        border-radius: 6px; 
        font-weight: 700; 
        display: inline-block;
        font-family: 'JetBrains Mono', monospace;
    }
    
    .risk-badge { 
        padding: 4px 10px; 
        border-radius: 4px; 
        font-size: 0.75em; 
        font-weight: 800; 
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    .badge-low { background: rgba(16, 185, 129, 0.2); color: #10B981; }
    .badge-med { background: rgba(245, 158, 11, 0.2); color: #F59E0B; }
    .badge-high { background: rgba(239, 68, 68, 0.2); color: #EF4444; }
    
    .stButton>button { 
        background: rgba(255, 255, 255, 0.05) !important; 
        color: #FFFFFF !important; 
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        font-weight: 700 !important;
        border-radius: 6px !important;
        transition: 0.3s;
    }
    .stButton>button:hover {
        background: #FFFFFF !important;
        color: #05080c !important;
    }
    
    /* Custom Developer Signature */
    .dev-signature {
        margin-top: auto;
        padding-top: 40px;
        padding-bottom: 20px;
        text-align: center;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        color: rgba(255, 255, 255, 0.2);
        letter-spacing: 2px;
        text-transform: uppercase;
    }
</style>
""", unsafe_allow_html=True)

st.title("⚽ Quant Pro Engine")
st.markdown("<p style='color: #64748B; font-family: JetBrains Mono; font-size: 0.9em; margin-top: -15px;'>// ALGORITHMIC FOOTBALL FORECASTING TERMINAL</p>", unsafe_allow_html=True)

# --- SIDEBAR CONTROL PANEL ---
st.sidebar.markdown("### ⚙️ SYSTEM CONTROLS")
if st.sidebar.button("🔄 Sync Market Data"):
    with st.spinner("Compiling historical and live telemetry..."):
        init_db()
        scrape_football_data()
    st.sidebar.success("Telemetry Synced!")

# --- LOAD DATA ---
df = get_historical_data()
if df.empty:
    st.warning("Data matrices empty. Initialize 'Sync Market Data' in the control panel.")
    st.stop()

# --- SESSION STATE (Portfolio, not Bet Slip) ---
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = []

def add_to_portfolio(fixture, pick, odds, risk):
    sid = f"{fixture}_{pick}"
    if not any(item['id'] == sid for item in st.session_state.portfolio):
        st.session_state.portfolio.append({'id': sid, 'fixture': fixture, 'pick': pick, 'odds': odds, 'risk': risk})

# --- GLOBAL FILTERS ---
st.markdown("<div style='background: rgba(255,255,255,0.02); padding: 15px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); margin-bottom: 20px;'>", unsafe_allow_html=True)
col_f1, col_f2 = st.columns(2)
with col_f1:
    date_choice = st.selectbox("📅 Projection Window", ["Today", "Tomorrow", "Next 48 Hours"])
with col_f2:
    available_leagues = ["Global Dataset"] + sorted(df['league'].unique().tolist())
    league_choice = st.selectbox("🏆 Filter Matrix", available_leagues)
st.markdown("</div>", unsafe_allow_html=True)

def get_fixtures_filtered():
    fixtures = get_upcoming_fixtures()
    if fixtures.empty: return pd.DataFrame()
    
    today = datetime.now().date()
    fixtures['date'] = pd.to_datetime(fixtures['date']).dt.date
    
    if date_choice == "Today": target = today
    elif date_choice == "Tomorrow": target = today + timedelta(days=1)
    else: target = today + timedelta(days=2)
    
    filtered = fixtures[fixtures['date'] <= target]
    if league_choice != "Global Dataset" and 'league' in filtered.columns:
        filtered = filtered[filtered['league'].str.contains(league_choice, case=False, na=False)]
    
    return filtered

# --- RENDER PORTFOLIO (Formerly Bet Slip) ---
def render_portfolio():
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"### 📋 MODEL PORTFOLIO ({len(st.session_state.portfolio)})")
    
    if not st.session_state.portfolio:
        st.sidebar.info("Awaiting model selections. Queue predictions from the dashboard.")
    else:
        total_multiplier = 1.0
        for item in st.session_state.portfolio:
            total_multiplier *= item['odds']
            st.sidebar.markdown(f"""
            <div style="background:rgba(255,255,255,0.03); padding:10px; border-radius:6px; margin-bottom:8px; border-left:3px solid #10B981;">
                <div style="font-weight:700; color:#fff; font-size: 0.9em;">{item['fixture']}</div>
                <div style="display:flex; justify-content:space-between; margin-top:5px;">
                    <span style="font-size:0.8em; color:#94A3B8;">Target: {item['pick']}</span>
                    <span style="color:#10B981; font-family:'JetBrains Mono'; font-size:0.9em;">x{item['odds']}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        st.sidebar.markdown(f"<div style='margin-top: 15px; font-family: JetBrains Mono; color: #94A3B8;'>COMPOUND YIELD: <span style='color: #FFFFFF; font-weight: bold; font-size: 1.2em;'>x{total_multiplier:.2f}</span></div>", unsafe_allow_html=True)
        
        with st.sidebar.form("portfolio_form"):
            sim_capital = st.number_input("Simulated Allocation (₦)", min_value=100, value=1000, step=500)
            proj_return = sim_capital * total_multiplier
            st.markdown(f"<div style='margin-bottom:15px;'>Proj. Value: <strong>₦{proj_return:,.0f}</strong></div>", unsafe_allow_html=True)
            
            if st.form_submit_button("🔒 LOCK PREDICTIONS", use_container_width=True):
                cap_per_model = sim_capital / len(st.session_state.portfolio)
                for item in st.session_state.portfolio:
                    add_bet(datetime.now().strftime('%Y-%m-%d'), item['fixture'], item['pick'], item['odds'], cap_per_model)
                st.session_state.portfolio = []
                st.success("Telemetry Logged. View in Backtest Ledger.")
                st.rerun()
            
            if st.form_submit_button("🗑️ PURGE QUEUE", use_container_width=True):
                st.session_state.portfolio = []
                st.rerun()

    # The Name Engraving
    st.sidebar.markdown("<div class='dev-signature'>Engineered by<br>Oyediran Sekande Crown</div>", unsafe_allow_html=True)

# --- RENDER MATCH CARD ---
def render_match_card(row, prefix):
    prob = float(row['Prob %'])
    odds = round((100 / prob) * 1.05, 2)
    sid = f"{prefix}_{row['Fixture']}_{row['Top Pick']}"
    added = any(x['id'] == sid for x in st.session_state.portfolio)
    
    card_class = "card-low" if "LOW" in row['Risk'] else ("card-med" if "MED" in row['Risk'] else "card-high")
    badge_class = "badge-low" if "LOW" in row['Risk'] else ("badge-med" if "MED" in row['Risk'] else "badge-high")

    ev_html = ""
    best_ev = row.get('Best EV')
    if isinstance(best_ev, dict) and best_ev:
        ev_html = (f"<div style='margin-top:8px;'><span class='odds-box' style='background:rgba(16,185,129,0.18);'>"
                   f"+EV: {best_ev['market']} @ x{best_ev['odds']} ({best_ev['ev_pct']:+.1f}%)</span></div>")

    st.markdown(f"""
    <div class="match-card {card_class}">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <div style="color:#64748B; font-size:0.75em; text-transform:uppercase; letter-spacing:1px;">{row.get('League', 'Data')} | {row['Date']}</div>
                <div class="team-names" style="margin-top: 4px; margin-bottom: 8px;">{row['Fixture']}</div>
                <div style="color:#94A3B8; font-size:0.9em;">
                    Engine Output: <span style="color:#10B981; font-weight:700;">{row['Top Pick']}</span> | Exp Goals: {row['Exp Goals']}
                </div>
                <div style="color:#64748B; font-size:0.8em; margin-top:4px;">Top scores: {row.get('Correct Scores', '')}</div>
            </div>
            <div style="text-align:right;">
                <div class="odds-box">Model {prob}%</div><br>
                <div style="margin-top:8px;"><span class="risk-badge {badge_class}">Risk: {row['Risk']}</span></div>
                {ev_html}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if added:
            st.button("✓ Queued", key=f"added_{sid}", disabled=True, use_container_width=True)
        else:
            if st.button("➕ Queue Pick", key=f"add_{sid}", use_container_width=True):
                add_to_portfolio(row['Fixture'], row['Top Pick'], odds, row['Risk'])
                st.rerun()
    
    with col2:
        with st.expander("🔬 Expand Market Probability Matrix", expanded=False):
            all_markets = row['All_Markets']
            sorted_markets = sorted(all_markets.items(), key=lambda x: -x[1])
            
            mdf = pd.DataFrame([{'Variable': k, 'AI Confidence': f"{v}%"} for k, v in sorted_markets])
            st.dataframe(mdf, hide_index=True, use_container_width=True)
            
            market_names = [k for k, v in sorted_markets]
            chosen = st.selectbox("Override Primary Output:", market_names, key=f"sel_{sid}")
            chosen_prob = all_markets[chosen]
            chosen_odds = round((100 / chosen_prob) * 1.05, 2)
            
            if st.button(f"➕ Queue Alternative [{chosen} @ x{chosen_odds}]", key=f"addm_{sid}"):
                add_to_portfolio(row['Fixture'], chosen, chosen_odds, row['Risk'])
                st.rerun()

# --- INITIALIZE SIDEBAR PORTFOLIO ---
render_portfolio()

# --- TAB ROUTING ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🏠 Top Projections", "🎯 AI Portfolio Builder", "📋 Full Slate Matrix", "📡 Edge Scanner", "📈 Backtest Ledger"])

# ==========================================
# TAB 1: TOP PROJECTIONS
# ==========================================
with tab1:
    st.markdown("### 🌟 Highest Conviction Outputs")
    st.markdown("<p style='color:#94A3B8;'>The algorithm's most statistically significant predictions.</p>", unsafe_allow_html=True)
    
    if st.button("⚡ EXECUTE NEURAL SCAN", use_container_width=True, key="gen_rec"):
        with st.spinner("Processing Poisson distributions and historic datasets..."):
            fixtures = get_fixtures_filtered()
            if fixtures.empty: st.error("No valid telemetry found for parameters.")
            else:
                pred_df = predict_next_matches(df, fixtures)
                st.session_state.rec_picks = {
                    'low': pred_df[pred_df['Risk'].str.contains('LOW')].sort_values('Prob %', ascending=False),
                    'med': pred_df[pred_df['Risk'].str.contains('MED')].sort_values('Prob %', ascending=False),
                    'high': pred_df[pred_df['Risk'].str.contains('HIGH')].sort_values('Prob %', ascending=False)
                }
    
    if 'rec_picks' in st.session_state:
        picks = st.session_state.rec_picks
        for level, title, color in [('low', '🛡️ Absolute Bankers (Low Variance)', '#10B981'), ('med', '⚖️ Optimal Value (Median Variance)', '#F59E0B'), ('high', '⚠️ Aggressive Projections (High Variance)', '#EF4444')]:
            data = picks[level]
            st.markdown(f"<h4 style='color:{color}; margin-top:20px;'>{title}</h4>", unsafe_allow_html=True)
            if data.empty:
                st.write("*No statistical edge detected in this tier.*")
            else:
                for idx, row in data.iterrows():
                    render_match_card(row, f"rec_{level}_{idx}")

# ==========================================
# TAB 2: PORTFOLIO BUILDER
# ==========================================
with tab2:
    st.markdown("### 🎯 Automated Portfolio Structuring")
    st.markdown("<p style='color:#94A3B8;'>Define risk constraints and let the engine construct mathematically optimal accumulations.</p>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        num_games = st.slider("Target Number of Variables", 1, 8, 3)
        risk_level = st.selectbox("Variance Tolerance", ["Low Variance Only", "Low + Median", "Unrestricted Model"])
    with col2:
        target_odds = st.slider("Target Compound Yield", 1.5, 30.0, 5.0)
        min_conf = st.slider("Minimum Model Confidence %", 50, 90, 60)
    
    if st.button("🔨 GENERATE OPTIMIZED STRUCTURE", use_container_width=True):
        with st.spinner("Optimizing combination for highest Expected Value..."):
            fixtures = get_fixtures_filtered()
            if fixtures.empty: st.error("No valid telemetry.")
            else:
                pred_df = predict_next_matches(df, fixtures)
                if risk_level == "Low Variance Only": filtered = pred_df[pred_df['Risk'].str.contains('LOW')]
                elif risk_level == "Low + Median": filtered = pred_df[pred_df['Risk'].str.contains('LOW|MED')]
                else: filtered = pred_df.copy()
                
                if not filtered.empty:
                    odds_per_game = target_odds ** (1.0 / num_games)
                    required_prob = (1.0 / odds_per_game) * 100
                    filtered = filtered[filtered['Prob %'].astype(float) >= max(required_prob, min_conf)]
                    filtered = filtered.sort_values('Prob %', ascending=False).head(num_games)
                    
                    if not filtered.empty:
                        st.success(f"✅ Generated {len(filtered)}-variable structure.")
                        slip_items = []
                        
                        for idx, row in filtered.iterrows():
                            prob = float(row['Prob %'].replace('%', ''))
                            odds = round((100 / prob) * 1.05, 2)
                            slip_items.append({'Fixture': row['Fixture'], 'Pick': row['Top Pick'], 'Odds': odds, 'Risk': row['Risk']})
                            
                            # Check if already queued
                            sid = f"build_{idx}_{row['Fixture']}_{row['Top Pick']}"
                            already_queued = any(x['id'] == f"{row['Fixture']}_{row['Top Pick']}" for x in st.session_state.portfolio)
                            
                            # Render card with status indicator
                            render_match_card(row, f"build_{idx}")
                            
                            # Show status below card
                            if already_queued:
                                st.info("✓ This projection is already queued in your portfolio")
                            else:
                                st.caption("Not yet queued. Use the button above or 'INJECT ALL' below.")
                        
                        if st.button("💾 INJECT ALL INTO QUEUE", use_container_width=True):
                            for item in slip_items: add_to_portfolio(item['Fixture'], item['Pick'], item['Odds'], item['Risk'])
                            st.rerun()
                    else: st.warning("Criteria too strict. Adjust constraints.")
                else: st.warning("No matches fit the variance profile.")

# ==========================================
# TAB 3: FULL SLATE MATRIX
# ==========================================
with tab3:
    st.markdown("### 📋 Complete Analytical Matrix")
    
    if st.button("🔍 RENDER FULL SLATE", use_container_width=True):
        with st.spinner("Processing entire database..."):
            fixtures = get_fixtures_filtered()
            if fixtures.empty: st.error("No fixtures in timeframe.")
            else: st.session_state.all_matches = predict_next_matches(df, fixtures)
    
    if 'all_matches' in st.session_state:
        st.info(f"Rendering {len(st.session_state.all_matches)} fixtures.")
        for idx, row in st.session_state.all_matches.iterrows():
            render_match_card(row, f"all_{idx}")

# ==========================================
# TAB 4: EDGE SCANNER
# ==========================================
with tab4:
    st.markdown("### 📡 Market Edge & Inefficiency Scanner")
    st.markdown("<p style='color:#94A3B8;'>Compares PURE model probabilities to real bookmaker prices. Requires a working odds source — no odds, no fake edges.</p>", unsafe_allow_html=True)
    
    if st.button("🔍 INITIATE DEEP SCAN", use_container_width=True):
        with st.spinner("Scraping and comparing global market data..."):
            fixtures = get_fixtures_filtered()
            if fixtures.empty: st.error("No matches available.")
            else:
                live = get_live_odds()
                if not live:
                    st.error("No real bookmaker odds available (odds source not configured or returned nothing). EV scan aborted - refusing to simulate fake prices.")
                else:
                    pred_df = predict_next_matches(df, fixtures, live_odds=live)
                    value_bets = []
                    for idx, row in pred_df.iterrows():
                        for b in best_ev_bets(row['Pure_Markets'], live.get(row['Fixture'], {}), min_edge=3.0):
                            value_bets.append({
                                'Fixture': row['Fixture'],
                                'Market': b['market'],
                                'Model Prob': f"{b['prob']}%",
                                'Bookie Odds': f"x{b['odds']}",
                                '+EV Edge': f"+{b['ev_pct']}%"
                            })
                    
                    if value_bets:
                        vdf = pd.DataFrame(value_bets).sort_values('+EV Edge', ascending=False)
                        st.success(f"Discovered {len(value_bets)} market inefficiencies.")
                        st.dataframe(vdf, hide_index=True, use_container_width=True)
                    else: st.info("No qualifying edge found vs available prices.")

# ==========================================
# TAB 5: BACKTEST LEDGER (WITH MODEL VALIDATION)
# ==========================================
with tab5:
    st.markdown("### 📊 Model Performance Validation")
    
    # Backtest Section
    st.markdown("#### Historical Model Accuracy Test")
    st.markdown("<p style='color:#94A3B8;'>Evaluates probability quality (Brier/log-loss) vs the bookmaker and simulates +EV betting ROI on real historical prices.</p>", unsafe_allow_html=True)
    
    if st.button("🧪 Run Historical Backtest", use_container_width=True):
        with st.spinner("Fitting models chronologically and scoring test matches..."):
            backtest_results = run_backtest(df)
            if not backtest_results.empty:
                st.session_state.backtest_results = backtest_results
                st.rerun()
    
    if 'backtest_results' in st.session_state:
        bt_df = st.session_state.backtest_results
        
        brier = bt_df['Brier'].mean()
        ll = bt_df['LogLoss'].mean()
        bm = bt_df['BM_LogLoss'].dropna()
        bm_ll = bm.mean() if len(bm) else None
        acc = bt_df['Correct?'].mean() * 100
        all_bets = [b for recs in bt_df['bets'] for b in recs]
        roi = (sum(b['return'] for b in all_bets) / len(all_bets) * 100) if all_bets else 0.0
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Test Matches", len(bt_df))
        c2.metric("Brier Score", f"{brier:.4f}")
        c3.metric("Model LogLoss", f"{ll:.4f}")
        c4.metric("1X2 Accuracy", f"{acc:.1f}%")
        
        if bm_ll is not None:
            verdict = "✅ BEATS the market" if ll < bm_ll else f"⚠️ {abs(ll - bm_ll):.3f} behind the market"
            st.markdown(f"**Benchmark:** Bookmaker log-loss on same matches: **{bm_ll:.4f}** — model {verdict}")
        if all_bets:
            st.markdown(f"**Betting simulation:** {len(all_bets)} bets at ≥5pt edge → ROI **{roi:+.2f}%**")
        
        detail = bt_df[['Date', 'League', 'Fixture', 'Score', 'Top Pick', 'Correct?']].copy()
        detail = detail.sort_values('Date', ascending=False)
        st.markdown("#### Detailed Results")
        st.dataframe(detail, hide_index=True, use_container_width=True)
    
    st.divider()
    
    # Bet Tracker Section
    st.markdown("#### Live Portfolio Tracker")
    bets_df = get_my_bets()
    
    if not bets_df.empty:
        total_stake = bets_df['stake'].sum()
        total_win = bets_df[bets_df['status'] == 'Won']['payout'].sum()
        profit = total_win - total_stake
        roi = (profit / total_stake * 100) if total_stake > 0 else 0
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Simulated Capital Deployed", f"₦{total_stake:,.0f}")
        c2.metric("Net Yield", f"₦{profit:,.0f}")
        c3.metric("System ROI", f"{roi:.1f}%")
        st.divider()
        
        for idx, row in bets_df.iterrows():
            cols = st.columns([3, 1, 1, 1, 2])
            cols[0].write(f"**{row['fixture']}** ({row['market']})")
            cols[1].write(f"Yield: x{row['odds']}")
            cols[2].write(f"Cap: ₦{row['stake']:,.0f}")
            
            status_color = "#10B981" if row['status'] == 'Won' else ("#EF4444" if row['status'] == 'Lost' else "#F59E0B")
            cols[3].markdown(f"<span style='color:{status_color}; font-weight:bold;'>{row['status']}</span>", unsafe_allow_html=True)
            
            if row['status'] == 'Pending':
                if cols[4].button("Verify Win", key=f"w{row['bet_id']}", use_container_width=True):
                    update_bet_status(row['bet_id'], "Won")
                    st.rerun()
                if cols[4].button("Log Miss", key=f"l{row['bet_id']}", use_container_width=True):
                    update_bet_status(row['bet_id'], "Lost")
                    st.rerun()
    else:
        st.info("No active portfolio positions. Queue predictions from the dashboard.")