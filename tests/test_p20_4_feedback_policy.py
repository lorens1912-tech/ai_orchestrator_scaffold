from app.policy_feedback import adjust_policy_from_feedback

def test_high_pressure_tightens_policy():
    current = {
        "quality_min": 0.78,
        "max_retries": 2,
        "critic_weight": 1.00,
        "writer_temperature": 0.55,
    }
    feedback = {
        "reject_rate": 0.55,
        "retry_rate": 0.60,
        "accept_rate": 0.20,
        "observed_quality": 0.61,
        "user_satisfaction": 0.30,
    }
    adjusted, audit = adjust_policy_from_feedback(current, feedback)
    assert audit["band"] == "tighten"
    assert adjusted["quality_min"] > 0.78
    assert adjusted["max_retries"] >= 3
    assert adjusted["writer_temperature"] < 0.55

def test_low_pressure_relaxes_policy():
    current = {
        "quality_min": 0.78,
        "max_retries": 2,
        "critic_weight": 1.00,
        "writer_temperature": 0.55,
    }
    feedback = {
        "reject_rate": 0.02,
        "retry_rate": 0.05,
        "accept_rate": 0.96,
        "observed_quality": 0.88,
        "user_satisfaction": 0.95,
    }
    adjusted, audit = adjust_policy_from_feedback(current, feedback)
    assert audit["band"] == "relax"
    assert adjusted["quality_min"] < 0.78
    assert adjusted["max_retries"] <= 2
    assert adjusted["writer_temperature"] > 0.55

def test_policy_clamps_ranges():
    current = {
        "quality_min": 0.99,
        "max_retries": 50,
        "critic_weight": 9.0,
        "writer_temperature": -5.0,
    }
    feedback = {
        "reject_rate": 1.0,
        "retry_rate": 1.0,
        "accept_rate": 0.0,
        "observed_quality": 0.0,
        "user_satisfaction": 0.0,
    }
    adjusted, _ = adjust_policy_from_feedback(current, feedback)
    assert 0.72 <= adjusted["quality_min"] <= 0.90
    assert 1 <= adjusted["max_retries"] <= 5
    assert 0.80 <= adjusted["critic_weight"] <= 1.60
    assert 0.20 <= adjusted["writer_temperature"] <= 0.90
