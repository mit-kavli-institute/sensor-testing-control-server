# devices/labjack.py
from __future__ import annotations

import labjack.ljm as ljm  # or your preferred wrapper

from .base import BaseShutter


class LabJackT4Shutter(BaseShutter):
    def __init__(self, name, address, line="FIO4", active_high=True):
        self.name = name
        self.address = address
        self.line = line
        self.active_high = active_high
        self._handle = None
        self._state = False

    def connect(self):
        # address can be IP or "ANY"
        self._handle = ljm.open("T4", "ANY", self.address)
        self.close()  # default closed

    def disconnect(self):
        if self._handle:
            ljm.close(self._handle)
            self._handle = None

    def is_connected(self):
        return self._handle is not None

    def _write_line(self, val):
        # val is logical open/close; map via active_high
        ljm.eWriteName(self._handle, self.line, 1 if val == self.active_high else 0)

    def open(self):
        self._write_line(True)
        self._state = True

    def close(self):
        self._write_line(False)
        self._state = False

    def get_state(self):
        return self._state
