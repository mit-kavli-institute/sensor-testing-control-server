# labserver/devices/picoammeter.py   (rev 5 – direct port of working script)
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import serial

from .base import BaseAmmeter


class MultiSampleResult:
    def __init__(
        self,
        n_samples: Optional[int] = None,
        mean: Optional[float] = None,
        median: Optional[float] = None,
        std: Optional[float] = None,
        samples: Optional[list] = None,
        times: Optional[list] = None,
    ):
        self.n_samples = n_samples
        self.samples = samples
        self.mean = mean
        self.median = median
        self.std = std
        self.times = times

    def dict(self):
        """Convert to a dictionary."""
        return {
            "n_samples": self.n_samples,
            "mean": self.mean,
            "median": self.median,
            "std": self.std,
            "samples": self.samples,
            "times": self.times,
        }


class PicoAmmeter(BaseAmmeter):
    """Minimal wrapper that duplicates the user’s proven test script."""

    def __init__(
        self,
        *,
        port: str,
        baudrate: int = 9600,
        timeout: float = 2.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None

    # ---------------- low‑level helpers ------------------------------
    def _send(self, cmd: str):
        if not self._ser or not self._ser.is_open:
            raise RuntimeError("ammeter not connected")
        self._ser.write((cmd + "\r").encode())
        self._ser.flush()
        time.sleep(0.1)  # same pacing as script

    def _query(self, cmd: str) -> str:
        self._send(cmd)
        return self._ser.readline().decode().strip()

    # ---------------- initialisation ---------------------------------
    def _configure(self):
        # exact command sequence from working script
        cmds = (
            "*RST",
            ":FORM:ELEM READ",
            "TRIG:DEL 0",
            "TRIG:COUNT 1",
            "SENS:CURR:NPLC 6",
            "SENS:CURR:RANG 0.000002",
            "SENS:CURR:RANG:AUTO ON",
            "SYST:ZCOR ON",
            "SYST:AZER:STAT OFF",
            "DISP:ENAB ON",
            ":SYST:ZCH:STAT OFF",
        )
        for c in cmds:
            self._send(c)
        time.sleep(0.5)  # settle, like script

    # ---------------- public API -------------------------------------
    def connect(self):
        if self._ser and self._ser.is_open:
            return
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._configure()

    def disconnect(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    def is_connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    def read_current(self) -> float:
        """Trigger one reading exactly like the test script (`READ?`)."""
        if not self.is_connected():
            raise RuntimeError("ammeter not connected")
        resp = self._query("READ?")
        try:
            cur = float(resp)
            self._last_current = cur
            return cur
        except ValueError as exc:
            self._last_current = np.nan
            raise RuntimeError(f"Bad ammeter response: {resp!r}") from exc

    def read_multisample(
        self, n: int, dt: float = 0.1, return_arr: bool = True
    ) -> MultiSampleResult:
        """Read `n` samples, returning a MultiSampleResult."""
        if not self.is_connected():
            raise RuntimeError("ammeter not connected")
        samples = []
        times = []
        for _ in range(n):
            samples.append(self.read_current())
            times.append(time.time())
            time.sleep(dt)

        mean = np.mean(samples)
        median = np.median(samples)
        std = np.std(samples)

        res = MultiSampleResult(
            n_samples=n,
            mean=mean,
            median=median,
            std=std,
            samples=samples if return_arr else None,
            times=times if return_arr else None,
        )

        return res

    def status(self):
        return {
            "connected": self.is_connected(),
            "port": self.port,
            "baud": self.baudrate,
            "last_current_A": self._last_current,
        }
