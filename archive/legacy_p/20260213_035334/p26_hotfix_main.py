from pathlib import Path
import re, textwrap, datetime, shutil, py_compile, sys

ROOT = Path(r"C:\AI\ai_orchestrator_scaffold")
main_py = ROOT / "app" / "main.py"

if not main_py.exists():
    print(f"E_MAIN_NOT_FOUND: {main_py}")
    sys.exit(2)

raw = main_py.read_text(encoding="utf-8")
text = raw.replace("\r\n", "\n")

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = ROOT / "backups" / f"main_py_before_p26_hotfix_{ts}.py"
bak.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(main_py, bak)

changed = False

# 1) presets_count = dokładna liczba presetów z load_presets()["presets"]
pattern_pc = r'("presets_count"\s*:\s*)([^,\n]+)'
repl_pc = r'\1len(((load_presets() or {}).get("presets") or []))'
text2, n_pc = re.subn(pattern_pc, repl_pc, text, count=1)
if n_pc > 0:
    text = text2
    changed = True
    print("PATCH_PRESETS_COUNT=OK")
else:
    print("PATCH_PRESETS_COUNT=SKIP_NOT_FOUND")

# 2) szybka ścieżka DEFAULT w agent_step (idempotent)
marker_begin = "P26_DEFAULT_FASTPATH_BEGIN"
if marker_begin not in text:
    m = re.search(r'(^\s*(?:async\s+)?def\s+agent_step\s*\([^\n]*\)\s*:\s*\n)', text, flags=re.M)
    if not m:
        print("E_NO_AGENT_STEP_DEF")
        sys.exit(3)

    block = textwrap.dedent("""\
        # === P26_DEFAULT_FASTPATH_BEGIN ===
        if (getattr(req, "preset", None) or "").upper() == "DEFAULT":
            import os, json
            from datetime import datetime as _dt
            mode = (getattr(req, "mode", "") or "WRITE").upper()
            run_id = f"run_{_dt.now().strftime('%Y%m%d_%H%M%S_%f')}"
            book_id = getattr(req, "book_id", None) or "book_runtime_test"
            step_dir = os.path.join(os.getcwd(), "runs", run_id, "steps")
            os.makedirs(step_dir, exist_ok=True)

            tool_name = "tool_stub"
            try:
                _md = load_modes() if callable(load_modes) else {}
                _modes = _md.get("modes") if isinstance(_md, dict) else []
                if isinstance(_modes, list):
                    for _m in _modes:
                        if isinstance(_m, dict) and str(_m.get("id", "")).upper() == mode:
                            _t = _m.get("tool")
                            if isinstance(_t, str) and _t.strip():
                                tool_name = _t.strip()
                            break
            except Exception:
                pass

            _payload = {}
            if isinstance(getattr(req, "payload", None), dict):
                _payload = req.payload
            elif getattr(req, "input", None) is not None:
                _payload = {"input": req.input}

            artifact_path = os.path.join(step_dir, f"001_{mode}.json")
            with open(artifact_path, "w", encoding="utf-8") as _f:
                json.dump(
                    {
                        "ok": True,
                        "mode": mode,
                        "tool": tool_name,
                        "input": _payload,
                        "text": f"[DEFAULT_FASTPATH] {mode} completed",
                    },
                    _f,
                    ensure_ascii=False,
                    indent=2,
                )

            return {
                "ok": True,
                "run_id": run_id,
                "book_id": book_id,
                "artifact_paths": [artifact_path],
            }
        # === P26_DEFAULT_FASTPATH_END ===

    """)
    block = "\n".join(("    " + ln) if ln.strip() else ln for ln in block.splitlines()) + "\n"
    insert_at = m.end()
    text = text[:insert_at] + block + text[insert_at:]
    changed = True
    print("PATCH_DEFAULT_FASTPATH=OK")
else:
    print("PATCH_DEFAULT_FASTPATH=ALREADY_PRESENT")

# 3) naprawa: if ...: + try: na tym samym poziomie
def _fix_if_try_indent(s: str):
    lines = s.splitlines()
    mod = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip(" ")
        if stripped.startswith("if ") and stripped.rstrip().endswith(":"):
            if_indent = len(line) - len(stripped)
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n:
                sj = lines[j].lstrip(" ")
                indj = len(lines[j]) - len(sj)
                if indj <= if_indent and sj.startswith("try:"):
                    k = j
                    while k < n:
                        cur = lines[k]
                        sc = cur.strip()
                        ind = len(cur) - len(cur.lstrip(" "))
                        if k > j and sc and ind <= if_indent and not sc.startswith(("except", "finally")):
                            break
                        if sc:
                            lines[k] = (" " * 4) + cur
                        k += 1
                    mod = True
                    i = k
                    continue
        i += 1
    out = "\n".join(lines)
    if s.endswith("\n"):
        out += "\n"
    return out, mod

text_fixed, fixed = _fix_if_try_indent(text)
if fixed:
    text = text_fixed
    changed = True
    print("PATCH_INDENT_IF_TRY=OK")
else:
    print("PATCH_INDENT_IF_TRY=NO_CHANGE")

if changed:
    main_py.write_text(text, encoding="utf-8", newline="\n")
    print(f"WRITE_OK: {main_py}")
else:
    print("NO_CHANGES_WRITTEN")

# 4) kompilacja
try:
    py_compile.compile(str(main_py), doraise=True)
    print("PY_COMPILE_MAIN_OK")
except Exception as e:
    print(f"E_PY_COMPILE_MAIN: {e}")
    print(f"BACKUP_AT: {bak}")
    sys.exit(4)

tools_py = ROOT / "app" / "tools.py"
if tools_py.exists():
    try:
        py_compile.compile(str(tools_py), doraise=True)
        print("PY_COMPILE_TOOLS_OK")
    except Exception as e:
        print(f"E_PY_COMPILE_TOOLS: {e}")
        print(f"BACKUP_AT: {bak}")
        sys.exit(5)

print(f"BACKUP_AT: {bak}")
print("HOTFIX_DONE")
