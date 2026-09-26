import pytest


def test_legacy_p0_write_cannot_masquerade_as_production_generation(
    monkeypatch, isolated_agentpro_storage,
):
    del isolated_agentpro_storage
    monkeypatch.setenv("WRITE_MODEL_FORCE", "gpt-5.1")
    monkeypatch.setenv("AGENT_TEST_MODE", "0")
    from app.orchestrator_stub import execute_stub

    with pytest.raises(RuntimeError, match="WRITE_MODEL_TRANSPORT_REQUIRED"):
        execute_stub(
            run_id="run-legacy-p0-write",
            book_id="legacy-p0-write-book",
            modes=["WRITE"],
            payload={"input": "neutral"},
            steps=None,
        )
