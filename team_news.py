"""
Injury / team-news integration.

IMPORTANT: The previous version hardcoded fake injuries (Haaland/KDB out, Saka out)
which silently distorted every Man City / Arsenal prediction on every run.
That mock has been removed. The model now assumes full squads unless a real
feed is connected.

To enable real news later: implement get_team_news() to return a dict of the form:
    { "Team Name": [ {"player": "...", "status": "injury|suspension|international_duty"}, ... ] }
The calculate_pwis_impact() machinery below will then apply it.
"""

def get_team_news():
    # No real data source connected yet - assume full squads.
    return {}

# --- PWIS machinery kept for when a real feed is added ---

ARCHETYPE_IMPACT = {
    "Key Attacker": {"xG_mult": 0.80, "xGA_mult": 1.00},
    "Key Midfielder": {"xG_mult": 0.90, "xGA_mult": 1.10},
    "Key Defender": {"xG_mult": 1.00, "xGA_mult": 1.20},
    "Key Goalkeeper": {"xG_mult": 1.00, "xGA_mult": 1.25},
    "Regular Starter": {"xG_mult": 0.95, "xGA_mult": 1.05},
    "Squad Player": {"xG_mult": 0.98, "xGA_mult": 1.02}
}

PLAYER_DATABASE = {}

def register_players(players: dict):
    """players: {"Player Name": {"team": ..., "archetype": ...}}"""
    PLAYER_DATABASE.update(players)

def calculate_pwis_impact(team_name, missing_players_list):
    if not missing_players_list:
        return 1.0, 1.0

    total_xg_impact = 1.0
    total_xga_impact = 1.0

    for absence in missing_players_list:
        player_name = absence.get("player")
        status = absence.get("status", "injury")

        if player_name not in PLAYER_DATABASE:
            continue

        archetype = PLAYER_DATABASE[player_name]["archetype"]
        base_impact = ARCHETYPE_IMPACT[archetype]

        status_mult = 1.0
        if status == "international_duty":
            status_mult = 0.5
        elif status == "doubt":
            status_mult = 0.5

        xg_delta = (1.0 - base_impact["xG_mult"]) * status_mult
        xga_delta = (base_impact["xGA_mult"] - 1.0) * status_mult

        total_xg_impact -= xg_delta
        total_xga_impact += xga_delta

    total_xg_impact = max(0.70, min(1.30, total_xg_impact))
    total_xga_impact = max(0.70, min(1.30, total_xga_impact))

    return total_xg_impact, total_xga_impact
