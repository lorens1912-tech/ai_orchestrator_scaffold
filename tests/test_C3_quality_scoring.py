from app.tools import tool_quality

def test_c3_empty_reject_score_zero():
    out = tool_quality({"text": ""})
    assert out["payload"]["DECISION"] == "REJECT"
    assert out["payload"]["SCORE"] == 0.0

def test_c3_meta_reject():
    out = tool_quality({"text": "Napiszę teraz rozdział o czymś tam...\n\nDalej opiszę..."})
    assert out["payload"]["DECISION"] == "REJECT"

def test_c3_long_with_paragraphs_accept_default_thresholds():
    t = ("a" * 900) + "\n\n" + ("b" * 900)
    out = tool_quality({"text": t})
    assert out["payload"]["DECISION"] == "ACCEPT"
    assert out["payload"]["SCORE"] >= 0.85

def test_c3_paragraphs_increase_score():
    t_flat = ("a" * 1800)  # no paragraphs
    t_para = ("a" * 900) + "\n\n" + ("b" * 900)
    o1 = tool_quality({"text": t_flat})
    o2 = tool_quality({"text": t_para})
    assert o2["payload"]["SCORE"] > o1["payload"]["SCORE"]
