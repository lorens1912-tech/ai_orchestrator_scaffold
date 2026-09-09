from __future__ import annotations

import json

import hashlib

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any


PROJECT_TRUTH_CONTRACT = "MASTER_CANON_AGENTPRO"
DEFAULT_MASTER_CANON_FILENAME = "MASTER_CANON_AGENTPRO.md"


PROJECT_TRUTH_CONTRACT_VERSION = "1.0"

PROJECT_TRUTH_VERSIONING_POLICY: tuple[str, ...] = (
    "Bump PROJECT_TRUTH_CONTRACT_VERSION whenever PROJECT_TRUTH_CONTRACT_FIELDS changes.",
    "Refresh PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_VERSION together with the new version.",
    "Refresh PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT after intentional contract-field changes only.",
    "A change to project_truth binding fields without version bump and guard refresh is a contract violation.",
)

PROJECT_TRUTH_CONTRACT_FIELDS: tuple[str, ...] = (
    "contract",
    "path",
    "sha256",
    "contract_version",
)

PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_VERSION = "1.0"
PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT = "e0802a2dd5b9383d02cc6cd5649396559b98b5ea37effefd21ef80aa48d0683f"


def build_project_truth_contract_field_payload() -> dict[str, list[str]]:
    return {
        "contract_fields": list(PROJECT_TRUTH_CONTRACT_FIELDS),
    }


def compute_project_truth_contract_field_fingerprint() -> str:
    raw = json.dumps(
        build_project_truth_contract_field_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
class ProjectTruthError(RuntimeError):
    pass


class ProjectTruthNotFound(ProjectTruthError):
    pass


class ProjectTruthMismatch(ProjectTruthError):
    pass


@dataclass(frozen=True)
class ProjectTruthBinding:
    contract: str
    path: str
    sha256: str
    contract_version: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_relative_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def resolve_project_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / DEFAULT_MASTER_CANON_FILENAME).exists():
            return candidate
    raise ProjectTruthNotFound(
        f"Cannot resolve project root containing {DEFAULT_MASTER_CANON_FILENAME}"
    )


def resolve_project_truth(project_root: Path | None = None) -> ProjectTruthBinding:
    root = (project_root or resolve_project_root()).resolve()
    master_canon_path = (root / DEFAULT_MASTER_CANON_FILENAME).resolve()

    if not master_canon_path.exists():
        raise ProjectTruthNotFound(
            f"Missing required project truth file: {master_canon_path}"
        )

    return ProjectTruthBinding(
        contract=PROJECT_TRUTH_CONTRACT,
        path=_source_relative_path(master_canon_path, root),
        sha256=_sha256_file(master_canon_path),
        contract_version=PROJECT_TRUTH_CONTRACT_VERSION,
        source="repo_file",
    )


def build_project_truth_binding(project_root: Path | None = None) -> dict[str, Any]:
    return resolve_project_truth(project_root=project_root).to_dict()


def attach_project_truth(target: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    target["project_truth"] = dict(binding)
    return target



def extract_project_truth(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}

    candidate = doc
    nested = doc.get("project_truth")
    if isinstance(nested, dict):
        candidate = nested

    binding = {
        "contract": str(candidate.get("contract") or ""),
        "path": str(candidate.get("path") or ""),
        "sha256": str(candidate.get("sha256") or ""),
        "contract_version": str(candidate.get("contract_version") or ""),
        "source": str(candidate.get("source") or ""),
        "loaded_at": str(candidate.get("loaded_at") or ""),
    }

    if not binding["contract"] and not binding["path"] and not binding["sha256"]:
        return {}

    binding["contract_version"] = str(binding.get("contract_version") or "1.0")
    return binding

def assert_project_truth_consistency(
    expected_binding: dict[str, Any],
    *,
    response_doc: dict[str, Any] | None = None,
    audit_doc: dict[str, Any] | None = None,
    chapter_doc: dict[str, Any] | None = None,
    canon_snapshot_doc: dict[str, Any] | None = None,
    run_state_doc: dict[str, Any] | None = None,
) -> None:
    expected_contract = expected_binding.get("contract")
    expected_path = expected_binding.get("path")
    expected_sha = expected_binding.get("sha256")

    if expected_contract != PROJECT_TRUTH_CONTRACT:
        raise ProjectTruthMismatch(
            f"Unexpected contract: {expected_contract!r}"
        )

    if not expected_path or not expected_sha:
        raise ProjectTruthMismatch("Expected binding is incomplete")

    docs = {
        "response": response_doc,
        "audit": audit_doc,
        "chapter": chapter_doc,
        "canon_snapshot": canon_snapshot_doc,
        "run_state": run_state_doc,
    }

    for doc_name, doc in docs.items():
        if doc is None:
            continue

        binding = extract_project_truth(doc)

        contract = binding.get("contract")
        path = binding.get("path")
        sha = binding.get("sha256")

        if contract != expected_contract:
            raise ProjectTruthMismatch(
                f"{doc_name} contract mismatch: {contract!r} != {expected_contract!r}"
            )

        if path != expected_path:
            raise ProjectTruthMismatch(
                f"{doc_name} path mismatch: {path!r} != {expected_path!r}"
            )

        if sha != expected_sha:
            raise ProjectTruthMismatch(
                f"{doc_name} sha256 mismatch: {sha!r} != {expected_sha!r}"
            )


def assert_resume_project_truth_consistency(
    expected_project_truth: dict[str, Any] | None,
    persisted_run_state: dict[str, Any] | None,
) -> None:
    if persisted_run_state is None:
        return

    if not isinstance(persisted_run_state, dict):
        raise ProjectTruthMismatch("Persisted run_state is not a dict")

    expected_binding = extract_project_truth(expected_project_truth)
    persisted_binding = extract_project_truth(persisted_run_state)

    if not expected_binding or not persisted_binding:
        return

    expected_contract = str(expected_binding.get("contract") or "")
    persisted_contract = str(persisted_binding.get("contract") or "")
    if persisted_contract != expected_contract:
        raise ProjectTruthMismatch(
            f"resume run_state contract mismatch: {persisted_contract!r} != {expected_contract!r}"
        )

    expected_path = str(expected_binding.get("path") or "")
    persisted_path = str(persisted_binding.get("path") or "")
    if persisted_path != expected_path:
        raise ProjectTruthMismatch(
            f"resume run_state path mismatch: {persisted_path!r} != {expected_path!r}"
        )

    expected_sha = str(expected_binding.get("sha256") or "")
    persisted_sha = str(persisted_binding.get("sha256") or "")
    if persisted_sha != expected_sha:
        raise ProjectTruthMismatch(
            f"resume run_state sha256 mismatch: {persisted_sha!r} != {expected_sha!r}"
        )

    expected_version = str(expected_binding.get("contract_version") or "")
    persisted_version = str(persisted_binding.get("contract_version") or "")
    if expected_version and persisted_version and persisted_version != expected_version:
        raise ProjectTruthMismatch(
            f"resume run_state contract_version mismatch: {persisted_version!r} != {expected_version!r}"
        )

