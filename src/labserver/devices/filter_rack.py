"""
labserver/devices/filter_rack.py   (rev 5)

Manages every *FilterWheel* described in a YAML config.

Capabilities
------------
• select_bandpass(wavelength_nm)      – put desired BP filter in path
• select_nd(od)                       – put desired ND filter in path
• list_wheels(), wheel_status(), etc. – per‑wheel helpers
• available_filters()                 – global lookup
• Robust to unplugged wheels           (kept in .offline list)
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .thorlabs_fw import FilterWheel, ThorlabsError

BP = "bandpass"
ND = "nd"

__all__ = ["FilterRack"]


class FilterRack:
    # ------------------------------------------------------------------
    def __init__(self, wheels: Dict[str, FilterWheel], meta: Dict[str, dict]):
        self.wheels: Dict[str, FilterWheel] = wheels
        self.meta: Dict[str, dict] = meta

        self.offline: List[str] = [k for k, w in wheels.items() if not w.is_connected()]
        self.online: List[str] = [k for k in wheels if k not in self.offline]

        # Build lookup tables
        self._wl_index: Dict[float, Tuple[str, int, str]] = (
            {}
        )  # nm -> (wheel,slot,name)
        self._nd_index: Dict[float, Tuple[str, int, str]] = (
            {}
        )  # OD -> (wheel,slot,name)
        self._build_indices()

    # ------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path) -> "FilterRack":
        """Load YAML and open wheels; wheels that fail to open are kept offline."""
        FilterWheel.list_devices()  # primes Thorlabs DLL

        cfg = yaml.safe_load(Path(path).read_text())
        wheels_cfg = cfg.get("filter_wheels", {})
        filter_meta = cfg.get("filters", {})

        wheels: Dict[str, FilterWheel] = {}
        for key, spec in wheels_cfg.items():
            wheel = FilterWheel(**spec)
            try:
                wheel.connect()
                if not wheel.is_connected():
                    raise ThorlabsError("wheel not detected")
            except ThorlabsError as e:
                warnings.warn(f"[FilterRack] {key} offline: {e}")
            wheels[key] = wheel

        return cls(wheels, filter_meta)

    # ------------------------------------------------------------------
    # Index builders
    # ------------------------------------------------------------------
    def _build_indices(self) -> None:
        """Populate band‑pass and ND lookup tables for **connected** wheels."""
        for wkey, wheel in self.wheels.items():
            if not wheel.is_connected():
                continue
            for slot, raw_name in wheel.filters.items():
                if isinstance(raw_name, (int, float)):  # numeric in YAML
                    name = str(raw_name)
                else:
                    name = str(raw_name)

                if name.upper() == "EMPTY":
                    continue

                f_meta = self.meta.get(name, {})
                f_type = f_meta.get("type", ND if wheel.type == ND else BP)

                # ----- band‑pass --------------------------------------
                if f_type == BP and "wavelength" in f_meta:
                    wl = float(f_meta["wavelength"])
                    self._wl_index[wl] = (wkey, slot, name)

                # ----- neutral density -------------------------------
                elif f_type == ND:
                    try:
                        # Accept 'ND 0.5', '0.5', 0.5
                        od = float(name.split()[-1])
                        self._nd_index[od] = (wkey, slot, name)
                    except ValueError:
                        continue

    # ------------------------------------------------------------------
    # Band‑pass selection
    # ------------------------------------------------------------------
    def _nearest_bp(
        self, target_nm: float, tol_nm: float
    ) -> Optional[Tuple[str, int, str]]:
        cands = [
            (abs(target_nm - wl), triple)
            for wl, triple in self._wl_index.items()
            if abs(target_nm - wl) <= tol_nm
        ]
        return min(cands, default=(None, None))[1]

    def select_bandpass(self, wl_nm: float, *, tol_nm: float = 2.0, block: bool = True):
        match = self._nearest_bp(wl_nm, tol_nm)
        if match is None:
            raise KeyError(f"No band‑pass filter near {wl_nm} nm (±{tol_nm})")

        tgt_key, tgt_slot, _ = match

        # First EMPTY slot for every other BP wheel
        empty: Dict[str, int] = {}
        for k, w in self.wheels.items():
            if w.type == ND or not w.is_connected():
                continue
            for s, n in w.filters.items():
                if str(n).upper() == "EMPTY":
                    empty[k] = s
                    break

        for k, w in self.wheels.items():
            if not w.is_connected() or w.type == ND:
                continue
            try:
                w.move_to(tgt_slot if k == tgt_key else empty[k], block=block)
            except ThorlabsError as e:
                warnings.warn(f"[FilterRack] {k}: {e}")

    # ------------------------------------------------------------------
    # ND selection
    # ------------------------------------------------------------------
    def select_nd(self, od: float | str, *, tol: float = 0.05, block: bool = True):
        """Place ND filter with optical density *od* into the beam."""
        try:
            od_val = float(od) if isinstance(od, str) else od
        except ValueError:
            raise ValueError("od must be numeric or like 'ND 0.5'")

        # choose closest within tolerance
        cands = [
            (abs(od_val - dens), triple)
            for dens, triple in self._nd_index.items()
            if abs(od_val - dens) <= tol
        ]
        if not cands:
            raise KeyError(f"No ND filter ≈{od_val} (±{tol})")

        _, (wkey, slot, _) = min(cands, key=lambda t: t[0])
        self.wheels[wkey].move_to(slot, block=block)

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    def wheels_keys(self) -> List[str]:
        return list(self.wheels)

    def filters_for_wheel(self, key: str) -> Dict[int, str]:
        return self.wheels[key].list_filters()

    def available_filters(self) -> Dict[str, Tuple[str, int]]:
        """Return all filters present on connected wheels."""
        out: Dict[str, Tuple[str, int]] = {}
        for k in self.online:
            w = self.wheels[k]
            for slot, name in w.filters.items():
                if str(name).upper() != "EMPTY":
                    out[str(name)] = (k, slot)
        return out

    def status(self) -> Dict[str, dict]:
        """Per‑wheel status dict."""
        return {k: w.status() for k, w in self.wheels.items()}

    # ------------------------------------------------------------------
    def close(self):
        for w in self.wheels.values():
            w.disconnect()
