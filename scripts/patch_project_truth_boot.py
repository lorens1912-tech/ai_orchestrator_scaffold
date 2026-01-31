from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BOOKS = ROOT / "books"

DEFAULT_TRUTH = """# PROJECT TRUTH — AgentAI Author Platform (PRIVATE / PRO)

## Non-negotiable goal
Build a private, professional author-agent system that can write serious long-form material and books (200k+ words) with:
- enforced canon (characters/facts/timeline/who-knows-what)
- thread registry (open/close/payoff)
- quality loop (critic -> revise -> quality; retry)
- resumable/auditable runs
- multi-book, multi-genre (genre is config, not identity)

## P0 Definition of Done
- Canon is DATA (not prompt memory) and is injected before every WRITE.
- Threads are DATA and updated every chapter/segment.
- Quality gate returns ACCEPT/REVISE/REJECT with deterministic retry.
- Marathon benchmark exists (E2E): long run updates canon/threads and blocks on hard contradictions.

## Not the goal (do not drift)
- SaaS/product-market features (billing/auth/onboarding)
- genre tangents (no fantasy talk unless project config asks)
"""

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def ensure_project_truth() -> Path:
    p = ROOT / "PROJECT_TRUTH.md"
    if not p.exists():
        atomic_write_text(p, DEFAULT_TRUTH)
    return p

def write_file(path: Path, content: str) -> None:
    atomic_write_text(path, content)

def patch_orchestrator() -> None:
    p = APP / "orchestrator_stub.py"
    if not p.exists():
        raise SystemExit("BLOKER: app/orchestrator_stub.py not found")

    s = p.read_text(encoding="utf-8")

    # add import if missing
    if "from app.tool_dispatcher import dispatch_tool" not in s:
        # try to insert after existing imports
        m = re.search(r"^from app\.tools import TOOLS\s*$", s, flags=re.M)
        if m:
            insert_at = m.end()
            s = s[:insert_at] + "\nfrom app.tool_dispatcher import dispatch_tool\n" + s[insert_at:]
        else:
            # fallback: insert near top after last import line
            m2 = re.search(r"^(?:import .*\n|from .* import .*\n)+", s, flags=re.M)
            if m2:
                insert_at = m2.end()
                s = s[:insert_at] + "from app.tool_dispatcher import dispatch_tool\n" + s[insert_at:]
            else:
                s = "from app.tool_dispatcher import dispatch_tool\n" + s

    # replace direct tool calls to ensure truth injection
    s = re.sub(r"TOOLS\[(?P<mode>[^\]]+)\]\((?P<payload>[^\)]+)\)",
               r"dispatch_tool(\g<mode>, \g<payload>)", s)

    atomic_write_text(p, s)

def patch_main_include_router() -> None:
    p = APP / "main.py"
    if not p.exists():
        raise SystemExit("BLOKER: app/main.py not found")

    s = p.read_text(encoding="utf-8")

    if "project_truth_api" in s:
        return

    # ensure import
    add_import = "from app.project_truth_api import router as project_truth_router\n"
    if add_import not in s:
        # insert after FastAPI import if present
        m = re.search(r"^from fastapi import FastAPI.*\n", s, flags=re.M)
        if m:
            insert_at = m.end()
            s = s[:insert_at] + add_import + s[insert_at:]
        else:
            s = add_import + s

    # include router after app creation
    m2 = re.search(r"^app\s*=\s*FastAPI\([^\)]*\)\s*$", s, flags=re.M)
    if m2:
        insert_at = m2.end()
        s = s[:insert_at] + "\napp.include_router(project_truth_router)\n" + s[insert_at:]
    else:
        # fallback: append at end
        s = s + "\n\n# project truth router\ntry:\n    app.include_router(project_truth_router)\nexcept Exception:\n    pass\n"

    atomic_write_text(p, s)

def main() -> int:
    ensure_project_truth()

    write_file(APP / "project_truth_store.py", """from __future__ import annotations
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

ROOT = Path(__file__).resolve().parents[1]
GLOBAL_TRUTH = ROOT / "PROJECT_TRUTH.md"
BOOKS_DIR = ROOT / "books"

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""

def _book_truth_path(book_id: str) -> Path:
    return BOOKS_DIR / book_id / "PROJECT_TRUTH.md"

def get_truth(book_id: Optional[str] = None) -> Dict[str, Any]:
    # book override if exists
    if book_id:
        bp = _book_truth_path(book_id)
        if bp.exists():
            text = _read_text(bp)
            return {
                "scope": "book",
                "book_id": book_id,
                "path": str(bp),
                "sha256": _sha256(text),
                "text": text,
                "loaded_at": datetime.utcnow().isoformat()
            }

    text = _read_text(GLOBAL_TRUTH)
    return {
        "scope": "global",
        "book_id": book_id,
        "path": str(GLOBAL_TRUTH),
        "sha256": _sha256(text),
        "text": text,
        "loaded_at": datetime.utcnow().isoformat()
    }

def build_truth_pack(book_id: Optional[str] = None) -> Dict[str, Any]:
    t = get_truth(book_id)
    # minimal, stable keys for injection
    return {
        "scope": t["scope"],
        "book_id": t["book_id"],
        "sha256": t["sha256"],
        "text": t["text"]
    }
""")

    write_file(APP / "tool_dispatcher.py", """from __future__ import annotations
from typing import Any, Dict

from app.tools import TOOLS
from app.project_truth_store import build_truth_pack

def dispatch_tool(mode_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Inject project truth into every tool call (so agent never 'forgets' what it is)
    book_id = payload.get("book_id")
    truth = build_truth_pack(book_id)

    pl = dict(payload)
    pl["_project_truth"] = truth["text"]
    pl["_project_truth_sha256"] = truth["sha256"]
    pl["_project_truth_scope"] = truth["scope"]

    return TOOLS[mode_id](pl)
""")

    write_file(APP / "project_truth_api.py", """from __future__ import annotations
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.project_truth_store import get_truth

router = APIRouter(prefix="/project_truth", tags=["project_truth"])

class TruthResponse(BaseModel):
    scope: str
    book_id: str | None = None
    path: str
    sha256: str
    text: str
    loaded_at: str

@router.get("", response_model=TruthResponse)
def project_truth(book_id: str | None = Query(default=None)):
    return get_truth(book_id)
""")

    patch_orchestrator()
    patch_main_include_router()

    print("[OK] PROJECT_TRUTH.md ensured")
    print("[OK] app/project_truth_store.py created")
    print("[OK] app/tool_dispatcher.py created")
    print("[OK] app/project_truth_api.py created")
    print("[OK] patched app/orchestrator_stub.py (truth injection)")
    print("[OK] patched app/main.py (router include)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
