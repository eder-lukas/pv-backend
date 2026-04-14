"""
shared_state.py

Global variables updated by the background data-collection task and
read by the solar charging regulator and REST API.
"""

# ── Energy meters ──────────────────────────────────────────────────────────────
grid_power    = 0   # W; negative = feed-in to grid, positive = consumption from grid
emeter_power  = 0   # W; positive = PV production
battery_power = 0   # W; negative = charging battery, positive = discharging
battery_SoC   = 0   # %, integer

# ── Home battery control ───────────────────────────────────────────────────────
# SoC the home battery must reach before its charging power is counted as
# excess power available for EV charging.
home_bat_min_soc = 80

# ── Per-wallbox state (keyed by wallbox id) ────────────────────────────────────
# number_of_phases_used: how many phases the *car* currently charges on.
#   Determines how many W one Ampere corresponds to for that session.
#   Can be updated via the REST API at runtime.
# priority: 1 = highest. Lower number = served first when excess power is limited.
# charging_state: latest unified IEC 61851 code (0–6), read from hardware.
# maximum_current: last known / last set max current (mA int).
# solar_only_charging: True = regulate to solar excess; False = charge at max.
# paused: True when the wallbox was explicitly paused by the regulator.

wallbox_states: dict[int, dict] = {
    1: {
        "number_of_phases_used": 2,
        "priority": 2,
        "charging_state": 0,
        "maximum_current": 16000,
        "solar_only_charging": False,
        "paused": False,
    },
    2: {
        "number_of_phases_used": 3,
        "priority": 1,
        "charging_state": 0,
        "maximum_current": 16000,
        "solar_only_charging": False,
        "paused": False,
    },
}