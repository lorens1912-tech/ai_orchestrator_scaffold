
import os
import re
from typing import Any, Dict

import yaml

CFG_PATH = "config.yml"


def _default_cfg() -> Dict[str, Any]:
    return {
        "safety": {
            "health": {
                "enabled": True,
                "risk_terms": [],
                "disclaimer": "Informacyjne, nie medyczne.",
            }
        }
    }


def _load_cfg() -> Dict[str, Any]:
    if not os.path.exists(CFG_PATH):
        return _default_cfg()

    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return _default_cfg()
        return data
    except Exception:
        # config może być uszkodzony — nie wywalaj całego systemu
        return _default_cfg()


def _term_hit(text: str, term: str) -> bool:
    t = (term or "").strip()
    if not t:
        return False

    # jeśli termin ma spacje lub znaki specjalne, szukamy jako frazy (case-insensitive)
    if re.search(r"\s|[-_/]", t):
        return re.search(re.escape(t), text, re.IGNORECASE) is not None

    # pojedyncze słowo: granice słowa
    return re.search(rf"\b{re.escape(t)}\b", text, re.IGNORECASE) is not None


def guard_health(text: str) -> str:
    cfg = _load_cfg()
    health = (cfg.get("safety") or {}).get("health") or {}

    if not health.get("enabled", True):
        return text

    terms = health.get("risk_terms") or []
    disclaimer = health.get("disclaimer", "Informacyjne, nie medyczne.")

    try:
        hit = any(_term_hit(text, t) for t in terms)
    except Exception:
        hit = False

    if hit:
        return f"{text}\n\n[Uwaga] {disclaimer}"

    return text
