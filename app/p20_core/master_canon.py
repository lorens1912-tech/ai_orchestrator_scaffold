from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
MASTER_CANON_PATH = REPO_ROOT / "MASTER_CANON_AGENTPRO.md"
AGENT_IDENTITY_LOCK_PATH = REPO_ROOT / "AGENT_IDENTITY_LOCK.md"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def resolve_master_canon() -> Dict[str, Any]:
    if not MASTER_CANON_PATH.exists():
        raise FileNotFoundError(
            f"Brak nadrzędnego pliku kanonicznego: {MASTER_CANON_PATH}"
        )

    raw_bytes = MASTER_CANON_PATH.read_bytes()
    text = _read_text(MASTER_CANON_PATH)

    return {
        "scope": "MASTER_CANON_AGENTPRO",
        "path": _rel(MASTER_CANON_PATH),
        "sha256": hashlib.sha256(raw_bytes).hexdigest().upper(),
        "text": text,
        "identity_lock_path": None,
        "identity_lock_sha256": None,
    }

