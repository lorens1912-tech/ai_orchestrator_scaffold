"""Validated, curated AgentPRO Style Library package.

Loading and seeding are explicit operator actions. Runtime WRITE paths only
read profiles already persisted by :class:`StyleRepository`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.p20_core.adaptive_style import AdaptiveStyleError, StyleLibraryProfile, StyleRepository


CATALOG_ID = "AGENTPRO_STYLE_LIBRARY_24"
CATALOG_VERSION = 1
CATALOG_PROFILE_SET_HASH = "ffb23daa35b5c5dfd2bc8f76aec30460f44f0b1d63ead0c4ae6020eeb0b04893"
CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "style_library"
    / "agentpro_style_library_24_v1.json"
)
EXPECTED_PROFILE_NAMES = (
    "Freida McFadden",
    "Dan Brown",
    "Lee Child",
    "Graham Brown",
    "Gillian Flynn",
    "Graham Masterton",
    "Michael Connelly",
    "Dennis Lehane",
    "Stephen King",
    "Michael Crichton",
    "Tom Clancy",
    "Frank Herbert",
    "George R.R. Martin",
    "Philip K. Dick",
    "Arthur C. Clarke",
    "Liu Cixin",
    "Andy Weir",
    "Blake Crouch",
    "Harlan Coben",
    "Tana French",
    "Donna Tartt",
    "Cormac McCarthy",
    "Raymond Chandler",
    "Dashiell Hammett",
)


def _load_document(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdaptiveStyleError(f"cannot load Style Library catalog: {path}") from exc
    if not isinstance(value, dict):
        raise AdaptiveStyleError("Style Library catalog must be an object")
    return value


def catalog_profiles(path: Path = CATALOG_PATH) -> tuple[StyleLibraryProfile, ...]:
    document = _load_document(path)
    if document.get("package_id") != CATALOG_ID:
        raise AdaptiveStyleError("unexpected Style Library package_id")
    if document.get("package_version") != CATALOG_VERSION:
        raise AdaptiveStyleError("unexpected Style Library package_version")
    if document.get("source_profile_set_hash") != CATALOG_PROFILE_SET_HASH:
        raise AdaptiveStyleError("Style Library source profile set hash differs from verified source")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list) or len(raw_profiles) != len(EXPECTED_PROFILE_NAMES):
        raise AdaptiveStyleError("Style Library catalog must contain exactly 24 profiles")

    profiles = tuple(StyleLibraryProfile.from_dict(item) for item in raw_profiles)
    names = tuple(profile.display_name for profile in profiles)
    if names != EXPECTED_PROFILE_NAMES:
        raise AdaptiveStyleError("Style Library profile list differs from verified 24-profile source")
    if len({profile.profile_id for profile in profiles}) != len(profiles):
        raise AdaptiveStyleError("Style Library profile_id values must be unique")
    if len({name.casefold() for name in names}) != len(profiles):
        raise AdaptiveStyleError("Style Library display names must be unique")

    for profile in profiles:
        raw_source = profile.source_metadata.get("raw_profile_markdown")
        if not isinstance(raw_source, str) or not raw_source.strip():
            raise AdaptiveStyleError(f"profile {profile.profile_id} has no source excerpt")
        source_hash = hashlib.sha256(raw_source.encode("utf-8")).hexdigest()
        if source_hash != profile.source_hash:
            raise AdaptiveStyleError(f"profile {profile.profile_id} source hash mismatch")
    return profiles


def seed_curated_style_library(repository: StyleRepository) -> dict[str, Any]:
    profiles = catalog_profiles()
    for profile in profiles:
        repository.save_library_profile(profile)
    persisted = {
        (item.profile_id, item.version): item
        for item in repository.list_library_profiles()
    }
    expected = {(item.profile_id, item.version): item for item in profiles}
    missing = sorted(set(expected) - set(persisted))
    if missing:
        raise AdaptiveStyleError(f"Style Library profiles missing after seed: {missing}")
    for key, profile in expected.items():
        if persisted[key].to_dict() != profile.to_dict():
            raise AdaptiveStyleError(f"Style Library persisted profile differs after seed: {key}")
    return {
        "package_id": CATALOG_ID,
        "package_version": CATALOG_VERSION,
        "source_profile_set_hash": CATALOG_PROFILE_SET_HASH,
        "profiles_loaded": len(profiles),
        "unique_authors": len({profile.display_name.casefold() for profile in profiles}),
        "duplicates": 0,
    }


__all__ = [
    "CATALOG_ID",
    "CATALOG_PATH",
    "CATALOG_PROFILE_SET_HASH",
    "CATALOG_VERSION",
    "EXPECTED_PROFILE_NAMES",
    "catalog_profiles",
    "seed_curated_style_library",
]
