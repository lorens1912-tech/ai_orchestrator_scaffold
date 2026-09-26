"""Versioned execution audit, separate from frozen memory/research evidence.

The existing project repository owns persistence. No process-global execution
context, routing, domain authority, or evaluation cache lives here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

_ABSENT = object()


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvocationRecoveryRequired(RuntimeError):
    pass


class ModelInvocationAudit:
    def __init__(self, repository, execution, package, *, role, mode, requested_model,
                 routing=None, scope="PROJECT", artifact_refs=None):
        if (repository.scope.scope_id != execution.project_id
                or repository.context.book_id != execution.book_id
                or package.project_id != execution.project_id
                or package.book_id != execution.book_id
                or package.run_id != execution.run_id or package.step_id != execution.step_id):
            raise ValueError("model invocation scope/context mismatch")
        saved = repository.get_context_package(package.context_package_id)
        if saved is None or saved.to_dict() != package.to_dict():
            raise ValueError("model invocation requires persisted context")
        self.repository = repository
        self.operation_id = execution.operation_id
        self.attempt_id = None
        binding = dict(project_id=execution.project_id, book_id=execution.book_id,
                       series_id=execution.series_id, scope=scope, run_id=execution.run_id,
                       step_id=execution.step_id, operation_id=self.operation_id, role=role, mode=mode,
                       context_package_id=package.context_package_id, context_hash=package.context_hash,
                       effective_model=package.effective_model, artifact_refs=list(artifact_refs or []))
        with repository.model_invocation_transaction(self.operation_id) as state:
            if state:
                if state.get("schema_version") != 1:
                    raise ValueError("unsupported model provenance schema")
                if state["binding_hash"] != fingerprint(binding):
                    raise ValueError("model invocation binding changed on retry")
            else:
                state.update(schema_version=1, invocation_id="INV-" + uuid4().hex,
                             **binding, binding_hash=fingerprint(binding),
                             requested={"model": requested_model,
                                        "routing_inputs": copy.deepcopy((routing or {}).get("requested")),
                                        "sources": copy.deepcopy((routing or {}).get("request_sources"))},
                             resolved=copy.deepcopy(routing) if routing else {
                                 "effective_model": package.effective_model,
                                 "source": "PINNED_EXECUTION", "policy_status": "NOT_RECORDED"},
                             attempts=[], status="PREPARED", validation="NOT_PERFORMED")

    def event(self, event: dict) -> None:
        """Explicit adapter callback. Persist START before SDK; END before use."""
        with self.repository.model_invocation_transaction(self.operation_id) as state:
            if event["event"] == "START":
                if any(a["status"] == "STARTED" for a in state["attempts"]):
                    raise InvocationRecoveryRequired("MODEL_INVOCATION_RECOVERY_REQUIRED")
                self.attempt_id = "ATTEMPT-" + uuid4().hex
                state["attempts"].append(dict(
                    attempt_id=self.attempt_id, ordinal=len(state["attempts"]) + 1,
                    started_at=now(), status="STARTED", remote_outcome="UNKNOWN",
                    **{k: copy.deepcopy(v) for k, v in event.items() if k != "event"}))
                state["status"] = "RUNNING"
                state["validation"] = "NOT_PERFORMED"
                state.pop("result", None)
                state.pop("result_hash", None)
                state.pop("output_hash", None)
                state.pop("failure_phase", None)
                state.pop("quality", None)
            else:
                attempt = state["attempts"][-1]
                if attempt["attempt_id"] != self.attempt_id or attempt["status"] != "STARTED":
                    raise InvocationRecoveryRequired("MODEL_INVOCATION_ATTEMPT_FENCED")
                attempt.update(finished_at=now(), **{k: copy.deepcopy(v) for k, v in event.items()
                                                   if k != "event"})
                state["status"] = event["status"]

    def call(self, *, prompt, model, temperature=_ABSENT, response_schema=None):
        from app.llm_provider_openai import call_text
        schema_binding = None
        if response_schema is not None:
            schema_binding = {
                "name": response_schema.get("name"),
                "strict": response_schema.get("strict"),
                "schema_hash": fingerprint(response_schema.get("schema")),
            }
        request = dict(input_hash=hashlib.sha256(prompt.encode()).hexdigest(), model=model,
                       parameters={
                           **({} if temperature is _ABSENT else {"temperature": temperature}),
                           **({} if schema_binding is None else {"response_schema": schema_binding}),
                       },
                       api_mode=(os.getenv("OPENAI_API_MODE", "responses") or "responses").strip().lower())
        if temperature is _ABSENT:
            temperature = None
        with self.repository.model_invocation_transaction(self.operation_id) as state:
            if model != state["effective_model"]:
                raise ValueError("model invocation model differs from persisted context")
            if state.get("request") is not None and state["request"] != request:
                raise ValueError("model invocation input/configuration changed on retry")
            state["request"] = request
            state["configuration_hash"] = fingerprint({
                "configuration": {k: v for k, v in request.items() if k != "input_hash"},
                "resolved": state["resolved"]})
            state["input_fingerprint"] = fingerprint({"input_hash": request["input_hash"],
                                                       "context_hash": state["context_hash"]})
            if "result" in state and state["validation"] != "INVALID":
                if state.get("result_hash") != fingerprint(state["result"]):
                    raise ValueError("model invocation stored result integrity mismatch")
                return copy.deepcopy(state["result"])
            if any(a["status"] == "STARTED" for a in state["attempts"]):
                raise InvocationRecoveryRequired("MODEL_INVOCATION_RECOVERY_REQUIRED")
            if any(a["status"] == "INTERRUPTED" and not a.get("recovered_by") for a in state["attempts"]):
                raise InvocationRecoveryRequired("MODEL_INVOCATION_RECOVERY_REQUIRED")
            if (state["attempts"] and state["attempts"][-1]["status"] == "RECEIVED"
                    and state["validation"] != "INVALID"):
                raise InvocationRecoveryRequired("MODEL_RESULT_PERSISTENCE_INCOMPLETE")
        try:
            result = call_text(
                prompt=prompt,
                model=model,
                temperature=temperature,
                observer=self.event,
                response_schema=response_schema,
            )
        except Exception:
            with self.repository.model_invocation_transaction(self.operation_id) as state:
                if not state["attempts"]:
                    state["status"] = "CONFIGURATION_FAILED"
            raise
        with self.repository.model_invocation_transaction(self.operation_id) as state:
            # If END was persisted but this write fails, the next call must not
            # silently perform another external generation.
            if (state["attempts"][-1]["attempt_id"] != self.attempt_id
                    or state["attempts"][-1]["status"] != "RECEIVED"
                    or state["status"] != "RECEIVED"):
                raise InvocationRecoveryRequired("MODEL_INVOCATION_ATTEMPT_FENCED")
            state["result"] = result
            state["result_hash"] = fingerprint(result)
            state["output_hash"] = hashlib.sha256(result["text"].encode()).hexdigest()
            state["attempts"][-1]["result"] = result
            state["attempts"][-1]["output_hash"] = state["output_hash"]
            state["status"] = "REFUSED" if result["refused"] else "TRANSPORT_COMPLETED"
        return result

    def validated(self, *, status="VALID", quality=None, failure_phase=None):
        with self.repository.model_invocation_transaction(self.operation_id) as state:
            state["validation"] = status
            state["quality"] = quality
            if state["attempts"]:
                state["attempts"][-1].update(validation=status, quality=quality)
            if failure_phase:
                state["failure_phase"] = failure_phase
                if failure_phase == "CONFIGURATION":
                    state["status"] = "CONFIGURATION_FAILED"


def recover_invocation(repository, operation_id, *, recovered_by, connection=None):
    """Called only by an existing authorized recovery, never steals a live call."""
    with repository.model_invocation_transaction(operation_id, connection=connection) as state:
        if not state:
            return
        recovered = False
        for attempt in state["attempts"]:
            if (attempt["status"] == "STARTED"
                    or (attempt["status"] == "INTERRUPTED" and not attempt.get("recovered_by"))
                    or (attempt["status"] == "RECEIVED" and "result" not in state)):
                stamp = now()
                attempt["pre_recovery_status"] = attempt["status"]
                attempt.setdefault("finished_at", stamp)
                attempt.update(status="INTERRUPTED", recovered_at=stamp, recovered_by=recovered_by)
                recovered = True
        if recovered:
            state["status"] = "RECOVERED"


def public_trace(repository, *, run_id):
    # Raw transport output is local recovery material, never copied into logs.
    return [project_trace(record) for record in repository.list_model_invocations(run_id=run_id)]


def project_trace(record):
    result = {k: v for k, v in record.items() if k != "result"}
    result["attempts"] = [{k: v for k, v in attempt.items() if k != "result"}
                          for attempt in record["attempts"]]
    return result


def mark_validation(repository, operation_id, status, *, connection=None):
    with repository.model_invocation_transaction(operation_id, connection=connection) as state:
        if state:
            state["validation"] = status
            if state["attempts"]:
                state["attempts"][-1]["validation"] = status
