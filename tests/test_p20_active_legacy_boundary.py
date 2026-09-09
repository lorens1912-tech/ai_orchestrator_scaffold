from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ACTIVE_IMPORT_SOURCES = [
    Path("main.py"),
    Path("app/main.py"),
    Path("app/p20_core/runtime.py"),
    Path("app/p20_core/canon_rebuild.py"),
]

FORBIDDEN_LEGACY_MODULES = [
    "agents",
    "tasks",
    "tools.memory",
    "llm_client",
    "run_task",
    "app.main_agent",
    "server_agent",
    "app.novel",
    "app.novel_app",
    "app.p20_core.agent_step",
    "app.p20_core.book_bible",
    "app.p20_core.book_bible_agent_step_guard",
    "app.p20_core.agent_step_book_bible_route_wrapper",
]


def _is_forbidden(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(f"{forbidden}.")


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append((node.lineno, alias.name))
            continue

        if isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            if node.module:
                imported.append((node.lineno, base))
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.append((node.lineno, f"{base}.{alias.name}" if base else alias.name))

    return imported


def test_active_p20_entrypoints_do_not_import_forbidden_legacy_modules() -> None:
    violations: list[str] = []

    for relative_path in ACTIVE_IMPORT_SOURCES:
        path = ROOT / relative_path
        for line_no, imported in _imported_modules(path):
            for forbidden in FORBIDDEN_LEGACY_MODULES:
                if _is_forbidden(imported, forbidden):
                    violations.append(
                        f"{relative_path.as_posix()}:{line_no}: "
                        f"imports forbidden legacy module {imported!r} "
                        f"(matched {forbidden!r})"
                    )

    assert violations == []


def test_importing_active_api_does_not_load_forbidden_legacy_modules() -> None:
    code = """
import json
import sys

from app.main import app  # noqa: F401

forbidden = [
    "agents",
    "tasks",
    "tools.memory",
    "llm_client",
    "run_task",
    "app.main_agent",
    "server_agent",
    "app.novel",
    "app.novel_app",
    "app.p20_core.agent_step",
    "app.p20_core.book_bible",
    "app.p20_core.book_bible_agent_step_guard",
    "app.p20_core.agent_step_book_bible_route_wrapper",
]

loaded = []
for name in forbidden:
    if name in sys.modules or any(mod.startswith(name + ".") for mod in sys.modules):
        loaded.append(name)

print(json.dumps({
    "loaded_forbidden": loaded,
    "active_legacy_dependency_loaded": "app.orchestrator_stub" in sys.modules,
}, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)

    assert data["loaded_forbidden"] == []
