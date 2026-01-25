import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def load_json(relative_path: str) -> dict:
    path = BASE_DIR / relative_path
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
