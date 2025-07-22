# devices/labjack.py
from __future__ import annotations

import labjack.ljm as ljm  # LabJack LJM driver

from .base import BaseShutter


class LabJackT4Shutter(BaseShutter):
    """Digital‑line shutter driver for a LabJack T4.

    Parameters
    ----------
    address : str, optional
        IP address ("192.168.x.x") **or** "ANY" for USB/auto‑open.  Default "ANY" so you can instantiate even with no hardware attached for tests.
    line : str, optional
        Register name to toggle (e.g. "FIO4").
    active_high : bool, optional
        If *True* the shutter **opens** when the line is driven high (1 VIO).  If *False* line‑high means *closed*.
    name : str, optional
        Friendly identifier used in logs/status (defaults to "shutter").
    """

    def __init__(
        self,
        address: str = "ANY",
        *,
        line: str = "FIO4",
        active_high: bool = True,
        name: str = "shutter",
    ) -> None:
        self.name = name
        self.address = address
        self.line = line
        self.active_high = active_high
        self._handle: int | None = None
        self._state: bool = False  # logical state: True=open

        # connect to the LabJack on instantiation
        self.connect()

    # ------------------------------------------------------------------
    # BaseDevice API
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """Open the LabJack.  Safe to call twice."""
        if self._handle is None:
            # First arg "T4" narrows model; second arg "ANY" lets driver pick USB/TCP; third is IP/serial.
            self._handle = ljm.openS("T4", "ANY", self.address)

    def disconnect(self) -> None:
        if self._handle:
            ljm.close(self._handle)
            self._handle = None

    def is_connected(self) -> bool:
        return self._handle is not None

    def status(self) -> dict:
        """Return a lightweight status dict for dashboards / RPC calls."""
        return {
            "connected": self.is_connected(),
            "state": "open" if self._state else "closed",
            "line": self.line,
            "address": self.address,
        }

    # ------------------------------------------------------------------
    # BaseShutter API
    # ------------------------------------------------------------------
    def _write_line(self, logical_open: bool) -> None:
        if not self.is_connected():
            raise RuntimeError("LabJack shutter not connected")
        physical_level = 1 if logical_open == self.active_high else 0
        print(f"Setting {self.line} to {physical_level}")
        ljm.eWriteName(self._handle, self.line, physical_level)

    def open(self) -> None:
        self._write_line(True)
        self._state = True

    def close(self) -> None:
        self._write_line(False)
        self._state = False

    def get_state(self) -> bool:
        return self._state
