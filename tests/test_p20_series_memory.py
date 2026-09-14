from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.p20_core.domain_records import (
    CharacterState,
    FactRecord,
    KnowledgeEvent,
    SetupRecord,
    ThreadRecord,
)
from app.p20_core.project_repository import (
    SERIES_DB_SCHEMA_VERSION,
    ProjectRepository,
    SeriesAccessContext,
    SeriesAccessError,
    SeriesRepository,
    SeriesStorageError,
    StorageResolver,
    StorageScope,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord,
    SeriesMemoryContractError,
    SeriesStateKind,
    SeriesStateRecord,
    VolumeClosingSnapshot,
    VolumeSnapshotItem,
    VolumeTransferTarget,
)


STAMP = "2026-09-14T12:00:00Z"
LATER_STAMP = "2026-09-14T13:00:00Z"


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        conn.close()


def _create_series_v1_db(db_path: Path, series_id: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE schema_version ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE series_identity ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), series_id TEXT NOT NULL, "
            "schema_version INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE series_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 1)")
        conn.execute(
            "INSERT INTO series_identity (id, series_id, schema_version) "
            "VALUES (1, ?, 1)",
            (series_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _member(
    suffix: str,
    *,
    series_suffix: str | None = None,
) -> tuple[SeriesRepository, SeriesAccessContext, SeriesMembershipRecord]:
    project_id = f"PROJ-{suffix}"
    book_id = f"BOOK-{suffix}"
    series_id = f"SERIES-{series_suffix or suffix}"
    access = SeriesAccessContext.bind(project_id, series_id)
    repo = SeriesRepository(StorageResolver().resolve_series(series_id))
    membership = SeriesMembershipRecord(
        series_id=series_id,
        project_id=project_id,
        book_id=book_id,
        source_ref=f"projects/{project_id}/series-membership.json",
        version=1,
        created_at=STAMP,
    )
    repo.register_member(access, membership)
    return repo, access, membership


def _fact(project_id: str, suffix: str = "persistent", **overrides) -> FactRecord:
    data = {
        "fact_id": f"FACT-{suffix}",
        "project_id": project_id,
        "subject_id": "CHAR-ada",
        "predicate": "remains",
        "object_type": "state",
        "object_id": None,
        "object_value": "guardian of the archive",
        "reality_status": "PROJECT_CANON",
        "verification_status": "VERIFIED",
        "confidence": 1.0,
        "frozen": False,
        "author_locked": False,
        "valid_from": "SCENE-001",
        "valid_to": None,
        "established_event_id": "EVENT-volume-close",
        "established_scene_id": "SCENE-volume-close",
        "source_artifact_ref": "books/volume/chapters/chapter_100.json",
        "source_refs": ["SCENE-volume-close#fact-1"],
        "canon_version": 1,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return FactRecord(**data)


def _character(project_id: str, **overrides) -> CharacterState:
    data = {
        "state_id": "CONTEXT-ada-volume-close",
        "project_id": project_id,
        "character_id": "CHAR-ada",
        "source_scene_id": "SCENE-volume-close",
        "source_event_id": "EVENT-volume-close",
        "source_artifact_ref": "books/volume/chapters/chapter_100.json",
        "state_payload": {
            "alive": True,
            "location_id": "PLACE-archive",
            "physical_state": "healed",
            "emotional_state": "resolved",
        },
        "valid_from": "SCENE-volume-close",
        "valid_to": None,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return CharacterState(**data)


def _knowledge(project_id: str) -> KnowledgeEvent:
    return KnowledgeEvent(
        knowledge_event_id="KNOWLEDGE-ada-archive",
        project_id=project_id,
        character_id="CHAR-ada",
        fact_id="FACT-persistent",
        event_id=None,
        knowledge_status="KNOWS",
        learned_at_scene_id="SCENE-volume-close",
        learned_at_event_id="EVENT-volume-close",
        learned_from_character_id=None,
        source_ref="SCENE-volume-close#knowledge-1",
        valid_from="SCENE-volume-close",
        valid_to=None,
        confidence=1.0,
        version=1,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _thread(project_id: str, *, status: str = "OPEN") -> ThreadRecord:
    return ThreadRecord(
        thread_id="THREAD-archive-secret",
        project_id=project_id,
        name="Archive secret",
        description="The origin of the archive remains unresolved.",
        importance=9,
        opened_scene_id="SCENE-volume-close",
        closed_scene_id="SCENE-volume-close" if status == "CLOSED" else None,
        status=status,
        payoff_required=status != "CLOSED",
        target_payoff="Reveal the founder." if status != "CLOSED" else None,
        actual_payoff_ref=None,
        deliberately_left_reason=None,
        version=1,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _setup(project_id: str) -> SetupRecord:
    return SetupRecord(
        setup_id="SETUP-founder-key",
        project_id=project_id,
        created_scene_id="SCENE-volume-close",
        description="Ada keeps the founder's key.",
        importance=8,
        expected_payoff="The key opens the founder's vault.",
        target_range="BOOK-next",
        actual_payoff_scene_id=None,
        status="OPEN",
        version=1,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _series_state(
    access: SeriesAccessContext,
    book_id: str,
    record,
    *,
    kind: SeriesStateKind = SeriesStateKind.MEMORY,
    operation_id: str = "series-state-operation",
) -> SeriesStateRecord:
    return SeriesStateRecord.from_project_record(
        access=access,
        source_book_id=book_id,
        record=record,
        state_kind=kind,
        source_ref="books/volume/chapters/chapter_100.json",
        provenance_refs=["SCENE-volume-close#approved-memory"],
        transfer_reason="Required continuity for the next volume.",
        operation_id=operation_id,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _item(
    record,
    *,
    target: VolumeTransferTarget = VolumeTransferTarget.SNAPSHOT_ONLY,
    reason: str | None = None,
) -> VolumeSnapshotItem:
    return VolumeSnapshotItem.from_project_record(
        record,
        source_ref="books/volume/chapters/chapter_100.json",
        provenance_refs=["SCENE-volume-close#approved-memory"],
        transfer_target=target,
        transfer_reason=reason,
    )


def _snapshot(
    access: SeriesAccessContext,
    book_id: str,
    items,
    *,
    snapshot_id: str = "volume-closing-snapshot",
    operation_id: str = "volume-closing-operation",
    created_at: str = STAMP,
    source_state_version: int = 1,
) -> VolumeClosingSnapshot:
    return VolumeClosingSnapshot(
        snapshot_id=snapshot_id,
        operation_id=operation_id,
        series_id=access.series_id,
        project_id=access.project_id,
        book_id=book_id,
        source_state_version=source_state_version,
        items=items,
        created_at=created_at,
    )


def test_series_schema_v2_uses_controlled_migration(isolated_agentpro_storage) -> None:
    series_id = "SERIES-gap012-migration"
    repo = SeriesRepository(StorageResolver().resolve_series(series_id))
    _create_series_v1_db(repo.db_path, series_id)

    with pytest.raises(SeriesStorageError, match="controlled migration"):
        repo.initialize()

    status = repo.migrate_schema()
    repo.initialize()

    assert status.current_version == SERIES_DB_SCHEMA_VERSION
    assert {
        "series_memberships",
        "series_state_records",
        "volume_closing_snapshots",
        "series_operations",
    }.issubset(_table_names(repo.db_path))


def test_series_canon_record_is_created_and_read_by_member(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-canon")
    record = _series_state(
        access,
        str(membership.book_id),
        _fact(access.project_id),
        kind=SeriesStateKind.CANON,
        operation_id="create-series-canon",
    )

    saved = repo.save_series_canon(access, record)

    assert saved.scope == StorageScope.series(access.series_id)
    assert repo.list_series_canon(access) == (record,)
    assert repo.list_series_memory(access) == ()


def test_unregistered_project_cannot_read_series_state(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-owner")
    repo.save_series_memory(
        access,
        _series_state(access, str(membership.book_id), _fact(access.project_id)),
    )
    outsider = SeriesAccessContext.bind("PROJ-gap012-outsider", access.series_id)

    with pytest.raises(SeriesAccessError, match="not a registered member"):
        repo.list_series_memory(outsider)


def test_series_memory_reuses_project_structured_record(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-memory")
    fact = _fact(access.project_id)
    record = _series_state(access, str(membership.book_id), fact)

    repo.save_series_memory(access, record)
    saved = repo.list_series_memory(access)[0]

    assert saved.record_type == fact.memory_record_type
    assert saved.record_id == fact.record_id
    assert saved.state == fact.to_dict()


def test_series_memory_does_not_leak_between_series(isolated_agentpro_storage) -> None:
    repo_a, access_a, member_a = _member("gap012-a", series_suffix="gap012-series-a")
    repo_b, access_b, _ = _member("gap012-b", series_suffix="gap012-series-b")
    repo_a.save_series_memory(
        access_a,
        _series_state(access_a, str(member_a.book_id), _fact(access_a.project_id)),
    )

    assert repo_b.list_series_memory(access_b) == ()
    with pytest.raises(SeriesAccessError, match="requested series"):
        repo_b.list_series_memory(access_a)


def test_volume_closing_snapshot_is_persisted(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-snapshot")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(_character(access.project_id))],
    )

    assert repo.close_volume(access, snapshot) == snapshot
    assert repo.get_latest_volume_snapshot(access) == snapshot
    assert repo.list_volume_snapshots(access) == (snapshot,)


def test_snapshot_semantic_identity_is_deterministic(isolated_agentpro_storage) -> None:
    _, access, membership = _member("gap012-deterministic")
    fact_item = _item(_fact(access.project_id, "z-fact"))
    character_item = _item(_character(access.project_id))
    first = _snapshot(
        access,
        str(membership.book_id),
        [fact_item, character_item],
        snapshot_id="snapshot-first",
        operation_id="operation-first",
        created_at=STAMP,
    )
    second = _snapshot(
        access,
        str(membership.book_id),
        [character_item, fact_item],
        snapshot_id="snapshot-second",
        operation_id="operation-second",
        created_at=LATER_STAMP,
    )

    assert first.semantic_payload() == second.semantic_payload()
    assert first.semantic_identity == second.semantic_identity


def test_snapshot_items_require_stable_ids_and_provenance(isolated_agentpro_storage) -> None:
    with pytest.raises(SeriesMemoryContractError, match="stable domain id"):
        VolumeSnapshotItem(
            record_type="FACT",
            record_id="not-stable",
            source_version=1,
            source_ref="scene",
            provenance_refs=["scene#fact"],
            state={},
        )
    with pytest.raises(SeriesMemoryContractError, match="provenance_refs"):
        VolumeSnapshotItem(
            record_type="FACT",
            record_id="FACT-stable",
            source_version=1,
            source_ref="scene",
            provenance_refs=[],
            state={},
        )


def test_character_state_transfers_to_series_memory(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-character")
    character = _character(access.project_id)
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(character, target=VolumeTransferTarget.SERIES_MEMORY, reason="Lead continues.")],
    )

    repo.close_volume(access, snapshot)

    saved = repo.list_series_memory(access)[0]
    assert saved.record_type == "CHARACTER_STATE"
    assert saved.state["state_payload"]["location_id"] == "PLACE-archive"


def test_relationship_state_can_be_carried_by_stable_reference(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-relationship")
    item = VolumeSnapshotItem(
        record_type="RELATIONSHIP",
        record_id="REL-ada-bea",
        source_version=3,
        source_ref="SCENE-volume-close#relationship",
        provenance_refs=["REL-ada-bea@v3"],
        state={"from_id": "CHAR-ada", "to_id": "CHAR-bea", "status": "allies"},
        transfer_target=VolumeTransferTarget.SERIES_MEMORY,
        transfer_reason="Relationship continues in the next volume.",
    )

    repo.close_volume(access, _snapshot(access, str(membership.book_id), [item]))

    assert repo.list_series_memory(access)[0].state["status"] == "allies"


def test_knowledge_state_transfers_to_series_memory(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-knowledge")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(
            _knowledge(access.project_id),
            target=VolumeTransferTarget.SERIES_MEMORY,
            reason="Character knowledge persists.",
        )],
    )

    repo.close_volume(access, snapshot)

    saved = repo.list_series_memory(access)[0]
    assert saved.record_type == "KNOWLEDGE_EVENT"
    assert saved.state["knowledge_status"] == "KNOWS"


def test_open_thread_can_be_explicitly_transferred(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-open-thread")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(
            _thread(access.project_id),
            target=VolumeTransferTarget.SERIES_MEMORY,
            reason="Thread continues in the next volume.",
        )],
    )

    repo.close_volume(access, snapshot)

    assert repo.list_series_memory(access)[0].record_type == "THREAD"


def test_closed_thread_cannot_be_transferred_to_series_state(isolated_agentpro_storage) -> None:
    with pytest.raises(SeriesMemoryContractError, match="closed thread"):
        _item(
            _thread("PROJ-gap012-closed-thread", status="CLOSED"),
            target=VolumeTransferTarget.SERIES_MEMORY,
            reason="Should not transfer.",
        )


def test_setup_requiring_payoff_can_transfer_to_series_memory(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-setup")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(
            _setup(access.project_id),
            target=VolumeTransferTarget.SERIES_MEMORY,
            reason="Setup requires payoff in a later volume.",
        )],
    )

    repo.close_volume(access, snapshot)

    assert repo.list_series_memory(access)[0].state["status"] == "OPEN"


def test_project_canon_is_not_automatically_series_canon(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-no-project-promotion")
    project_repo = ProjectRepository(
        StorageResolver().resolve_project(access.project_id, book_id=str(membership.book_id))
    )
    with project_repo.domain_transaction() as tx:
        tx.add_structured_memory_record(_fact(access.project_id))

    assert project_repo.list_structured_memory_records("FACT")
    assert repo.list_series_canon(access) == ()


def test_snapshot_only_item_does_not_promote_to_series_canon_or_memory(
    isolated_agentpro_storage,
) -> None:
    repo, access, membership = _member("gap012-no-snapshot-promotion")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(_fact(access.project_id))],
    )

    repo.close_volume(access, snapshot)

    assert repo.list_volume_snapshots(access) == (snapshot,)
    assert repo.list_series_memory(access) == ()
    assert repo.list_series_canon(access) == ()


def test_retry_does_not_duplicate_snapshot_or_series_memory(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-retry")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(
            _fact(access.project_id),
            target=VolumeTransferTarget.SERIES_MEMORY,
            reason="Persistent fact.",
        )],
    )

    first = repo.close_volume(access, snapshot)
    second = repo.close_volume(access, snapshot)

    assert first == second
    assert len(repo.list_volume_snapshots(access)) == 1
    assert len(repo.list_series_memory(access)) == 1


def test_cross_project_series_state_write_is_denied(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-cross-project")
    other_fact = _fact("PROJ-gap012-other")

    with pytest.raises(SeriesAccessError, match="project scope"):
        _series_state(access, str(membership.book_id), other_fact)

    assert repo.list_series_memory(access) == ()


def test_series_access_context_is_required_for_reads_and_writes(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-access-type")
    record = _series_state(access, str(membership.book_id), _fact(access.project_id))

    with pytest.raises(SeriesAccessError, match="SeriesAccessContext"):
        repo.save_series_memory(None, record)
    with pytest.raises(SeriesAccessError, match="SeriesAccessContext"):
        repo.list_series_memory(None)


def test_one_series_cannot_read_another_series_snapshot(isolated_agentpro_storage) -> None:
    repo_a, access_a, membership_a = _member("gap012-snapshot-a", series_suffix="gap012-snap-a")
    _, access_b, _ = _member("gap012-snapshot-b", series_suffix="gap012-snap-b")
    repo_a.close_volume(
        access_a,
        _snapshot(access_a, str(membership_a.book_id), [_item(_fact(access_a.project_id))]),
    )

    with pytest.raises(SeriesAccessError, match="requested series"):
        repo_a.get_latest_volume_snapshot(access_b)


def test_project_and_series_databases_keep_separate_responsibilities(
    isolated_agentpro_storage,
) -> None:
    repo, access, membership = _member("gap012-db-boundary")
    project_repo = ProjectRepository(
        StorageResolver().resolve_project(access.project_id, book_id=str(membership.book_id))
    )
    with project_repo.domain_transaction() as tx:
        tx.add_structured_memory_record(_fact(access.project_id))
    repo.save_series_memory(
        access,
        _series_state(access, str(membership.book_id), _fact(access.project_id)),
    )

    assert "project_structured_memory_records" in _table_names(project_repo.db_path)
    assert "series_state_records" not in _table_names(project_repo.db_path)
    assert "series_state_records" in _table_names(repo.db_path)
    assert "project_structured_memory_records" not in _table_names(repo.db_path)


def test_reopened_repository_reads_durable_snapshot(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-reopen")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [_item(_character(access.project_id))],
    )
    repo.close_volume(access, snapshot)

    reopened = SeriesRepository(StorageResolver().resolve_series(access.series_id))

    assert reopened.get_latest_volume_snapshot(access) == snapshot


def test_series_memory_is_not_available_to_independent_project(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-no-global")
    repo.save_series_memory(
        access,
        _series_state(access, str(membership.book_id), _fact(access.project_id)),
    )
    independent = SeriesAccessContext.bind("PROJ-independent", access.series_id)

    with pytest.raises(SeriesAccessError, match="not a registered member"):
        repo.get_opening_state(independent)


def test_gap012_does_not_create_second_storage_or_memory_path(
    isolated_agentpro_storage,
) -> None:
    repo, access, membership = _member("gap012-one-storage")
    repo.save_series_memory(
        access,
        _series_state(access, str(membership.book_id), _fact(access.project_id)),
    )

    assert repo.db_path == (
        isolated_agentpro_storage / "series" / access.series_id / "series.db"
    )
    assert not (isolated_agentpro_storage / "series_memory").exists()
    assert not (isolated_agentpro_storage / "series_canon").exists()
    assert not (isolated_agentpro_storage / "global_memory").exists()


def test_next_volume_member_receives_series_opening_state(isolated_agentpro_storage) -> None:
    repo, access_one, membership_one = _member("gap012-volume-one", series_suffix="gap012-saga")
    snapshot = _snapshot(
        access_one,
        str(membership_one.book_id),
        [_item(
            _fact(access_one.project_id),
            target=VolumeTransferTarget.SERIES_MEMORY,
            reason="Fact persists into volume two.",
        )],
    )
    repo.close_volume(access_one, snapshot)
    access_two = SeriesAccessContext.bind("PROJ-gap012-volume-two", access_one.series_id)
    repo.register_member(
        access_two,
        SeriesMembershipRecord(
            series_id=access_one.series_id,
            project_id=access_two.project_id,
            book_id="BOOK-gap012-volume-two",
            source_ref="projects/PROJ-gap012-volume-two/series-membership.json",
            version=1,
            created_at=STAMP,
        ),
    )

    opening = repo.get_opening_state(access_two)

    assert opening.project_id.value == access_two.project_id
    assert opening.latest_snapshot == snapshot
    assert opening.series_memory[0].record_id.value == "FACT-persistent"


def test_domain_mutation_guard_protects_series_memory(isolated_agentpro_storage) -> None:
    repo, access, membership = _member("gap012-guard")
    frozen = _fact(access.project_id, frozen=True)
    repo.save_series_memory(
        access,
        _series_state(
            access,
            str(membership.book_id),
            frozen,
            operation_id="guard-create",
        ),
    )
    changed = frozen.with_updates(version=2, object_value="changed protected fact")

    with pytest.raises(SeriesStorageError, match="FROZEN_RECORD"):
        repo.save_series_memory(
            access,
            _series_state(
                access,
                str(membership.book_id),
                changed,
                operation_id="guard-update",
            ),
        )


def test_snapshot_and_transfers_roll_back_as_one_series_transaction(
    isolated_agentpro_storage,
) -> None:
    repo, access, membership = _member("gap012-atomic")
    frozen = _fact(access.project_id, "z-protected", frozen=True)
    repo.save_series_memory(
        access,
        _series_state(
            access,
            str(membership.book_id),
            frozen,
            operation_id="atomic-protected-create",
        ),
    )
    new_fact = _fact(access.project_id, "a-new")
    blocked_update = frozen.with_updates(version=2, object_value="blocked update")
    snapshot = _snapshot(
        access,
        str(membership.book_id),
        [
            _item(
                new_fact,
                target=VolumeTransferTarget.SERIES_MEMORY,
                reason="New carry state.",
            ),
            _item(
                blocked_update,
                target=VolumeTransferTarget.SERIES_MEMORY,
                reason="Protected carry state.",
            ),
        ],
    )

    with pytest.raises(SeriesStorageError, match="FROZEN_RECORD"):
        repo.close_volume(access, snapshot)

    assert repo.list_volume_snapshots(access) == ()
    assert [str(record.record_id) for record in repo.list_series_memory(access)] == [
        "FACT-z-protected"
    ]
