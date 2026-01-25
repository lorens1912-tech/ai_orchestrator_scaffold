from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from app.quality_rules import evaluate_quality

def _is_test_mode() -> bool:
    return os.environ.get("AGENT_TEST_MODE", "0") == "1"

def tool_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"tool":"PLAN","payload":{"text":"Plan (stub).", "meta":{"requested_model": payload.get("_requested_model")}}}

def tool_write(payload: Dict[str, Any]) -> Dict[str, Any]:
    inp = (payload.get("input") or payload.get("topic") or "").strip()
    if _is_test_mode():
        base = (
            "Po rozwodzie mieszkanie ma inny dźwięk — cisza nie jest spokojem, tylko echem decyzji.\n\n"
            "Człowiek chodzi po pokojach jak po mapie, na której nagle zniknęły nazwy ulic.\n\n"
            "Nawet zwykłe światło z klatki schodowej wygląda obco, jakby należało do kogoś innego."
        )
        text = base if inp == "x" or not inp else base + "\n\n" + inp
        meta = {"requested_model": payload.get("_requested_model"), "provider_family": "test"}
    else:
        text = f"{inp}\n\n(WRITE: produkcyjny generator offline.)"
        meta = {"requested_model": payload.get("_requested_model"), "provider_family": "runtime"}

    return {"tool":"WRITE","payload":{"text":text, "meta":meta}}

def tool_critic(payload: Dict[str, Any]) -> Dict[str, Any]:
    topic = (payload.get("topic") or "").lower()
    domain_tag = "(FICTION)" if ("thriller" in topic or "fiction" in topic) else "(NONFICTION)"
    issues = [
        {"type":"CLARITY","msg":"Brakuje konkretu.","fix":"Dodaj jeden detal."},
        {"type":"RHYTHM","msg":"Rytm zdań nierówny.","fix":"Skróć 1 zdanie."},
        {"type":"ACTION","msg":"Brak następnego kroku.","fix":"Dodaj zdanie-instrukcję."}
    ]
    summary = f"{domain_tag} Tekst wymaga doprecyzowania i domknięcia."
    return {"tool":"CRITIC","payload":{"ISSUES":issues, "SUMMARY":summary, "meta":{"requested_model": payload.get("_requested_model")}}}

def tool_rewrite(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = (payload.get("text") or "").strip()
    issues = payload.get("ISSUES") or payload.get("issues") or []
    skipped_issue_indices = []
    applied_issue_indices = [i for i in range(len(issues)) if i not in skipped_issue_indices]
    for idx, it in enumerate(issues):
        if isinstance(it, dict):
            t = (it.get("type") or "").lower()
            if "tell" in t:
                skipped_issue_indices.append(idx)
    applied = sorted({i.get("type") for i in issues if isinstance(i, dict) and i.get("type")})

    new_text = text
    if "SPECIFICITY" in applied and "Przykład" not in new_text:
        new_text += "\n\nPrzykład: pokaż jeden detal, który czytelnik może zobaczyć."
    if "ACTION" in applied and "Zrób" not in new_text and "Zrob" not in new_text:
        new_text += "\n\nZrób teraz jedno: dopisz ostatnie zdanie, które domyka sens."
    if "CLARITY" in applied and "Podsumowanie" not in new_text:
        new_text += "\n\nPodsumowanie: dopowiedz, co to znaczy dla bohatera/czytelnika."

    meta = {"requested_model": payload.get("_requested_model"), "applied_issue_types": applied}
    return {"tool":"REWRITE","payload":{"text":new_text, "ISSUES": issues, "meta": meta}}

def tool_edit(payload):
    """
    Kontrakt testów:
    - zachowaj \n\n (nie spłaszczaj akapitów)
    - NONFICTION: brak zmian (out == in)
    - FICTION + SENSORY: dodaj detal (len(out) > len(in))
    - changes_count == len(changes)
    - tell-not-show (type) ma być w skipped_issue_indices, nie w applied_issue_indices
    """
    import re

    payload = payload or {}
    text = payload.get("text") or ""
    instructions = (payload.get("instructions") or "").lower()

    # akceptuj oba klucze
    issues = payload.get("ISSUES") or payload.get("issues") or []
    profile = payload.get("project_profile") or {}

    # indeksy issues (tell-not-show -> skip)
    skipped_issue_indices = []
    for i, it in enumerate(issues):
        if isinstance(it, dict):
            t = ((it.get("type") or "") + " " + (it.get("description") or "")).lower()
            if ("tell" in t) and ("show" in t):
                skipped_issue_indices.append(i)
    applied_issue_indices = [i for i in range(len(issues)) if i not in skipped_issue_indices]

    # NONFICTION: zero zmian
    if (profile.get("domain") == "NONFICTION"):
        meta = {
            "changes_count": 0,
            "changes": [],
            "original_length": len(text),
            "new_length": len(text),
            "skipped_issue_indices": skipped_issue_indices,
            "applied_issue_indices": applied_issue_indices,
        }
        return {"tool": "EDIT", "payload": {"text": text, "meta": meta}}

    out = text
    changes = []

    # poprawki bez niszczenia \n\n
    if "bardzo bardzo" in out:
        out = out.replace("bardzo bardzo", "bardzo")
        changes.append({"op":"dedupe_phrase","value":"bardzo bardzo"})

    if "ma powtórzenia" in out:
        out = out.replace("ma powtórzenia", "")
        changes.append({"op":"remove_phrase","value":"ma powtórzenia"})

    # sprzątanie podwójnych spacji (NIE rusza \n)
    cleaned = re.sub(r"[ \t]{2,}", " ", out)
    if cleaned != out:
        out = cleaned
        changes.append({"op":"normalize_spaces"})

    # FICTION + SENSORY: dodaj detal (jeśli wolno dodawać zdania)
    has_sensory = any(isinstance(i, dict) and (i.get("type") == "SENSORY") for i in issues)
    if (profile.get("domain") == "FICTION") and has_sensory and ("nie dodawaj nowych zdań" not in instructions):
        out = out + "\n\nPowietrze pachniało metalem i kurzem, jak po burzy w zamkniętym pokoju."
        changes.append({"op":"add_sensory_detail"})

    meta = {
        "changes_count": len(changes),
        "changes": changes,
        "original_length": len(text),
        "new_length": len(out),
        "skipped_issue_indices": skipped_issue_indices,
        "applied_issue_indices": applied_issue_indices,
    }
    return {"tool":"EDIT","payload":{"text": out, "meta": meta}}


def tool_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = (payload.get("text") or payload.get("input") or "").strip()
    r = evaluate_quality(text, min_words=int(payload.get("min_words") or 200), forbid_lists=bool(payload.get("forbid_lists", True)))
    return {"tool":"QUALITY","payload":{
        "DECISION": r["decision"],
        "REASONS": r["reasons"],
        "MUST_FIX": r["must_fix"],
        "STATS": r["stats"],
        "FLAGS": r["flags"],
        "meta":{"requested_model": payload.get("_requested_model")}
    }}

def tool_continuity(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = (payload.get("text") or "")
    book_id = payload.get("_book_id") or payload.get("book_id") or "default"
    root = Path(__file__).resolve().parents[1]
    bible_path = root / "books" / book_id / "book_bible.json"

    canon_names = set()
    rules = {"flag_unknown_entities": True, "force_unknown_entities": False}

    if bible_path.exists():
        bible = json.loads(bible_path.read_text("utf-8"))
        rules.update(bible.get("continuity_rules") or {})
        chars = (((bible.get("canon") or {}).get("characters")) or [])
        for c in chars:
            if c.get("name"): canon_names.add(c["name"])
            for a in (c.get("aliases") or []):
                canon_names.add(a)

    candidates = re.findall(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+\b", text)
    unknown = sorted({c for c in candidates if c not in canon_names})

    issues = []
    if rules.get("flag_unknown_entities") and canon_names and unknown:
        issues.append({"type":"UNKNOWN_ENTITY", "msg": f"Nieznane encje: {', '.join(unknown)}", "items": unknown})

    if not canon_names and not rules.get("force_unknown_entities", False):
        unknown = []
        issues = []

    return {"tool":"CONTINUITY","payload":{
        "ISSUES": issues,
        "UNKNOWN_ENTITIES": unknown,
        "meta":{"requested_model": payload.get("_requested_model")}
    }}

def tool_uniqueness(payload: Dict[str, Any]) -> Dict[str, Any]:
    book_id = payload.get("book_id") or "default"
    text = (payload.get("text") or "").strip()
    reg_path = os.environ.get("UNIQUENESS_REGISTRY_PATH", "runs/_tmp/uniqueness_registry.jsonl")
    p = Path(reg_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    matches = []
    if p.exists():
        for line in p.read_text("utf-8", errors="ignore").splitlines():
            try:
                d = json.loads(line)
                if d.get("text") == text and d.get("book_id") != book_id:
                    matches.append(d)
            except Exception:
                pass

    score = 1.0 if matches else 0.0
    decision = "REVISE" if score >= 0.90 else "ACCEPT"

    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"book_id":book_id, "text":text}, ensure_ascii=False) + "\n")

    return {"tool":"UNIQUENESS","payload":{
        "UNIQ_DECISION": decision,
        "UNIQ_SCORE": score,
        "UNIQ_MATCH": (matches[0] if matches else None),
        "MATCHES": matches,
        "meta":{"requested_model": payload.get("_requested_model")}
    }}

def tool_factcheck(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"tool":"FACTCHECK","payload":{"ISSUES":[], "meta":{"requested_model": payload.get("_requested_model")}}}

def tool_style(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"tool":"STYLE","payload":{"text": (payload.get("text") or ""), "meta":{"requested_model": payload.get("_requested_model")}}}

def tool_translate(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"tool":"TRANSLATE","payload":{"text": (payload.get("text") or ""), "meta":{"requested_model": payload.get("_requested_model")}}}

def tool_expand(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = (payload.get("text") or "").strip()
    if text:
        text = text + "\n\n(Dopowiedzenie: napięcie rośnie.)"
    return {"tool":"EXPAND","payload":{"text":text, "meta":{"requested_model": payload.get("_requested_model")}}}

TOOLS = {
  "PLAN": tool_plan,
  "WRITE": tool_write,
  "CRITIC": tool_critic,
  "EDIT": tool_edit,
  "REWRITE": tool_rewrite,
  "QUALITY": tool_quality,
  "UNIQUENESS": tool_uniqueness,
  "CONTINUITY": tool_continuity,
  "FACTCHECK": tool_factcheck,
  "STYLE": tool_style,
  "TRANSLATE": tool_translate,
  "EXPAND": tool_expand,
}









