from app.tools import tool_quality

def test_quality_reject_empty():
    out = tool_quality({"text": ""})
    assert out["payload"]["DECISION"] == "REJECT"

def test_quality_reject_short():
    out = tool_quality({"text": "abc"})
    assert out["payload"]["DECISION"] == "REJECT"

def test_quality_revise_mid():
    out = tool_quality({"text": "a" * 400})
    assert out["payload"]["DECISION"] == "REVISE"

def test_quality_accept_long():
    # long + has paragraphs
    t = ("a" * 800) + "\n\n" + ("b" * 800)
    out = tool_quality({"text": t})
    assert out["payload"]["DECISION"] == "ACCEPT"
