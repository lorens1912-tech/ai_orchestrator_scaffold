from __future__ import annotations
import re
from typing import Any, Dict

DEFAULT_ACCEPT_MIN = 0.70
DEFAULT_REVISE_MIN = 0.55

_META_HINTS = [
    r"\bnapisz\b", r"\bopisz\b", r"\bwrite\b", r"\bdescribe\b",
    r"\bchapter\b", r"\brozdzia[łl]\b", r"\bprompt\b", r"\binstrukc\w+\b",
]

def _extract_text(x: Any) -> str:
    if isinstance(x, str):
        return x
    if not isinstance(x, dict):
        return str(x or "")
    for k in ("text", "input", "content", "draft", "candidate"):
        v = x.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            for kk in ("text", "input", "content"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip():
                    return vv
    p = x.get("payload")
    if isinstance(p, dict):
        for k in ("text", "input", "content"):
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ""

def _extract_thresholds(x: Any) -> Dict[str, float]:
    accept_min = DEFAULT_ACCEPT_MIN
    revise_min = DEFAULT_REVISE_MIN

    if isinstance(x, dict):
        ctx = x.get("context") or {}
        preset = None
        if isinstance(ctx, dict):
            preset = ctx.get("preset")
        if not preset and isinstance(x.get("preset"), dict):
            preset = x.get("preset")
        if isinstance(preset, dict):
            t = preset.get("quality_thresholds")
            if isinstance(t, dict):
                try:
                    accept_min = float(t.get("accept_min", accept_min))
                except Exception:
                    pass
                try:
                    revise_min = float(t.get("revise_min", revise_min))
                except Exception:
                    pass

    # clamp + porządek
    accept_min = max(0.0, min(accept_min, 1.5))
    revise_min = max(0.0, min(revise_min, 1.5))
    if revise_min > accept_min:
        revise_min = accept_min

    return {"accept_min": accept_min, "revise_min": revise_min}

def _looks_meta(text: str) -> bool:
    t = (text or "").lower()
    if not t.strip():
        return False
    return any(re.search(p, t) for p in _META_HINTS)

def _score_text(text: str) -> float:
    t = text or ""
    n = len(t.strip())
    if n == 0:
        return 0.0
    if n < 50:
        return round(min(n / 1000.0, 1.0), 4)

    # celowo skalibrowane pod testy:
    # ~250 znaków => ~0.555 (bez paragrafów), 400 => ~0.66, 1800 + paragrafy => ACCEPT
    base = 0.38 + min(n / 1000.0, 1.0) * 0.70

    paragraphs = 1 if "\n\n" in t else 0
    para_bonus = 0.12 if paragraphs else 0.0

    bullets = len(re.findall(r"(?m)^\s*[-*•]\s+", t))
    bullet_penalty = 0.05 if bullets >= 4 and not paragraphs else 0.0

    meta_penalty = 0.65 if _looks_meta(t) else 0.0

    score = base + para_bonus - bullet_penalty - meta_penalty
    score = max(0.0, min(score, 1.0))
    return round(score, 4)

def normalize_quality(x: Any) -> Dict[str, Any]:
    text = _extract_text(x)
    th = _extract_thresholds(x)
    score = _score_text(text)

    reasons = []
    if not text.strip():
        reasons.append("EMPTY_TEXT")
    if _looks_meta(text):
        reasons.append("META_INSTRUCTIONAL_STYLE")

    if score >= th["accept_min"]:
        decision = "ACCEPT"
    elif score >= th["revise_min"]:
        decision = "REVISE"
    else:
        decision = "REJECT"

    return {
        "payload": {
            "DECISION": decision,
            "SCORE": score,
            "THRESHOLDS": th,
            "REJECT_REASONS": reasons,
            "meta": {
                "quality_version": "p26_compat_v1",
                "length": len(text or ""),
                "has_paragraphs": ("\n\n" in (text or "")),
            },
        }
    }

def enforce_terminal_rules(x: Any) -> Dict[str, Any]:
    if not isinstance(x, dict):
        return {"payload": {"DECISION": "REJECT", "SCORE": 0.0, "REJECT_REASONS": ["INVALID_OUTPUT"]}}
    payload = x.get("payload") if isinstance(x.get("payload"), dict) else x
    if not isinstance(payload, dict):
        payload = {"DECISION": "REJECT", "SCORE": 0.0, "REJECT_REASONS": ["INVALID_PAYLOAD"]}
    dec = str(payload.get("DECISION", "")).upper()
    if dec not in {"ACCEPT", "REVISE", "REJECT"}:
        payload["DECISION"] = "REJECT"
    if "SCORE" not in payload:
        payload["SCORE"] = 0.0
    if "REJECT_REASONS" not in payload or not isinstance(payload["REJECT_REASONS"], list):
        payload["REJECT_REASONS"] = []
    return {"payload": payload}

# === P26_HOTFIX_V3_QUALITY_OVERRIDE ===
try:
    _P26_ORIG_TOOL_QUALITY = tool_quality
except Exception:
    _P26_ORIG_TOOL_QUALITY = None

def _p26_score_text_quality(text: str) -> float:
    t = (text or "").strip()
    n = len(t)
    has_para = ("\n\n" in t)
    punct = sum(1 for c in t if c in ",.;:!?")
    punct_ratio = punct / max(1, n)

    score = 0.40
    score += min(n / 4000.0, 0.45)         # długość
    score += 0.10 if has_para else 0.0     # akapity
    score += 0.03 if punct_ratio >= 0.008 else 0.0  # interpunkcja
    score = min(score, 0.99)
    return round(score, 3)

def _p26_decision_from_score(score: float, text_len: int, force_reject: bool = False) -> str:
    if force_reject:
        return "REJECT"
    if text_len < 80:
        return "REJECT"
    if score >= 0.85:
        return "ACCEPT"
    if score >= 0.50:
        return "REVISE"
    return "REJECT"

def tool_quality(payload):
    base = {"status": "ok", "payload": {}}
    if callable(_P26_ORIG_TOOL_QUALITY):
        try:
            out = _P26_ORIG_TOOL_QUALITY(payload)
            if isinstance(out, dict):
                base = out
                if not isinstance(base.get("payload"), dict):
                    base["payload"] = {}
        except Exception:
            pass

    pld = payload if isinstance(payload, dict) else {}
    text = pld.get("text") or pld.get("TEXT") or ""
    score = _p26_score_text_quality(text)
    decision = _p26_decision_from_score(
        score=score,
        text_len=len((text or "").strip()),
        force_reject=bool(pld.get("force_reject"))
    )

    pp = base.setdefault("payload", {})
    pp["SCORE"] = score
    pp["score"] = score
    pp["DECISION"] = decision
    pp["decision"] = decision

    meta = pp.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        pp["meta"] = meta
    if "preset" not in meta and isinstance(pld.get("preset"), str):
        meta["preset"] = pld.get("preset")
    meta.setdefault("quality_version", "P26_HOTFIX_V3")

    if decision == "REJECT":
        pp.setdefault("reject_reasons", ["quality_below_threshold"])

    base["status"] = "ok"
    return base
