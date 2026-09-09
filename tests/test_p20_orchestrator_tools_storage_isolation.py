from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app import canon_store
from app.orchestrator_stub import execute_stub
from app.tools import TOOLS


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_book_bible(book_root: Path) -> None:
    book_root.mkdir(parents=True, exist_ok=True)
    (book_root / "book_bible.json").write_text(
        json.dumps(
            {
                "continuity_rules": {
                    "flag_unknown_entities": True,
                    "force_unknown_entities": False,
                },
                "canon": {
                    "characters": [
                        {
                            "name": "Alicja",
                            "aliases": [],
                        }
                    ]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_canon(book_root: Path, tx_id: str, amount: int) -> None:
    book_root.mkdir(parents=True, exist_ok=True)
    (book_root / "canon.json").write_text(
        json.dumps(
            {
                "timeline": [],
                "world_facts": [],
                "character_facts": {},
                "decisions": [],
                "ledger": [{"id": tx_id, "amount": amount, "scene_ref": "tmp-scene"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_execute_stub_and_active_tools_use_configured_storage_root_without_cwd_scope(
    tmp_path,
    monkeypatch,
) -> None:
    storage_root = (tmp_path / "agentpro").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))
    monkeypatch.delenv("UNIQUENESS_REGISTRY_PATH", raising=False)

    book_id = f"storage_tools_{uuid4().hex}"
    run_id = f"storage_tools_run_{uuid4().hex}"
    unique_marker = f"storage-tools-marker-{uuid4().hex}"
    input_text = f"Alicja spotkała Boruta przy archiwum. {unique_marker}"

    real_book_dir = REPO_ROOT / "books" / book_id
    real_run_dir = REPO_ROOT / "runs" / run_id
    real_uniqueness_registry = REPO_ROOT / "runs" / "_tmp" / "uniqueness_registry.jsonl"
    assert not real_book_dir.exists()
    assert not real_run_dir.exists()

    book_root = storage_root / "books" / book_id
    _write_book_bible(book_root)

    artifact_paths = execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=["WRITE", "CONTINUITY", "UNIQUENESS"],
        payload={"input": input_text, "book_id": book_id},
        steps=None,
    )

    assert artifact_paths == [
        f"runs/{run_id}/steps/001_WRITE.json",
        f"runs/{run_id}/steps/002_CONTINUITY.json",
        f"runs/{run_id}/steps/003_UNIQUENESS.json",
    ]
    for artifact_path in artifact_paths:
        assert not Path(artifact_path).is_absolute()
        assert artifact_path.startswith("runs/")
        assert (storage_root / artifact_path).exists()

    assert (storage_root / "runs" / run_id / "state.json").exists()
    assert (storage_root / "books" / book_id / "draft" / "latest.txt").exists()

    continuity_doc = json.loads(
        (storage_root / f"runs/{run_id}/steps/002_CONTINUITY.json").read_text(encoding="utf-8")
    )
    continuity_payload = continuity_doc["result"]["payload"]
    assert "Boruta" in continuity_payload["UNKNOWN_ENTITIES"]

    uniqueness_registry = storage_root / "runs" / "_tmp" / "uniqueness_registry.jsonl"
    assert uniqueness_registry.exists()
    assert unique_marker in uniqueness_registry.read_text(encoding="utf-8")

    assert not real_book_dir.exists()
    assert not real_run_dir.exists()
    if real_uniqueness_registry.exists():
        assert unique_marker not in real_uniqueness_registry.read_text(encoding="utf-8")


def test_active_canon_check_uses_configured_books_root_without_cwd_or_repo_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    storage_root = (tmp_path / "agentpro").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    book_id = f"storage_canon_{uuid4().hex}"
    tx_id = "tx_storagealpha"
    real_book_dir = REPO_ROOT / "books" / book_id
    assert not real_book_dir.exists()
    assert not (real_book_dir / "canon.json").exists()
    assert not (real_book_dir / "memory" / "canon.json").exists()

    _write_canon(storage_root / "books" / book_id, tx_id=tx_id, amount=123)

    cwd_root = tmp_path / "cwd"
    cwd_root.mkdir()
    monkeypatch.chdir(cwd_root)

    result = TOOLS["CANON_CHECK"](
        {
            "book_id": book_id,
            "text": f"Transakcja {tx_id} ma kwote 999.",
            "scene_ref": "tmp-scene",
        }
    )
    payload = result["payload"]
    assert payload["ok"] is False
    assert {
        "type": "ledger_amount_mismatch",
        "tx_id": tx_id,
        "expected": 123.0,
        "found": 999.0,
        "scene_ref": "tmp-scene",
    } in payload["issues"]

    assert not real_book_dir.exists()
    assert not (real_book_dir / "canon.json").exists()
    assert not (real_book_dir / "memory" / "canon.json").exists()


def test_active_canon_check_does_not_fallback_to_repo_root_books(
    tmp_path,
    monkeypatch,
) -> None:
    storage_root = (tmp_path / "agentpro").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    book_id = f"storage_canon_repo_only_{uuid4().hex}"
    tx_id = "tx_storagebeta"
    real_book_dir = REPO_ROOT / "books" / book_id
    assert not real_book_dir.exists()
    assert not (real_book_dir / "canon.json").exists()
    assert not (real_book_dir / "memory" / "canon.json").exists()

    fake_repo_root = tmp_path / "fake_repo_root"
    _write_canon(fake_repo_root / "books" / book_id, tx_id=tx_id, amount=321)
    assert not (storage_root / "books" / book_id / "canon.json").exists()
    monkeypatch.setattr(canon_store, "_project_root", lambda: fake_repo_root)

    cwd_root = tmp_path / "cwd_repo_only"
    cwd_root.mkdir()
    monkeypatch.chdir(cwd_root)

    result = TOOLS["CANON_CHECK"](
        {
            "book_id": book_id,
            "text": f"Transakcja {tx_id} ma kwote 999.",
            "scene_ref": "repo-only-scene",
        }
    )
    payload = result["payload"]
    assert payload["ok"] is False
    assert {
        "type": "ledger_unknown_tx",
        "tx_id": tx_id,
        "scene_ref": "repo-only-scene",
    } in payload["issues"]
    assert not any(
        issue.get("type") == "ledger_amount_mismatch" and issue.get("expected") == 321.0
        for issue in payload["issues"]
    )
    assert not (storage_root / "books" / book_id / "canon.json").exists()

    assert not real_book_dir.exists()
    assert not (real_book_dir / "canon.json").exists()
    assert not (real_book_dir / "memory" / "canon.json").exists()
