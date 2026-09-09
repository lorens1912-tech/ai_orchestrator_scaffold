from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_contract import (
    BOOK_BIBLE_CONTRACT_VERSION,
    build_valid_book_bible_payload,
)

client = TestClient(app)


def _write_book_bible(book_id: str, payload: dict) -> Path:
    root = Path("books") / book_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "book_bible.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _post_write(book_id: str):
    body = {
        "mode": "WRITE",
        "payload": {
            "book_id": book_id,
            "text": "Scena testowa. Bohater otwiera sejf i odkrywa, że wiadomość była pułapką.",
        },
    }
    return client.post("/agent/step", json=body)


def test_agent_step_rejects_write_when_book_bible_missing():
    book_id = f"novel_bible_missing_{uuid4().hex[:8]}"
    book_root = Path("books") / book_id
    book_root.mkdir(parents=True, exist_ok=True)

    response = _post_write(book_id)
    assert response.status_code in (200, 400, 422), response.text
    data = response.json()

    assert data.get("ok") is False or data.get("decision") == "REJECT", data
    reason_blob = json.dumps(data, ensure_ascii=False)
    assert "book_bible" in reason_blob.lower(), data
    assert not (book_root / "chapters").exists()


def test_agent_step_rejects_write_when_book_bible_invalid_json():
    book_id = f"novel_bible_badjson_{uuid4().hex[:8]}"
    book_root = Path("books") / book_id
    book_root.mkdir(parents=True, exist_ok=True)
    (book_root / "book_bible.json").write_text("{ bad json", encoding="utf-8")

    response = _post_write(book_id)
    assert response.status_code in (200, 400, 422), response.text
    data = response.json()

    assert data.get("ok") is False or data.get("decision") == "REJECT", data
    reason_blob = json.dumps(data, ensure_ascii=False)
    assert "invalid" in reason_blob.lower() or "json" in reason_blob.lower(), data
    assert not (book_root / "chapters").exists()


def test_agent_step_rejects_write_when_book_bible_missing_required_fields():
    book_id = f"novel_bible_missingfields_{uuid4().hex[:8]}"
    payload = build_valid_book_bible_payload(book_id)
    payload.pop("facts")
    _write_book_bible(book_id, payload)

    response = _post_write(book_id)
    assert response.status_code in (200, 400, 422), response.text
    data = response.json()

    assert data.get("ok") is False or data.get("decision") == "REJECT", data
    reason_blob = json.dumps(data, ensure_ascii=False)
    assert "missing keys" in reason_blob.lower() or "book_bible" in reason_blob.lower(), data
    assert not (Path("books") / book_id / "chapters").exists()


def test_agent_step_rejects_write_when_book_bible_has_empty_semantic_structure():
    book_id = f"novel_bible_emptysemantic_{uuid4().hex[:8]}"
    payload = build_valid_book_bible_payload(book_id)
    payload["title"] = "   "
    payload["genre"] = ""
    payload["premise"] = " "
    payload["acts"] = []
    payload["characters"] = []
    payload["timeline"] = []
    payload["facts"] = []
    payload["decisions"] = []
    _write_book_bible(book_id, payload)

    response = _post_write(book_id)
    assert response.status_code in (200, 400, 422), response.text
    data = response.json()

    assert data.get("ok") is False or data.get("decision") == "REJECT", data
    reason_blob = json.dumps(data, ensure_ascii=False)
    assert "non-empty string" in reason_blob.lower() or "non-empty list" in reason_blob.lower(), data
    assert not (Path("books") / book_id / "chapters").exists()


def test_agent_step_rejects_write_when_book_bible_list_items_have_no_id():
    book_id = f"novel_bible_missingid_{uuid4().hex[:8]}"
    payload = build_valid_book_bible_payload(book_id)
    payload["acts"] = [{"summary": "Act I"}]
    payload["characters"] = [{"name": "Hero"}]
    payload["timeline"] = [{"event": "Start"}]
    payload["facts"] = [{"text": "Fact"}]
    payload["decisions"] = [{"text": "Decision"}]
    _write_book_bible(book_id, payload)

    response = _post_write(book_id)
    assert response.status_code in (200, 400, 422), response.text
    data = response.json()

    assert data.get("ok") is False or data.get("decision") == "REJECT", data
    reason_blob = json.dumps(data, ensure_ascii=False)
    assert "id" in reason_blob.lower(), data
    assert not (Path("books") / book_id / "chapters").exists()


def test_agent_step_accepts_write_when_book_bible_valid():
    book_id = f"novel_bible_valid_{uuid4().hex[:8]}"
    _write_book_bible(book_id, build_valid_book_bible_payload(book_id))

    response = _post_write(book_id)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data.get("ok") is True, data
    assert data.get("quality_gate", {}).get("decision") == "ACCEPT", data

    chapter_path = data.get("chapter_path")
    assert chapter_path, data
    chapter_file = Path(chapter_path)
    assert chapter_file.exists(), data

    chapter_doc = json.loads(chapter_file.read_text(encoding="utf-8"))
    assert chapter_doc.get("book_bible_path", "").endswith(f"{book_id}/book_bible.json"), chapter_doc
    assert chapter_doc.get("book_bible_sha256"), chapter_doc
    assert chapter_doc.get("book_bible_contract_version") == BOOK_BIBLE_CONTRACT_VERSION, chapter_doc
    assert chapter_doc.get("canon_validation", {}).get("source") == "book_bible.json", chapter_doc


def test_agent_step_binds_same_book_bible_sha_to_run_state_audit_and_chapter():
    book_id = f"novel_bible_binding_{uuid4().hex[:8]}"
    bible_path = _write_book_bible(book_id, build_valid_book_bible_payload(book_id))
    expected_sha = hashlib.sha256(
        bible_path.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()

    response = _post_write(book_id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("ok") is True, data

    run_state = data.get("run_state") or {}
    assert run_state.get("book_bible", {}).get("sha256") == expected_sha, run_state

    chapter_path = Path(data["chapter_path"])
    chapter_doc = json.loads(chapter_path.read_text(encoding="utf-8"))
    assert chapter_doc.get("book_bible_sha256") == expected_sha, chapter_doc

    step_paths = data.get("artifact_paths") or data.get("artifacts") or []
    assert step_paths, data
    step_doc = json.loads(Path(step_paths[0]).read_text(encoding="utf-8"))
    assert step_doc.get("book_bible", {}).get("sha256") == expected_sha, step_doc


def test_agent_step_binds_same_book_bible_contract_version_to_runtime_chapter_audit_and_run_state():
    book_id = f"novel_bible_contractver_{uuid4().hex[:8]}"
    _write_book_bible(book_id, build_valid_book_bible_payload(book_id))

    response = _post_write(book_id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("ok") is True, data

    runtime_contract = data.get("book_bible") or {}
    assert runtime_contract.get("contract_version") == BOOK_BIBLE_CONTRACT_VERSION, runtime_contract

    run_state = data.get("run_state") or {}
    assert run_state.get("book_bible", {}).get("contract_version") == BOOK_BIBLE_CONTRACT_VERSION, run_state

    chapter_path = Path(data["chapter_path"])
    chapter_doc = json.loads(chapter_path.read_text(encoding="utf-8"))
    assert chapter_doc.get("book_bible_contract_version") == BOOK_BIBLE_CONTRACT_VERSION, chapter_doc

    step_paths = data.get("artifact_paths") or data.get("artifacts") or []
    assert step_paths, data
    step_doc = json.loads(Path(step_paths[0]).read_text(encoding="utf-8"))
    assert step_doc.get("book_bible", {}).get("contract_version") == BOOK_BIBLE_CONTRACT_VERSION, step_doc
