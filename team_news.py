import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = "team_news_cache.json"
CACHE_EXPIRY = 7200  # 2 hours

# 1. PLAYER ARCHETYPES & BASE IMPACT (The PWIS "Role Weights")
# Impact on Attack (xG) and Defense (xGA) if this player is removed and replaced by an average backup.
ARCHETYPE_IMPACT = {
    "Key Attacker": {"xG_mult": 0.80, "xGA_mult": 1.00},  # -20% Attack
    "Key Midfielder": {"xG_mult": 0.90, "xGA_mult": 1.10}, # -10% Attack, +10% Defense weakness
    "Key Defender": {"xG_mult": 1.00, "xGA_mult": 1.20},   # +20% Defense weakness
    "Key Goalkeeper": {"xG_mult": 1.00, "xGA_mult": 1.25}, # +25% Defense weakness
    "Regular Starter": {"xG_mult": 0.95, "xGA_mult": 1.05},
    "Squad Player": {"xG_mult": 0.98, "xGA_mult": 1.02}
}

# 2. THE REPLACEMENT GAP (The PWIS "Fit" Factor)
# If a Key Attacker is out, how good is the backup? 
# 1.0 = Backup is just as good (e.g., Foden replacing Haaland). 0.5 = Backup is a youth player.
SQUAD_DEPTH = {
    "Man City": 0.90,  # Incredible squad depth
    "Arsenal": 0.80,   # Good depth, but drops off after starters
    "Liverpool": 0.75,
    "Man United": 0.60, # Poor depth, backups are significantly worse
    "Chelsea": 0.70,
    "Tottenham": 0.65,
    "Newcastle": 0.60,
    "Aston Villa": 0.55,
    "Default": 0.50    # Average team
}

# 3. KEY PLAYER DATABASE (You only need to track the top 20-30 players per league)
# Format: "Player Name": {"team": "...", "archetype": "...", "status": "injury/suspension/intl/new_signing"}
PLAYER_DATABASE = {
    "Erling Haaland": {"team": "Man City", "archetype": "Key Attacker"},
    "Kevin De Bruyne": {"team": "Man City", "archetype": "Key Midfielder"},
    "Bukayo Saka": {"team": "Arsenal", "archetype": "Key Attacker"},
    "Martin Odegaard": {"team": "Arsenal", "archetype": "Key Midfielder"},
    "William Saliba": {"team": "Arsenal", "archetype": "Key Defender"},
    "Mohamed Salah": {"team": "Liverpool", "archetype": "Key Attacker"},
    "Virgil van Dijk": {"team": "Liverpool", "archetype": "Key Defender"},
    "Bruno Fernandes": {"team": "Man United", "archetype": "Key Midfielder"},
    "Marcus Rashford": {"team": "Man United", "archetype": "Key Attacker"},
    "Son Heung-min": {"team": "Tottenham", "archetype": "Key Attacker"},
    "James Maddison": {"team": "Tottenham", "archetype": "Key Midfielder"},
    "Alexander Isak": {"team": "Newcastle", "archetype": "Key Attacker"},
    "Ollie Watkins": {"team": "Aston Villa", "archetype": "Key Attacker"},
    "Cole Palmer": {"team": "Chelsea", "archetype": "Key Attacker"},
    # Add more as needed
}

def get_team_news():
    """
    In a real app, this fetches from an API. 
    For now, we return a mock dictionary to prove the engine works.
    """
    # TODO: Integrate with API-Football injuries endpoint later
    # For now, manually update this dict to test the model
    mock_news = {
        "Man City": [
            {"player": "Erling Haaland", "status": "injury"},
            {"player": "Kevin De Bruyne", "status": "international_duty"}
        ],
        "Arsenal": [
            {"player": "Bukayo Saka", "status": "suspension"}
        ]
    }
    return mock_news

def calculate_pwis_impact(team_name, missing_players_list):
    """
    The "Broke but Brilliant" PWIS Engine.
    Calculates the net impact on xG For and xG Against based on missing players and squad depth.
    """
    if not missing_players_list:
        return 1.0, 1.0 # No impact

    squad_depth = SQUAD_DEPTH.get(team_name, SQUAD_DEPTH["Default"])
    
    total_xg_impact = 1.0
    total_xga_impact = 1.0

    for absence in missing_players_list:
        player_name = absence["player"]
        status = absence["status"]
        
        if player_name not in PLAYER_DATABASE:
            continue # Ignore unknown players
            
        player_info = PLAYER_DATABASE[player_name]
        archetype = player_info["archetype"]
        base_impact = ARCHETYPE_IMPACT[archetype]
        
        # 1. Calculate the "Replacement Gap"
        # If squad depth is 0.9, the backup is 90% as good, so the impact is only 10% of the base impact.
        # If squad depth is 0.5, the backup is terrible, so the full base impact applies.
        gap_factor = 1.0 - squad_depth 
        
        # 2. Adjust for Status
        status_mult = 1.0
        if status == "international_duty":
            status_mult = 0.5 # Fatigue factor, less severe than injury
        elif status == "new_signing":
            status_mult = -0.2 # Negative impact means it HELPS the team (boosts xG)
            
        # 3. Apply the PWIS Formula
        # Impact = Base Impact * Gap Factor * Status Multiplier
        xg_delta = (1.0 - base_impact["xG_mult"]) * gap_factor * status_mult
        xga_delta = (base_impact["xGA_mult"] - 1.0) * gap_factor * status_mult
        
        total_xg_impact -= xg_delta
        total_xga_impact += xga_delta

    # Cap the impact so one injury doesn't break the model (Max 30% swing)
    total_xg_impact = max(0.70, min(1.30, total_xg_impact))
    total_xga_impact = max(0.70, min(1.30, total_xga_impact))

    return total_xg_impact, total_xga_impact