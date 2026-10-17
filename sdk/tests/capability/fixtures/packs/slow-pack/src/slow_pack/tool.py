"""Sleep, then answer."""

from __future__ import annotations

import time


def run(seconds: float) -> dict:
    time.sleep(float(seconds))
    return {"slept": float(seconds)}
