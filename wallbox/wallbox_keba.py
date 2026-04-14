"""
wallbox_keba.py

Concrete implementation for the KEBA P30 X wallbox.

Register map (read):
  1000 – Charging state (KEBA-specific codes, see mapping below)
  1004 – Cable state
  1020 – Active power (W)          ← used for is_car_fully_charged()
  1100 – Max charging current (mA) ← read back
  1110 – Max supported current (mA)

Register map (write):
  5004 – Set charging current (mA)
  5014 – Enable/Disable charging station (1 = enable, 0 = disable / pause)

Pause/resume:
  KEBA does NOT support 0 A. Pause via register 5014 = 0, resume via 5014 = 1.
"""

import logging
from modbus_interaction import write_modbus_data, read_modbus_data
from wallbox.wallbox_base import WallboxBase

logger = logging.getLogger(__name__)

# KEBA P30 X charging state → unified state mapping
# Source: KEBA P30 Modbus documentation
#   0 = Startup / not ready
#   1 = Not ready for charging  (no vehicle)
#   2 = Ready for charging      (vehicle connected, not authorised / not charging)
#   3 = Charging
#   4 = Error
#   5 = Authorisation rejected
KEBA_STATE_MAP = {
    0: 0,  # startup          → No state available
    1: 1,  # not ready        → A: EV disconnected
    2: 2,  # ready            → B: EV connected
    3: 3,  # charging         → C: EV charge
    4: 5,  # error            → E: Error condition
    5: 5,  # auth rejected    → E: Error condition
}

# Active-power threshold below which we consider the car fully charged (W)
FULLY_CHARGED_POWER_THRESHOLD_W = 50

# Register addresses
REG_CHARGING_STATE = 1000
REG_ACTIVE_POWER   = 1020   # read: mW
REG_MAX_CURRENT    = 1100   # read: mA
REG_SET_CURRENT    = 5004   # write: mA
REG_ENABLE         = 5014   # write: 1=enable, 0=disable/pause

MODBUS_SLAVE = 1


class KebaP30X(WallboxBase):
    """KEBA P30 X – milli-Ampere resolution, pause via enable register."""

    def __init__(
        self,
        wallbox_id: int,
        name: str,
        number_of_phases: int,
        ip: str,
        modbus_port: int,
        slave: int = MODBUS_SLAVE,
    ):
        super().__init__(wallbox_id, name, number_of_phases)
        self.ip = ip
        self.modbus_port = modbus_port
        self.slave = slave
        self._enabled = True  # track enable state to avoid redundant writes

    @staticmethod
    def _combine_registers(registers: list[int]) -> int:
        if len(registers) != 2:
            logger.error(f"Error reading charging state for Keba wallbox")
            return None
        
        return (registers[0] << 16) | registers[1]
    
        
    # ------------------------------------------------------------------
    # Abstract implementations
    # ------------------------------------------------------------------

    def read_charging_state(self) -> int:
        """
        Register 1000 returns KEBA-specific codes.
        Map to unified IEC 61851 codes via KEBA_STATE_MAP.
        """
        registers = read_modbus_data(
            ip=self.ip,
            modbus_port=self.modbus_port,
            register=REG_CHARGING_STATE,
            slave=self.slave,
            count=2,
        )
        
        charging_state = self._combine_registers(registers)
        if charging_state is None:
            logger.error(f"Error reading charging state for Keba wallbox")
            return 4
        
        unified = KEBA_STATE_MAP.get(charging_state, 0)
        logger.debug(f"[{self.name}] Charging state raw={charging_state} → unified={unified}")
        return unified

    def read_max_current(self) -> float:
        """
        Register 1100 returns current in mA.
        Convert to Ampere (float, one decimal place).
        """
        registers = read_modbus_data(
            ip=self.ip,
            modbus_port=self.modbus_port,
            register=REG_MAX_CURRENT,
            slave=self.slave,
            count=2,
        )

        value = self._combine_registers(registers)
        if value is None:
            logger.error(f"Error reading max current for Keba wallbox")
            return 0
        
        return value
    

    def write_max_current(self, milliampere) -> None:
        """
        Write charging current. Accepts int or float in Ampere.
        Converts to mA for the hardware register.
        """

        # Check whether the value actually changed (within 0.05 A tolerance)
        old_current = self.read_max_current()
        if abs(milliampere - old_current) < 100:
            logger.debug(f"[{self.name}] not setting current because of (nearly) no change. Old: {old_current} mA, New:{milliampere} mA")
            return

        logger.debug(f"[{self.name}] Writing max current: {milliampere} mA")
        write_modbus_data(
            ip=self.ip,
            modbus_port=self.modbus_port,
            register=REG_SET_CURRENT,
            slave=self.slave,
            value=milliampere,
        )

    def pause_charging(self) -> None:
        """KEBA does not support 0 A. Pause via the enable/disable register."""
        if self._enabled:
            logger.info(f"[{self.name}] Pausing charging (disable register 5014=0)")
            write_modbus_data(
                ip=self.ip,
                modbus_port=self.modbus_port,
                register=REG_ENABLE,
                slave=self.slave,
                value=0,
            )
            self._enabled = False

    def resume_charging(self) -> None:
        """Re-enable the charging station after a pause."""
        if not self._enabled:
            logger.info(f"[{self.name}] Resuming charging (enable register 5014=1)")
            write_modbus_data(
                ip=self.ip,
                modbus_port=self.modbus_port,
                register=REG_ENABLE,
                slave=self.slave,
                value=1,
            )
            self._enabled = True

    # ------------------------------------------------------------------
    # Meter-based fully-charged detection
    # ------------------------------------------------------------------

    def _read_active_power(self) -> int:
        """Return active power in mW from register 1020."""
        registers = read_modbus_data(
            ip=self.ip,
            modbus_port=self.modbus_port,
            register=REG_ACTIVE_POWER,
            slave=self.slave,
            count=2,
        )
        value = self._combine_registers(registers=registers)
        if value is None:
            logger.error(f"Error reading active power for Keba wallbox")
            return 0
        
        return value
    

    def is_car_fully_charged(self) -> bool:
        """
        The KEBA has a built-in meter.
        If the car is connected (state B or C) but draws less than the threshold,
        we treat it as fully charged → do not increase current.
        """
        state = self.read_charging_state()
        if state not in (2, 3):
            return False  # not connected or not charging
        power = self._read_active_power()
        fully_charged = power < FULLY_CHARGED_POWER_THRESHOLD_W
        if fully_charged:
            logger.debug(
                f"[{self.name}] Car appears fully charged "
                f"(active power={power} W < {FULLY_CHARGED_POWER_THRESHOLD_W} W)"
            )
        return fully_charged