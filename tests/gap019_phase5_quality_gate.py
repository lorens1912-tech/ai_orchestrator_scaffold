"""Live GAP-019 QA check on deliberately wrong synthetic translations."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.local_operator import initialize_operator
from app.p20_core.project_repository import ensure_system_repository
from app.p20_core.translation import record_translation_unit, seal_translation_version
from app.p20_core.translation_contract import (
    QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION,
)
from tests.test_gap018_manuscript_version import case, make_ready_candidate
from tests.test_gap019_phase2 import _source


def _ok(response):
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


def _head():
    return {"record_id": None, "version": None, "head_hash": None}


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("LIVE_PROVIDER_NOT_AVAILABLE")
    with (tempfile.TemporaryDirectory(prefix="agentpro-gap019-qa-live-") as directory,
          tempfile.TemporaryDirectory(prefix="agentpro-gap019-operator-") as credential_directory):
        os.environ["AGENTPRO_STORAGE_ROOT"] = directory
        repo, _root, _actor, source = _source(
            None, make_ready_candidate(case.__wrapped__(Path(directory))))
        system = ensure_system_repository()
        system.bind_project(repo.context.project_id, repo.context.book_id)
        secret = Path(credential_directory) / "credential.dpapi"
        actor = initialize_operator(system, secret)
        auth = {"Authorization": "Bearer " + read_secret(secret)}
        base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
        summary = []
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
            for locale in ("en-US", "en-GB"):
                scope = base + "/translations/" + locale
                bible = _ok(client.post(base + "/translation-bibles/" + locale + "/versions",
                    headers=auth, json={"request_id": "QA-LIVE-BIBLE-" + locale,
                        "source_master_id": source["source_master_id"],
                        "source_master_hash": source["artifact_hash"],
                        "expected_head": _head(), "decision_entries": []}))
                run = _ok(client.post(scope + "/runs", headers=auth, json={
                    "request_id": "QA-LIVE-START-" + locale,
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "bible_id": bible["translation_bible_id"],
                    "bible_hash": bible["record_hash"],
                    "policy_version": TRANSLATION_POLICY_VERSION,
                    "policy_hash": TRANSLATION_POLICY_HASH,
                    "expected_head": _head()}))
                source_text = "Neutral chapter 1."
                unit = record_translation_unit(repo, locale, {
                    "request_id": "QA-LIVE-UNIT-" + locale,
                    "run_id": run["run_id"], "chapter_id": "chapter_1",
                    "start_byte": 0, "end_byte": len(source_text.encode()),
                    "source_span_hash": hashlib.sha256(source_text.encode()).hexdigest(),
                    "target_text": "Bananas.", "origin": "USER_EDIT",
                }, identity=actor)
                version = seal_translation_version(repo, locale, {
                    "request_id": "QA-LIVE-SEAL-" + locale,
                    "run_id": run["run_id"], "unit_ids": [unit["unit_id"]],
                    "parent_version_id": None,
                    "expected_head": [None, None, None],
                })
                request_id = "QA-LIVE-CHECK-" + locale
                report = _ok(client.post(scope + "/versions/" +
                    version["translation_version_id"] + "/qa",
                    headers=auth, json={"request_id": request_id}))
                audit_rows = repo.list_model_invocations(run_id="QA-" + request_id)
                assert len(audit_rows) == 1
                audit = audit_rows[0]
                criteria = {item["criterion"] for item in report["findings"]}
                summary.append({
                    "locale": locale, "execution": report["execution_status"],
                    "validation": report["validation_status"],
                    "decision": report["quality_decision"],
                    "finding_criteria": sorted(criteria),
                    "coverage_count": len(report["coverage"]),
                    "transport": audit["status"],
                    "invocation_id": audit["invocation_id"],
                    "response_id": audit["attempts"][-1]["provider_reported"]["response_id"],
                    "context_package_id": audit["context_package_id"],
                    "output_hash": audit["output_hash"],
                    "source_master_id": source["source_master_id"],
                    "bible_hash": bible["record_hash"],
                })
                assert audit["status"] == "TRANSPORT_COMPLETED"
                assert audit["validation"] == "VALID"
                assert report["execution_status"] == "COMPLETED"
                assert report["validation_status"] == "VALID"
                assert report["quality_decision"] in {"REVISE", "REJECT"}
                assert len(report["coverage"]) == len(QA_CRITERIA)
                assert criteria & {"SEMANTIC_FIDELITY", "OMISSIONS", "MISTRANSLATIONS"}
                assert client.get(scope + "/masters/current", headers=auth).json() is None
        assert summary[0]["invocation_id"] != summary[1]["invocation_id"]
        print(json.dumps({"status": "PASS", "qa_probes": summary}, sort_keys=True))


if __name__ == "__main__":
    main()
