from __future__ import annotations

from pathlib import Path

from app.p20_core.storage_paths import (
    get_audit_root,
    get_books_root,
    get_novel_runs_root,
    get_runs_root,
    get_storage_root,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_storage_paths_default_to_repo_roots(monkeypatch) -> None:
    monkeypatch.delenv("AGENTPRO_STORAGE_ROOT", raising=False)

    assert get_storage_root() == REPO_ROOT
    assert get_books_root() == REPO_ROOT / "books"
    assert get_runs_root() == REPO_ROOT / "runs"
    assert get_audit_root() == REPO_ROOT / "audit"
    assert get_novel_runs_root() == REPO_ROOT / "novel_runs"


def test_storage_paths_follow_env_without_creating_directories(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "agentpro"
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    expected_paths = [
        storage_root,
        storage_root / "books",
        storage_root / "runs",
        storage_root / "audit",
        storage_root / "novel_runs",
    ]
    assert all(not path.exists() for path in expected_paths)

    assert get_storage_root() == storage_root.resolve()
    assert get_books_root() == storage_root.resolve() / "books"
    assert get_runs_root() == storage_root.resolve() / "runs"
    assert get_audit_root() == storage_root.resolve() / "audit"
    assert get_novel_runs_root() == storage_root.resolve() / "novel_runs"

    assert all(not path.exists() for path in expected_paths)


def test_storage_paths_follow_env_changes_in_same_process(tmp_path, monkeypatch) -> None:
    first_root = tmp_path / "first-agentpro"
    second_root = tmp_path / "second-agentpro"

    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(first_root))
    assert get_books_root() == first_root.resolve() / "books"

    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(second_root))
    assert get_storage_root() == second_root.resolve()
    assert get_books_root() == second_root.resolve() / "books"
    assert get_runs_root() == second_root.resolve() / "runs"


def test_storage_paths_are_absolute_and_deterministic(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "agentpro"
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    first = (
        get_storage_root(),
        get_books_root(),
        get_runs_root(),
        get_audit_root(),
        get_novel_runs_root(),
    )
    second = (
        get_storage_root(),
        get_books_root(),
        get_runs_root(),
        get_audit_root(),
        get_novel_runs_root(),
    )

    assert first == second
    assert all(path.is_absolute() for path in first)
