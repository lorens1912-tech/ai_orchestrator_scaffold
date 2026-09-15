from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.p20_core.cross_store_recovery import (
    CrossStoreOperationPlan,
    CrossStoreRecoveryError,
    CrossStoreRecoveryService,
    FaultPoint,
    InjectedRecoveryCrash,
    RecoveryInterventionRequired,
    RecoveryStatus,
)
from app.p20_core.project_repository import (
    ProjectRepository,
    SeriesAccessContext,
    SeriesRepository,
    StorageResolver,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord,
    VolumeClosingSnapshot,
    VolumeSnapshotItem,
    VolumeTransferTarget,
)


STAMP = "2026-09-16T10:00:00Z"


def _repositories(storage_root: Path, suffix: str):
    project_id = f"PROJ-f004-{suffix}"
    book_id = f"BOOK-f004-{suffix}"
    series_id = f"SERIES-f004-{suffix}"
    resolver = StorageResolver(storage_root)
    project = ProjectRepository(
        resolver.resolve_project(project_id, book_id=book_id)
    )
    series = SeriesRepository(resolver.resolve_series(series_id))
    access = SeriesAccessContext.bind(project_id, series_id)
    series.register_member(
        access,
        SeriesMembershipRecord(
            series_id=series_id,
            project_id=project_id,
            book_id=book_id,
            source_ref=f"synthetic/{project_id}/membership.json",
            version=1,
            created_at=STAMP,
        ),
    )
    return project, series, access, book_id


def _plan(
    project: ProjectRepository,
    access: SeriesAccessContext | None,
    book_id: str,
    suffix: str,
    *,
    artifact_bytes: bytes | None = None,
) -> CrossStoreOperationPlan:
    operation_id = f"f004-operation-{suffix}"
    content = artifact_bytes or json.dumps(
        {"operation_id": operation_id, "value": "neutral synthetic artifact"},
        sort_keys=True,
    ).encode("utf-8")
    snapshot = None
    if access is not None:
        item = VolumeSnapshotItem(
            record_type="FACT",
            record_id=f"FACT-f004-{suffix}",
            source_version=1,
            source_ref=f"synthetic/{suffix}/source.json",
            provenance_refs=(f"SYNTHETIC-{suffix}#fact",),
            state={
                "fact_id": f"FACT-f004-{suffix}",
                "project_id": access.project_id,
                "frozen": False,
                "author_locked": False,
            },
            transfer_target=VolumeTransferTarget.SERIES_MEMORY,
            transfer_reason="Synthetic continuity proof.",
        )
        snapshot = VolumeClosingSnapshot(
            snapshot_id=f"f004-snapshot-{suffix}",
            operation_id=operation_id,
            series_id=access.series_id,
            project_id=access.project_id,
            book_id=book_id,
            source_state_version=1,
            items=(item,),
            created_at=STAMP,
        )
    return CrossStoreOperationPlan(
        operation_id=operation_id,
        project_id=project.context.project_id,
        book_id=book_id,
        series_id=None if access is None else access.series_id,
        run_id=f"RUN-f004-{suffix}",
        step_id="STEP-volume-close",
        operation_type="VOLUME_CLOSE_EXPORT" if access else "PROJECT_EXPORT",
        source_ref=f"project_metadata:synthetic-source-{suffix}",
        artifact_relative_path=f"artifacts/f004/{suffix}.json",
        artifact_bytes=content,
        expected_versions={"project_state": 1, "series_state": 1},
        provenance_refs=(f"SYNTHETIC-{suffix}#approved",),
        series_snapshot=snapshot,
    )


def _crash_at(point: FaultPoint):
    def hook(current: FaultPoint) -> None:
        if current == point:
            raise InjectedRecoveryCrash(point.value)

    return hook


def _artifact_path(project: ProjectRepository, plan: CrossStoreOperationPlan) -> Path:
    return project.context.project_root / plan.artifact_relative_path


def _assert_complete(
    project: ProjectRepository,
    series: SeriesRepository | None,
    access: SeriesAccessContext | None,
    plan: CrossStoreOperationPlan,
) -> dict:
    record = project.get_cross_store_operation(plan.operation_id)
    assert record is not None
    assert record["status"] == RecoveryStatus.COMMITTED.value
    assert set(record["completed_writes"]) == set(record["planned_writes"])
    artifact = _artifact_path(project, plan)
    assert artifact.read_bytes() == plan.artifact_bytes
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == plan.artifact_sha256
    audit = json.loads(
        project.get_metadata(f"cross_store_audit.v1:{plan.operation_id}") or "{}"
    )
    assert audit["operation_id"] == plan.operation_id
    assert audit["input_hash"] == plan.input_hash
    assert audit["status"] == RecoveryStatus.COMMITTED.value
    if series is not None and access is not None:
        assert len(series.list_volume_snapshots(access)) == 1
        assert len(series.list_series_memory(access)) == 1
    return record


def test_project_owned_operation_commits_file_and_replays_without_duplicates(
    tmp_path: Path,
) -> None:
    resolver = StorageResolver(tmp_path / "storage")
    project = ProjectRepository(
        resolver.resolve_project("PROJ-f004-project", book_id="BOOK-f004-project")
    )
    plan = _plan(project, None, "BOOK-f004-project", "project")

    first = CrossStoreRecoveryService(project).execute(plan)
    reopened = ProjectRepository(project.context)
    second = CrossStoreRecoveryService(reopened).execute(plan)

    assert first == second
    record = _assert_complete(reopened, None, None, plan)
    assert record["retry_count"] == 0
    assert record["owner_store"] == "project.db"
    assert record["scope"]["series_id"] is None


@pytest.mark.parametrize(
    "fault_point",
    [
        FaultPoint.AFTER_INTENT,
        FaultPoint.AFTER_PROJECT_WRITE,
        FaultPoint.AFTER_ARTIFACT_PREPARE,
        FaultPoint.AFTER_ARTIFACT_COMMIT,
        FaultPoint.AFTER_SERIES_WRITE,
        FaultPoint.AFTER_LOGICAL_COMMIT,
    ],
)
def test_each_crash_boundary_recovers_after_reopen_without_duplicates(
    tmp_path: Path,
    fault_point: FaultPoint,
) -> None:
    suffix = fault_point.value.lower().replace("_", "-")
    project, series, access, book_id = _repositories(tmp_path / suffix, suffix)
    plan = _plan(project, access, book_id, suffix)

    with pytest.raises(InjectedRecoveryCrash):
        CrossStoreRecoveryService(
            project,
            fault_hook=_crash_at(fault_point),
        ).execute(plan)

    reopened_project = ProjectRepository(project.context)
    recovered = CrossStoreRecoveryService(reopened_project).recover_pending()

    assert len(recovered) == 1
    record = _assert_complete(reopened_project, series, access, plan)
    assert record["retry_count"] == 1
    assert [event["kind"] for event in record["lineage"]] == [
        "INITIAL",
        "RECOVERY",
        "FINAL_AUDIT",
    ]


def test_retry_fault_is_durable_and_later_recovery_is_idempotent(tmp_path: Path) -> None:
    project, series, access, book_id = _repositories(tmp_path / "retry", "retry")
    plan = _plan(project, access, book_id, "retry")
    with pytest.raises(InjectedRecoveryCrash):
        CrossStoreRecoveryService(
            project,
            fault_hook=_crash_at(FaultPoint.AFTER_INTENT),
        ).execute(plan)

    with pytest.raises(InjectedRecoveryCrash):
        CrossStoreRecoveryService(
            ProjectRepository(project.context),
            fault_hook=_crash_at(FaultPoint.DURING_RETRY),
        ).execute(plan)

    reopened = ProjectRepository(project.context)
    CrossStoreRecoveryService(reopened).recover(plan.operation_id)
    record = _assert_complete(reopened, series, access, plan)
    assert record["retry_count"] == 2
    assert [event["kind"] for event in record["lineage"]] == [
        "INITIAL",
        "TECHNICAL_RETRY",
        "RECOVERY",
        "FINAL_AUDIT",
    ]


def test_conflicting_operation_identity_is_rejected_without_overwrite(tmp_path: Path) -> None:
    project, _series, access, book_id = _repositories(tmp_path / "identity", "identity")
    plan = _plan(project, access, book_id, "identity")
    with pytest.raises(InjectedRecoveryCrash):
        CrossStoreRecoveryService(
            project,
            fault_hook=_crash_at(FaultPoint.AFTER_INTENT),
        ).execute(plan)
    conflicting = _plan(
        project,
        access,
        book_id,
        "identity",
        artifact_bytes=b"different synthetic artifact",
    )

    with pytest.raises(CrossStoreRecoveryError, match="different cross-store input"):
        CrossStoreRecoveryService(project).execute(conflicting)

    record = project.get_cross_store_operation(plan.operation_id)
    assert record is not None
    assert record["input_hash"] == plan.input_hash
    assert not _artifact_path(project, plan).exists()


def test_unexpected_final_artifact_requires_manual_intervention(tmp_path: Path) -> None:
    project, series, access, book_id = _repositories(tmp_path / "tamper", "tamper")
    plan = _plan(project, access, book_id, "tamper")
    with pytest.raises(InjectedRecoveryCrash):
        CrossStoreRecoveryService(
            project,
            fault_hook=_crash_at(FaultPoint.AFTER_ARTIFACT_COMMIT),
        ).execute(plan)
    _artifact_path(project, plan).write_bytes(b"ambiguous external content")

    reopened = ProjectRepository(project.context)
    with pytest.raises(RecoveryInterventionRequired, match="unexpected hash"):
        CrossStoreRecoveryService(reopened).recover(plan.operation_id)

    record = reopened.get_cross_store_operation(plan.operation_id)
    assert record is not None
    assert record["status"] == RecoveryStatus.NEEDS_INTERVENTION.value
    assert record["last_error_class"] == "RecoveryInterventionRequired"
    assert record["lineage"][-1]["kind"] == "MANUAL_ESCALATION_REQUIRED"
    assert series.list_volume_snapshots(access) == ()


def test_committed_replay_detects_artifact_tamper_without_rewriting_it(
    tmp_path: Path,
) -> None:
    project, series, access, book_id = _repositories(
        tmp_path / "committed-tamper", "committed-tamper"
    )
    plan = _plan(project, access, book_id, "committed-tamper")
    CrossStoreRecoveryService(project).execute(plan)
    artifact = _artifact_path(project, plan)
    artifact.write_bytes(b"external tamper after commit")

    with pytest.raises(RecoveryInterventionRequired, match="missing or has an unexpected hash"):
        CrossStoreRecoveryService(ProjectRepository(project.context)).execute(plan)

    assert artifact.read_bytes() == b"external tamper after commit"
    record = project.get_cross_store_operation(plan.operation_id)
    assert record is not None
    assert record["status"] == RecoveryStatus.NEEDS_INTERVENTION.value
    assert record["lineage"][-1]["kind"] == "MANUAL_ESCALATION_REQUIRED"
    assert len(series.list_volume_snapshots(access)) == 1


def test_missing_series_membership_fails_closed_and_can_resume_after_repair(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "membership"
    resolver = StorageResolver(storage_root)
    project = ProjectRepository(
        resolver.resolve_project("PROJ-f004-membership", book_id="BOOK-f004-membership")
    )
    series = SeriesRepository(resolver.resolve_series("SERIES-f004-membership"))
    access = SeriesAccessContext.bind(
        "PROJ-f004-membership", "SERIES-f004-membership"
    )
    plan = _plan(project, access, "BOOK-f004-membership", "membership")

    with pytest.raises(Exception, match="not a registered member"):
        CrossStoreRecoveryService(project).execute(plan)
    failed = project.get_cross_store_operation(plan.operation_id)
    assert failed is not None
    assert failed["status"] == RecoveryStatus.IN_PROGRESS.value
    assert failed["last_error_class"] == "SeriesAccessError"

    series.register_member(
        access,
        SeriesMembershipRecord(
            series_id=access.series_id,
            project_id=access.project_id,
            book_id="BOOK-f004-membership",
            source_ref="synthetic/membership.json",
            version=1,
            created_at=STAMP,
        ),
    )
    CrossStoreRecoveryService(ProjectRepository(project.context)).recover(
        plan.operation_id
    )
    record = _assert_complete(project, series, access, plan)
    assert [event["kind"] for event in record["lineage"]] == [
        "INITIAL",
        "ERROR",
        "RECOVERY",
        "FINAL_AUDIT",
    ]
    audit = json.loads(
        project.get_metadata(f"cross_store_audit.v1:{plan.operation_id}") or "{}"
    )
    assert audit["lineage"] == record["lineage"]


def test_concurrent_retries_share_one_owner_and_one_effect_set(tmp_path: Path) -> None:
    project, series, access, book_id = _repositories(
        tmp_path / "concurrency", "concurrency"
    )
    plan = _plan(project, access, book_id, "concurrency")

    def execute() -> dict:
        repository = ProjectRepository(project.context)
        return CrossStoreRecoveryService(repository).execute(plan)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _index: execute(), range(6)))

    assert all(result["status"] == RecoveryStatus.COMMITTED.value for result in results)
    record = _assert_complete(project, series, access, plan)
    assert record["owner_store"] == "project.db"
    assert len(series.list_volume_snapshots(access)) == 1
    assert len(series.list_series_memory(access)) == 1
