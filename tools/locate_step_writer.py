from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = []
KEYS = [
    "runs", "steps", "result", "payload", "tool",
    "json.dump", "json.dumps", "write_text", "write_bytes",
    "StepResult", "artifact", "step_", "save_", "write_step",
    "APIRouter", "add_api_route", "include_router",
]

def score(text: str) -> int:
    s = 0
    for k in KEYS:
        if k in text:
            s += 1
    # bonus: coś w stylu "steps/<num>_"
    if re.search(r"steps.*\d{3}_", text):
        s += 3
    # bonus: kluczowa struktura
    if "payload" in text and "tool" in text and "result" in text:
        s += 3
    return s

def main() -> int:
    py_files = list((ROOT / "app").rglob("*.py"))
    py_files += [p for p in [ROOT/"main.py", ROOT/"app"/"main.py"] if p.exists()]

    rows = []
    for p in py_files:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        sc = score(txt)
        if sc <= 0:
            continue
        rows.append((sc, p))

    rows.sort(key=lambda x: x[0], reverse=True)

    print("TOP CANDIDATES (highest score first):")
    for sc, p in rows[:12]:
        print(f"- {sc:02d}  {p.relative_to(ROOT)}")

    print("\nHINT LINES (first matches):")
    patterns = [
        r"steps", r"runs", r"result", r"payload", r"tool",
        r"json\.dump", r"json\.dumps", r"write_text", r"StepResult",
    ]
    rx = re.compile("|".join(patterns))
    for sc, p in rows[:8]:
        txt = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(txt, start=1):
            if rx.search(line):
                print(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
                break

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
