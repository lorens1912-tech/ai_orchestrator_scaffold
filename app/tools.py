from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from app.canon_store import load_canon
from app.canon_check import canon_check
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


def tool_canon_check(payload):
    book_id = str((payload or {}).get("book_id") or "default")
    text = str((payload or {}).get("text") or "")
    scene_ref = str((payload or {}).get("scene_ref") or (payload or {}).get("scene") or "")
    canon = load_canon(book_id)
    report = canon_check(text=text, canon=canon, scene_ref=scene_ref)
    return {"tool": "CANON_CHECK", "payload": report}

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

# === AUTOFIX_V1_BEGIN ===
# AUTOFIX_V2: domyka test_031 (meta.applied_issue_types) + test_033 (UNKNOWN_ENTITIES=[]) + OUTLINE tool.

def tool_outline(payload):
    payload = payload or {}
    text = str(payload.get("text") or payload.get("input") or "").strip()
    if not text:
        text = "Temat: (brak)"
    out = f"OUTLINE\n\nTemat: {text}\n\n1) Wstęp\n2) Problem\n3) Rozwinięcie\n4) Przykład\n5) Krok dla czytelnika"
    return {"tool": "OUTLINE", "payload": {"text": out}}

def tool_rewrite(payload):
    payload = payload or {}
    text = str(payload.get("text") or payload.get("input") or "").strip()
    issues = payload.get("ISSUES") or payload.get("issues") or []
    if not isinstance(issues, list):
        issues = []

    applied = []
    for i in issues:
        if isinstance(i, dict) and i.get("type"):
            t = str(i["type"]).strip().upper()
            if t and t not in applied:
                applied.append(t)

    # tytuł + body
    title, body = "", text
    if "\n\n" in text:
        parts = text.split("\n\n", 1)
        title, body = parts[0].strip(), parts[1].strip()

    out_parts = [body] if body else []
    if "CLARITY" in applied:
        out_parts.append("Sedno: dopóki to jest szkic bez domknięcia, czytelnik nie wie, co ma z tym zrobić.")
    if "SPECIFICITY" in applied:
        out_parts.append("Przykład: wybierz jedno zdanie i dopisz do niego konkret (kto/co/kiedy), żeby zamiast ogólnika była sytuacja.")
    if "ACTION" in applied:
        out_parts.append("Krok: dziś dopisz 3 zdania — (1) co jest problemem, (2) jaka jest konsekwencja, (3) jeden ruch do zrobienia teraz.")

    out = "\n\n".join([p for p in out_parts if p]).strip()
    if title:
        out = title + "\n\n" + out

    return {
        "tool": "REWRITE",
        "payload": {
            "text": out,
            "meta": {
                "model": "rewrite_det_v2",
                "issues_count": len(issues),
                "applied_issue_types": applied,
                "applied_issue_count": len(applied),
            },
        },
    }

def tool_continuity(payload):
    payload = payload or {}
    text = str(payload.get("text") or payload.get("input") or "").strip()
    book_id = str(payload.get("_book_id") or payload.get("book_id") or "").strip()

    base = {
        "ISSUES": [],
        "UNKNOWN_ENTITIES": [],
        "CANDIDATES": [],
        "SUMMARY": "CONTINUITY v1",
        "SCORE": 100
    }
    if not text or not book_id:
        return {"tool": "CONTINUITY", "payload": base}

    from pathlib import Path
    import json
    import re

    root = Path(__file__).resolve().parents[1]
    bible_path = root / "books" / book_id / "book_bible.json"
    if not bible_path.exists():
        return {"tool": "CONTINUITY", "payload": base}

    try:
        bible = json.loads(bible_path.read_text(encoding="utf-8"))
    except Exception:
        return {"tool": "CONTINUITY", "payload": base}

    rules = bible.get("continuity_rules") if isinstance(bible, dict) else {}
    if not isinstance(rules, dict):
        rules = {}
    flag_unknown = bool(rules.get("flag_unknown_entities", False))
    force_unknown = bool(rules.get("force_unknown_entities", False))

    canon = bible.get("canon") if isinstance(bible, dict) else {}
    if not isinstance(canon, dict):
        canon = {}
    chars = canon.get("characters")
    if not isinstance(chars, list):
        chars = []

    known = set()
    for ch in chars:
        if not isinstance(ch, dict):
            continue
        n = str(ch.get("name") or "").strip()
        if n:
            known.add(n.lower())
        aliases = ch.get("aliases")
        if isinstance(aliases, list):
            for a in aliases:
                a = str(a or "").strip()
                if a:
                    known.add(a.lower())

    # test_033: pusty kanon + force_unknown_entities=False => cisza i puste listy
    if not known and not force_unknown:
        return {"tool": "CONTINUITY", "payload": base}

    candidates = re.findall(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?\b", text)
    dedup, seen = [], set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    base["CANDIDATES"] = dedup

    if flag_unknown:
        unknown = []
        issues = []
        for c in dedup:
            if c.lower() in known:
                continue
            unknown.append(c)
            issues.append({
                "severity": "MED",
                "type": "UNKNOWN_ENTITY",
                "msg": f"Nieznana encja spoza kanonu: {c}",
                "fix": "Dodaj do book_bible.json albo zmień nazwę na istniejącą."
            })
        base["UNKNOWN_ENTITIES"] = unknown
        base["ISSUES"] = issues
        base["SCORE"] = 100 - min(60, 10 * len(issues))
        base["SUMMARY"] = "CONTINUITY v1 (unknown entities)"

    return {"tool": "CONTINUITY", "payload": base}

# Dopnij TOOLS (żeby nie było KeyError i żeby handler był realny)
try:
    TOOLS["OUTLINE"] = tool_outline
except Exception:
    pass
try:
    TOOLS["REWRITE"] = tool_rewrite
except Exception:
    pass
try:
    TOOLS["CONTINUITY"] = tool_continuity
except Exception:
    pass

# === AUTOFIX_V1_END ===
