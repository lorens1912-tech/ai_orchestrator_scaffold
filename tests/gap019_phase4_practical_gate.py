"""GAP-019 Phase 4 practical gate: real short transport, durable API flow.

Run directly, outside pytest, so its live provider calls cannot inherit the
pytest transport or credential stubs. All book content is neutral synthetic.
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import openai
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.local_operator import initialize_operator
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository
from app.p20_core.translation_contract import QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION
from tests.test_gap018_manuscript_version import case
from tests.test_gap019_phase2 import _source


def _safe(value: object) -> str:
    text = str(value)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret)=([^&\s]+)", r"\1=[REDACTED]", text)
    return text


def _connection_diagnostics(exc: openai.APIConnectionError) -> dict:
    configured = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    parsed = urlsplit(configured)
    host = parsed.hostname or "api.openai.com"
    port = parsed.port or 443
    result = {
        "error": type(exc).__name__,
        "cause_type": str(type(exc.__cause__)),
        "cause_repr": _safe(repr(exc.__cause__)),
        "target_base_url": f"{parsed.scheme or 'https'}://{host}:{port}{parsed.path}",
        "dns_resolution": False,
        "tcp_tls_connection": False,
    }
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        result["dns_resolution"] = True
    except OSError as error:
        result["dns_error"] = _safe(repr(error))
    try:
        with socket.create_connection(("api.openai.com", 443), timeout=5) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname="api.openai.com"):
                result["tcp_tls_connection"] = True
    except OSError as error:
        result["tcp_tls_error"] = _safe(repr(error))
    return result


def _require(response):
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {_safe(response.text)}")
    return response.json()


def _stub_qa_call(*, prompt, model, temperature, observer, response_schema=None):
    payload = json.loads(prompt)
    assert payload["protocol"] == "AGENTPRO_GAP019_TRANSLATION_QA_V1"
    observer({"event": "START", "api": "responses", "sent": {"model": model}})
    observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
    output = {"unit_id": payload["unit_id"], "source_hash": payload["source_hash"],
              "target_hash": payload["target_hash"], "target_locale": payload["target_locale"],
              "bible_hash": payload["bible_hash"],
              "coverage": [{"criterion": criterion, "status": "PASS"} for criterion in QA_CRITERIA],
              "findings": []}
    return {"text": json.dumps(output), "refused": False,
            "provider_returned_model": model}


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print(json.dumps({"status": "LIVE_PROVIDER_NOT_AVAILABLE", "reason": "credential_absent"}))
        raise SystemExit(2)
    with (tempfile.TemporaryDirectory(prefix="agentpro-gap019-live-") as directory,
          tempfile.TemporaryDirectory(prefix="agentpro-gap019-operator-") as credential_directory):
        root = Path(directory)
        os.environ["AGENTPRO_STORAGE_ROOT"] = str(root)
        repo, _root, _actor, source = _source(case.__wrapped__(root))
        system = ensure_system_repository()
        system.bind_project(repo.context.project_id, repo.context.book_id)
        secret = Path(credential_directory) / "credential.dpapi"
        initialize_operator(system, secret)
        auth = {"Authorization": "Bearer " + read_secret(secret)}
        base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
        import app.llm_provider_openai as transport
        real_call = transport.call_text

        def observed_live_call(**kwargs):
            try:
                return real_call(**kwargs)
            except openai.APIConnectionError as exc:
                print(json.dumps({"live_connection_diagnostics": _connection_diagnostics(exc)},
                                 sort_keys=True))
                raise

        transport.call_text = observed_live_call
        summary = []
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
            assert _require(client.get(base + "/source-masters/current", headers=auth))["source_master_id"] == source["source_master_id"]
            assert client.get(base + "/translations/en-US/current", headers=auth).json() is None
            for locale in ("en-US", "en-GB"):
                scope = base + "/translations/" + locale
                bible = _require(client.post(base + "/translation-bibles/" + locale + "/versions",
                    headers=auth, json={"request_id": "LIVE-BIBLE-" + locale,
                        "source_master_id": source["source_master_id"],
                        "source_master_hash": source["artifact_hash"],
                        "expected_head": {"record_id": None, "version": None, "head_hash": None},
                        "decision_entries": []}))
                run = _require(client.post(scope + "/runs", headers=auth, json={
                    "request_id": "LIVE-START-" + locale,
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "bible_id": bible["translation_bible_id"], "bible_hash": bible["record_hash"],
                    "policy_version": TRANSLATION_POLICY_VERSION,
                    "policy_hash": TRANSLATION_POLICY_HASH,
                    "expected_head": {"record_id": None, "version": None, "head_hash": None}}))
                version = _require(client.post(scope + "/runs/" + run["run_id"] + "/execute",
                                               headers=auth, json={}))
                unit = repo.get_gap019_record(locale, "UNIT", version["unit_ids"][0])
                audit = json.loads(repo.get_metadata_readonly(
                    "model_invocation.v1:" + unit["model_invocation_refs"][0]))
                assert audit["status"] == "TRANSPORT_COMPLETED" and audit["validation"] == "VALID"
                assert audit["resolved"]["provider"] == "OPENAI"
                assert source["source_master_id"] in audit["artifact_refs"]
                assert bible["translation_bible_id"] in audit["artifact_refs"]
                assert "locale:" + locale in audit["artifact_refs"]
                assert audit["output_hash"] == unit["target_text_hash"]
                assert audit["context_hash"] == unit["context_hash"]
                assert audit["attempts"][-1]["provider_reported"]["response_id"]
                transport.call_text = _stub_qa_call
                report = _require(client.post(scope + "/versions/" +
                    version["translation_version_id"] + "/qa", headers=auth,
                    json={"request_id": "LIVE-QA-" + locale}))
                transport.call_text = observed_live_call
                assert report["quality_decision"] == "ACCEPT"
                candidate = _require(client.post(scope + "/candidates", headers=auth, json={
                    "request_id": "LIVE-CANDIDATE-" + locale,
                    "qa_id": report["qa_id"], "qa_hash": report["record_hash"],
                    "expected_head": {"record_id": None, "version": None, "head_hash": None}}))
                assert client.get(scope + "/masters/current", headers=auth).json() is None
                review = _require(client.post(scope + "/candidates/" +
                    candidate["candidate_id"] + "/review", headers=auth))
                master = _require(client.post(scope + "/candidates/" +
                    candidate["candidate_id"] + "/decision", headers=auth, json={
                    "request_id": "LIVE-APPROVE-" + locale,
                    "challenge_id": review["challenge_id"], "decision": "APPROVE",
                    "candidate_hash": candidate["record_hash"],
                    "expected_head": {"record_id": None, "version": None, "head_hash": None}}))
                assert master["approved_by_user"] is True
                summary.append({"locale": locale, "source_master_id": source["source_master_id"],
                    "bible_id": bible["translation_bible_id"],
                    "bible_hash": bible["record_hash"], "version_id": version["translation_version_id"],
                    "qa_id": report["qa_id"], "target_master_id": master["target_master_id"],
                    "approval_id": master["approval_id"],
                    "requested_model": audit["requested"]["model"],
                    "effective_model": audit["effective_model"],
                    "provider": audit["resolved"]["provider"],
                    "transport": audit["status"], "validation": audit["validation"],
                    "invocation_id": audit["invocation_id"],
                    "response_id": audit["attempts"][-1]["provider_reported"]["response_id"],
                    "context_package_id": audit["context_package_id"],
                    "output_hash": audit["output_hash"]})
            assert summary[0]["version_id"] != summary[1]["version_id"]
            assert summary[0]["target_master_id"] != summary[1]["target_master_id"]
            for left, right in ((summary[0], summary[1]), (summary[1], summary[0])):
                assert repo.get_gap019_record(left["locale"], "BIBLE", right["bible_id"]) is None
                assert repo.get_gap019_record(left["locale"], "VERSION", right["version_id"]) is None
                assert repo.get_gap019_record(left["locale"], "QA", right["qa_id"]) is None
                assert repo.get_gap019_record(left["locale"], "APPROVAL", right["approval_id"]) is None
                assert repo.get_gap019_record(left["locale"], "MASTER", right["target_master_id"]) is None
        reopened = ProjectRepository(StorageResolver(root).resolve_project(
            repo.context.project_id, book_id=repo.context.book_id))
        for item in summary:
            head = reopened.get_gap019_head(item["locale"], "MASTER")
            assert head[0] == item["target_master_id"]
        print(json.dumps({"status": "PASS", "real_provider": summary,
                          "reopen_schema": reopened.get_schema_version()}, sort_keys=True))


if __name__ == "__main__":
    main()
