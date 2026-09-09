from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root


REQUIRED_PATHS = {
    "/health",
    "/config/validate",
    "/agent/step",
    "/canon/rebuild",
}

NOVEL_CONTRACT_KEYS = {
    "artifact_count",
    "quality_gate",
    "pre_canon_check",
    "post_canon_check",
    "chapter_path",
    "run_state",
    "canon_memory",
    "canon_snapshot_path",
}

FORBIDDEN_IMPORT_PATTERNS = (
    r"from\s+.*agent_api\s+import\s+",
    r"import\s+.*agent_api",
    r"from\s+.*compat.*\s+import\s+",
    r"import\s+.*compat",
    r"from\s+.*legacy.*\s+import\s+",
    r"import\s+.*legacy",
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _business_paths() -> set[str]:
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        if path.startswith(("/openapi", "/docs", "/redoc")):
            continue
        paths.add(path)
    return paths


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _storage_path(public_path: str) -> Path:
    return get_storage_root() / public_path


def _repo_book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _repo_run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def _snapshot_book_id(snapshot: dict) -> str:
    value = snapshot.get("book_id")
    if isinstance(value, str) and value:
        return value

    meta = snapshot.get("_meta") or {}
    value = meta.get("book_id")
    if isinstance(value, str) and value:
        return value

    return ""


def _snapshot_engine(snapshot: dict) -> str:
    value = snapshot.get("engine")
    if isinstance(value, str) and value:
        return value

    meta = snapshot.get("_meta") or {}
    value = meta.get("engine")
    if isinstance(value, str) and value:
        return value

    return ""


def _chapter_id_from_obj(obj) -> str:
    if isinstance(obj, str):
        if obj.startswith("chapter_"):
            return obj
        return Path(obj).stem if "chapter_" in obj else ""

    if isinstance(obj, dict):
        chapter_id = obj.get("chapter_id")
        if isinstance(chapter_id, str) and chapter_id.startswith("chapter_"):
            return chapter_id

        chapter_path = obj.get("chapter_path") or obj.get("path")
        if isinstance(chapter_path, str) and chapter_path:
            stem = Path(chapter_path).stem
            if stem.startswith("chapter_"):
                return stem

    return ""


def _snapshot_last_accepted(snapshot: dict) -> str:
    direct = snapshot.get("last_accepted_chapter")
    chapter_id = _chapter_id_from_obj(direct)
    if chapter_id:
        return chapter_id

    chapters = snapshot.get("chapters") or []
    if isinstance(chapters, list) and chapters:
        chapter_id = _chapter_id_from_obj(chapters[-1])
        if chapter_id:
            return chapter_id

    return ""


def test_app_main_exposes_required_routes() -> None:
    business_paths = _business_paths()
    missing = REQUIRED_PATHS - business_paths
    assert not missing, f"Brak wymaganych endpointów w app.main: {sorted(missing)}"


def test_app_main_is_the_owner_of_the_agent_step_contract() -> None:
    main_source = inspect.getsource(main_module)

    assert "/agent/step" in main_source, "app.main nie rejestruje /agent/step"
    assert "run_agent_step" in main_source, "app.main nie spina /agent/step z P20 runtime"

    for pattern in FORBIDDEN_IMPORT_PATTERNS:
        assert re.search(pattern, main_source, flags=re.IGNORECASE) is None, (
            f"W app.main wykryto niedozwolony import ownera/bridge: {pattern}"
        )


def test_agent_step_returns_p20_novel_contract_and_updates_canon_snapshot(
    isolated_agentpro_storage,
) -> None:
    client = TestClient(app)
    book_id = f"novel_runtime_test_{uuid4().hex[:8]}"
    assert not _repo_book_dir(book_id).exists()
    ensure_test_book_bible(book_id)

    response = client.post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "text": (
                    "Regresyjny zapis sceny. Bohater odkrywa, że wiadomość była próbą lojalności, "
                    "a prawdziwy ślad prowadzi do archiwum pod ziemią."
                ),
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data.get("ok") is True, data

    missing_contract = {
        key for key in NOVEL_CONTRACT_KEYS if key not in data or data.get(key) in (None, "")
    }
    assert not missing_contract, (
        f"Brak pól kontraktu novel P20: {sorted(missing_contract)} | response={data}"
    )

    pre_check = data.get("pre_canon_check") or {}
    post_check = data.get("post_canon_check") or {}
    quality_gate = data.get("quality_gate") or {}
    run_state = data.get("run_state") or {}
    canon_memory = data.get("canon_memory") or {}

    assert pre_check.get("ok") is True, pre_check
    assert post_check.get("ok") is True, post_check
    assert str(quality_gate.get("decision", "")).upper() == "ACCEPT", quality_gate
    assert run_state.get("book_id") == book_id, run_state

    assert data["chapter_path"].startswith("books/"), data
    chapter_path = _storage_path(data["chapter_path"])
    assert chapter_path.exists(), f"chapter_path nie istnieje: {chapter_path}"

    chapter_json = _read_json(chapter_path)
    assert chapter_json.get("book_id") == book_id, chapter_json
    assert str(chapter_json.get("chapter_id", "")).startswith("chapter_"), chapter_json
    assert str(chapter_json.get("run_id", "")).startswith("run_"), chapter_json

    assert data["canon_snapshot_path"].startswith("books/"), data
    snapshot_path = _storage_path(data["canon_snapshot_path"])
    assert snapshot_path.exists(), f"canon_snapshot_path nie istnieje: {snapshot_path}"

    snapshot_json = _read_json(snapshot_path)

    assert _snapshot_book_id(snapshot_json) == book_id, snapshot_json
    assert int(snapshot_json.get("chapter_count", 0)) >= 1, snapshot_json

    last_accepted = _snapshot_last_accepted(snapshot_json)
    assert last_accepted.startswith("chapter_"), snapshot_json
    assert chapter_path.stem == last_accepted, (
        f"Snapshot kanonu nie wskazuje na zapisany chapter | "
        f"chapter_path={chapter_path.stem} | last_accepted={last_accepted}"
    )

    engine = str(
        data.get("engine")
        or canon_memory.get("engine")
        or _snapshot_engine(snapshot_json)
        or ""
    )
    if engine:
        assert engine == "P20.0-novel-core", (
            f"Nieprawidłowy engine w odpowiedzi/pamięci/snapshot: {engine}"
        )

    assert not _repo_book_dir(book_id).exists()
    assert not _repo_run_dir(data["run_id"]).exists()
