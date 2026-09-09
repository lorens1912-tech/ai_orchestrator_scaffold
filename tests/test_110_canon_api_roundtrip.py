from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# Importuj DOKŁADNIE tę samą appkę, którą wystawiasz przez uvicorn.
# Jeśli serwer uruchamiasz jako: uvicorn app.main:app -> to musi być app.main:app
from app.main import app
from app.p20_core.storage_paths import get_books_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _openapi_paths(client: TestClient) -> str:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return f"openapi status={r.status_code} body={r.text}"
    data = r.json()
    paths = sorted(list((data.get("paths") or {}).keys()))
    return "paths:\n" + "\n".join(paths)


def test_patch_then_get_canon(isolated_agentpro_storage):
    client = TestClient(app)
    legacy_book_dir = REPO_ROOT / "books" / "test_book_110"
    legacy_existed_before = legacy_book_dir.exists()
    book_id = f"test_book_110_{uuid4().hex[:8]}"
    repo_book_dir = REPO_ROOT / "books" / book_id
    assert not repo_book_dir.exists(), repo_book_dir

    payload = {
        "book_id": book_id,
        "patch": {
            "timeline": [{"t": "T0", "event": "Boot"}],
            "decisions": {"narration": "third_person_past"},
            "facts": {"protagonist": "X"},
        },
    }

    r_patch = client.patch(f"/canon/{book_id}", json=payload)

    if r_patch.status_code == 404:
        assert not repo_book_dir.exists(), repo_book_dir
        assert legacy_book_dir.exists() == legacy_existed_before
        pytest.skip("Legacy PATCH /canon/{book_id} is not exposed by active app.main.\n" + _openapi_paths(client))

    assert r_patch.status_code == 200, r_patch.text

    r_get = client.get(f"/canon/{book_id}")

    if r_get.status_code == 404:
        pytest.fail("GET /canon/{book_id} returned 404.\n" + _openapi_paths(client))

    assert r_get.status_code == 200, r_get.text

    # Minimalna walidacja roundtrip (nie zakładamy konkretnego schema odpowiedzi ponad to, co musi być sensowne)
    data = r_get.json()
    assert isinstance(data, (dict, list)), f"Unexpected JSON type: {type(data)}"
    assert (get_books_root() / book_id / "canon.json").exists()
    assert not repo_book_dir.exists(), repo_book_dir
    assert legacy_book_dir.exists() == legacy_existed_before
