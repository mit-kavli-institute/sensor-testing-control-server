"""
labserver/devices/thorlabs_fwx.py  (rev 7)

* Helper functions are class‑methods for a tidy public surface:
    FilterWheel.list_devices()
    FilterWheel.open_device() / close_device()
    FilterWheel.get_position_raw() / set_position_raw() / get_position_count_raw()
* Robust connect(): auto–reopens after cable re‑attach or if DLL thinks the
  device is still open elsewhere.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from . import FWxC_COMMAND_LIB as fwx  # Thorlabs helper (with DLL already loaded)

__all__ = ["ThorlabsError", "FilterWheel", "load_wheels_from_yaml"]


class ThorlabsError(RuntimeError):
    """Raised when the Thorlabs DLL returns a negative status code."""


# -----------------------------------------------------------------------------
# FilterWheel class
# -----------------------------------------------------------------------------
class FilterWheel:
    # ---------- low‑level class helpers ------------------------------------
    @classmethod
    def list_devices(cls) -> List[str]:
        devices = fwx.FWxCListDevices()
        try:
            if isinstance(devices, bytes):
                txt = devices.decode("utf-8").strip()
                return [d for d in txt.replace(",", " ").split() if d]
            return [dev[0] for dev in devices]
        except Exception:
            return [dev[0] for dev in devices]

    @classmethod
    def open_device(
        cls, device_id: str | bytes, *, baud: int = 115200, timeout: int = 3
    ) -> int:
        serial_str = (
            device_id.decode() if isinstance(device_id, bytes) else str(device_id)
        )
        h = fwx.FWxCOpen(serial_str, baud, timeout)
        if h < 0:
            raise ThorlabsError(f"FWxCOpen({serial_str}) failed (code {h})")
        return h

    @classmethod
    def close_device(cls, handle: int) -> None:
        fwx.FWxCClose(handle)

    @classmethod
    def get_position_raw(cls, handle: int) -> int:
        pos = [0]
        rc = fwx.FWxCGetPosition(handle, pos)
        if rc < 0:
            raise ThorlabsError(f"GetPosition failed (code {rc})")
        return pos[0]

    @classmethod
    def set_position_raw(cls, handle: int, slot: int) -> None:
        rc = fwx.FWxCSetPosition(handle, int(slot))
        if rc < 0:
            raise ThorlabsError(f"SetPosition({slot}) failed (code {rc})")

    @classmethod
    def get_position_count_raw(cls, handle: int) -> int:
        cnt = [0]
        rc = fwx.FWxCGetPositionCount(handle, cnt)
        if rc < 0:
            raise ThorlabsError(f"GetPositionCount failed (code {rc})")
        return cnt[0]

    # ---------- constructor ----------------------------------------------
    def __init__(
        self,
        *,
        serial: str,
        baud: int = 115200,
        timeout_s: int = 3,
        slots: int = 6,
        poll_s: float = 0.05,
        type: str | None = None,
        filters: Optional[Dict[int, str]] = None,
    ):
        self.serial = serial
        self.baud = baud
        self.timeout_s = timeout_s
        self.slots = slots
        self.poll_s = poll_s
        self.type = type or "unknown"
        self.filters = {int(k): (v or "EMPTY") for k, v in (filters or {}).items()}

        self._hdl: Optional[int] = None
        self._lock = threading.RLock()

    # ---------- internal helpers -----------------------------------------
    def _handle_alive(self) -> bool:
        if self._hdl is None:
            return False
        try:
            # Quick harmless command:
            _ = self.get_position_raw(self._hdl)
            return True
        except Exception:
            return False

    def _safe_reopen(self) -> None:
        """Close stale handle if present, then try a fresh open."""
        try:
            if self._hdl is not None:
                self.close_device(self._hdl)
        except Exception:
            pass  # ignore close error
        self._hdl = None
        self._hdl = self.open_device(
            self.serial, baud=self.baud, timeout=self.timeout_s
        )

    # ---------- public connection API ------------------------------------
    def connect(self, *, force: bool = False) -> None:
        with self._lock:
            if self._hdl and not force and self._handle_alive():
                return
            try:
                self._safe_reopen()
            except ThorlabsError as e:
                # if DLL reports 'already open' we force a reopen once
                if "failed" in str(e).lower():
                    self._safe_reopen()
                else:
                    raise

    def disconnect(self) -> None:
        with self._lock:
            if self._hdl:
                try:
                    self.close_device(self._hdl)
                except Exception:
                    pass  # swallow any close errors
                self._hdl = None

    def is_connected(self) -> bool:
        return self._handle_alive()

    # ---------- motion ---------------------------------------------------
    def move_to(self, slot: int, *, block: bool = True, timeout: float | None = None):
        if slot < 0 or slot > self.slots:
            raise ValueError(f"slot {slot} out of range 0‑{self.slots}")
        if not self.is_connected():
            raise ThorlabsError("wheel not connected")
        self.set_position_raw(self._hdl, slot)
        if not block:
            return
        deadline = time.monotonic() + (timeout or self.timeout_s)
        while time.monotonic() < deadline:
            if self.get_position() == slot:
                return
            time.sleep(self.poll_s)
        raise TimeoutError(f"Timed out waiting for slot {slot}")

    def get_position(self) -> int:
        if not self.is_connected():
            raise ThorlabsError("wheel not connected")
        return self.get_position_raw(self._hdl)

    # ---------- filter helpers ------------------------------------------
    def list_filters(self) -> Dict[int, str]:
        return dict(self.filters)

    def move_to_filter(self, name: str, **move_kw):
        target = name.lower()
        for s, fname in self.filters.items():
            if fname.lower() == target:
                self.move_to(s, **move_kw)
                return
        raise KeyError(f"Filter '{name}' not found on wheel SN{self.serial}")

    # ---------- status ---------------------------------------------------
    def status(self) -> dict:
        pos = self.get_position() if self.is_connected() else None
        return {
            "serial": self.serial,
            "connected": self.is_connected(),
            "position": pos,
            "current_filter": self.filters.get(pos, "UNKNOWN") if pos else None,
            "type": self.type,
        }

    # ---------- context manager -----------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


# -----------------------------------------------------------------------------
# YAML loader
# -----------------------------------------------------------------------------
def load_wheels_from_yaml(
    path: str | Path,
) -> Tuple[Dict[str, FilterWheel], Dict[str, List[Tuple[str, int]]]]:
    cfg = yaml.safe_load(Path(path).read_text())
    wheels_cfg = cfg.get("filter_wheels", {})
    wheels: Dict[str, FilterWheel] = {}
    index: Dict[str, List[Tuple[str, int]]] = {}

    for key, spec in wheels_cfg.items():
        wheel = FilterWheel(**spec)
        wheels[key] = wheel
        for slot, fname in wheel.filters.items():
            if fname and fname.upper() != "EMPTY":
                index.setdefault(fname.lower(), []).append((key, slot))

    return wheels, index
