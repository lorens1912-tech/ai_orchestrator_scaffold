from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.p20_core import runtime
from app.p20_core.book_bible_contract import BookBibleContractError
from app.p20_core.contracts import AgentStepRequest


WRITE_CASES = [
    (
        "A_top_level_mode",
        {
            "mode": "WRITE",
            "payload": {"book_id": "x"},
        },
    ),
    (
        "B_top_level_modes",
        {
            "modes": ["WRITE"],
            "payload": {"book_id": "x"},
        },
    ),
    (
        "C_payload_mode",
        {
            "payload": {
                "book_id": "x",
                "mode": "WRITE",
            },
        },
    ),
    (
        "D_payload_modes",
        {
            "payload": {
                "book_id": "x",
                "modes": ["WRITE"],
            },
        },
    ),
    (
        "E_runtime_fallback",
        {
            "payload": {
                "book_id": "x",
            },
        },
    ),
    (
        "F_write_preset",
        {
            "preset": "DEFAULT",
            "payload": {
                "book_id": "x",
            },
        },
    ),
]


@pytest.mark.parametrize(("case_name", "request_body"), WRITE_CASES)
def test_supported_write_shapes_resolve_to_write(case_name: str, request_body: dict) -> None:
    req = AgentStepRequest(**request_body)
    payload = runtime.build_request_payload(req)

    assert runtime.resolve_modes(req, payload) == ["WRITE"], case_name


@pytest.mark.parametrize(("case_name", "request_body"), WRITE_CASES)
def test_supported_write_shapes_require_same_book_bible_guard(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    request_body: dict,
) -> None:
    loader_calls: list[str] = []

    def fake_resolve_resume_run_id(
        book_id: str,
        payload_run_id,
        resume: bool,
        *,
        project_id: str | None = None,
        domain_book_id: str | None = None,
    ) -> str:
        return f"run_{case_name.lower()}"

    def fake_load_book_bible_or_raise(book_id: str):
        loader_calls.append(book_id)
        raise BookBibleContractError("BOOK_BIBLE_MISSING")

    def fail_if_guard_is_bypassed(book_id: str):
        pytest.fail(f"Book Bible guard was bypassed for {case_name}: {book_id}")

    monkeypatch.setattr(runtime, "resolve_resume_run_id", fake_resolve_resume_run_id)
    monkeypatch.setattr(runtime, "load_book_bible_or_raise", fake_load_book_bible_or_raise)
    monkeypatch.setattr(runtime, "ensure_book_dirs", fail_if_guard_is_bypassed)

    response = TestClient(app).post("/agent/step", json=request_body)

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "decision": "REJECT",
        "error": "BOOK_BIBLE_MISSING",
    }
    assert loader_calls == ["x"]


def test_lowercase_write_mode_is_normalized_and_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_body = {
        "mode": "write",
        "payload": {"book_id": "x"},
    }
    req = AgentStepRequest(**request_body)
    payload = runtime.build_request_payload(req)
    loader_calls: list[str] = []

    def fake_load_book_bible_or_raise(book_id: str):
        loader_calls.append(book_id)
        raise BookBibleContractError("BOOK_BIBLE_MISSING")

    monkeypatch.setattr(runtime, "resolve_resume_run_id", lambda *args, **kwargs: "run_lowercase_write")
    monkeypatch.setattr(runtime, "load_book_bible_or_raise", fake_load_book_bible_or_raise)
    monkeypatch.setattr(
        runtime,
        "ensure_book_dirs",
        lambda book_id: pytest.fail(f"Book Bible guard was bypassed: {book_id}"),
    )

    assert runtime.resolve_modes(req, payload) == ["WRITE"]

    response = TestClient(app).post("/agent/step", json=request_body)

    assert response.status_code == 200
    assert response.json()["decision"] == "REJECT"
    assert response.json()["error"] == "BOOK_BIBLE_MISSING"
    assert loader_calls == ["x"]
