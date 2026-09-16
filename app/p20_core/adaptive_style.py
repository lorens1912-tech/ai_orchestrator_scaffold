"""Adaptive Style Composition contracts and project-scoped P20 integration."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from app.p20_core.project_repository import ProjectRepository, ProjectStorageError


STYLE_SCHEMA_VERSION = 1
STYLE_DECAY_PER_DNA_VERSION = 0.25
NUMERIC_STYLE_FEATURES = (
    "pacing",
    "dialogue_density",
    "suspense",
    "syntax_variation",
    "sentence_length_mean",
    "sentence_length_variance",
    "figurative_density",
    "pov_intimacy",
)
LEXICAL_REGISTERS = ("PLAIN", "NEUTRAL", "FORMAL", "LYRICAL", "TECHNICAL")


class AdaptiveStyleError(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise AdaptiveStyleError(f"{name} is required")
    return normalized


def _version(value: Any, name: str = "version") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AdaptiveStyleError(f"{name} must be a positive integer")
    return value


def _unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdaptiveStyleError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 1:
        raise AdaptiveStyleError(f"{name} must be between 0 and 1")
    return result


@dataclass(frozen=True)
class StyleGenome:
    pacing: float
    dialogue_density: float
    suspense: float
    syntax_variation: float
    sentence_length_mean: float
    sentence_length_variance: float
    lexical_register: str
    figurative_density: float
    pov_intimacy: float

    def __post_init__(self) -> None:
        for name in NUMERIC_STYLE_FEATURES:
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        register = _text(self.lexical_register, "lexical_register").upper()
        if register not in LEXICAL_REGISTERS:
            raise AdaptiveStyleError(f"unsupported lexical_register: {register}")
        object.__setattr__(self, "lexical_register", register)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StyleGenome":
        return cls(**dict(value))


@dataclass(frozen=True)
class GenomeBound:
    minimum: float
    maximum: float
    target: float

    def __post_init__(self) -> None:
        minimum = _unit(self.minimum, "minimum")
        maximum = _unit(self.maximum, "maximum")
        target = _unit(self.target, "target")
        if minimum > maximum or not minimum <= target <= maximum:
            raise AdaptiveStyleError("genome bound requires minimum <= target <= maximum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(self, "target", target)

    def clamp(self, value: float) -> float:
        return round(min(self.maximum, max(self.minimum, value)), 6)

    def to_dict(self) -> dict[str, float]:
        return {"min": self.minimum, "max": self.maximum, "target": self.target}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GenomeBound":
        return cls(value.get("min"), value.get("max"), value.get("target"))


@dataclass(frozen=True)
class StyleLibraryProfile:
    profile_id: str
    source_ref: str
    extracted_techniques: tuple[str, ...]
    genome: StyleGenome
    applicability_tags: tuple[str, ...]
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile_id"))
        source = _text(self.source_ref, "source_ref")
        if re.search(r"\bwrite\s+like\b", source, re.IGNORECASE):
            raise AdaptiveStyleError("source_ref must be an abstract reference, not a runtime imitation instruction")
        object.__setattr__(self, "source_ref", source)
        object.__setattr__(self, "version", _version(self.version))
        object.__setattr__(self, "extracted_techniques", tuple(sorted({_text(x, "technique") for x in self.extracted_techniques})))
        object.__setattr__(self, "applicability_tags", tuple(sorted({_text(x, "applicability_tag") for x in self.applicability_tags})))
        if not isinstance(self.genome, StyleGenome):
            raise AdaptiveStyleError("genome must be StyleGenome")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "genome": self.genome.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StyleLibraryProfile":
        data = dict(value)
        data["genome"] = StyleGenome.from_dict(data["genome"])
        data["extracted_techniques"] = tuple(data.get("extracted_techniques", ()))
        data["applicability_tags"] = tuple(data.get("applicability_tags", ()))
        return cls(**data)


@dataclass(frozen=True)
class BookStyleDNA:
    book_id: str
    version: int
    genome_bounds: Mapping[str, GenomeBound]
    lexical_register: str
    locked_identity_markers: tuple[str, ...]
    derived_from: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "book_id", _text(self.book_id, "book_id"))
        object.__setattr__(self, "version", _version(self.version))
        object.__setattr__(self, "derived_from", _version(self.derived_from, "derived_from"))
        if set(self.genome_bounds) != set(NUMERIC_STYLE_FEATURES):
            raise AdaptiveStyleError("genome_bounds must define every numeric StyleGenome feature exactly once")
        bounds = {}
        for name in NUMERIC_STYLE_FEATURES:
            bound = self.genome_bounds[name]
            bounds[name] = bound if isinstance(bound, GenomeBound) else GenomeBound.from_dict(bound)
        object.__setattr__(self, "genome_bounds", bounds)
        register = _text(self.lexical_register, "lexical_register").upper()
        if register not in LEXICAL_REGISTERS:
            raise AdaptiveStyleError(f"unsupported lexical_register: {register}")
        object.__setattr__(self, "lexical_register", register)
        markers = tuple(sorted({_text(x, "locked_identity_marker") for x in self.locked_identity_markers}))
        unknown = set(markers) - (set(NUMERIC_STYLE_FEATURES) | {"lexical_register"})
        if unknown:
            raise AdaptiveStyleError(f"unknown locked identity markers: {sorted(unknown)}")
        object.__setattr__(self, "locked_identity_markers", markers)

    def target_genome(self) -> StyleGenome:
        return StyleGenome(
            **{name: self.genome_bounds[name].target for name in NUMERIC_STYLE_FEATURES},
            lexical_register=self.lexical_register,
        )

    def bounds_genome(self, genome: StyleGenome) -> StyleGenome:
        values = {
            name: (
                self.genome_bounds[name].target
                if name in self.locked_identity_markers
                else self.genome_bounds[name].clamp(getattr(genome, name))
            )
            for name in NUMERIC_STYLE_FEATURES
        }
        values["lexical_register"] = self.lexical_register
        return StyleGenome(**values)

    def contains(self, genome: StyleGenome) -> bool:
        return genome.lexical_register == self.lexical_register and all(
            self.genome_bounds[name].minimum <= getattr(genome, name) <= self.genome_bounds[name].maximum
            and (name not in self.locked_identity_markers or getattr(genome, name) == self.genome_bounds[name].target)
            for name in NUMERIC_STYLE_FEATURES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "version": self.version,
            "genome_bounds": {name: self.genome_bounds[name].to_dict() for name in NUMERIC_STYLE_FEATURES},
            "lexical_register": self.lexical_register,
            "locked_identity_markers": list(self.locked_identity_markers),
            "derived_from": self.derived_from,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BookStyleDNA":
        data = dict(value)
        data["locked_identity_markers"] = tuple(data.get("locked_identity_markers", ()))
        return cls(**data)


@dataclass(frozen=True)
class SceneIndexKey:
    scene_type: str
    narrative_function: str
    pov: str
    target_tension: float
    target_pace: float
    book_style_dna_version: int

    def __post_init__(self) -> None:
        for name in ("scene_type", "narrative_function", "pov"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "target_tension", _unit(self.target_tension, "target_tension"))
        object.__setattr__(self, "target_pace", _unit(self.target_pace, "target_pace"))
        object.__setattr__(self, "book_style_dna_version", _version(self.book_style_dna_version, "book_style_dna_version"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneIndexKey":
        return cls(**dict(value))

    @property
    def hash(self) -> str:
        return _hash(self.to_dict())


@dataclass(frozen=True)
class SceneStyleRecipe:
    recipe_id: str
    scene_id: str
    book_style_dna_version: int
    target_genome: StyleGenome
    selected_techniques: tuple[str, ...]
    rationale: str
    scene_index_key: SceneIndexKey
    provenance: tuple[str, ...]
    low_confidence: bool
    recipe_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipe_id", _text(self.recipe_id, "recipe_id"))
        object.__setattr__(self, "scene_id", _text(self.scene_id, "scene_id"))
        object.__setattr__(self, "book_style_dna_version", _version(self.book_style_dna_version, "book_style_dna_version"))
        object.__setattr__(self, "selected_techniques", tuple(sorted({_text(x, "technique") for x in self.selected_techniques})))
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))
        object.__setattr__(self, "provenance", tuple(sorted({_text(x, "provenance") for x in self.provenance})))
        if not isinstance(self.low_confidence, bool):
            raise AdaptiveStyleError("low_confidence must be boolean")
        if self.scene_index_key.book_style_dna_version != self.book_style_dna_version:
            raise AdaptiveStyleError("recipe DNA version and SceneIndexKey version differ")
        payload = self.to_dict(include_hash=False)
        expected = _hash(payload)
        if self.recipe_hash and self.recipe_hash != expected:
            raise AdaptiveStyleError("recipe_hash does not match recipe content")
        object.__setattr__(self, "recipe_hash", expected)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "recipe_id": self.recipe_id,
            "scene_id": self.scene_id,
            "book_style_dna_version": self.book_style_dna_version,
            "target_genome": self.target_genome.to_dict(),
            "selected_techniques": list(self.selected_techniques),
            "rationale": self.rationale,
            "scene_index_key": self.scene_index_key.to_dict(),
            "provenance": list(self.provenance),
            "low_confidence": self.low_confidence,
        }
        if include_hash:
            value["recipe_hash"] = self.recipe_hash
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneStyleRecipe":
        data = dict(value)
        data["target_genome"] = StyleGenome.from_dict(data["target_genome"])
        data["scene_index_key"] = SceneIndexKey.from_dict(data["scene_index_key"])
        data["selected_techniques"] = tuple(data.get("selected_techniques", ()))
        data["provenance"] = tuple(data.get("provenance", ()))
        return cls(**data)


@dataclass(frozen=True)
class StyleEvaluation:
    style_evaluation_id: str
    scene_id: str
    recipe_id: str
    artifact_id: str
    artifact_hash: str
    achieved_genome: StyleGenome
    deviation_from_target: float
    dna_compliance: bool
    style_score: float
    style_status: str
    reasons: tuple[str, ...] = ()
    must_fix: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("style_evaluation_id", "scene_id", "recipe_id", "artifact_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not re.fullmatch(r"[0-9a-f]{64}", self.artifact_hash):
            raise AdaptiveStyleError("artifact_hash must be sha256")
        object.__setattr__(self, "deviation_from_target", _unit(self.deviation_from_target, "deviation_from_target"))
        object.__setattr__(self, "style_score", _unit(self.style_score, "style_score"))
        if not isinstance(self.dna_compliance, bool):
            raise AdaptiveStyleError("dna_compliance must be boolean")
        status = _text(self.style_status, "style_status").upper()
        if status not in {"COMPLIANT", "NEEDS_REVISION", "NONCOMPLIANT"}:
            raise AdaptiveStyleError("invalid style_status")
        object.__setattr__(self, "style_status", status)
        object.__setattr__(self, "reasons", tuple(_text(x, "reason") for x in self.reasons))
        object.__setattr__(self, "must_fix", tuple(_text(x, "must_fix") for x in self.must_fix))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "achieved_genome": self.achieved_genome.to_dict(), "reasons": list(self.reasons), "must_fix": list(self.must_fix)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StyleEvaluation":
        data = dict(value)
        data["achieved_genome"] = StyleGenome.from_dict(data["achieved_genome"])
        data["reasons"] = tuple(data.get("reasons", ()))
        data["must_fix"] = tuple(data.get("must_fix", ()))
        return cls(**data)


@dataclass(frozen=True)
class StylePerformanceRecord:
    performance_id: str
    recipe_id: str
    scene_index_key: SceneIndexKey
    quality_score: float
    dna_version: int
    style_evaluation_id: str
    quality_evaluation_id: str
    accepted_artifact_id: str
    accepted_artifact_hash: str
    achieved_genome: StyleGenome
    status: str = "ACCEPTED"

    def __post_init__(self) -> None:
        for name in ("performance_id", "recipe_id", "style_evaluation_id", "quality_evaluation_id", "accepted_artifact_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "quality_score", _unit(self.quality_score, "quality_score"))
        object.__setattr__(self, "dna_version", _version(self.dna_version, "dna_version"))
        if self.scene_index_key.book_style_dna_version != self.dna_version:
            raise AdaptiveStyleError("performance DNA version and SceneIndexKey version differ")
        if not re.fullmatch(r"[0-9a-f]{64}", self.accepted_artifact_hash):
            raise AdaptiveStyleError("accepted_artifact_hash must be sha256")
        if self.status != "ACCEPTED":
            raise AdaptiveStyleError("StylePerformanceRecord status must be ACCEPTED")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "scene_index_key": self.scene_index_key.to_dict(), "achieved_genome": self.achieved_genome.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StylePerformanceRecord":
        data = dict(value)
        data["scene_index_key"] = SceneIndexKey.from_dict(data["scene_index_key"])
        data["achieved_genome"] = StyleGenome.from_dict(data["achieved_genome"])
        return cls(**data)

    def decayed_weight(self, current_dna_version: int) -> float:
        gap = max(0, current_dna_version - self.dna_version)
        return round(self.quality_score * (STYLE_DECAY_PER_DNA_VERSION ** gap), 8)


class StyleRepository:
    """Persistence owner for project-scoped style state in project.db."""

    def __init__(self, project_repository: ProjectRepository) -> None:
        self.project_repository = project_repository

    def _write(self, table: str, key_columns: tuple[str, ...], key_values: tuple[Any, ...], payload: Mapping[str, Any]) -> None:
        self.project_repository.initialize()
        payload_json = _canonical_json(payload)
        scoped_columns = ("scope_type", "scope_id") + key_columns
        scoped_values = ("PROJECT", self.project_repository.scope.scope_id) + key_values
        where = " AND ".join(f"{column} = ?" for column in scoped_columns)
        with self.project_repository.connect() as conn:
            existing = conn.execute(f"SELECT payload_json FROM {table} WHERE {where}", scoped_values).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise ProjectStorageError(f"{table} identity already exists with different content")
                return
            columns = scoped_columns + ("payload_json",)
            placeholders = ",".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                scoped_values + (payload_json,),
            )

    def save_library_profile(self, profile: StyleLibraryProfile) -> None:
        self._write("style_library_profiles", ("profile_id", "version"), (profile.profile_id, profile.version), profile.to_dict())

    def list_library_profiles(self) -> tuple[StyleLibraryProfile, ...]:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM style_library_profiles WHERE scope_type='PROJECT' AND scope_id=? ORDER BY profile_id, version", (self.project_repository.scope.scope_id,)).fetchall()
        return tuple(StyleLibraryProfile.from_dict(json.loads(row["payload_json"])) for row in rows)

    def save_book_style_dna(self, dna: BookStyleDNA) -> None:
        if dna.book_id != self.project_repository.context.book_id:
            raise ProjectStorageError("BookStyleDNA book scope does not match repository")
        self.project_repository.initialize()
        payload_json = _canonical_json(dna.to_dict())
        with self.project_repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT payload_json FROM book_style_dna WHERE scope_type='PROJECT' AND scope_id=? AND book_id=? AND version=?", (self.project_repository.scope.scope_id, dna.book_id, dna.version)).fetchone()
            if existing is not None and str(existing["payload_json"]) != payload_json:
                raise ProjectStorageError("BookStyleDNA version already exists with different content")
            conn.execute("UPDATE book_style_dna SET is_active=0 WHERE scope_type='PROJECT' AND scope_id=? AND book_id=?", (self.project_repository.scope.scope_id, dna.book_id))
            if existing is None:
                conn.execute("INSERT INTO book_style_dna(scope_type,scope_id,book_id,version,is_active,payload_json) VALUES('PROJECT',?,?,?,?,?)", (self.project_repository.scope.scope_id, dna.book_id, dna.version, 1, payload_json))
            else:
                conn.execute("UPDATE book_style_dna SET is_active=1 WHERE scope_type='PROJECT' AND scope_id=? AND book_id=? AND version=?", (self.project_repository.scope.scope_id, dna.book_id, dna.version))

    def get_active_book_style_dna(self) -> BookStyleDNA | None:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            row = conn.execute("SELECT payload_json FROM book_style_dna WHERE scope_type='PROJECT' AND scope_id=? AND book_id=? AND is_active=1", (self.project_repository.scope.scope_id, self.project_repository.context.book_id)).fetchone()
        return None if row is None else BookStyleDNA.from_dict(json.loads(row["payload_json"]))

    def list_book_style_dna(self) -> tuple[BookStyleDNA, ...]:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM book_style_dna WHERE scope_type='PROJECT' AND scope_id=? AND book_id=? ORDER BY version", (self.project_repository.scope.scope_id, self.project_repository.context.book_id)).fetchall()
        return tuple(BookStyleDNA.from_dict(json.loads(row["payload_json"])) for row in rows)

    def save_recipe(self, recipe: SceneStyleRecipe) -> None:
        self._write("scene_style_recipes", ("recipe_id",), (recipe.recipe_id,), recipe.to_dict())

    def get_recipe(self, recipe_id: str) -> SceneStyleRecipe | None:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            row = conn.execute("SELECT payload_json FROM scene_style_recipes WHERE scope_type='PROJECT' AND scope_id=? AND recipe_id=?", (self.project_repository.scope.scope_id, recipe_id)).fetchone()
        return None if row is None else SceneStyleRecipe.from_dict(json.loads(row["payload_json"]))

    def find_recipe_for_scene(self, scene_id: str, dna_version: int) -> SceneStyleRecipe | None:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM scene_style_recipes WHERE scope_type='PROJECT' AND scope_id=? ORDER BY recipe_id", (self.project_repository.scope.scope_id,)).fetchall()
        matches = [
            SceneStyleRecipe.from_dict(json.loads(row["payload_json"]))
            for row in rows
        ]
        matches = [
            item for item in matches
            if item.scene_id == scene_id and item.book_style_dna_version == dna_version
        ]
        return matches[0] if matches else None

    def bind_recipe_operation(self, operation_id: str, recipe: SceneStyleRecipe) -> None:
        key = "adaptive_style_recipe.v1:" + _hash(_text(operation_id, "operation_id"))
        existing = self.project_repository.get_metadata(key)
        if existing is not None and existing != recipe.recipe_id:
            raise ProjectStorageError("adaptive style operation is already bound to a different recipe")
        self.project_repository.set_metadata(key, recipe.recipe_id)

    def get_recipe_for_operation(self, operation_id: str) -> SceneStyleRecipe | None:
        key = "adaptive_style_recipe.v1:" + _hash(_text(operation_id, "operation_id"))
        recipe_id = self.project_repository.get_metadata(key)
        return None if recipe_id is None else self.get_recipe(recipe_id)

    def save_evaluation(self, evaluation: StyleEvaluation) -> None:
        self._write("style_evaluations", ("style_evaluation_id",), (evaluation.style_evaluation_id,), evaluation.to_dict())

    def list_evaluations(self) -> tuple[StyleEvaluation, ...]:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM style_evaluations WHERE scope_type='PROJECT' AND scope_id=? ORDER BY style_evaluation_id", (self.project_repository.scope.scope_id,)).fetchall()
        return tuple(StyleEvaluation.from_dict(json.loads(row["payload_json"])) for row in rows)

    def save_performance(self, record: StylePerformanceRecord) -> None:
        self._write("style_performance_records", ("performance_id",), (record.performance_id,), record.to_dict())

    def list_performance(self) -> tuple[StylePerformanceRecord, ...]:
        self.project_repository.initialize()
        with self.project_repository.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM style_performance_records WHERE scope_type='PROJECT' AND scope_id=? ORDER BY performance_id", (self.project_repository.scope.scope_id,)).fetchall()
        return tuple(StylePerformanceRecord.from_dict(json.loads(row["payload_json"])) for row in rows)


class StyleComposer:
    def compose(self, *, dna: BookStyleDNA, scene_id: str, scene_index_key: SceneIndexKey, library: Iterable[StyleLibraryProfile], history: Iterable[StylePerformanceRecord]) -> SceneStyleRecipe:
        if scene_index_key.book_style_dna_version != dna.version:
            raise AdaptiveStyleError("SceneIndexKey must use active BookStyleDNA version")
        profiles = tuple(sorted(library, key=lambda item: (item.profile_id, item.version)))
        exact = tuple(item for item in history if item.scene_index_key.to_dict() == scene_index_key.to_dict())
        fallback = tuple(item for item in history if item.scene_index_key.scene_type == scene_index_key.scene_type and item.scene_index_key.narrative_function == scene_index_key.narrative_function)
        selected_history = exact or fallback
        low_confidence = not bool(exact)
        matching_profiles = tuple(profile for profile in profiles if not profile.applicability_tags or scene_index_key.scene_type in profile.applicability_tags or scene_index_key.narrative_function in profile.applicability_tags)
        values: dict[str, Any] = {}
        for name in NUMERIC_STYLE_FEATURES:
            target = dna.genome_bounds[name].target
            components = [(target, 0.6)]
            if matching_profiles:
                components.append((sum(getattr(item.genome, name) for item in matching_profiles) / len(matching_profiles), 0.2))
            weighted_history = [(getattr(item.achieved_genome, name), item.decayed_weight(dna.version)) for item in selected_history]
            if weighted_history and sum(weight for _value, weight in weighted_history) > 0:
                components.append((sum(value * weight for value, weight in weighted_history) / sum(weight for _value, weight in weighted_history), 0.2))
            total_weight = sum(weight for _value, weight in components)
            values[name] = dna.genome_bounds[name].clamp(sum(value * weight for value, weight in components) / total_weight)
        values["lexical_register"] = dna.lexical_register
        target_genome = dna.bounds_genome(StyleGenome(**values))
        if not dna.contains(target_genome):
            raise AdaptiveStyleError("composer produced a recipe outside BookStyleDNA")
        techniques = tuple(sorted({technique for profile in matching_profiles for technique in profile.extracted_techniques}))
        provenance = (f"BookStyleDNA:{dna.book_id}:v{dna.version}",) + tuple(f"StyleLibraryProfile:{p.profile_id}:v{p.version}" for p in matching_profiles) + tuple(f"StylePerformanceRecord:{r.performance_id}:weight={r.decayed_weight(dna.version)}" for r in selected_history)
        semantic = {"scene_id": scene_id, "dna_version": dna.version, "target_genome": target_genome.to_dict(), "techniques": techniques, "scene_index_key": scene_index_key.to_dict(), "provenance": provenance, "low_confidence": low_confidence}
        digest = _hash(semantic)
        return SceneStyleRecipe(recipe_id=f"STYLE-RECIPE-{digest[:24]}", scene_id=scene_id, book_style_dna_version=dna.version, target_genome=target_genome, selected_techniques=techniques, rationale="deterministic bounded composition from DNA, applicable library profiles, and indexed performance history", scene_index_key=scene_index_key, provenance=provenance, low_confidence=low_confidence)


class StyleCritic:
    def evaluate(self, *, dna: BookStyleDNA, recipe: SceneStyleRecipe, artifact_id: str, text: str, achieved_genome: StyleGenome | None = None) -> StyleEvaluation:
        artifact_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        achieved_was_reported = achieved_genome is not None
        achieved = achieved_genome or recipe.target_genome
        deviations = [abs(getattr(achieved, name) - getattr(recipe.target_genome, name)) for name in NUMERIC_STYLE_FEATURES]
        deviation = round(sum(deviations) / len(deviations), 6)
        compliant = achieved_was_reported and dna.contains(achieved)
        status = "COMPLIANT" if compliant and deviation <= 0.2 else ("NEEDS_REVISION" if compliant else "NONCOMPLIANT")
        reasons = () if status == "COMPLIANT" else (
            ("writer did not report achieved abstract style features",)
            if not achieved_was_reported
            else (("target genome deviation exceeds tolerance",) if compliant else ("achieved genome violates BookStyleDNA bounds",))
        )
        eval_id = "STYLE-EVAL-" + _hash({"recipe_id": recipe.recipe_id, "artifact_hash": artifact_hash})[:24]
        return StyleEvaluation(style_evaluation_id=eval_id, scene_id=recipe.scene_id, recipe_id=recipe.recipe_id, artifact_id=artifact_id, artifact_hash=artifact_hash, achieved_genome=achieved, deviation_from_target=min(1, deviation), dna_compliance=compliant, style_score=round(max(0, 1 - deviation), 6), style_status=status, reasons=reasons, must_fix=reasons)


@dataclass
class AdaptiveStyleSession:
    repository: StyleRepository
    dna: BookStyleDNA
    recipe: SceneStyleRecipe
    latest_evaluation: StyleEvaluation | None = field(default=None, init=False)

    def writer_features(self) -> dict[str, Any]:
        return {"target_genome": self.recipe.target_genome.to_dict(), "selected_techniques": list(self.recipe.selected_techniques), "recipe_id": self.recipe.recipe_id, "recipe_hash": self.recipe.recipe_hash}

    def evaluate(self, *, artifact_id: str, text: str, achieved_genome: Mapping[str, Any] | None = None) -> StyleEvaluation:
        evaluation = StyleCritic().evaluate(dna=self.dna, recipe=self.recipe, artifact_id=artifact_id, text=text, achieved_genome=StyleGenome.from_dict(achieved_genome) if achieved_genome else None)
        self.repository.save_evaluation(evaluation)
        self.latest_evaluation = evaluation
        return evaluation

    def finalize(self, *, quality_decision: str, quality_score: float, quality_evaluation_id: str, quality_artifact_hash: str, artifact_id: str, artifact_text: str) -> StylePerformanceRecord | None:
        evaluation = self.latest_evaluation
        artifact_hash = hashlib.sha256(artifact_text.encode("utf-8")).hexdigest()
        if quality_decision != "ACCEPT" or quality_artifact_hash != artifact_hash or evaluation is None or evaluation.style_status != "COMPLIANT" or evaluation.artifact_hash != artifact_hash or evaluation.artifact_id != artifact_id:
            return None
        performance_id = "STYLE-PERF-" + _hash({"recipe_id": self.recipe.recipe_id, "artifact_hash": artifact_hash})[:24]
        record = StylePerformanceRecord(performance_id=performance_id, recipe_id=self.recipe.recipe_id, scene_index_key=self.recipe.scene_index_key, quality_score=quality_score, dna_version=self.dna.version, style_evaluation_id=evaluation.style_evaluation_id, quality_evaluation_id=quality_evaluation_id, accepted_artifact_id=artifact_id, accepted_artifact_hash=artifact_hash, achieved_genome=evaluation.achieved_genome)
        self.repository.save_performance(record)
        return record


def prepare_adaptive_style_session(
    project_repository: ProjectRepository,
    payload: Mapping[str, Any],
    *,
    technical_retry: bool = False,
    operation_id: str | None = None,
) -> AdaptiveStyleSession | None:
    config = payload.get("adaptive_style")
    style_repository = StyleRepository(project_repository)
    if config is None:
        return None
    if not isinstance(config, Mapping):
        raise AdaptiveStyleError("adaptive_style must be an object")
    dna_payload = config.get("book_style_dna")
    if dna_payload is not None:
        dna = BookStyleDNA.from_dict(dna_payload)
        style_repository.save_book_style_dna(dna)
    else:
        dna = style_repository.get_active_book_style_dna()
    if dna is None:
        raise AdaptiveStyleError("adaptive_style requires persisted or supplied BookStyleDNA")
    for item in config.get("style_library_profiles", ()):
        style_repository.save_library_profile(StyleLibraryProfile.from_dict(item))
    scene = config.get("scene")
    if not isinstance(scene, Mapping):
        raise AdaptiveStyleError("adaptive_style.scene is required")
    scene_id = _text(scene.get("scene_id"), "scene_id")
    key = SceneIndexKey(scene_type=scene.get("scene_type"), narrative_function=scene.get("narrative_function"), pov=scene.get("pov"), target_tension=scene.get("target_tension"), target_pace=scene.get("target_pace"), book_style_dna_version=dna.version)
    recipe = (
        style_repository.get_recipe_for_operation(operation_id)
        if operation_id is not None
        else None
    )
    if recipe is None and technical_retry:
        recipe = style_repository.find_recipe_for_scene(scene_id, dna.version)
    if recipe is not None and recipe.scene_index_key != key:
        raise AdaptiveStyleError("technical retry scene context does not match persisted recipe")
    if recipe is None:
        recipe = StyleComposer().compose(dna=dna, scene_id=scene_id, scene_index_key=key, library=style_repository.list_library_profiles(), history=style_repository.list_performance())
    style_repository.save_recipe(recipe)
    if operation_id is not None:
        style_repository.bind_recipe_operation(operation_id, recipe)
    return AdaptiveStyleSession(style_repository, dna, recipe)


__all__ = [
    "AdaptiveStyleError", "AdaptiveStyleSession", "BookStyleDNA", "GenomeBound",
    "SceneIndexKey", "SceneStyleRecipe", "StyleComposer", "StyleCritic", "StyleEvaluation",
    "StyleGenome", "StyleLibraryProfile", "StylePerformanceRecord", "StyleRepository",
    "prepare_adaptive_style_session",
]
