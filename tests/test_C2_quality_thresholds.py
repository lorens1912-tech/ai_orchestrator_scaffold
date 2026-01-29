from app.tools import tool_quality

def _mk_good_text():
    return ("a" * 900) + "\n\n" + ("b" * 900)

def test_thresholds_force_reject_when_thresholds_above_one():
    # score is clamped to <= 1.0, so thresholds > 1.0 MUST yield REJECT
    ctx = {"preset": {"quality_thresholds": {"accept_min": 1.01, "revise_min": 1.01}}}
    out = tool_quality({"text": _mk_good_text(), "context": ctx})
    assert out["payload"]["DECISION"] == "REJECT"

def test_thresholds_force_accept_when_accept_min_very_low():
    ctx = {"preset": {"quality_thresholds": {"accept_min": 0.01, "revise_min": 0.00}}}
    out = tool_quality({"text": _mk_good_text(), "context": ctx})
    assert out["payload"]["DECISION"] == "ACCEPT"
