# imagedaemon/utils/serialization.py

from typing import Any

import numpy as np


def sanitize_for_serialization(obj: Any) -> Any:
    """
    Recursively convert numpy types to native Python types so that
    the result contains only dicts, lists, str, int, float, bool, None.

    - np.generic → Python scalar via .item()
    - np.ndarray → Python list (nested)
    - tuple → list
    - dict  → new dict with sanitized values
    """
    # 1) numpy scalar (int64, float32, bool_, …)
    if isinstance(obj, np.generic):
        return obj.item()

    # 2) numpy array → nested list
    if isinstance(obj, np.ndarray):
        # for 1-D or higher, tolist() is fine (it always yields nested lists)
        return sanitize_for_serialization(obj.tolist())

    # 3) dict → sanitize each value
    if isinstance(obj, dict):
        return {
            # keep the same keys, sanitize the values
            key: sanitize_for_serialization(val)
            for key, val in obj.items()
        }

    # 4) list or tuple → sanitize each element, return a list
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_serialization(val) for val in obj]

    # 5) plain Python: int, float, str, bool, None
    # leave untouched
    return obj
