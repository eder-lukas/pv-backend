
from pymodbus.client import ModbusTcpClient # older versions pymodbus.client.sync
from wallbox.wallbox_config import WALLBOXES
from wallbox.wallbox_base import WallboxBase
from wallbox.wallbox_juice import JuiceChargerMe
from wallbox.wallbox_keba import KebaP30X


# juice = JuiceChargerMe(1, "Wallbox Halle (Lukas)", 3, "192.168.188.94", 502, 1)

# print(juice.read_charging_state())
# print(juice.read_max_current())
# juice.write_max_current(16000)
# print(juice.read_max_current())
# juice.pause_charging()

# print(juice.read_charging_state())
# print(juice.read_max_current())
# juice.write_max_current(16000)

# print(juice.read_charging_state())
# print(juice.read_max_current())

keba = KebaP30X(2, "Wallbox Garage (Papa)", 3, "192.168.188.132", 502, 1)

print(keba.read_charging_state())
print(keba.read_max_current())
# keba.write_max_current(10000)
# print(keba.read_max_current()) #10a
# print(keba._read_active_power()) #4,6 kw oder so
# print(keba.is_car_fully_charged()) # no

# keba.pause_charging()
# print(keba.read_charging_state()) # pause
# print(keba._read_active_power()) # 0
# print(keba.is_car_fully_charged()) # no
# keba.resume_charging()
# keba.write_max_current(16000)
# print(keba.read_charging_state()) # pause
# print(keba._read_active_power()) # 0
# print(keba.is_car_fully_charged()) # no