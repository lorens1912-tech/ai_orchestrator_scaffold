from app.policy_targeted import adjust_policy_targeted

def _high_pressure_feedback():
    return {
        "reject_rate": 0.55,
        "retry_rate": 0.60,
        "accept_rate": 0.20,
        "observed_quality": 0.61,
        "user_satisfaction": 0.30,
    }

def test_targeted_adjust_uses_mode_source():
    current = {
        "quality_min": 0.78,
        "max_retries": 2,
        "critic_weight": 1.00,
        "writer_temperature": 0.55,
    }
    flags = {
        "enabled_global": True,
        "overrides_global": {"quality_min": 0.79},
        "overrides_by_preset": {"ORCH_STANDARD": {"max_retries": 3}},
        "overrides_by_mode": {"WRITE": {"max_retries": 4, "critic_weight": 1.25}},
    }

    adjusted, meta = adjust_policy_targeted(
        current_policy=current,
        feedback=_high_pressure_feedback(),
        preset="ORCH_STANDARD",
        mode="WRITE",
        flags=flags,
    )

    assert meta["telemetry"]["policy_source"] == "mode"
    assert meta["audit"]["band"] in {"tighten", "hold", "relax"}
    assert adjusted["max_retries"] >= 4

def test_targeted_skip_when_disabled():
    current = {
        "quality_min": 0.78,
        "max_retries": 2,
        "critic_weight": 1.00,
        "writer_temperature": 0.55,
    }
    flags = {
        "enabled_global": True,
        "enabled_by_mode": {"WRITE": False},
        "overrides_by_mode": {"WRITE": {"quality_min": 0.88}},
    }

    adjusted, meta = adjust_policy_targeted(
        current_policy=current,
        feedback=_high_pressure_feedback(),
        preset="ORCH_STANDARD",
        mode="WRITE",
        flags=flags,
    )

    assert meta["audit"]["band"] == "skip"
    assert meta["audit"]["reason"] == "disabled_by_flags"
    assert abs(adjusted["quality_min"] - 0.88) < 1e-9
