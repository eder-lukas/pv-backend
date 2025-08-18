import math
from modbus_interaction import read_wallbox_modbus_data, ev_charging_modbus_registers, write_modbus_data
from shared_state import grid_power, battery_power


POWER_DELTA = 200 # Power in W, which should always be available as a buffer, even when the car is charging
ONE_AMP_DELTA_POWER = 2*230 # two phases, 1A
MIN_CHARGING_CURRENT = 6 # 6A minimum charging current
MAX_CHARGING_CURRENT = 16 # 16A maximum charging current
PAUSE_CHARGING_CURRENT = 0 # Charging current setting for pausing the charging process
MIN_CHARGING_START_POWER = 2*230*MIN_CHARGING_CURRENT # two phases, minimum charging current


charging_states = {
    0: "Not available",
    1: "A: EV disconnected",
    2: "B: EV connected",
    3: "C: EV charge",
    4: "D: EV charge (ventilation required)",
    5: "E: Error condition",
    6: "F: Fault condition"
}


# regulates the ev charging max current, so that no power is drained from the battery and there is always the POWER_DELTA available (e.g. for home battery charging or grid feed in)
def regulate_ev_charging():
    global ev_charging_state, ev_max_current

    ev_charging_state = read_wallbox_modbus_data(**ev_charging_modbus_registers["charging_state"])
    ev_max_current = read_wallbox_modbus_data(**ev_charging_modbus_registers["maximum_current"])

    if (ev_charging_state >= 2 and ev_charging_state <= 4):
        calculate_and_set_max_current()


# calculates the maximum current for ev charging and sets a new value if needed
# two phases charging (VW e-Golf)
# battery should not be drained for ev charging
# minimum current 6A - pause current 0A
def calculate_and_set_max_current():
    # calculate if power is drained from the grid and/or battery
    excess_power = (grid_power + battery_power) * (-1)
    # subtract the power delta which should always be available
    excess_power = excess_power - POWER_DELTA
    
    if (ev_max_current == PAUSE_CHARGING_CURRENT):
        check_for_charging_start(excess_power)
    else:
        if excess_power > 0 and ev_max_current < MAX_CHARGING_CURRENT:
            check_for_power_increase(excess_power)
        elif excess_power <= 0 and ev_max_current > PAUSE_CHARGING_CURRENT:
            check_for_power_decrease(excess_power)


# check if charging can be started
# only run, if ev_max_current is 0 before method execution
def check_for_charging_start(excess_power: int):
    if (excess_power >= MIN_CHARGING_START_POWER):
        set_charging_current(MIN_CHARGING_CURRENT)


# check if more current is possible
def check_for_power_increase(excess_power: int):
    current_increase = math.floor(excess_power / ONE_AMP_DELTA_POWER) # floor always

    new_current = ev_max_current + current_increase

    if (new_current > MAX_CHARGING_CURRENT):
        new_current = MAX_CHARGING_CURRENT
    
    set_charging_current(new_current)


# check if less current or charging pause is necessary
def check_for_power_decrease(excess_power: int):
    current_reduction = math.ceil((excess_power * (-1)) / ONE_AMP_DELTA_POWER) # round up always

    new_current = ev_max_current - current_reduction

    if (new_current < MIN_CHARGING_CURRENT):
        new_current = PAUSE_CHARGING_CURRENT
    
    set_charging_current(new_current)

# only sets the new current, if it changed
def set_charging_current(new_current):
    if new_current != ev_max_current:
        write_modbus_data(**ev_charging_modbus_registers["maximum_current"], value=new_current)