"""
wallbox_config.py

Central registry of all configured wallboxes.
Each wallbox is instantiated here; solar_charging and rest_api import
WALLBOXES directly and never need to know the concrete class.
"""

from wallbox.wallbox_base import WallboxBase
from wallbox.wallbox_juice import JuiceChargerMe
from wallbox.wallbox_keba import KebaP30X

# ── Network addresses ──────────────────────────────────────────────────────────
CHARGER_ME_IP   = "192.168.188.94"
KEBA_WALLBOX_IP = "192.168.188.132"

# ── Wallbox instances ──────────────────────────────────────────────────────────
# number_of_phases: phases the *car* uses for charging.
#   Adjust via the REST API (/wallbox/{id}/number_of_phases_used) at runtime.
#   This value is stored in shared_state and passed to solar_charging;
#   the wallbox object itself does not need to know it (it's a car property,
#   not a charger property).

WALLBOXES: dict[int, "WallboxBase"] = {
    1: JuiceChargerMe(
        wallbox_id=1,
        name="Halle (Lukas)",
        number_of_phases=2,          # default; overrideable via API
        ip=CHARGER_ME_IP,
        modbus_port=502,
    ),
    2: KebaP30X(
        wallbox_id=2,
        name="Garage (Papa)",
        number_of_phases=3,          # default; overrideable via API
        ip=KEBA_WALLBOX_IP,
        modbus_port=1502,
    ),
}
