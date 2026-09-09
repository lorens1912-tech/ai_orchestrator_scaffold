from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from app.p20_core.storage_paths import get_books_root, get_storage_root

BOOK_BIBLE_CONTRACT_VERSION = "1.0"


class BookBibleContractError(ValueError):
    pass

BOOK_BIBLE_VERSIONING_POLICY: tuple[str, ...] = (
    "Bump BOOK_BIBLE_CONTRACT_VERSION whenever REQUIRED_* contract fields change.",
    "Refresh BOOK_BIBLE_CONTRACT_GUARD_FROZEN_VERSION together with the new version.",
    "Refresh BOOK_BIBLE_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT after intentional required-field changes only.",
    "A change to book_bible required fields without version bump and guard refresh is a contract violation.",
)

REQUIRED_NON_EMPTY_STRING_KEYS = [
    "title",
    "genre",
    "premise",
]

REQUIRED_NON_EMPTY_LIST_KEYS = [
    "acts",
    "characters",
    "timeline",
    "facts",
    "decisions",
]

REQUIRED_OBJECT_LIST_KEYS = list(REQUIRED_NON_EMPTY_LIST_KEYS)
REQUIRED_BOOK_BIBLE_KEYS = ["book_id"] + REQUIRED_NON_EMPTY_STRING_KEYS + REQUIRED_NON_EMPTY_LIST_KEYS
LIST_KEYS_REQUIRING_ID = list(REQUIRED_OBJECT_LIST_KEYS)

BOOK_BIBLE_CONTRACT_GUARD_FROZEN_VERSION = "1.0"
BOOK_BIBLE_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT = "1948d5fc6ff27d688d048fefb8664041dda6466554d3382f47f7621cfe42c71c"


def build_required_contract_fingerprint_payload() -> Dict[str, list[str]]:
    return {
        "required_keys": list(REQUIRED_BOOK_BIBLE_KEYS),
        "required_non_empty_string_keys": list(REQUIRED_NON_EMPTY_STRING_KEYS),
        "required_non_empty_list_keys": list(REQUIRED_NON_EMPTY_LIST_KEYS),
        "required_object_list_keys": list(REQUIRED_OBJECT_LIST_KEYS),
    }


def compute_required_contract_fingerprint() -> str:
    raw = json.dumps(
        build_required_contract_fingerprint_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _non_empty_text(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _non_empty_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0


def _items_are_dicts(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(item, dict) for item in v)


def _items_have_id(v: Any) -> bool:
    if not isinstance(v, list):
        return False
    for item in v:
        if not isinstance(item, dict):
            return False
        if not _non_empty_text(item.get("id")):
            return False
    return True


def build_valid_book_bible_payload(book_id: str) -> Dict[str, Any]:
    return {
        "book_id": book_id,
        "title": f"Test title for {book_id}",
        "genre": "Thriller",
        "premise": "A valid test premise for the novel project.",
        "acts": [{"id": "act_1", "summary": "Opening act."}],
        "characters": [{"id": "char_1", "name": "Hero", "role": "protagonist"}],
        "timeline": [{"id": "tl_1", "event": "Story begins."}],
        "facts": [{"id": "fact_1", "text": "Core canon fact."}],
        "decisions": [{"id": "dec_1", "text": "Canon decision."}],
    }


def validate_book_bible_payload(payload: Dict[str, Any]) -> None:
    validate_book_bible_payload_or_raise(payload)


def validate_book_bible_payload_or_raise(
    payload: Dict[str, Any],
    expected_book_id: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise BookBibleContractError("book_bible must be valid json object")

    for key in REQUIRED_BOOK_BIBLE_KEYS:
        if key not in payload:
            raise BookBibleContractError(f"{key} is required")

    if not _non_empty_text(payload.get("book_id")):
        raise BookBibleContractError("book_id must be a non-empty string")

    for key in REQUIRED_NON_EMPTY_STRING_KEYS:
        if not _non_empty_text(payload.get(key)):
            raise BookBibleContractError(f"{key} must be a non-empty string")

    for key in REQUIRED_NON_EMPTY_LIST_KEYS:
        if not _non_empty_list(payload.get(key)):
            raise BookBibleContractError(f"{key} must be a non-empty list")

    for key in REQUIRED_OBJECT_LIST_KEYS:
        if not _items_are_dicts(payload.get(key)):
            raise BookBibleContractError(f"{key} must be a list of objects")

    for key in LIST_KEYS_REQUIRING_ID:
        if not _items_have_id(payload.get(key)):
            raise BookBibleContractError(f"{key} items must contain id")

    if expected_book_id is not None and "book_id" in payload and str(payload.get("book_id")) != str(expected_book_id):
        raise BookBibleContractError("book_id must match expected_book_id")

    return payload


def _map_validator_error_for_runtime(msg: str) -> str:
    if msg.endswith(" is required"):
        key = msg[:-len(" is required")]
        return f"BOOK_BIBLE_MISSING_FIELD:{key}"
    if "must be a non-empty string" in msg or "must be a non-empty list" in msg:
        return msg
    if "items must contain id" in msg:
        return "BOOK_BIBLE_LIST_ITEM_MISSING_ID"
    return msg


def _storage_relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(get_storage_root().resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def load_book_bible_or_raise(book_id: str) -> Dict[str, Any]:
    if not _non_empty_text(book_id):
        raise BookBibleContractError("BOOK_ID_MISSING")

    bible_path = get_books_root() / str(book_id) / "book_bible.json"

    if not bible_path.exists():
        raise BookBibleContractError("BOOK_BIBLE_MISSING")

    try:
        raw = bible_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except Exception:
        raise BookBibleContractError("BOOK_BIBLE_INVALID_JSON")

    try:
        validate_book_bible_payload_or_raise(payload, expected_book_id=book_id)
    except BookBibleContractError as e:
        raise BookBibleContractError(_map_validator_error_for_runtime(str(e)))

    contract = {
        "path": _storage_relative_path(bible_path),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "contract_version": BOOK_BIBLE_CONTRACT_VERSION,
    }

    out = dict(payload)
    out["path"] = contract["path"]
    out["sha256"] = contract["sha256"]
    out["contract_version"] = contract["contract_version"]
    out["_contract"] = dict(contract)
    return out


def ensure_test_book_bible(book_id: str) -> Dict[str, Any]:
    payload = build_valid_book_bible_payload(book_id)
    book_dir = get_books_root() / str(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    path = book_dir / "book_bible.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": _storage_relative_path(path),
        "payload": payload,
    }


__all__ = [
    "BOOK_BIBLE_CONTRACT_VERSION",
    "BookBibleContractError",
    "BOOK_BIBLE_VERSIONING_POLICY",
    "BOOK_BIBLE_CONTRACT_GUARD_FROZEN_VERSION",
    "BOOK_BIBLE_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT",
    "REQUIRED_NON_EMPTY_STRING_KEYS",
    "REQUIRED_NON_EMPTY_LIST_KEYS",
    "REQUIRED_OBJECT_LIST_KEYS",
    "REQUIRED_BOOK_BIBLE_KEYS",
    "LIST_KEYS_REQUIRING_ID",
    "build_required_contract_fingerprint_payload",
    "compute_required_contract_fingerprint",
    "build_valid_book_bible_payload",
    "validate_book_bible_payload",
    "validate_book_bible_payload_or_raise",
    "load_book_bible_or_raise",
    "ensure_test_book_bible",
]
