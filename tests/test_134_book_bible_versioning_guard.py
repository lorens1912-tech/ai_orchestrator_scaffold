from __future__ import annotations

from app.p20_core.book_bible_contract import (
    BOOK_BIBLE_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT,
    BOOK_BIBLE_CONTRACT_GUARD_FROZEN_VERSION,
    BOOK_BIBLE_CONTRACT_VERSION,
    BOOK_BIBLE_VERSIONING_POLICY,
    REQUIRED_BOOK_BIBLE_KEYS,
    REQUIRED_NON_EMPTY_LIST_KEYS,
    REQUIRED_NON_EMPTY_STRING_KEYS,
    REQUIRED_OBJECT_LIST_KEYS,
    build_required_contract_fingerprint_payload,
    compute_required_contract_fingerprint,
)


def test_book_bible_versioning_policy_is_declared():
    assert BOOK_BIBLE_VERSIONING_POLICY
    joined = " ".join(BOOK_BIBLE_VERSIONING_POLICY).lower()
    assert "bump" in joined
    assert "required_*" in joined
    assert "fingerprint" in joined
    assert "contract_version" in joined


def test_book_bible_required_contract_fingerprint_guard_is_current():
    assert BOOK_BIBLE_CONTRACT_VERSION == BOOK_BIBLE_CONTRACT_GUARD_FROZEN_VERSION

    current_payload = build_required_contract_fingerprint_payload()
    assert current_payload == {
        "required_keys": list(REQUIRED_BOOK_BIBLE_KEYS),
        "required_non_empty_string_keys": list(REQUIRED_NON_EMPTY_STRING_KEYS),
        "required_non_empty_list_keys": list(REQUIRED_NON_EMPTY_LIST_KEYS),
        "required_object_list_keys": list(REQUIRED_OBJECT_LIST_KEYS),
    }

    assert compute_required_contract_fingerprint() == BOOK_BIBLE_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT
