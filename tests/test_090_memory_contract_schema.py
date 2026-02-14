import pytest

from app.contracts.memory_contract import validate_memory_snapshot, snapshot_to_dict

def test_memory_contract_minimal_valid():
    payload = {
        "project_id": "proj-001",
        "book_id": "book-001",
        "version": 1,
        "facts": [
            {
                "fact_id": "f-001",
                "type": "character",
                "text": "Bohater ma bliznę na lewym policzku.",
                "source_step": "010_WRITE",
                "confidence": 0.95
            }
        ],
        "unresolved_threads": ["Kim jest informator?"],
        "updated_at": "2026-02-14T20:00:00+01:00"
    }

    snap = validate_memory_snapshot(payload)
    out = snapshot_to_dict(snap)

    assert out["project_id"] == "proj-001"
    assert out["book_id"] == "book-001"
    assert out["facts"][0]["type"] == "character"
    assert out["unresolved_threads"][0] == "Kim jest informator?"

def test_memory_contract_invalid_fact_type_fails():
    payload = {
        "project_id": "proj-001",
        "book_id": "book-001",
        "version": 1,
        "facts": [
            {
                "fact_id": "f-002",
                "type": "INVALID_TYPE",
                "text": "To ma się wywalić.",
                "confidence": 0.5
            }
        ],
        "unresolved_threads": [],
        "updated_at": "2026-02-14T20:00:00+01:00"
    }

    with pytest.raises(Exception):
        validate_memory_snapshot(payload)
