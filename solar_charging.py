import math
import logging
from modbus_interaction import (
    read_wallbox_modbus_data,
    ev_charging_modbus_registers,
    write_modbus_data,
)
import shared_state

ONE_PHASE_VOLTAGE = 230
NUMBER_OF_CHARGING_PHASES = 2
POWER_DELTA = 200  # Power in W, which should always be available as a buffer, even when the car is charging
ONE_AMP_POWER = NUMBER_OF_CHARGING_PHASES * ONE_PHASE_VOLTAGE
MIN_CHARGING_CURRENT = 6  # 6A minimum charging current
MAX_CHARGING_CURRENT = 16  # 16A maximum charging current
PAUSE_CHARGING_CURRENT = 0  # Charging current setting for pausing the charging process
MIN_CHARGING_START_POWER = (
    ONE_AMP_POWER * MIN_CHARGING_CURRENT
)  # two phases, minimum charging current


charging_states = {
    0: "No charging state available",
    1: "A: EV disconnected",
    2: "B: EV connected",
    3: "C: EV charge",
    4: "D: EV charge (ventilation required)",
    5: "E: Error condition",
    6: "F: Fault condition",
}

logger = logging.getLogger(__name__)


# regulates the ev charging max current, so that no power is drained from the battery and there is always the POWER_DELTA available (e.g. for home battery charging or grid feed in)
def regulate_ev_charging():
    # Read current values from Modbus
    current_charging_state = read_wallbox_modbus_data(
        **ev_charging_modbus_registers["charging_state"]
    )
    current_max_current = read_wallbox_modbus_data(
        **ev_charging_modbus_registers["maximum_current"]
    )
    if current_charging_state is not None:
        shared_state.ev_charging_state = current_charging_state
    if current_max_current is not None:
        shared_state.ev_max_current = current_max_current

    if shared_state.ev_charging_state >= 2 and shared_state.ev_charging_state <= 4:
        calculate_and_set_max_current()


# calculates the maximum current for ev charging and sets a new value if needed
# two phases charging (VW e-Golf)
# battery should not be drained for ev charging
# minimum current 6A - pause current 0A
def calculate_and_set_max_current():
    # calculate if power is drained from the grid and/or battery
    # grid power in 10W -> //10 (negative is feed in)
    excess_power = shared_state.grid_power // -10 + calculate_battery_power_for_excess()
    logger.debug(f"Excess power without delta: {excess_power}")

    # subtract the power delta which should always be available
    excess_power = excess_power - POWER_DELTA
    logger.debug(f"Excess power with delta: {excess_power}")

    if shared_state.ev_max_current == PAUSE_CHARGING_CURRENT:
        check_for_charging_start(excess_power)
    else:
        if excess_power > 0 and shared_state.ev_max_current < MAX_CHARGING_CURRENT:
            check_for_power_increase(excess_power)
        elif excess_power <= 0 and shared_state.ev_max_current > PAUSE_CHARGING_CURRENT:
            check_for_power_decrease(excess_power)


# returns battery excess power for calculating total excess power
# if battery is charging and SoC >= HOME_BAT_MIN_SOC the charging power counts as excess power
# negativ return values are missing power
# positive return values are excess power
def calculate_battery_power_for_excess():
    # shared state battery power is negative for charging the battery

    if shared_state.battery_SoC < shared_state.home_bat_min_soc and shared_state.battery_power < 0:
        # battery charging and not yet fully charged
        logger.debug(
            f"Home battery is charging and below min soc. SoC: {shared_state.battery_SoC} with {shared_state.battery_power} W",
        )
        return 0
    else:
        logger.debug(f"Battery excess power: {-shared_state.battery_power}")
        return -shared_state.battery_power


# check if charging can be started
# only run, if ev_max_current is 0 before method execution
def check_for_charging_start(excess_power: int):
    if excess_power >= MIN_CHARGING_START_POWER:
        logger.debug(f"start charging with min current: {MIN_CHARGING_CURRENT}")
        set_charging_current(MIN_CHARGING_CURRENT)


# check if more current is possible
def check_for_power_increase(excess_power: int):
    current_increase = math.floor(excess_power / ONE_AMP_POWER)  # floor always

    new_current = shared_state.ev_max_current + current_increase

    if new_current > MAX_CHARGING_CURRENT:
        new_current = MAX_CHARGING_CURRENT

    set_charging_current(new_current)


# check if less current or charging pause is necessary
def check_for_power_decrease(excess_power: int):
    current_reduction = math.ceil(
        (excess_power * (-1)) / ONE_AMP_POWER
    )  # round up always

    new_current = shared_state.ev_max_current - current_reduction

    if new_current < MIN_CHARGING_CURRENT:
        new_current = PAUSE_CHARGING_CURRENT

    set_charging_current(new_current)


# only sets the new current, if it changed
def set_charging_current(new_current):
    if new_current != shared_state.ev_max_current:
        logger.info(
            f"Setting Charging Current to new Value: {new_current} A. Old Value: {shared_state.ev_max_current} A"
        )

        write_modbus_data(
            **ev_charging_modbus_registers["maximum_current"], value=new_current
        )
