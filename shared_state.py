# Global variables for Modbus and UDP data
grid_power = 0  # in W, negative == feed_in, positive == consumption
emeter_power = 0  # in W, positive == production
battery_power = 0  # in W, negative = charging, positive = discharging
battery_SoC = 0  # percentage, int
ev_charging_state = 0  # 1-6
ev_max_current = 0  # 0-16
is_solar_only_charging = (
    True  # Boolean, if instant charging or solar charging is activated
)
home_bat_min_soc = 90 # SoC until home battery is prioritized before EV charging. SoC of battery must reach this level, than charging power of the battery counts as excess power and could be used for ev charging