from fastapi import APIRouter, HTTPException

from .config_registry import load_kernel, load_modes, load_presets, validate_all

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/validate")
def validate_config():
    try:
        return validate_all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kernel")
def get_kernel():
    try:
        return load_kernel()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/modes")
def get_modes():
    try:
        return load_modes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/presets")
def get_presets():
    try:
        return load_presets()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
