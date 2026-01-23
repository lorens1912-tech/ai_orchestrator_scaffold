
import argparse
import json
import os
from pathlib import Path

from tasks.book_pipeline import run

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PAYLOAD = BASE_DIR / "payload.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=str(DEFAULT_PAYLOAD), help="Path to payload.json")
    args = ap.parse_args()

    payload_path = Path(args.payload).resolve()
    if not payload_path.exists():
        raise FileNotFoundError(f"payload.json not found at {payload_path}")

    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    print("START.PY: PAYLOAD LOADED:")
    print(payload)

    result = run(payload)

    print("START.PY: PIPELINE FINISHED")
    print(result)


if __name__ == "__main__":
    main()
