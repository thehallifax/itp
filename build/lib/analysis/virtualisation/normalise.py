"""Provider state normalization and safe capacity helpers."""
from __future__ import annotations


POWER_STATES = {
    "poweredon": "running", "running": "running", "up": "running",
    "poweredoff": "stopped", "off": "stopped", "stopped": "stopped",
    "paused": "paused", "suspended": "suspended", "saved": "saved",
}


def power_state(value):
    return POWER_STATES.get(str(value or "").replace("_", "").replace("-", "").casefold(),
                            "unknown")


def percentage(used, total):
    if not isinstance(used, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
        return None
    return round(used * 100 / total, 3)
