from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from .config_registry import load_kernel, load_modes, load_presets, validate_all

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/kernel")
def get_kernel() -> Dict[str, Any]:
    return load_kernel()


@router.get("/modes")
def get_modes() -> Dict[str, Any]:
    return load_modes()


@router.get("/presets")
def get_presets() -> Dict[str, Any]:
    pd = load_presets()
    return {"presets": pd, "presets_count": len(pd)}


@router.get("/presets/{preset_id}")
def get_preset(preset_id: str) -> Dict[str, Any]:
    pd = load_presets()
    if preset_id not in pd:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {preset_id}")
    return {"id": preset_id, "preset": pd[preset_id]}


@router.get("/validate")
def validate() -> Dict[str, Any]:
    return validate_all()
