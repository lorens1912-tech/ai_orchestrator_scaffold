import json
import re
from pathlib import Path
from uuid import uuid4

from app.orchestrator_stub import execute_stub
from app.p20_core.storage_paths import get_storage_root


FORBIDDEN = [r"lorem ipsum", r"placeholder", r"dummy text"]
REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact_path(public_path: str) -> Path:
    path = Path(public_path)
    assert not path.is_absolute(), public_path
    return get_storage_root() / path


def test_write_produces_real_text(monkeypatch, isolated_agentpro_storage):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    run_id = f"test_run_015_{uuid4().hex[:8]}"
    book_id = f"test_book_015_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

    artifact_paths = execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=["WRITE"],
        payload={"input": "Napisz krótki, konkretny akapit o samotności po rozwodzie."},
    )

    p = _artifact_path(artifact_paths[-1])
    assert p.exists(), f"WRITE artifact missing: {p}"

    step = json.loads(p.read_text(encoding="utf-8"))
    text = ((step.get("result") or {}).get("payload") or {}).get("text", "")

    assert len(text) > 100, "WRITE output too short"

    lowered = text.lower()
    for pat in FORBIDDEN:
        assert not re.search(pat, lowered), f"Forbidden placeholder detected: {pat}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()
