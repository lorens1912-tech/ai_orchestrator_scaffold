
import argparse
import json
from pathlib import Path

from tasks.registry import run_pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--out", default="", help="Output json path (default: next to payload)")
    args = parser.parse_args()

    payload_path = Path(args.payload).resolve()
    if not payload_path.exists():
        raise FileNotFoundError(f"payload not found: {payload_path}")

    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    result = run_pipeline(args.task, payload)

    out_path = Path(args.out).resolve() if args.out else (payload_path.parent / "out_book.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("OK")


if __name__ == "__main__":
    main()
