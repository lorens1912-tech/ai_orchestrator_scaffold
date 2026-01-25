from __future__ import annotations

import re
from typing import Optional

_DATE_SUFFIX_RE = re.compile(r"^(?P<base>.+)-\d{4}-\d{2}-\d{2}$")

def model_family(model: Optional[str]) -> Optional[str]:
    """
    Normalizuje nazwę modelu do 'rodziny'.
    Przykład:
      gpt-5-2025-08-07 -> gpt-5
      gpt-4.1-mini -> gpt-4.1-mini
    """
    if not model:
        return None
    m = model.strip()
    if not m:
        return None

    mo = _DATE_SUFFIX_RE.match(m)
    if mo:
        return mo.group("base")
    return m
