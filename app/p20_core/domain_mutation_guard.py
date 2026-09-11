from __future__ import annotations

import json
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any


DOMAIN_MUTATION_GUARD_VERSION = 1


class DomainMutationError(ValueError):
    pass


class MutationOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class MutationSource(str, Enum):
    SYSTEM = "SYSTEM"
    AUTOMATION = "AUTOMATION"
    MODEL = "MODEL"
    AUTHOR = "AUTHOR"
    ADMIN_CONTROLLED_OPERATION = "ADMIN_CONTROLLED_OPERATION"


class MutationType(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    REPLACE = "REPLACE"


class MutationReasonCode(str, Enum):
    CREATE_ALLOWED = "CREATE_ALLOWED"
    IDEMPOTENT = "IDEMPOTENT"
    UPDATE_ALLOWED = "UPDATE_ALLOWED"
    FROZEN_RECORD = "FROZEN_RECORD"
    AUTHOR_LOCKED_RECORD = "AUTHOR_LOCKED_RECORD"
    FROZEN_AND_AUTHOR_LOCKED_RECORD = "FROZEN_AND_AUTHOR_LOCKED_RECORD"
    SILENT_UNFREEZE_DENIED = "SILENT_UNFREEZE_DENIED"
    SILENT_UNLOCK_DENIED = "SILENT_UNLOCK_DENIED"
    DOMAIN_ID_CHANGED = "DOMAIN_ID_CHANGED"
    VERSION_NOT_INCREMENTED = "VERSION_NOT_INCREMENTED"


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(value[key]) for key in sorted(value, key=str)}
    return value


def _coerce_enum(enum_type: type[Enum], value: Any, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise DomainMutationError(f"{field_name} is unsupported") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainMutationError(f"{field_name} must be a string")
    if value != value.strip() or not value:
        raise DomainMutationError(f"{field_name} is required")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise DomainMutationError(f"{field_name} must be a boolean")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DomainMutationError(f"{field_name} must be a positive integer")
    return value


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DomainMutationError(f"{field_name} must be JSON") from exc
    if not isinstance(value, dict):
        raise DomainMutationError(f"{field_name} must be an object")
    json.dumps(value, sort_keys=True, separators=(",", ":"))
    return dict(value)


def _payload_frozen(payload: dict[str, Any]) -> bool:
    return bool(payload.get("frozen", False))


def _payload_author_locked(payload: dict[str, Any]) -> bool:
    return bool(payload.get("author_locked", False))


def _payload_version(payload: dict[str, Any]) -> int | None:
    value = payload.get("version")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DomainMutationError("version must be a positive integer")
    return value


def _payload_identity_key(payload: dict[str, Any], record_id: str) -> str | None:
    for key, value in payload.items():
        if key.endswith("_id") and str(value) == record_id:
            return str(key)
    return None


@dataclass(frozen=True)
class MutationPolicy:
    allow_frozen_mutation: bool = False
    allow_author_locked_mutation: bool = False
    allow_silent_unfreeze: bool = False
    allow_silent_unlock: bool = False
    require_version_increment: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _serialize(getattr(self, item.name))
            for item in fields(self)
        }


DEFAULT_MUTATION_POLICY = MutationPolicy()


@dataclass(frozen=True)
class MutationContext:
    project_id: str
    record_type: str
    record_id: str
    mutation_type: MutationType | str
    source: MutationSource | str
    actor_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _required_text(self.project_id, "project_id"))
        object.__setattr__(self, "record_type", _required_text(self.record_type, "record_type"))
        object.__setattr__(self, "record_id", _required_text(self.record_id, "record_id"))
        object.__setattr__(
            self,
            "mutation_type",
            _coerce_enum(MutationType, self.mutation_type, "mutation_type"),
        )
        object.__setattr__(
            self,
            "source",
            _coerce_enum(MutationSource, self.source, "source"),
        )
        object.__setattr__(self, "actor_id", _optional_text(self.actor_id, "actor_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _serialize(getattr(self, item.name))
            for item in fields(self)
        }


@dataclass(frozen=True)
class MutationDecision:
    outcome: MutationOutcome | str
    reason_code: MutationReasonCode | str
    project_id: str
    record_type: str
    record_id: str
    mutation_type: MutationType | str
    source: MutationSource | str
    actor_id: str | None
    current_version: int | None
    proposed_version: int | None
    current_frozen: bool
    proposed_frozen: bool
    current_author_locked: bool
    proposed_author_locked: bool
    guard_version: int = DOMAIN_MUTATION_GUARD_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outcome",
            _coerce_enum(MutationOutcome, self.outcome, "outcome"),
        )
        object.__setattr__(
            self,
            "reason_code",
            _coerce_enum(MutationReasonCode, self.reason_code, "reason_code"),
        )
        for field_name in ("project_id", "record_type", "record_id"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "mutation_type",
            _coerce_enum(MutationType, self.mutation_type, "mutation_type"),
        )
        object.__setattr__(self, "source", _coerce_enum(MutationSource, self.source, "source"))
        object.__setattr__(self, "actor_id", _optional_text(self.actor_id, "actor_id"))
        if self.current_version is not None:
            object.__setattr__(self, "current_version", _positive_int(self.current_version, "current_version"))
        if self.proposed_version is not None:
            object.__setattr__(self, "proposed_version", _positive_int(self.proposed_version, "proposed_version"))
        for field_name in (
            "current_frozen",
            "proposed_frozen",
            "current_author_locked",
            "proposed_author_locked",
        ):
            object.__setattr__(self, field_name, _bool(getattr(self, field_name), field_name))
        object.__setattr__(self, "guard_version", _positive_int(self.guard_version, "guard_version"))

    @property
    def allowed(self) -> bool:
        return self.outcome == MutationOutcome.ALLOW

    @property
    def denied(self) -> bool:
        return self.outcome == MutationOutcome.DENY

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _serialize(getattr(self, item.name))
            for item in fields(self)
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


class DomainMutationGuard:
    def __init__(self, policy: MutationPolicy = DEFAULT_MUTATION_POLICY) -> None:
        if not isinstance(policy, MutationPolicy):
            raise DomainMutationError("policy must be a MutationPolicy")
        self.policy = policy

    def evaluate(
        self,
        *,
        current_payload: dict[str, Any] | str | None,
        proposed_payload: dict[str, Any] | str,
        context: MutationContext,
    ) -> MutationDecision:
        proposed = _json_object(proposed_payload, "proposed_payload")
        current = None if current_payload is None else _json_object(current_payload, "current_payload")
        if not isinstance(context, MutationContext):
            raise DomainMutationError("context must be a MutationContext")

        current_version = None if current is None else _payload_version(current)
        proposed_version = _payload_version(proposed)
        current_frozen = False if current is None else _payload_frozen(current)
        proposed_frozen = _payload_frozen(proposed)
        current_author_locked = False if current is None else _payload_author_locked(current)
        proposed_author_locked = _payload_author_locked(proposed)

        if current is None:
            return self._decision(
                context,
                MutationOutcome.ALLOW,
                MutationReasonCode.CREATE_ALLOWED,
                current_version=current_version,
                proposed_version=proposed_version,
                current_frozen=current_frozen,
                proposed_frozen=proposed_frozen,
                current_author_locked=current_author_locked,
                proposed_author_locked=proposed_author_locked,
            )

        if _canonical_json(current) == _canonical_json(proposed):
            return self._decision(
                context,
                MutationOutcome.ALLOW,
                MutationReasonCode.IDEMPOTENT,
                current_version=current_version,
                proposed_version=proposed_version,
                current_frozen=current_frozen,
                proposed_frozen=proposed_frozen,
                current_author_locked=current_author_locked,
                proposed_author_locked=proposed_author_locked,
            )

        current_identity_key = _payload_identity_key(current, context.record_id)
        if (
            current_identity_key is not None
            and str(proposed.get(current_identity_key)) != context.record_id
        ):
            return self._deny(
                context,
                MutationReasonCode.DOMAIN_ID_CHANGED,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        if current_frozen and not proposed_frozen and not self.policy.allow_silent_unfreeze:
            return self._deny(
                context,
                MutationReasonCode.SILENT_UNFREEZE_DENIED,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        if current_author_locked and not proposed_author_locked and not self.policy.allow_silent_unlock:
            return self._deny(
                context,
                MutationReasonCode.SILENT_UNLOCK_DENIED,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        if current_frozen and current_author_locked:
            return self._deny(
                context,
                MutationReasonCode.FROZEN_AND_AUTHOR_LOCKED_RECORD,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        if current_frozen and not self.policy.allow_frozen_mutation:
            return self._deny(
                context,
                MutationReasonCode.FROZEN_RECORD,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        if current_author_locked and not self.policy.allow_author_locked_mutation:
            return self._deny(
                context,
                MutationReasonCode.AUTHOR_LOCKED_RECORD,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        if (
            self.policy.require_version_increment
            and current_version is not None
            and proposed_version is not None
            and proposed_version <= current_version
        ):
            return self._deny(
                context,
                MutationReasonCode.VERSION_NOT_INCREMENTED,
                current_version,
                proposed_version,
                current_frozen,
                proposed_frozen,
                current_author_locked,
                proposed_author_locked,
            )

        return self._decision(
            context,
            MutationOutcome.ALLOW,
            MutationReasonCode.UPDATE_ALLOWED,
            current_version=current_version,
            proposed_version=proposed_version,
            current_frozen=current_frozen,
            proposed_frozen=proposed_frozen,
            current_author_locked=current_author_locked,
            proposed_author_locked=proposed_author_locked,
        )

    def _deny(
        self,
        context: MutationContext,
        reason_code: MutationReasonCode,
        current_version: int | None,
        proposed_version: int | None,
        current_frozen: bool,
        proposed_frozen: bool,
        current_author_locked: bool,
        proposed_author_locked: bool,
    ) -> MutationDecision:
        return self._decision(
            context,
            MutationOutcome.DENY,
            reason_code,
            current_version=current_version,
            proposed_version=proposed_version,
            current_frozen=current_frozen,
            proposed_frozen=proposed_frozen,
            current_author_locked=current_author_locked,
            proposed_author_locked=proposed_author_locked,
        )

    def _decision(
        self,
        context: MutationContext,
        outcome: MutationOutcome,
        reason_code: MutationReasonCode,
        *,
        current_version: int | None,
        proposed_version: int | None,
        current_frozen: bool,
        proposed_frozen: bool,
        current_author_locked: bool,
        proposed_author_locked: bool,
    ) -> MutationDecision:
        return MutationDecision(
            outcome=outcome,
            reason_code=reason_code,
            project_id=context.project_id,
            record_type=context.record_type,
            record_id=context.record_id,
            mutation_type=context.mutation_type,
            source=context.source,
            actor_id=context.actor_id,
            current_version=current_version,
            proposed_version=proposed_version,
            current_frozen=current_frozen,
            proposed_frozen=proposed_frozen,
            current_author_locked=current_author_locked,
            proposed_author_locked=proposed_author_locked,
        )


def evaluate_domain_mutation(
    *,
    current_payload: dict[str, Any] | str | None,
    proposed_payload: dict[str, Any] | str,
    context: MutationContext,
    policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
) -> MutationDecision:
    return DomainMutationGuard(policy).evaluate(
        current_payload=current_payload,
        proposed_payload=proposed_payload,
        context=context,
    )


__all__ = [
    "DEFAULT_MUTATION_POLICY",
    "DOMAIN_MUTATION_GUARD_VERSION",
    "DomainMutationError",
    "DomainMutationGuard",
    "MutationContext",
    "MutationDecision",
    "MutationOutcome",
    "MutationPolicy",
    "MutationReasonCode",
    "MutationSource",
    "MutationType",
    "evaluate_domain_mutation",
]
