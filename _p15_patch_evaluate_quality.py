from pathlib import Path
from datetime import datetime
import re, shutil, sys, py_compile

# 1) znajdź plik z def evaluate_quality(...)
candidates = []
for p in Path("app").rglob("*.py"):
    try:
        t = p.read_text(encoding="utf-8")
    except Exception:
        continue
    if re.search(r'^\s*def\s+evaluate_quality\s*\(', t, flags=re.MULTILINE):
        candidates.append(p)

if not candidates:
    print("ERROR: Nie znaleziono def evaluate_quality(...) w app/")
    sys.exit(2)

# preferuj quality_rules.py jeśli istnieje
target = None
for p in candidates:
    if p.name.lower() == "quality_rules.py":
        target = p
        break
if target is None:
    target = candidates[0]

print("TARGET=", target.as_posix())

txt = target.read_text(encoding="utf-8")

start = "# P15_EVAL_HARDFAIL_START"
end   = "# P15_EVAL_HARDFAIL_END"

block = r'''
# P15_EVAL_HARDFAIL_START
def _p15__count_words(x):
    s = str(x or "").strip()
    return len([w for w in s.split() if w.strip()]) if s else 0

def _p15__list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]

def _p15__dict(v):
    return v if isinstance(v, dict) else {}

if "_p15_orig_evaluate_quality" not in globals():
    _p15_orig_evaluate_quality = evaluate_quality

    def evaluate_quality(*args, **kwargs):
        out = _p15_orig_evaluate_quality(*args, **kwargs)

        if not isinstance(out, dict):
            return out

        # key aliases (upper/lower)
        decision_key = "DECISION" if "DECISION" in out else ("decision" if "decision" in out else "DECISION")
        block_key = "BLOCK_PIPELINE" if "BLOCK_PIPELINE" in out else ("block_pipeline" if "block_pipeline" in out else "BLOCK_PIPELINE")
        reasons_key = "REASONS" if "REASONS" in out else ("reasons" if "reasons" in out else "REASONS")
        must_key = "MUST_FIX" if "MUST_FIX" in out else ("must_fix" if "must_fix" in out else "MUST_FIX")
        stats_key = "STATS" if "STATS" in out else ("stats" if "stats" in out else "STATS")
        flags_key = "FLAGS" if "FLAGS" in out else ("flags" if "flags" in out else "FLAGS")

        reasons = _p15__list(out.get(reasons_key))
        must_fix = _p15__list(out.get(must_key))
        stats = _p15__dict(out.get(stats_key))
        flags = _p15__dict(out.get(flags_key))

        # min_words detection
        min_words = kwargs.get("min_words")
        if min_words is None and len(args) >= 2 and isinstance(args[1], (int, float)):
            min_words = int(args[1])
        if min_words is None:
            min_words = 0
        try:
            min_words = int(min_words)
        except Exception:
            min_words = 0

        words = stats.get("words", 0)
        try:
            words = int(words)
        except Exception:
            words = 0
        if words <= 0 and len(args) >= 1:
            words = _p15__count_words(args[0])

        has_min_reason = any("MIN_WORDS" in str(r).upper() for r in reasons)
        too_short = bool(flags.get("too_short", False)) or has_min_reason or (min_words > 0 and words < min_words)

        if too_short:
            out[decision_key] = "FAIL"
            out[block_key] = True

            if not has_min_reason:
                reasons.insert(0, f"MIN_WORDS: Words={words}, min_words={min_words}.")
            out[reasons_key] = reasons

            found = False
            for item in must_fix:
                if isinstance(item, dict) and str(item.get("id","")).upper() == "MIN_WORDS":
                    if "severity" in item:
                        item["severity"] = "FAIL"
                    else:
                        item["severity"] = "FAIL"
                    found = True

            if not found:
                must_fix.insert(0, {
                    "id": "MIN_WORDS",
                    "severity": "FAIL",
                    "title": "Za mało słów",
                    "detail": f"Words={words}, min_words={min_words}.",
                    "hint": "Rozwiń tekst do minimum."
                })
            out[must_key] = must_fix

            # uzupełnij stats/flags spójnie
            stats["words"] = words
            out[stats_key] = stats
            flags["too_short"] = True
            out[flags_key] = flags

        return out
# P15_EVAL_HARDFAIL_END
'''

if start in txt and end in txt:
    txt2 = re.sub(
        re.escape(start) + r'[\s\S]*?' + re.escape(end),
        block.strip(),
        txt,
        flags=re.MULTILINE
    )
    status = "UPDATED"
else:
    txt2 = txt.rstrip() + "\n\n" + block.strip() + "\n"
    status = "APPENDED"

# backup + save
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak_dir = Path("backups") / f"p15_eval_hardfail_{ts}"
bak_dir.mkdir(parents=True, exist_ok=True)
bak_file = bak_dir / target.name
shutil.copy2(target, bak_file)
target.write_text(txt2, encoding="utf-8")

# compile
py_compile.compile(str(target), doraise=True)

print("PATCH_STATUS=", status)
print("BACKUP_FILE=", bak_file.as_posix())
print("MARKER_PRESENT=", ("P15_EVAL_HARDFAIL_START" in target.read_text(encoding="utf-8")))
