"""
wallbox_juice.py

Concrete implementation for the Juice Charger Me wallbox.

Charging state: register 122  (IEC 61851 code, maps 1:1 to unified states)
Max current:    register 1000  (integer Ampere, writable)
Pause:          write 0 A to register 1000  (device supports 0 A)
"""

import logging
import math
from modbus_interaction import read_wallbox_modbus_data, write_modbus_data
from wallbox_base import WallboxBase

logger = logging.getLogger(__name__)

PAUSE_CURRENT = 0  # Juice supports setting current to 0 A to pause
CHARGING_STATE_REGISTER=122,
MAX_CURRENT_REGISTER=1000,
MODBUS_SLAVE=1,

class JuiceChargerMe(WallboxBase):
    """Juice Charger Me – integer-Ampere resolution, pause via 0 A."""

    def __init__(
        self,
        wallbox_id: int,
        name: str,
        number_of_phases: int,
        ip: str,
        slave: int = MODBUS_SLAVE,
    ):
        super().__init__(wallbox_id, name, number_of_phases)
        self.ip = ip
        self.slave = slave

    # ------------------------------------------------------------------
    # Abstract implementations
    # ------------------------------------------------------------------

    def read_charging_state(self) -> int:
        """
        Register 122 directly returns IEC 61851 state codes 1–6 (or 0 on error).
        These map 1:1 to the unified state codes.
        """
        value = read_wallbox_modbus_data(
            ip=self.ip,
            register=CHARGING_STATE_REGISTER,
            slave=self.slave,
        )
        return value if value is not None else 0

    def read_max_current(self) -> int:
        value = read_wallbox_modbus_data(
            ip=self.ip,
            register=MAX_CURRENT_REGISTER,
            slave=self.slave,
        )
        return value * 1000 if value is not None else 0

    def write_max_current(self, milliampere:int) -> None:
        """Write integer Ampere. Juice only supports whole Ampere steps. Therefore in this Method milliampere are floored to ampere"""
        ampere_int = math.floor(milliampere/1000)

        # Check whether the value actually changed
        old_current = self.read_max_current()
        if abs(ampere_int - old_current) < 0:
            logger.debug(f"[{self.name}] not setting current because of no change. Old: {old_current} mA, New:{milliampere} mA")
            return

        write_modbus_data(
            ip=self.ip,
            register=MAX_CURRENT_REGISTER,
            slave=self.slave,
            value=ampere_int,
        )

    def pause_charging(self) -> None:
        """Juice Charger Me supports pausing by writing 0 A."""
        logger.info(f"[{self.name}] Pausing charging (setting current to 0 A)")
        self.write_max_current(PAUSE_CURRENT)
