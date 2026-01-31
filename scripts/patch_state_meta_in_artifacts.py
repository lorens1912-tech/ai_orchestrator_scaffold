from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "app" / "orchestrator_stub.py"

s = P.read_text(encoding="utf-8")

# 1) ensure import
if "from app.project_state_store import build_state_pack" not in s:
    # insert after build_truth_pack import if present, else after dispatch_tool import
    m = re.search(r"^from app\.project_truth_store import build_truth_pack\s*$", s, flags=re.M)
    if m:
        insert_at = m.end()
        s = s[:insert_at] + "\nfrom app.project_state_store import build_state_pack\n" + s[insert_at:]
    else:
        m2 = re.search(r"^from app\.tool_dispatcher import dispatch_tool\s*$", s, flags=re.M)
        if not m2:
            raise SystemExit("BLOKER: cannot find import anchor for build_state_pack")
        insert_at = m2.end()
        s = s[:insert_at] + "\nfrom app.project_state_store import build_state_pack\n" + s[insert_at:]

# 2) add cached state pack once per run inside execute_stub
if "_state_pack = build_state_pack()" not in s:
    # anchor: after preset_id resolution block (very stable line: preset_id = payload.get(...))
    m = re.search(r"^\s*preset_id\s*=\s*None\s*\n\s*if\s+isinstance\(payload,\s*dict\):\s*\n\s*    preset_id\s*=\s*payload\.get\(\"_preset_id\"\)\s*or\s*payload\.get\(\"preset\"\)\s*$",
                  s, flags=re.M)
    if not m:
        # fallback: after artifact_paths init
        m = re.search(r"^\s*artifact_paths:\s*List\[str\]\s*=\s*\[\]\s*$", s, flags=re.M)
        if not m:
            raise SystemExit("BLOKER: cannot find anchor to add _state_pack cache")
        insert_at = m.end()
        block = """

    # project_state cache (once per run)
    try:
        _state_pack = build_state_pack()
    except Exception:
        _state_pack = {}
"""
        s = s[:insert_at] + block + s[insert_at:]
    else:
        insert_at = m.end()
        block = """

    # project_state cache (once per run)
    try:
        _state_pack = build_state_pack()
    except Exception:
        _state_pack = {}
"""
        s = s[:insert_at] + block + s[insert_at:]

# 3) inject state meta into tool_in where you already inject truth meta
needle = "truth = build_truth_pack(tool_in.get(\"book_id\"))"
if needle not in s:
    raise SystemExit("BLOKER: cannot find truth injection line to attach state meta")

if "_project_state_phase" not in s:
    s = s.replace(
        needle,
        needle + """
        # log project_state meta in artifacts input (same rationale as truth meta)
        tool_in["_project_state_phase"] = _state_pack.get("phase") if isinstance(_state_pack, dict) else None
        tool_in["_project_state_blocker"] = _state_pack.get("current_blocker") if isinstance(_state_pack, dict) else None
        tool_in["_project_state_next_action"] = _state_pack.get("next_action") if isinstance(_state_pack, dict) else None
"""
    )

P.write_text(s, encoding="utf-8")
print("OK: patched orchestrator_stub.py to log _project_state_* in step input")
