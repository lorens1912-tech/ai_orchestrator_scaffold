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
        "reasons": [f"{i.id}: {i.title}" for i in issues],
        "must_fix": [
            {"id": i.id, "severity": i.severity, "title": i.title, "detail": i.detail, "hint": i.hint}
            for i in issues
        ],
        "flags": flags,
        "stats": {"chars": len(t), "words": w},
    }
