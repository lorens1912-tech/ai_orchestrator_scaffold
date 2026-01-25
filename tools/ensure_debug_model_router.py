from __future__ import annotations

from pathlib import Path

p = Path(r"app\main.py")
txt = p.read_text(encoding="utf-8")

import_line = "from app.debug_model_router import router as debug_model_router"
include_line = "app.include_router(debug_model_router)"

changed = False

if import_line not in txt:
    # wstaw import po innych importach (heurystyka: po ostatnim imporcie)
    lines = txt.splitlines()
    last_import_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_idx = i
    insert_at = last_import_idx + 1
    lines.insert(insert_at, import_line)
    txt = "\n".join(lines) + ("\n" if not txt.endswith("\n") else "")
    changed = True

if include_line not in txt:
    # wstaw include router możliwie blisko miejsca gdzie jest app = FastAPI(...)
    lines = txt.splitlines()
    app_idx = -1
    for i, line in enumerate(lines):
        if "FastAPI(" in line and "=" in line:
            app_idx = i
            break
    if app_idx == -1:
        raise SystemExit("Nie znaleziono instancji FastAPI w app/main.py")

    # znajdź miejsce po inicjalizacji app i ewentualnych middleware
    insert_at = app_idx + 1
    for i in range(app_idx + 1, min(app_idx + 80, len(lines))):
        if lines[i].strip().startswith("app.include_router("):
            insert_at = i + 1
    lines.insert(insert_at, include_line)
    txt = "\n".join(lines) + ("\n" if not txt.endswith("\n") else "")
    changed = True

if changed:
    p.write_text(txt, encoding="utf-8")
    print("[OK] patched app/main.py")
else:
    print("[OK] app/main.py already contains debug router")
