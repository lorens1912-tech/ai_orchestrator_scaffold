from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_runs_root, get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append((node.lineno, alias.name))
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append((node.lineno, node.module))

    return imported


def _read_json(public_path: str) -> dict:
    path = Path(public_path)
    assert not path.is_absolute(), public_path
    return json.loads((get_storage_root() / path).read_text(encoding="utf-8"))


def test_runtime_static_import_boundary_uses_p20_executor_only() -> None:
    runtime_path = REPO_ROOT / "app" / "p20_core" / "runtime.py"
    imports = _imported_modules(runtime_path)

    assert not [
        f"{runtime_path.name}:{line}: {module}"
        for line, module in imports
        if module == "app.orchestrator_stub" or module.startswith("app.orchestrator_stub.")
    ]
    assert any(module == "app.p20_core.executor" for _line, module in imports)


def test_importing_p20_runtime_does_not_load_orchestrator_stub() -> None:
    code = """
import json
import sys

import app.p20_core.runtime as runtime

print(json.dumps({
    "executor_module": runtime.execute_stub.__module__,
    "orchestrator_loaded": "app.orchestrator_stub" in sys.modules,
}, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == {
        "executor_module": "app.p20_core.executor",
        "orchestrator_loaded": False,
    }


def test_p20_executor_has_single_execute_source() -> None:
    import app.p20_core.executor as executor

    executor_path = REPO_ROOT / "app" / "p20_core" / "executor.py"
    tree = ast.parse(executor_path.read_text(encoding="utf-8"), filename=str(executor_path))
    execute_defs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"execute_p20", "execute_stub"}
    ]

    assert execute_defs == ["execute_p20"]
    assert executor.execute_stub is executor.execute_p20
    assert executor.execute_p20.__module__ == "app.p20_core.executor"


def test_p20_executor_single_mode_artifact_and_book_bible_contract(
    isolated_agentpro_storage,
) -> None:
    book_id = f"gap005_single_{uuid4().hex[:8]}"
    run_id = f"gap005_single_run_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()
    ensure_test_book_bible(book_id)

    response = TestClient(app).post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "run_id": run_id,
                "text": (
                    "GAP-005 scene verifies that P20 writes through its clean executor, "
                    "keeps the Book Bible binding, and preserves public artifact paths."
                ),
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True, data
    assert data["run_id"] == run_id
    assert data["artifact_paths"] == [f"runs/{run_id}/steps/001_WRITE.json"]

    step = _read_json(data["artifact_paths"][0])
    assert step["mode"] == "WRITE"
    assert step["index"] == 1
    assert step["input"]["_team_id"] == "WRITER"
    assert step["input"]["_requested_model"]
    assert step["effective_model_id"] == step["input"]["_requested_model"]
    assert step["effective_policy_id"] == step["input"]["_requested_policy"]
    assert step["book_bible"]["path"].endswith(f"{book_id}/book_bible.json")
    assert data["chapter_path"].startswith("books/")

    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()


def test_p20_executor_preset_sequence_policy_and_quality_parity(
    isolated_agentpro_storage,
) -> None:
    book_id = f"gap005_preset_{uuid4().hex[:8]}"
    run_id = f"gap005_preset_run_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()
    ensure_test_book_bible(book_id)

    response = TestClient(app).post(
        "/agent/step",
        json={
            "book_id": book_id,
            "preset": "ORCH_STANDARD",
            "payload": {
                "book_id": book_id,
                "run_id": run_id,
                "text": "GAP-005 preset sequence parity smoke.",
                "team": "WRITER",
            },
            "resume": False,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True, data
    assert len(data["artifact_paths"]) == 5
    assert data["artifact_paths"] == [
        f"runs/{run_id}/steps/001_PLAN.json",
        f"runs/{run_id}/steps/002_WRITE.json",
        f"runs/{run_id}/steps/003_CRITIC.json",
        f"runs/{run_id}/steps/004_EDIT.json",
        f"runs/{run_id}/steps/005_QUALITY.json",
    ]

    sequence_path = get_runs_root() / run_id / "steps" / "000_SEQUENCE.json"
    assert sequence_path.exists()
    sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
    assert sequence["result"]["payload"]["modes"] == ["PLAN", "WRITE", "CRITIC", "EDIT", "QUALITY"]

    write_step = _read_json(f"runs/{run_id}/steps/002_WRITE.json")
    assert write_step["input"]["_requested_policy"] == "WRITE_POLICY_TEST"
    assert write_step["effective_policy_id"] == "WRITE_POLICY_TEST"

    quality_step = _read_json(f"runs/{run_id}/steps/005_QUALITY.json")
    quality_payload = quality_step["result"]["payload"]
    assert quality_payload["DECISION"] in {"ACCEPT", "REVISE", "REJECT"}
    assert isinstance(quality_payload["BLOCK_PIPELINE"], bool)

    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()


def test_p20_executor_unknown_input_contract_remains_controlled() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    unknown_mode = client.post(
        "/agent/step",
        json={"mode": "NO_SUCH_MODE", "payload": {"book_id": "gap005_unknown_mode"}},
    )
    assert unknown_mode.status_code == 400
    assert "Unknown mode" in unknown_mode.json()["detail"]

    unknown_preset = client.post(
        "/agent/step",
        json={"preset": "NO_SUCH_PRESET", "payload": {"book_id": "gap005_unknown_preset"}},
    )
    assert unknown_preset.status_code == 400
    assert "Unknown preset" in unknown_preset.json()["detail"]
