"""
wallbox_base.py

Abstract base class for wallbox implementations.
Each wallbox type subclasses this and overrides hardware-specific methods.
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


# Unified charging state mapping (same semantics as IEC 61851 / ChargeMe)
CHARGING_STATES = {
    0: "No charging state available",
    1: "A: EV disconnected",
    2: "B: EV connected",
    3: "C: EV charge",
    4: "D: EV charge (ventilation required)",
    5: "E: Error condition",
    6: "F: Fault condition",
}


class WallboxBase(ABC):
    """
    Abstract base class for all wallbox types.

    Subclasses must implement:
      - read_charging_state() -> int   (returns unified state code 0–6)
      - read_max_current()    -> int   (in Ampere)
      - write_max_current(a)           (set current in Ampere)
      - pause_charging()               (hardware-specific pause)
      - resume_charging()              (hardware-specific resume, if needed)

    Optional override:
      - is_car_fully_charged() -> bool (True if meter shows ~0 W draw while cable connected)
        Default returns False (no meter available).
    """

    def __init__(self, wallbox_id: int, name: str, number_of_phases: int):
        self.wallbox_id = wallbox_id
        self.name = name
        self.number_of_phases = number_of_phases  # phases the *car* actually uses

    # ------------------------------------------------------------------
    # Abstract hardware interface
    # ------------------------------------------------------------------

    @abstractmethod
    def read_charging_state(self) -> int:
        """Return unified charging state code (0–6)."""
        ...

    @abstractmethod
    def read_max_current(self) -> int:
        """Return currently configured max charging current in Milliampere."""
        ...

    @abstractmethod
    def write_max_current(self, milliampere: int) -> None:
        """Write a new max charging current in Milliampere to the hardware."""
        ...

    @abstractmethod
    def pause_charging(self) -> None:
        """Hardware-specific method to pause / stop charging."""
        ...

    def resume_charging(self) -> None:  # noqa: B027  (intentionally not abstract)
        """
        Hardware-specific method to resume charging after a pause.
        Default is a no-op (devices that resume automatically when current > 0
        do not need to override this).
        """
        pass

    def is_car_fully_charged(self) -> bool:
        """
        Return True when the car is connected and fully charged (draws ~0 W).
        Wallboxes without a power meter always return False.
        """
        return False


    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.wallbox_id} name={self.name!r}>"
