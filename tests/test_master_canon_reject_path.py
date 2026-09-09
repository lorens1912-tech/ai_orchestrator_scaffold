from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
import app.p20_core.runtime as runtime_module


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_precanon_reject_returns_master_canon_and_writes_audit(monkeypatch) -> None:
    client = TestClient(app)

    def fake_run_canon_check(text, canon_snapshot, scene_ref):
        return {
            "ok": False,
            "issues": ["forced_precanon_reject_for_regression_test"],
            "scene_ref": scene_ref,
        }

    def fake_canon_blocks(report):
        return True

    monkeypatch.setattr(runtime_module, "run_canon_check", fake_run_canon_check)
    monkeypatch.setattr(runtime_module, "canon_blocks", fake_canon_blocks)

    response = client.post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "payload": {
                "book_id": "novel_runtime_test",
                "text": "To jest test wymuszonego REJECT na etapie pre-canon.",
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data.get("ok") is False, data
    assert data.get("status") == "error", data

    quality_gate = data.get("quality_gate") or {}
    assert quality_gate.get("decision") == "REJECT", data

    mc = data.get("master_canon") or {}
    assert mc.get("scope") == "MASTER_CANON_AGENTPRO", data
    assert mc.get("path") == "MASTER_CANON_AGENTPRO.md", data
    assert isinstance(mc.get("sha256"), str) and len(mc["sha256"]) == 64, data

    run_id = data.get("run_id")
    assert isinstance(run_id, str) and run_id.startswith("run_"), data

    audit_path = Path("runs") / run_id / "audit.json"
    assert audit_path.exists(), f"Brak audit.json: {audit_path}"

    audit = _read_json(audit_path)
    assert audit.get("run_id") == run_id, audit
    assert audit.get("book_id") == "novel_runtime_test", audit
    assert audit.get("decision") == "REJECT", audit

    audit_mc = audit.get("master_canon") or {}
    assert audit_mc.get("scope") == "MASTER_CANON_AGENTPRO", audit
    assert audit_mc.get("path") == "MASTER_CANON_AGENTPRO.md", audit
    assert audit_mc.get("sha256") == mc.get("sha256"), audit
