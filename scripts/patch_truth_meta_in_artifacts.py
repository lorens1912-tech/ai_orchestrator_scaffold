from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "app" / "orchestrator_stub.py"
s = p.read_text(encoding="utf-8")

# 1) ensure import
imp = "from app.project_truth_store import build_truth_pack\n"
if imp not in s:
    # insert after dispatcher import if present
    m = re.search(r"^from app\.tool_dispatcher import dispatch_tool\s*$", s, flags=re.M)
    if not m:
        raise SystemExit("BLOKER: cannot find 'from app.tool_dispatcher import dispatch_tool' in orchestrator_stub.py")
    insert_at = m.end()
    s = s[:insert_at] + "\n" + imp + s[insert_at:]

# 2) inject meta before dispatch_tool call (only scope+sha256; no text)
needle = "result = dispatch_tool(mode_id, tool_in)"
if needle not in s:
    raise SystemExit("BLOKER: cannot find 'result = dispatch_tool(mode_id, tool_in)'")

replacement = (
    'truth = build_truth_pack(tool_in.get("book_id"))\n'
    '        tool_in["_project_truth_scope"] = truth.get("scope")\n'
    '        tool_in["_project_truth_sha256"] = truth.get("sha256")\n'
    "        result = dispatch_tool(mode_id, tool_in)"
)

s = s.replace(needle, replacement, 1)
p.write_text(s, encoding="utf-8")
print("OK: orchestrator_stub.py patched to log project truth meta in step input")
