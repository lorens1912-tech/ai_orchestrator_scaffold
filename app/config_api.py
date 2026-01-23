from __future__ import annotations

from fastapi import APIRouter, HTTPException
from .config_registry import load_kernel, load_modes, load_presets, validate_all, ConfigError

router = APIRouter(prefix="/config", tags=["config"])

@router.get("/kernel")
def get_kernel():
    try:
        return load_kernel()
    except ConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/modes")
def get_modes():
    try:
        return load_modes()
    except ConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/presets")
def get_presets():
    try:
        return load_presets()
    except ConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/validate")
def validate():
    try:
        return validate_all()
    except ConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))
