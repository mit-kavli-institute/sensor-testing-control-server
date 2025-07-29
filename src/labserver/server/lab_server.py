"""
labserver/server/lab_server.py  (rev 4)

Pyro5 daemon exposing wheels, shutter, and (optionally) an ammeter.
The server starts even if the ammeter COM port is absent.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import warnings
from pathlib import Path
from typing import Dict, Tuple

import Pyro5.api as pyro

from labserver.config import CONFIG_DIR
from labserver.devices.filter_rack import FilterRack
from labserver.devices.labjack import LabJackT4Shutter
from labserver.devices.picoammeter import BaseAmmeter, PicoAmmeter
from labserver.util.serialization import sanitize_for_serialization


@pyro.expose  # expose every public method in the class
class LabServer:
    """RPC facade for the full lab rig."""

    # ------------------------------------------------------------------
    def __init__(
        self,
        cfg_path: Path,
        *,
        lj_addr: str = "ANY",
        lj_line: str = "FIO4",
        ammeter_port: str = "COM7",
    ):

        # Wheels --------------------------------------------------------
        self.rack: FilterRack = FilterRack.from_yaml(cfg_path)

        # Shutter -------------------------------------------------------
        self._shutter = LabJackT4Shutter(address=lj_addr, line=lj_line)

        # Ammeter (optional) -------------------------------------------
        self.ammeter: BaseAmmeter | None = None
        try:
            am = PicoAmmeter(port=ammeter_port)
            am.connect()
            self.ammeter = am
        except Exception as e:
            warnings.warn(f"[LabServer] Ammeter not available on {ammeter_port}: {e}")

    # connect/disconnect for the ammeter
    def connect_ammeter(self):
        """Connect to the ammeter."""
        if self.ammeter:
            self.ammeter.connect()
        else:
            raise RuntimeError("Ammeter not configured")
        return self.ammeter.is_connected()

    def disconnect_ammeter(self):
        """Disconnect the ammeter."""
        if self.ammeter:
            self.ammeter.disconnect()
        else:
            raise RuntimeError("Ammeter not configured")
        return not self.ammeter.is_connected()

    # ---------- rack‑level --------------------------------------------
    def select_bandpass(self, wl_nm: float, tol_nm: float = 2.0):
        self.rack.select_bandpass(wl_nm, tol_nm=tol_nm)

    def available_filters(self):
        return self.rack.available_filters()

    # ---------- ND wheel ----------------------------------------------
    def set_nd(self, od_value, tol: float = 0.05):
        """Place requested ND filter (e.g. 0.5) in the beam path."""
        self.rack.select_nd(od_value, tol=tol)

    # ---------- per‑wheel ---------------------------------------------
    def list_wheels(self):
        return self.rack.wheels_keys()

    def wheel_status(self, key: str):
        return self.rack.wheels[key].status()

    def move_wheel(self, key: str, slot: int, block: bool = True):
        self.rack.wheels[key].move_to(slot, block=block)

    def wheel_filters(self, key: str):
        return self.rack.filters_for_wheel(key)

    # ---------- shutter / ammeter -------------------------------------
    def shutter(self, action: str):
        """'open' or 'close' the mechanical shutter."""
        action = action.lower()
        if action == "open":
            self._shutter.open()
        elif action == "close":
            self._shutter.close()
        else:
            raise ValueError("action must be 'open' or 'close'")

    def read_current(self):
        if self.ammeter and self.ammeter.is_connected():
            return self.ammeter.read_current()
        raise RuntimeError("Ammeter not connected")

    def read_multisample(
        self, n: int, dt: float = 0.1, return_arr: bool = True
    ) -> Tuple[float, float, float]:
        """Read `n` samples from the ammeter, returning mean, median, std."""
        if self.ammeter and self.ammeter.is_connected():
            res = self.ammeter.read_multisample(n, dt, return_arr)
            return sanitize_for_serialization(res.dict())
        raise RuntimeError("Ammeter not connected")

    # ---------- aggregated status -------------------------------------
    def status(self):
        return {
            "wheels": self.rack.status(),
            "shutter": self._shutter.status(),
            "ammeter_A": (
                None
                if self.ammeter is None or not self.ammeter.is_connected()
                else self.ammeter.read_current()
            ),
            "offline_wheels": self.rack.offline,
            "online_wheels": self.rack.online,
        }

    # ---------- graceful shutdown -------------------------------------
    def close(self):
        self._shutter.close()
        if self.ammeter:
            self.ammeter.disconnect()
        self.rack.close()


# ---------------------- CLI runner ------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(CONFIG_DIR, "filter_config.yaml"))
    ap.add_argument("--addr", default="localhost")
    ap.add_argument("--port", type=int, default=50000)
    ap.add_argument("--lj_addr", default="ANY")
    ap.add_argument("--lj_line", default="FIO4")
    ap.add_argument("--ammeter_port", default="COM7")
    args = ap.parse_args()

    srv = LabServer(
        Path(args.config),
        lj_addr=args.lj_addr,
        lj_line=args.lj_line,
        ammeter_port=args.ammeter_port,
    )

    daemon = pyro.Daemon(host=args.addr, port=args.port)
    uri = daemon.register(srv, objectId="LabServer")
    print("[LabServer] ready @", uri)

    def _sig(*_):
        print("\n[LabServer] shutting down …")
        srv.close()
        daemon.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    daemon.requestLoop()


if __name__ == "__main__":
    main()
