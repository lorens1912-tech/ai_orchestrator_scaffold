
from __future__ import annotations

from typing import Optional

def roi(initial: float, final: float) -> Optional[float]:
    if initial == 0:
        return None
    return (final - initial) / initial
