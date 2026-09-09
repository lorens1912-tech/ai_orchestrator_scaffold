from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_agentpro_storage(tmp_path, monkeypatch) -> Path:
    storage_root = tmp_path / "agentpro_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))
    return storage_root
