from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Optional, Set, Tuple

from app.runtime_overrides import get_forced_model


def _norm(m: Optional[str]) -> Optional[str]:
    if not m:
        return None
    m = m.strip()
    if not m:
        return None
    # lekkie aliasy (opcjonalnie)
    aliases = {
        "gpt5": "gpt-5",
        "gpt_5": "gpt-5",
        "gpt-5.0": "gpt-5",
    }
    return aliases.get(m, m)


def _allowlist() -> Optional[Set[str]]:
    raw = os.getenv("MODEL_ALLOWLIST", "").strip()
    if not raw:
        return None
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return set(items) if items else None


def _default_model() -> str:
    return os.getenv("MODEL_DEFAULT", "gpt-4.1-mini").strip() or "gpt-4.1-mini"


def _policy_mode() -> str:
    # STRICT => model spoza allowlist => 422
    # PERMISSIVE => fallback do default
    return (os.getenv("MODEL_POLICY_MODE", "PERMISSIVE") or "PERMISSIVE").strip().upper()


@dataclass(frozen=True)
class ModelDecision:
    requested_model: Optional[str]
    effective_model: str
    source: str
    allowlist_ok: bool
    note: str

    def to_dict(self):
        return asdict(self)


def resolve_model(
    requested_model: Optional[str],
    header_model: Optional[str] = None,
    preset_model: Optional[str] = None,
) -> ModelDecision:
    """
    Precedencja:
      body > header > preset > force_file > env_force > default
    """
    requested_model = _norm(requested_model)
    header_model = _norm(header_model)
    preset_model = _norm(preset_model)

    env_force = _norm(os.getenv("WRITE_MODEL_FORCE"))
    force_file = _norm(get_forced_model())
    default = _norm(_default_model()) or "gpt-4.1-mini"

    chosen: Optional[str] = None
    source = ""
    req = requested_model

    if requested_model:
        chosen = requested_model
        source = "body"
    elif header_model:
        chosen = header_model
        source = "header"
    elif preset_model:
        chosen = preset_model
        source = "preset"
    elif force_file:
        chosen = force_file
        source = "force_file"
    elif env_force:
        chosen = env_force
        source = "env_force"
    else:
        chosen = default
        source = "default"

    allow = _allowlist()
    if allow is None:
        return ModelDecision(
            requested_model=req,
            effective_model=chosen,
            source=source,
            allowlist_ok=True,
            note="allowlist=OFF",
        )

    if chosen in allow:
        return ModelDecision(
            requested_model=req,
            effective_model=chosen,
            source=source,
            allowlist_ok=True,
            note="allowlist=OK",
        )

    # chosen spoza allowlist
    mode = _policy_mode()
    if mode == "STRICT":
        return ModelDecision(
            requested_model=req,
            effective_model=default,
            source="blocked",
            allowlist_ok=False,
            note=f"BLOCKED({chosen}) => STRICT",
        )

    # PERMISSIVE => fallback
    fallback = default if default in allow else sorted(list(allow))[0]
    return ModelDecision(
        requested_model=req,
        effective_model=fallback,
        source="blocked",
        allowlist_ok=False,
        note=f"BLOCKED({chosen}) => fallback({fallback})",
    )
