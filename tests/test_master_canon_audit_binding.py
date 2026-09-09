from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_audit_doc(run_id: str) -> tuple[Path, dict]:
    run_dir = Path("runs") / run_id
    assert run_dir.exists(), f"Brak katalogu run: {run_dir}"

    matches: list[tuple[Path, dict]] = []
    for path in run_dir.rglob("*.json"):
        try:
            data = _read_json(path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("run_id") != run_id:
            continue
        if "decision" not in data:
            continue
        if "canon_snapshot_path" not in data:
            continue
        matches.append((path, data))

    assert matches, f"Nie znaleziono audit JSON dla run_id={run_id} w {run_dir}"

    matches.sort(key=lambda item: (0 if "audit" in item[0].name.lower() else 1, str(item[0])))
    return matches[0]


def test_agent_step_writes_master_canon_into_audit() -> None:
    client = TestClient(app)
    book_id = "novel_runtime_test"
    ensure_test_book_bible(book_id)

    response = client.post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "text": "Regresyjny test spięcia master canonu z audit artifact.",
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("ok") is True, data

    mc = data.get("master_canon") or {}
    assert mc.get("scope") == "MASTER_CANON_AGENTPRO", data
    assert mc.get("path") == "MASTER_CANON_AGENTPRO.md", data

    audit_path, audit = _find_audit_doc(data["run_id"])

    audit_mc = audit.get("master_canon") or {}
    assert audit.get("book_id") == "novel_runtime_test", (audit_path, audit)
    assert audit_mc.get("scope") == "MASTER_CANON_AGENTPRO", (audit_path, audit)
    assert audit_mc.get("path") == "MASTER_CANON_AGENTPRO.md", (audit_path, audit)
    assert audit_mc.get("sha256") == mc.get("sha256"), (audit_path, audit)
