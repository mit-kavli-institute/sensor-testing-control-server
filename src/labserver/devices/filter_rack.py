"""
labserver/devices/filter_rack.py   (rev 6)

Manages every *FilterWheel* described in a YAML config.

Capabilities
------------
• select_bandpass(wavelength_nm)      – put desired BP filter in path
• select_nd(od)                       – put desired ND filter in path
• list_wheels(), wheel_status(), etc. – per‑wheel helpers
• available_filters()                 – global lookup with type filtering
• Robust to unplugged wheels           (kept in .offline list)
• Special filter handling              – shortpass/longpass coordination
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import yaml

from .thorlabs_fw import FilterWheel, ThorlabsError

BP = "bandpass"
ND = "nd"
SHORTPASS = "shortpass"
LONGPASS = "longpass"
SPECIAL = "special"

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
        self._special_index: Dict[str, List[Tuple[str, int, str, dict]]] = (
            {}
        )  # type -> [(wheel,slot,name,meta), ...]
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
        """Populate band‑pass, ND, and special filter lookup tables for **connected** wheels."""
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

                # ----- special filters (shortpass, longpass, etc.) ---
                elif f_type in (SHORTPASS, LONGPASS):
                    if SHORTPASS not in self._special_index:
                        self._special_index[SHORTPASS] = []
                    if LONGPASS not in self._special_index:
                        self._special_index[LONGPASS] = []

                    self._special_index[f_type].append((wkey, slot, name, f_meta))

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

    def _find_blocking_filters(self, wl_nm: float) -> List[Tuple[str, int, str]]:
        """
        Find special filters (shortpass/longpass) that should be activated
        to block out-of-band light for the given wavelength.

        Returns list of (wheel_key, slot, name) tuples.
        """
        blocking = []

        # Shortpass: activates when bandpass wavelength < cutoff
        for wkey, slot, name, meta in self._special_index.get(SHORTPASS, []):
            cutoff = meta.get("wavelength")
            if cutoff and wl_nm < cutoff:
                blocking.append((wkey, slot, name))

        # Longpass: activates when bandpass wavelength > cutoff
        for wkey, slot, name, meta in self._special_index.get(LONGPASS, []):
            cutoff = meta.get("wavelength")
            if cutoff and wl_nm > cutoff:
                blocking.append((wkey, slot, name))

        return blocking

    def select_bandpass(
        self,
        wl_nm: float,
        *,
        tol_nm: float = 2.0,
        block_out_of_band: bool = True,
        block: bool = True,
    ):
        """
        Select a bandpass filter and optionally coordinate special filters.

        Parameters
        ----------
        wl_nm : float
            Target wavelength in nanometers
        tol_nm : float
            Tolerance for wavelength matching (default: 2.0)
        block_out_of_band : bool
            If True, activate shortpass/longpass filters as needed (default: True)
        block : bool
            If True, block until movement completes (default: True)
        """
        match = self._nearest_bp(wl_nm, tol_nm)
        if match is None:
            raise KeyError(f"No band‑pass filter near {wl_nm} nm (±{tol_nm})")

        tgt_key, tgt_slot, _ = match

        # First find EMPTY slots for every BP wheel
        empty: Dict[str, int] = {}
        for k, w in self.wheels.items():
            if w.type == ND or not w.is_connected():
                continue
            if w.type == SPECIAL:
                # Special wheels need their EMPTY slot tracked separately
                continue
            for s, n in w.filters.items():
                if str(n).upper() == "EMPTY":
                    empty[k] = s
                    break

        # Move all standard BP wheels
        for k, w in self.wheels.items():
            if not w.is_connected() or w.type == ND:
                continue
            if w.type == SPECIAL:
                continue
            try:
                w.move_to(tgt_slot if k == tgt_key else empty[k], block=block)
            except ThorlabsError as e:
                warnings.warn(f"[FilterRack] {k}: {e}")

        # Handle special filters (shortpass/longpass)
        if block_out_of_band:
            blocking_filters = self._find_blocking_filters(wl_nm)

            # Track which special wheels we're activating
            special_wheels_used = set()

            for wkey, slot, name in blocking_filters:
                wheel = self.wheels.get(wkey)
                if wheel and wheel.is_connected():
                    try:
                        wheel.move_to(slot, block=block)
                        special_wheels_used.add(wkey)
                    except ThorlabsError as e:
                        warnings.warn(f"[FilterRack] special filter {name}: {e}")

            # Move unused special wheels to EMPTY
            for k, w in self.wheels.items():
                if (
                    w.type == SPECIAL
                    and w.is_connected()
                    and k not in special_wheels_used
                ):
                    empty_slot = None
                    for s, n in w.filters.items():
                        if str(n).upper() == "EMPTY":
                            empty_slot = s
                            break
                    if empty_slot is not None:
                        try:
                            w.move_to(empty_slot, block=block)
                        except ThorlabsError as e:
                            warnings.warn(f"[FilterRack] {k} to empty: {e}")

    # ------------------------------------------------------------------
    # ND selection
    # ------------------------------------------------------------------
    def select_nd(
        self, od: float | str | None, *, tol: float = 0.05, block: bool = True
    ):
        """
        Place ND filter with optical density *od* into the beam, or move to EMPTY.

        Parameters
        ----------
        od : float, str, or None
            Optical density value (e.g., 0.5, 1.0, 3.0)
            Special values for no ND filter: None, 0, 0.0, "EMPTY", "empty"
        tol : float
            Tolerance for matching OD values (default: 0.05)
        block : bool
            If True, block until movement completes (default: True)
        """
        # Handle "no ND filter" cases
        if od is None or od == 0 or od == 0.0:
            self._move_nd_to_empty(block=block)
            return

        if isinstance(od, str) and od.upper() == "EMPTY":
            self._move_nd_to_empty(block=block)
            return

        # Normal ND filter selection
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

    def _move_nd_to_empty(self, block: bool = True):
        """Move all ND wheels to their EMPTY position."""
        for k, w in self.wheels.items():
            if w.type != ND or not w.is_connected():
                continue

            # Find EMPTY slot
            empty_slot = None
            for s, n in w.filters.items():
                if str(n).upper() == "EMPTY":
                    empty_slot = s
                    break

            if empty_slot is not None:
                try:
                    w.move_to(empty_slot, block=block)
                except ThorlabsError as e:
                    warnings.warn(f"[FilterRack] {k} to empty: {e}")

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    def wheels_keys(self) -> List[str]:
        return list(self.wheels)

    def filters_for_wheel(self, key: str) -> Dict[int, str]:
        return self.wheels[key].list_filters()

    def available_filters(
        self, filter_type: Optional[str] = None
    ) -> Union[Dict[str, Tuple[str, int]], List[float]]:
        """
        Return available filters on connected wheels.

        Parameters
        ----------
        filter_type : str, optional
            Filter for specific type: 'bandpass', 'nd', 'shortpass', 'longpass'.
            If None, returns all filters with their metadata.
            If 'bandpass', returns sorted list of center wavelengths (nm).
            If 'nd', returns sorted list of optical densities.
            If 'shortpass' or 'longpass', returns list of cutoff wavelengths.

        Returns
        -------
        dict or list
            If filter_type is None: {name: (wheel_key, slot), ...}
            If filter_type is 'bandpass': [wl1, wl2, ...] sorted wavelengths in nm
            If filter_type is 'nd': [od1, od2, ...] sorted optical densities
            If filter_type is 'shortpass' or 'longpass': [cutoff1, cutoff2, ...]

        Examples
        --------
        >>> rack.available_filters()  # All filters
        {'FBH 1050-10': ('fw1', 2), '400 nm': ('fw3', 3), ...}

        >>> rack.available_filters('bandpass')  # Just wavelengths
        [296.7, 400.0, 500.0, 550.0, ..., 1650.0]

        >>> rack.available_filters('nd')  # Just OD values
        [0.5, 1.0, 3.0, 4.0, 5.0]
        """
        if filter_type is None:
            # Return all filters with metadata
            out: Dict[str, Tuple[str, int]] = {}
            for k in self.online:
                w = self.wheels[k]
                for slot, name in w.filters.items():
                    if str(name).upper() != "EMPTY":
                        out[str(name)] = (k, slot)
            return out

        # Return specific filter type
        ftype = filter_type.lower()

        if ftype == BP or ftype == "bp":
            # Return sorted list of bandpass center wavelengths
            return sorted(self._wl_index.keys())

        elif ftype == ND:
            # Return sorted list of ND optical densities
            return sorted(self._nd_index.keys())

        elif ftype in (SHORTPASS, LONGPASS):
            # Return list of cutoff wavelengths for special filters
            cutoffs = []
            for _, _, _, meta in self._special_index.get(ftype, []):
                if "wavelength" in meta:
                    cutoffs.append(float(meta["wavelength"]))
            return sorted(cutoffs)

        else:
            raise ValueError(
                f"Invalid filter_type '{filter_type}'. "
                f"Must be one of: 'bandpass', 'nd', 'shortpass', 'longpass', or None"
            )

    def status(self) -> Dict[str, dict]:
        """Per‑wheel status dict."""
        return {k: w.status() for k, w in self.wheels.items()}

    # ------------------------------------------------------------------
    def close(self):
        for w in self.wheels.values():
            w.disconnect()
