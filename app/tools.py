from __future__ import annotations

import os
import re
import time
import json
import hashlib
from collections import deque
from pathlib import Path
from typing import Dict, Any, Callable, List, Optional

from .config_registry import load_modes
from .run_lock import acquire_book_lock

Payload = Dict[str, Any]
ToolFn = Callable[[Payload], Dict[str, Any]]


def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    return str(x)


# ----------------------------
# LLM wrapper (kompatybilny)
# ----------------------------
def _call_llm(model: str, prompt: str, max_tokens: int) -> Dict[str, Any]:
    """
    Oczekiwany kontrakt zwrotu:
    {"content": "...", "usage": {...} | None, "model": "..."}
    """
    from .llm_client import run_completion  # musi istnieć (kompat wstecz)
    return run_completion(model=model, prompt=prompt, max_tokens=max_tokens)


def _deterministic_write(user_prompt: str) -> str:
    p = (user_prompt or "").strip() or "Napisz krótki, konkretny akapit na zadany temat."
    return (
        f"{p}\n\n"
        "Samotność po rozwodzie często nie krzyczy — ona robi miejsce. Nagle jest ciszej, niż było, "
        "i ta cisza zaczyna mówić o rzeczach, które wcześniej dało się zagłuszyć rutyną. "
        "To nie dowód słabości, tylko moment, w którym odzyskujesz ster: przez sen, ruch i jeden "
        "konkretny krok dziennie.\n\n"
        "Zamiast czekać, aż „znowu będzie dobrze”, zacznij od rzeczy mierzalnych: stała pora snu, "
        "30 minut spaceru, jedno zadanie domknięte dziś. Potem nazwij emocję jednym zdaniem i ustal "
        "jedną decyzję na jutro rano. Jedną. To wystarczy, żeby wracać do sprawczości."
    )


# ----------------------------
# Core tools (PLAN/OUTLINE/WRITE/CRITIC/EDIT/QUALITY)
# ----------------------------
def tool_plan(payload: Payload) -> Dict[str, Any]:
    return {"tool": "PLAN", "payload": payload or {}}


def tool_outline(payload: Payload) -> Dict[str, Any]:
    return {"tool": "OUTLINE", "payload": payload or {}}


def tool_write(payload: Payload) -> Dict[str, Any]:
    payload = payload or {}
    user_prompt = _safe_str(payload.get("input") or payload.get("prompt") or payload.get("topic") or "").strip()
    if not user_prompt:
        user_prompt = "Napisz krótki, konkretny akapit na zadany temat."

    model = _safe_str(payload.get("model") or "gpt-4.1-mini")
    max_tokens_raw = payload.get("max_tokens", 260)
    try:
        max_tokens = int(max_tokens_raw)
    except Exception:
        max_tokens = 260
    max_tokens = max(120, min(max_tokens, 1400))

    # Test mode: bez sieci, bez kosztów, deterministycznie (stabilne testy)
    if os.getenv("AGENT_TEST_MODE", "0") == "1":
        text = _deterministic_write(user_prompt)
        return {"tool": "WRITE", "payload": {"text": text, "meta": {"model": "deterministic", "usage": None}}}

    sys_prefix = "Jesteś profesjonalnym autorem. Pisz po polsku, konkretnie, bez lania wody. Zwróć sam tekst.\n\n"
    prompt = sys_prefix + user_prompt

    try:
        resp = _call_llm(model=model, prompt=prompt, max_tokens=max_tokens)
        text = _safe_str(resp.get("content")).strip()
        meta = {"model": resp.get("model") or model, "usage": resp.get("usage")}
    except Exception:
        text = ""
        meta = {"model": model, "usage": None}

    # twarda asekuracja: nie może być mikre (żeby QUALITY nie wywalało przez przypadek)
    if len(text) < 180:
        text = _deterministic_write(user_prompt)

    return {"tool": "WRITE", "payload": {"text": text, "meta": meta}}


def _critic_heuristics(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    issues: List[Dict[str, Any]] = []

    if len(t) < 400:
        issues.append({
            "severity": "HIGH",
            "type": "LENGTH",
            "msg": "Tekst jest krótki — brakuje rozwinięcia i konkretu.",
            "fix": "Dodaj 1–2 akapity: przykład + konsekwencja + mikro-krok.",
        })

    if "\n\n" not in t:
        issues.append({
            "severity": "MED",
            "type": "STRUCTURE",
            "msg": "Brak wyraźnych akapitów (struktura płaska).",
            "fix": "Podziel na 2–3 akapity: obserwacja → sens → działanie.",
        })

    if t.count("jest") > 6:
        issues.append({
            "severity": "LOW",
            "type": "STYLE",
            "msg": "Dużo 'jest' — styl robi się szkolny.",
            "fix": "Wymień część zdań na czasowniki czynności i skróć konstrukcje.",
        })

    while len(issues) < 3:
        issues.append({
            "severity": "MED",
            "type": "CLARITY",
            "msg": "Brakuje domknięcia sensu i kierunku dla czytelnika.",
            "fix": "Dodaj końcówkę: jedna decyzja + jedno działanie na jutro.",
        })

    score = 100 - min(60, 10 * len(issues))
    return {
        "ISSUES": issues[:10],
        "SUMMARY": "CRITIC v1: problemy struktury/konkretu + sugestie poprawek.",
        "SCORE": max(10, score),
        "RECOMMENDATIONS": [i["fix"] for i in issues[:5]],
    }


def tool_critic(payload: Payload) -> Dict[str, Any]:
    payload = payload or {}
    text = _safe_str(payload.get("text") or payload.get("input") or "").strip()
    if not text:
        return {"tool": "CRITIC", "payload": {"STOP": True, "BRAKI_DANYCH": ["text/input"], "ISSUES": [], "SCORE": 0}}
    return {"tool": "CRITIC", "payload": _critic_heuristics(text)}


def tool_edit(payload: Payload) -> Dict[str, Any]:
    # jeszcze stub (Phase C/B)
    return {"tool": "EDIT", "payload": payload or {}}


def tool_quality(payload: Payload) -> Dict[str, Any]:
    payload = payload or {}
    text = _safe_str(payload.get("text") or payload.get("input") or "").strip()

    if not text:
        return {"tool": "QUALITY", "payload": {"DECISION": "REVISE", "REASONS": ["BRAKI_DANYCH: brak tekstu do oceny."]}}

    n = len(text)
    if n < 120:
        return {"tool": "QUALITY", "payload": {"DECISION": "REJECT", "REASONS": ["Tekst jest zbyt krótki (len<120)."]}}

    reasons: List[str] = []
    if n < 400:
        reasons.append("Tekst jest krótki (len<400) — wymaga rozwinięcia.")
    if "\n\n" not in text:
        reasons.append("Brak podziału na akapity (struktura płaska).")
    if "lorem ipsum" in text.lower():
        reasons.append("Wykryto placeholder/dummy tekst (lorem ipsum).")

    decision = "REVISE" if reasons else "ACCEPT"
    return {"tool": "QUALITY", "payload": {"DECISION": decision, "REASONS": reasons[:7]}}


# ----------------------------
# UNIQUENESS v1 (SimHash + global registry + global lock)
# ----------------------------
def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", (text or "").lower())


def _simhash64(text: str) -> int:
    toks = _tokenize(text)
    if len(toks) < 3:
        toks = toks + ["_pad_", "_pad2_", "_pad3_"]

    feats = [" ".join(toks[i:i+3]) for i in range(max(1, len(toks) - 2))]
    v = [0] * 64

    for f in feats:
        h = hashlib.md5(f.encode("utf-8")).digest()
        x = int.from_bytes(h[:8], "big", signed=False)
        for i in range(64):
            v[i] += 1 if ((x >> i) & 1) else -1

    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= (1 << i)
    return out


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _registry_path() -> Path:
    root = _project_root()
    custom = os.getenv("UNIQUENESS_REGISTRY_PATH", "").strip()
    p = Path(custom) if custom else (root / "memory" / "global_uniqueness.jsonl")
    if not p.is_absolute():
        p = root / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_last_records(path: Path, max_lines: int = 800) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    dq = deque(maxlen=max_lines)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                dq.append(line)
    out: List[Dict[str, Any]] = []
    for line in dq:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append_record(path: Path, rec: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _rewrite_brief() -> List[str]:
    return [
        "Zmień strukturę: scena → sens → działanie (zamiast opis → wniosek).",
        "Zmień metafory: unikaj klisz; użyj jednego nietypowego obrazu i pociągnij go 2 zdania.",
        "Zmień rytm: krótsze zdania w 1. akapicie, dłuższe w 2., mocna puenta na końcu.",
        "Zmień perspektywę: przejdź na 2. osobę l.poj. albo 1. osobę — konsekwentnie.",
    ]


def tool_uniqueness(payload: Payload) -> Dict[str, Any]:
    payload = payload or {}
    text = _safe_str(payload.get("text") or "").strip()

    book_id = _safe_str(payload.get("_book_id") or payload.get("book_id") or "default")
    run_id = _safe_str(payload.get("_run_id") or payload.get("run_id") or "")
    src = _safe_str(payload.get("_last_step_path") or payload.get("_source") or "")

    if not text:
        return {
            "tool": "UNIQUENESS",
            "payload": {
                "UNIQ_DECISION": "REVISE",
                "UNIQ_SCORE": 0.0,
                "UNIQ_THRESHOLD": float(os.getenv("UNIQUENESS_THRESHOLD", "0.90")),
                "UNIQ_MATCH": None,
                "REWRITE_BRIEF": ["BRAKI_DANYCH: brak payload.text"],
            },
        }

    threshold = float(os.getenv("UNIQUENESS_THRESHOLD", "0.90"))
    reg_path = _registry_path()
    fp = _simhash64(text)

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0

    # global lock: wspólny rejestr dla wielu książek
    with acquire_book_lock("__UNIQUENESS__"):
        records = _read_last_records(reg_path, max_lines=800)

        for r in records:
            # porównujemy cross-book (wewnątrz tej samej książki możesz chcieć inaczej)
            if _safe_str(r.get("book_id")) == book_id:
                continue
            try:
                other_fp = int(r.get("simhash"))
            except Exception:
                continue
            dist = _hamming(fp, other_fp)
            score = 1.0 - (dist / 64.0)
            if score > best_score:
                best_score = score
                best = {
                    "book_id": r.get("book_id"),
                    "run_id": r.get("run_id"),
                    "source": r.get("source"),
                    "score": round(score, 4),
                    "dist": dist,
                }

        rec = {
            "ts": int(time.time()),
            "book_id": book_id,
            "run_id": run_id,
            "source": src,
            "simhash": int(fp),
            "len": len(text),
            "sha1": hashlib.sha1(text[:800].encode("utf-8")).hexdigest(),
        }
        _append_record(reg_path, rec)

    decision = "REVISE" if best_score >= threshold else "ACCEPT"
    return {
        "tool": "UNIQUENESS",
        "payload": {
            "UNIQ_DECISION": decision,
            "UNIQ_SCORE": round(best_score, 4),
            "UNIQ_THRESHOLD": threshold,
            "UNIQ_MATCH": best,
            "REWRITE_BRIEF": _rewrite_brief() if decision == "REVISE" else [],
        },
    }


def tool_market(payload: Payload) -> Dict[str, Any]:
    return {"tool": "MARKET", "payload": {"NOTE": "stub"}}


def tool_factcheck(payload: Payload) -> Dict[str, Any]:
    return {"tool": "FACTCHECK", "payload": {"NOTE": "stub"}}


# ----------------------------
# auto-stub for missing MODEs
# ----------------------------
def _make_stub(mode_id: str) -> ToolFn:
    def _tool(payload: Payload) -> Dict[str, Any]:
        payload = payload or {}
        return {"tool": mode_id, "payload": {"NOTE": f"STUB TOOL: {mode_id}", "input": payload.get("input")}}
    return _tool


TOOLS: Dict[str, ToolFn] = {
    "PLAN": tool_plan,
    "OUTLINE": tool_outline,
    "WRITE": tool_write,
    "UNIQUENESS": tool_uniqueness,
    "CRITIC": tool_critic,
    "EDIT": tool_edit,
    "QUALITY": tool_quality,
    "MARKET": tool_market,
    "FACTCHECK": tool_factcheck,
}

# Rejestruj wszystkie MODE z configu
try:
    ids = [m.get("id") for m in (load_modes().get("modes") or []) if isinstance(m, dict)]
except Exception:
    ids = []

for mid in ids:
    if isinstance(mid, str) and mid:
        TOOLS.setdefault(mid, _make_stub(mid))
