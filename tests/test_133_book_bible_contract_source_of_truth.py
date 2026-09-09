from __future__ import annotations

import inspect
import json
from pathlib import Path
from uuid import uuid4

import app.main as main_module
from app.p20_core.book_bible_contract import (
    REQUIRED_BOOK_BIBLE_KEYS,
    REQUIRED_NON_EMPTY_LIST_KEYS,
    REQUIRED_NON_EMPTY_STRING_KEYS,
    REQUIRED_OBJECT_LIST_KEYS,
    build_valid_book_bible_payload,
    load_book_bible_or_raise,
    validate_book_bible_payload_or_raise,
)
from app.p20_core.book_bible_test_helper import ensure_test_book_bible


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_book_bible_contract_module_is_single_source_of_truth_for_helper_and_runtime(
    isolated_agentpro_storage,
):
    book_id = f"book_bible_contract_source_{uuid4().hex[:8]}"
    repo_book_dir = REPO_ROOT / "books" / book_id

    assert not repo_book_dir.exists(), repo_book_dir

    helper_path = ensure_test_book_bible(book_id)
    assert helper_path.resolve().is_relative_to(isolated_agentpro_storage.resolve())

    helper_payload = json.loads(helper_path.read_text(encoding="utf-8"))

    built_payload = build_valid_book_bible_payload(book_id)
    validated_payload = validate_book_bible_payload_or_raise(
        helper_payload,
        expected_book_id=book_id,
    )
    loaded_payload = load_book_bible_or_raise(book_id)

    assert set(REQUIRED_BOOK_BIBLE_KEYS) == set(built_payload.keys())
    assert set(REQUIRED_BOOK_BIBLE_KEYS) == set(helper_payload.keys())
    assert validated_payload == built_payload
    assert loaded_payload["book_id"] == book_id
    assert loaded_payload["_contract"]["path"].endswith(f"{book_id}/book_bible.json")
    assert not repo_book_dir.exists(), repo_book_dir


def test_runtime_and_helper_reference_same_contract_module():
    helper_source = inspect.getsource(ensure_test_book_bible)
    runtime_source = inspect.getsource(main_module.agent_step)

    assert "build_valid_book_bible_payload" in helper_source
    assert "load_book_bible_or_raise" in runtime_source


def test_each_required_key_is_enforced_by_shared_validator():
    book_id = f"book_bible_missing_key_{uuid4().hex[:8]}"
    full_payload = build_valid_book_bible_payload(book_id)

    for missing_key in REQUIRED_BOOK_BIBLE_KEYS:
        payload = dict(full_payload)
        payload.pop(missing_key)

        try:
            validate_book_bible_payload_or_raise(payload, expected_book_id=book_id)
        except Exception as exc:
            assert missing_key in str(exc)
        else:
            raise AssertionError(f"Validator accepted payload missing key: {missing_key}")


def test_semantic_rules_are_enforced_by_shared_validator():
    book_id = f"book_bible_semantic_{uuid4().hex[:8]}"
    base = build_valid_book_bible_payload(book_id)

    for key in REQUIRED_NON_EMPTY_STRING_KEYS:
        payload = dict(base)
        payload[key] = "   "
        try:
            validate_book_bible_payload_or_raise(payload, expected_book_id=book_id)
        except Exception as exc:
            assert key in str(exc)
            assert "non-empty string" in str(exc)
        else:
            raise AssertionError(f"Validator accepted blank string for key: {key}")

    for key in REQUIRED_NON_EMPTY_LIST_KEYS:
        payload = dict(base)
        payload[key] = []
        try:
            validate_book_bible_payload_or_raise(payload, expected_book_id=book_id)
        except Exception as exc:
            assert key in str(exc)
            assert "non-empty list" in str(exc)
        else:
            raise AssertionError(f"Validator accepted empty list for key: {key}")


def test_structural_id_rules_are_enforced_by_shared_validator():
    book_id = f"book_bible_structural_{uuid4().hex[:8]}"
    base = build_valid_book_bible_payload(book_id)

    for key in REQUIRED_OBJECT_LIST_KEYS:
        payload = dict(base)
        payload[key] = [{"text": "X"}]
        try:
            validate_book_bible_payload_or_raise(payload, expected_book_id=book_id)
        except Exception as exc:
            assert key in str(exc)
            assert "id" in str(exc)
        else:
            raise AssertionError(f"Validator accepted list item without id for key: {key}")
