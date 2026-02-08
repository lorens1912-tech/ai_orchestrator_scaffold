from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Tuple

Decision = Literal["ACCEPT", "REVISE", "REJECT"]

@dataclass(frozen=True)
class Issue:
    id: str
    severity: Decision
    title: str
    detail: str
    hint: str

_META_PATTERNS: List[Tuple[str, str]] = [
    ("META_AI", r"(?i)\b(as an ai|as a language model|jako model językowy)\b"),
    ("META_PROCESS", r"(?i)\b(w tym rozdziale|w tej części|teraz opiszę|poniżej przedstawię|w kolejnym kroku)\b"),
]

_PLACEHOLDER_PATTERNS: List[Tuple[str, str]] = [
    ("PLACEHOLDER_TODO", r"(?i)\bTODO\b"),
    ("PLACEHOLDER_LOREM", r"(?i)\blorem ipsum\b"),
    ("PLACEHOLDER_TAGS", r"<[^>]{1,60}>"),
]

_LIST_PATTERN = r"(?m)^\s*([-*]|[0-9]+\.)\s+\S+"

def _count_words(text: str) -> int:
    return len(re.findall(r"[0-9A-Za-zÀ-ÿĄĆĘŁŃÓŚŹŻąćęłńóśźż]+(?:[-'][0-9A-Za-zÀ-ÿĄĆĘŁŃÓŚŹŻąćęłńóśźż]+)?", text))

def evaluate_quality(text: str, *, min_words: int = 200, forbid_lists: bool = True) -> Dict[str, Any]:
    t = (text or "").strip()
    w = _count_words(t)

    issues: List[Issue] = []
    flags = {"has_meta": False, "has_placeholders": False, "has_lists": False, "too_short": False}

    # długość: w testach to ma być REVISE, nie automatyczny REJECT (poza w==0)
    if w == 0:
        flags["too_short"] = True
        issues.append(Issue("EMPTY","REJECT","Brak treści","Words=0.","Dostarcz realny tekst."))
    elif w < int(min_words):
        flags["too_short"] = True
        issues.append(Issue("MIN_WORDS","REVISE","Za mało słów",f"Words={w}, min_words={min_words}.","Rozwiń scenę/sekcję do targetu."))

    for issue_id, pat in _META_PATTERNS:
        if re.search(pat, t):
            flags["has_meta"] = True
            sev: Decision = "REJECT" if issue_id == "META_AI" else "REVISE"
            issues.append(Issue(issue_id, sev, "Meta-kulisy", f"Wykryto {issue_id}.", "Usuń meta-komentarze; tekst ma być prozą."))

    for issue_id, pat in _PLACEHOLDER_PATTERNS:
        if re.search(pat, t):
            flags["has_placeholders"] = True
            issues.append(Issue(issue_id, "REJECT", "Placeholder", f"Wykryto {issue_id}.", "Zastąp placeholder finalną treścią."))

    if forbid_lists and re.search(_LIST_PATTERN, t):
        flags["has_lists"] = True
        issues.append(Issue("LISTS_IN_PROSE","REVISE","Lista zamiast prozy","Wykryto wypunktowania.","Zamień listę na narrację."))

    decision: Decision = "ACCEPT"
    if any(i.severity == "REJECT" for i in issues):
        decision = "REJECT"
    elif issues:
        decision = "REVISE"

    return {
        "decision": decision,
        "BLOCK_PIPELINE": (str(decision).upper() == "FAIL"),
        "block_pipeline": (str(decision).upper() == "FAIL"),
        "reasons": [f"{i.id}: {i.title}" for i in issues],
        "must_fix": [
            {"id": i.id, "severity": i.severity, "title": i.title, "detail": i.detail, "hint": i.hint}
            for i in issues
        ],
        "flags": flags,
        "stats": {"chars": len(t), "words": w},
    }

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
