from pathlib import Path
from app.pro_writer_runtime import should_use_pro_writer_lane, try_pro_writer_lane

def test_should_use_supported_modes():
    assert should_use_pro_writer_lane("WRITE", "ORCH_STANDARD", {"topic": "x"}) is True
    assert should_use_pro_writer_lane("EDIT", "ORCH_STANDARD", {"topic": "x"}) is True
    assert should_use_pro_writer_lane("CRITIC", "ORCH_STANDARD", {"topic": "x"}) is True

def test_should_not_use_for_unsupported_mode():
    assert should_use_pro_writer_lane("QUALITY", "ORCH_STANDARD", {"topic": "x"}) is False

def test_should_not_use_when_disabled_in_payload():
    assert should_use_pro_writer_lane("WRITE", "ORCH_STANDARD", {"disable_pro_writer_lane": True}) is False

def test_try_lane_returns_contract_triplet():
    handled, response, meta = try_pro_writer_lane("WRITE", "ORCH_STANDARD", {"topic": "x"})
    assert isinstance(handled, bool)
    assert isinstance(response, dict)
    assert isinstance(meta, dict)

def test_main_has_p26_hook_marker():
    txt = Path("app/main.py").read_text(encoding="utf-8")
    assert "P26_PRO_WRITER_RUNTIME_HOOK_BEGIN" in txt
