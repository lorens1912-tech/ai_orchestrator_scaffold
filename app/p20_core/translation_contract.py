"""GAP-019 immutable binding and Translation QA contracts.

No Source, Canon, GAP-017 evaluation or GAP-018 record is changed here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


LOCALES = frozenset({"en-US", "en-GB"})
BIBLE_DECISION_KINDS = frozenset({
    "PROPER_NAME", "TERM", "ORGANIZATION", "TITLE", "TECHNOLOGY",
    "LOCKED_TERM", "FORBIDDEN_SUBSTITUTION", "DO_NOT_TRANSLATE",
    "TRANSLITERATION", "IDIOM", "REGISTER", "DIALOGUE", "CHARACTER_VOICE",
    "LITERARY_STYLE", "UNIT", "FORMAT", "LOCAL_CONVENTION", "LOCALIZATION",
})
QA_CRITERIA = (
    "SEMANTIC_FIDELITY", "OMISSIONS", "ADDITIONS", "MISTRANSLATIONS",
    "TERMINOLOGY", "NAMES_ENTITIES", "CANON", "STYLE", "VOICE",
    "DIALOGUE_NATURALNESS", "TARGET_NATURALNESS", "LOCALE_CORRECTNESS",
    "CROSS_CHAPTER_CONTINUITY", "FORMATTING", "BIBLE_COMPLIANCE",
)
QA_MODEL_CRITERIA = frozenset({
    "SEMANTIC_FIDELITY", "OMISSIONS", "ADDITIONS", "MISTRANSLATIONS",
    "STYLE", "VOICE", "DIALOGUE_NATURALNESS", "TARGET_NATURALNESS",
    "CROSS_CHAPTER_CONTINUITY",
})
TRANSLATION_POLICY_VERSION = "GAP019_TRANSLATION_V1"
TRANSLATION_POLICY_HASH = hashlib.sha256(TRANSLATION_POLICY_VERSION.encode()).hexdigest()
QA_POLICY_VERSION = "GAP019_TRANSLATION_QA_V1"
QA_POLICY_HASH = hashlib.sha256(json.dumps(QA_CRITERIA).encode()).hexdigest()


def reduce_translation_qa(
    unit_ids: tuple[str, ...], coverage: tuple[Mapping[str, Any], ...],
    findings: tuple[Mapping[str, Any], ...],
) -> tuple[str, str, str | None]:
    """Required Phase 2 contract: BLOCKED has no quality decision."""
    required = {(unit_id, criterion) for unit_id in unit_ids for criterion in QA_CRITERIA}
    observed: dict[tuple[str, str], str] = {}
    if not unit_ids or len(set(unit_ids)) != len(unit_ids):
        return "BLOCKED", "INVALID", None
    for item in coverage:
        key = (item.get("unit_id"), item.get("criterion"))
        if key not in required or key in observed or item.get("status") not in {"PASS", "ISSUE"}:
            return "BLOCKED", "INVALID", None
        observed[key] = str(item["status"])
    if set(observed) != required:
        return "BLOCKED", "INVALID", None
    issues = {key for key, state in observed.items() if state == "ISSUE"}
    supported: set[tuple[str, str]] = set()
    for finding in findings:
        key = (finding.get("unit_id"), finding.get("criterion"))
        if (key not in issues or not str(finding.get("evidence") or "").strip()
                or finding.get("severity") not in {"MINOR", "MAJOR", "CRITICAL"}
                or type(finding.get("repairable")) is not bool
                or type(finding.get("blocking")) is not bool):
            return "BLOCKED", "INVALID", None
        supported.add(key)
    if issues != supported:
        return "BLOCKED", "INVALID", None
    if any(item["severity"] == "CRITICAL" and not item["repairable"] for item in findings):
        return "COMPLETED", "VALID", "REJECT"
    if issues:
        return "COMPLETED", "VALID", "REVISE"
    return "COMPLETED", "VALID", "ACCEPT"


def reduce_translation_hierarchy(
    chapter_units: Mapping[str, tuple[str, ...]],
    coverage: tuple[Mapping[str, Any], ...],
    findings: tuple[Mapping[str, Any], ...],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reduce exact unit coverage within chapters, then preserve the worst book decision."""
    all_units = tuple(unit_id for units in chapter_units.values() for unit_id in units)
    if (not chapter_units or any(not units for units in chapter_units.values())
            or len(set(all_units)) != len(all_units)):
        raise TranslationContractError("TRANSLATION_QA_CHAPTER_COVERAGE_INVALID")
    chapters: list[dict[str, Any]] = []
    for chapter_id, unit_ids in chapter_units.items():
        unit_set = set(unit_ids)
        chapter_coverage = tuple(item for item in coverage if item.get("unit_id") in unit_set)
        chapter_findings = tuple(item for item in findings if item.get("unit_id") in unit_set)
        execution, validation, decision = reduce_translation_qa(
            unit_ids, chapter_coverage, chapter_findings)
        chapters.append({"chapter_id": chapter_id, "unit_ids": list(unit_ids),
                         "execution_status": execution, "validation_status": validation,
                         "quality_decision": decision,
                         "finding_count": len(chapter_findings),
                         "blocking_count": sum(bool(item.get("blocking")) for item in chapter_findings)})
    global_result = reduce_translation_qa(all_units, coverage, findings)
    if global_result[0] == "BLOCKED" or any(
        item["execution_status"] == "BLOCKED" for item in chapters):
        book_result = ("BLOCKED", "INVALID", None)
    elif any(item["quality_decision"] == "REJECT" for item in chapters):
        book_result = ("COMPLETED", "VALID", "REJECT")
    elif any(item["quality_decision"] == "REVISE" for item in chapters):
        book_result = ("COMPLETED", "VALID", "REVISE")
    else:
        book_result = ("COMPLETED", "VALID", "ACCEPT")
    book = {"chapter_ids": list(chapter_units), "unit_count": len(all_units),
            "finding_count": len(findings),
            "blocking_count": sum(bool(item.get("blocking")) for item in findings),
            "execution_status": book_result[0], "validation_status": book_result[1],
            "quality_decision": book_result[2]}
    return chapters, book


class TranslationContractError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def signed(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("record_hash", None)
    result["record_hash"] = digest(result)
    return result


def verify_signed(value: Mapping[str, Any], *, project_id: str, book_id: str,
                  target_locale: str) -> None:
    if (value.get("schema_version") != 1
            or (value.get("project_id"), value.get("book_id"), value.get("target_locale"))
            != (project_id, book_id, target_locale)
            or target_locale not in LOCALES
            or value.get("record_hash") != digest({
                key: item for key, item in value.items() if key != "record_hash"
            })):
        raise TranslationContractError("TRANSLATION_RECORD_INTEGRITY")


@dataclass(frozen=True)
class SourceBinding:
    project_id: str
    book_id: str
    source_master_id: str
    source_master_version: int
    source_master_hash: str
    manuscript_id: str
    manuscript_version: int
    manuscript_content_hash: str
    source_language: str
    target_locale: str

    def validate(self) -> None:
        for name in ("project_id", "book_id", "source_master_id", "manuscript_id",
                     "source_language"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise TranslationContractError("SOURCE_BINDING_INVALID", 422)
        if self.target_locale not in LOCALES:
            raise TranslationContractError("TARGET_LOCALE_UNSUPPORTED", 422)
        for name in ("source_master_version", "manuscript_version"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise TranslationContractError("SOURCE_BINDING_INVALID", 422)
        for name in ("source_master_hash", "manuscript_content_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise TranslationContractError("SOURCE_BINDING_INVALID", 422)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class TranslationQAReport:
    schema_version: int
    project_id: str
    book_id: str
    target_locale: str
    qa_id: str
    version: int
    translation_version_id: str
    translation_hash: str
    source_binding: dict[str, Any]
    bible_id: str
    bible_hash: str
    policy_version: str
    policy_hash: str
    criteria: tuple[str, ...]
    coverage: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    chapter_results: tuple[dict[str, Any], ...]
    book_result: dict[str, Any]
    model_invocation_refs: tuple[str, ...]
    execution_status: str
    validation_status: str
    quality_decision: str | None
    reevaluation_of: str | None
    created_at: str
    artifact_ref: str
    artifact_hash: str
    record_hash: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["criteria"] = list(self.criteria)
        result["coverage"] = list(self.coverage)
        result["findings"] = list(self.findings)
        result["chapter_results"] = list(self.chapter_results)
        result["model_invocation_refs"] = list(self.model_invocation_refs)
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TranslationQAReport":
        data = dict(raw)
        for key in ("criteria", "coverage", "findings", "chapter_results", "model_invocation_refs"):
            data[key] = tuple(data[key])
        record = cls(**data)
        record.validate()
        return record

    def validate(self) -> None:
        verify_signed(self.to_dict(), project_id=self.project_id,
                      book_id=self.book_id, target_locale=self.target_locale)
        if self.policy_version != QA_POLICY_VERSION or self.policy_hash != QA_POLICY_HASH:
            raise TranslationContractError("TRANSLATION_QA_POLICY_INVALID")
        if self.criteria != QA_CRITERIA:
            raise TranslationContractError("TRANSLATION_QA_CRITERIA_INVALID")
        if (not self.chapter_results or
                (self.book_result.get("execution_status"),
                 self.book_result.get("validation_status"),
                 self.book_result.get("quality_decision")) !=
                (self.execution_status, self.validation_status, self.quality_decision)):
            raise TranslationContractError("TRANSLATION_QA_HIERARCHY_INVALID")
        if self.execution_status not in {"COMPLETED", "BLOCKED", "RECOVERY_REQUIRED"}:
            raise TranslationContractError("TRANSLATION_QA_STATUS_INVALID")
        if self.validation_status not in {"VALID", "INVALID", "NOT_PERFORMED"}:
            raise TranslationContractError("TRANSLATION_QA_STATUS_INVALID")
        if self.quality_decision not in {None, "ACCEPT", "REVISE", "REJECT"}:
            raise TranslationContractError("TRANSLATION_QA_DECISION_INVALID")
        if self.quality_decision is not None and (
            self.execution_status != "COMPLETED" or self.validation_status != "VALID"
        ):
            raise TranslationContractError("TRANSLATION_QA_PREMATURE_DECISION")
        if self.quality_decision is None and self.execution_status == "COMPLETED" and self.validation_status == "VALID":
            raise TranslationContractError("TRANSLATION_QA_DECISION_MISSING")
        if self.quality_decision is not None:
            observed = {(item.get("unit_id"), item.get("criterion")) for item in self.coverage}
            units = {item.get("unit_id") for item in self.coverage}
            if not units or any((unit, criterion) not in observed for unit in units for criterion in QA_CRITERIA):
                raise TranslationContractError("TRANSLATION_QA_COVERAGE_INCOMPLETE")
            if self.quality_decision == "ACCEPT" and any(item.get("blocking") for item in self.findings):
                raise TranslationContractError("TRANSLATION_QA_BLOCKING_FINDING")
