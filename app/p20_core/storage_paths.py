from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_storage_root() -> Path:
    configured = os.environ.get("AGENTPRO_STORAGE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return _repo_root()


def get_books_root() -> Path:
    return get_storage_root() / "books"


def get_runs_root() -> Path:
    return get_storage_root() / "runs"


def get_audit_root() -> Path:
    return get_storage_root() / "audit"


def get_novel_runs_root() -> Path:
    return get_storage_root() / "novel_runs"


def get_projects_root() -> Path:
    return get_storage_root() / "projects"


def get_series_root() -> Path:
    return get_storage_root() / "series"


def get_system_db_path() -> Path:
    return get_storage_root() / "agentpro_system.db"


__all__ = [
    "get_storage_root",
    "get_books_root",
    "get_runs_root",
    "get_audit_root",
    "get_novel_runs_root",
    "get_projects_root",
    "get_series_root",
    "get_system_db_path",
]
