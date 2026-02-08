from app.quality_taxonomy import classify_quality_payload

def test_p20_taxonomy_fail_min_words_empty():
    payload = {
        "DECISION": "FAIL",
        "REASONS": ["MIN_WORDS: Words=0, min_words=120.", "EMPTY: Brak treści"],
        "STATS": {"words": 0, "chars": 0},
        "FLAGS": {"too_short": True, "has_meta": False, "has_placeholders": False, "has_lists": False},
    }
    r = classify_quality_payload(payload)
    assert r["decision"] == "FAIL"
    assert "MIN_WORDS" in r["reason_codes"]
    assert "EMPTY" in r["reason_codes"]
    assert "DECISION.FAIL" in r["tags"]
    assert "REASON.MIN_WORDS" in r["tags"]
    assert "REASON.EMPTY" in r["tags"]
    assert "FLAG.TOO_SHORT" in r["tags"]

def test_p20_taxonomy_accept_clean():
    payload = {
        "DECISION": "ACCEPT",
        "REASONS": [],
        "STATS": {"words": 164, "chars": 1372},
        "FLAGS": {"too_short": False, "has_meta": False, "has_placeholders": False, "has_lists": False},
    }
    r = classify_quality_payload(payload)
    assert r["decision"] == "ACCEPT"
    assert r["reason_codes"] == []
    assert "DECISION.ACCEPT" in r["tags"]
    assert "FLAG.TOO_SHORT" not in r["tags"]

def test_p20_taxonomy_revise_other_reason():
    payload = {
        "DECISION": "REVISE",
        "REASONS": ["SOMETHING_NEW"],
        "STATS": {"words": 98, "chars": 600},
        "FLAGS": {"too_short": True},
    }
    r = classify_quality_payload(payload)
    assert r["decision"] == "REVISE"
    assert "OTHER" in r["reason_codes"]
    assert "DECISION.REVISE" in r["tags"]
    assert "REASON.OTHER" in r["tags"]
