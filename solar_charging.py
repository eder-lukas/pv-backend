"""
solar_charging.py

Solar-excess EV-charging regulator.

Key design decisions
────────────────────
* Each wallbox is represented by a WallboxBase subclass that knows its own
  hardware protocol (Juice only supports A, KEBA supports mA precision).
* Excess power is recalculated between wallboxes with a 10-second wait after
  any *increase* so the grid meter has time to reflect the new draw.
* Decreases are applied immediately, lowest-priority wallbox first.
* If a Wallbox (e.g. KEBA) reports the car is fully charged (meter = ~0 W) we skip increases
  for that wallbox.
"""

import asyncio
import logging
import math
from wallbox.wallbox_base import WallboxBase, CHARGING_STATES
import shared_state

ONE_PHASE_VOLTAGE   = 230          # V
POWER_DELTA         = 200          # W always kept as buffer
MIN_CHARGING_CURRENT = 6000        # mA  (IEC 61851 minimum)
MAX_CHARGING_CURRENT = 16000       # mA

# How long to wait after a current *increase* before re-reading excess power
# for the next wallbox in the queue.
INTER_WALLBOX_INCREASE_DELAY_S = 10

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _min_start_power(number_of_phases: int) -> int:
    """Minimum required power to start charging on N phases."""
    return math.floor(number_of_phases * ONE_PHASE_VOLTAGE * MIN_CHARGING_CURRENT / 1000)


def _calculate_battery_excess() -> int:
    """
    Return battery contribution to excess power (W).
    Negative → battery is still absorbing power (priority: charge battery first).
    Positive → battery is discharging or above min-SoC threshold.
    """
    if (
        shared_state.battery_SoC < shared_state.home_bat_min_soc
        and shared_state.battery_power < 0
    ):
        if (shared_state.battery_power < -1000):
            # battery should at least charge with 1000W - rest can be counted as exceeding power
            excess = -(shared_state.battery_power + 1000)
            logger.debug(
                f"Home battery charging below min SoC "
                f"({shared_state.battery_SoC}% < {shared_state.home_bat_min_soc}%). "
                f"Battery charging with {shared_state.battery_power}. "
                f"Counting {excess} W as excess poewr. "
            )
            return excess
        else:
            logger.debug(
                f"Home battery charging below min SoC "
                f"({shared_state.battery_SoC}% < {shared_state.home_bat_min_soc}%). "
                f"Not counting battery power as excess."
            )
            return 0
    logger.debug(f"Battery excess power: {-shared_state.battery_power} W")
    return (-shared_state.battery_power)


def _current_excess_power() -> int:
    """
    Total excess solar power available right now (W).
    grid_power is in 0.1 W units: negative = feed-in.
    """
    grid_excess  = shared_state.grid_power / -10   # positive when feeding in
    batt_excess  = _calculate_battery_excess()
    excess       = grid_excess + batt_excess - POWER_DELTA
    logger.debug(
        f"Excess power: grid={grid_excess:.0f}W  batt={batt_excess:.0f}W  "
        f"delta={POWER_DELTA}W  → net={excess:.0f}W"
    )
    return excess


# ── Per-wallbox regulation ─────────────────────────────────────────────────────

def _set_current(wallbox: WallboxBase, wb_state: dict, new_current: int) -> int:
    """
    Apply new_current (in mA) to the wallbox.
    - new_current == 0  → pause_charging()
    - new_current > 0   → resume if paused, then write current with appropriate precision

    Returns the power actually changed in W, or 0 for no change.
    Negative return value -> more power available now.
    Positive return value -> more power used now.
    """
    current_val = wb_state["maximum_current"]

    # Treat "pause" specially
    if new_current < MIN_CHARGING_CURRENT:
        if not wb_state["paused"]:
            logger.info(f"[{wallbox.name}] Pausing charging.")
            wallbox.pause_charging()
            wb_state["maximum_current"] = 0
            wb_state["paused"] = True
            return _calculate_power_from_current(0 - current_val, wb_state['number_of_phases_used'])
        return 0  # already paused
    
    # Resume if previously paused
    if wb_state["paused"]:
        logger.info(f"[{wallbox.name}] Resuming charging.")
        wallbox.resume_charging()
        wb_state["paused"] = False

    logger.info(
        f"[{wallbox.name}] Setting current: {current_val} A → {new_current} A"
    )
    wallbox.write_max_current(new_current)
    wb_state["maximum_current"] = new_current
    return _calculate_power_from_current(new_current - current_val, wb_state['number_of_phases_used'])


def _update_wb_state(wallbox: WallboxBase, wb_state: dict):
    """
    refresh wallbox state (charging state and current)
    """
    charging_state = wallbox.read_charging_state()
    wb_state["charging_state"] = charging_state

    current_ma = wallbox.read_max_current()
    wb_state["maximum_current"] = current_ma


def _calculate_power_from_current(milliampere: int, number_of_phases_used: int) -> int:
    """
    returns the power for phases and current in W
    e.g. for claculating power difference after changing target current
    """
    return math.floor(ONE_PHASE_VOLTAGE * (milliampere/1000) * number_of_phases_used)

def _calculate_wallbox_target_current(current_current: int, excess_power: int, number_of_phases_used: int) -> int:
    """
    calculate target current for wallbox based on excess power and number of phases used plus the current value for charging
    returns the value in mA
    """
    return math.floor(excess_power / number_of_phases_used / ONE_PHASE_VOLTAGE) + current_current


def regulate_single_wallbox(wallbox: WallboxBase, wb_state: dict, excess_power: int) -> int:
    """
    Regulate one wallbox given the current excess power.
    Returns the *change* in power consumption (W) that was requested:
      - positive → we asked for more power (increase)
      - negative → we asked for less power (decrease)
      - 0        → no change

    The caller is responsible for waiting INTER_WALLBOX_INCREASE_DELAY_S
    if the return value is positive before calculating excess for the next box.
    """
    phases = wb_state["number_of_phases_used"]

    _update_wb_state(wallbox, wb_state)

    # Only regulate when a vehicle is connected (states 2, 3, 4)
    if wb_state["charging_state"] not in (2, 3, 4):
        logger.debug(
            f"[{wallbox.name}] State {CHARGING_STATES.get(wb_state['charging_state'])} – skipping regulation."
        )
        return 0

    target_current = _calculate_wallbox_target_current(wb_state["maximum_current"], excess_power, wb_state["number_of_phases_used"])

    if wallbox.is_car_fully_charged():
        logger.info(
        f"[{wallbox.name}] Car fully charged (meter reads ~0 W). "
        "Setting current to default value."
        )
        return _set_current(wallbox, wb_state, MAX_CHARGING_CURRENT)
    
    return _set_current(wallbox, wb_state, target_current)


# ── Multi-wallbox regulation loop (called by rest_api background task) ─────────

async def regulate_all_wallboxes_solar(wallboxes: dict, wb_states: dict):
    """
    Full regulation pass over all solar-only wallboxes.

    Strategy:
    ─────────
    DECREASES (excess_power ≤ 0):
      Apply immediately, lowest priority (highest number) first.
      Recalculate excess after each decrease before moving to the next.

    INCREASES (excess_power > 0):
      Apply highest priority first.
      After each increase, wait INTER_WALLBOX_INCREASE_DELAY_S seconds so the
      grid meter can reflect the new load before we allocate power to the next.
    """
    from wallbox.wallbox_config import WALLBOXES

    solar_wbs = [
        (wb_id, wb_states[wb_id])
        for wb_id in wallboxes
        if wb_states.get(wb_id, {}).get("solar_only_charging", False)
    ]

    if not solar_wbs:
        return

    excess = _current_excess_power()

    if excess <= 0:
        # ── Decrease pass: lowest priority first ──────────────────────
        sorted_decrease = sorted(solar_wbs, key=lambda x: -x[1]["priority"])
        for wb_id, wb_state in sorted_decrease:
            wb = WALLBOXES[wb_id]
            delta = regulate_single_wallbox(wb, wb_state, excess)
            if delta != 0.0:
                # Immediately recalculate for next wallbox
                excess = _current_excess_power()
    else:
        # ── Increase pass: highest priority first ─────────────────────
        sorted_increase = sorted(solar_wbs, key=lambda x: x[1]["priority"])
        for wb_id, wb_state in sorted_increase:
            wb = WALLBOXES[wb_id]
            delta = regulate_single_wallbox(wb, wb_state, excess)
            if delta > 0:
                logger.info(
                    f"[{wb.name}] Increased by ~{delta:.0f} W. "
                    f"Waiting {INTER_WALLBOX_INCREASE_DELAY_S}s for grid meter to settle…"
                )
                await asyncio.sleep(INTER_WALLBOX_INCREASE_DELAY_S)
                excess = _current_excess_power()
            # If no change or decrease happened, continue without waiting
