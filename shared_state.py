# Global variables for Modbus and UDP data
grid_power = 0  # in W, negative == feed_in, positive == consumption
emeter_power = 0  # in W, positive == production
battery_power = 0  # in W, negative = charging, positive = discharging
battery_SoC = 0  # percentage, int
wallbox_states = {
    1: {
        "number_of_phases_used": 3, # should be changed dynamically via api
        "priority": 2,
        "charging_state": 0,
        "maximum_current": 0,
        "solar_only_charging": False,
    },
    2: {
        "number_of_phases_used": 3,
        "priority": 1,
        "charging_state": 0,
        "maximum_current": 0,
        "solar_only_charging": False,
    },
}
home_bat_min_soc = 90 # SoC until home battery is prioritized before EV charging. SoC of battery must reach this level, than charging power of the battery counts as excess power and could be used for ev charging