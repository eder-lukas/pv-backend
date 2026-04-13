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

Precision:
  KEBA supports milli-Ampere resolution → set_current_precise() sends mA values.

Pause/resume:
  KEBA does NOT support 0 A. Pause via register 5014 = 0, resume via 5014 = 1.
"""

import logging
from modbus_interaction import read_wallbox_modbus_data, write_modbus_data
from wallbox_base import WallboxBase

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
REG_ACTIVE_POWER   = 1020   # read: W
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
        slave: int = MODBUS_SLAVE,
    ):
        super().__init__(wallbox_id, name, number_of_phases)
        self.ip = ip
        self.slave = slave
        self._enabled = True  # track enable state to avoid redundant writes

    # ------------------------------------------------------------------
    # Abstract implementations
    # ------------------------------------------------------------------

    def read_charging_state(self) -> int:
        """
        Register 1000 returns KEBA-specific codes.
        Map to unified IEC 61851 codes via KEBA_STATE_MAP.
        """
        raw = read_wallbox_modbus_data(
            ip=self.ip,
            register=REG_CHARGING_STATE,
            slave=self.slave,
        )
        unified = KEBA_STATE_MAP.get(raw, 0)
        logger.debug(f"[{self.name}] Charging state raw={raw} → unified={unified}")
        return unified

    def read_max_current(self) -> float:
        """
        Register 1100 returns current in mA.
        Convert to Ampere (float, one decimal place).
        """
        raw_ma = read_wallbox_modbus_data(
            ip=self.ip,
            register=REG_MAX_CURRENT,
            slave=self.slave,
        )
        return raw_ma if raw_ma is not None else 0

    def write_max_current(self, milliampere) -> None:
        """
        Write charging current. Accepts int or float in Ampere.
        Converts to mA for the hardware register.
        """

        # Check whether the value actually changed (within 0.05 A tolerance)
        old_current = self.read_max_current()
        if abs(milliampere - old_current) < 0.05:
            logger.debug(f"[{self.name}] not setting current because of (nearly) no change. Old: {old_current} mA, New:{milliampere} mA")
            return

        logger.debug(f"[{self.name}] Writing max current: {milliampere} mA")
        write_modbus_data(
            ip=self.ip,
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
                register=REG_ENABLE,
                slave=self.slave,
                value=1,
            )
            self._enabled = True

    # ------------------------------------------------------------------
    # Meter-based fully-charged detection
    # ------------------------------------------------------------------

    def _read_active_power(self) -> int:
        """Return active power in W from register 1020."""
        return read_wallbox_modbus_data(
            ip=self.ip,
            register=REG_ACTIVE_POWER,
            slave=self.slave,
        ) or 0

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

    # ------------------------------------------------------------------
    # Current-setting: KEBA supports sub-Amp precision → use precise setter
    # (base class set_current_precise already does round(ampere,1); that's fine)
    # ------------------------------------------------------------------
