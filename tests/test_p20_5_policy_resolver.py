from app.policy_resolver import resolve_policy_for_scope

def test_priority_mode_over_preset_over_global():
    current = {"quality_min": 0.78, "max_retries": 2, "critic_weight": 1.0}
    flags = {
        "enabled_global": True,
        "overrides_global": {"quality_min": 0.80, "max_retries": 3},
        "overrides_by_preset": {"ORCH_STANDARD": {"quality_min": 0.82}},
        "overrides_by_mode": {"WRITE": {"quality_min": 0.84, "max_retries": 4}},
    }

    resolved, t = resolve_policy_for_scope(current, "orch_standard", "write", flags)
    assert resolved["quality_min"] == 0.84
    assert resolved["max_retries"] == 4
    assert t["policy_source"] == "mode"
    assert t["enabled"] is True

def test_disable_by_mode():
    current = {"quality_min": 0.78}
    flags = {"enabled_global": True, "enabled_by_mode": {"WRITE": False}}
    _, t = resolve_policy_for_scope(current, "ORCH_STANDARD", "WRITE", flags)
    assert t["enabled"] is False
    assert t["enabled_checks"]["mode"] is False

def test_disable_by_preset():
    current = {"quality_min": 0.78}
    flags = {"enabled_global": True, "enabled_by_preset": {"ORCH_STANDARD": False}}
    _, t = resolve_policy_for_scope(current, "ORCH_STANDARD", "WRITE", flags)
    assert t["enabled"] is False
    assert t["enabled_checks"]["preset"] is False
