import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_books_root, get_runs_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_profile_guard_and_dedupe(monkeypatch, isolated_agentpro_storage):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    client = TestClient(app)
    token = uuid4().hex[:8]
    book_id = f"profile_guard_{token}"
    assert not (REPO_ROOT / "books" / book_id).exists()

    ensure_test_book_bible(book_id)
    profile_path = get_books_root() / book_id / "project_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "book_id": book_id,
                "domain": "NONFICTION",
                "language": "pl",
                "topic": "Default profile fixture for tests",
                "banned_phrases": ["jak zwykle", "jak zwykle"],
                "style_rules": {
                    "no_meta_ai": True,
                    "no_placeholders": True,
                    "dedupe_lists": True,
                },
                "meta": {"version": 1},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    body = {
        "preset": "DRAFT_EDIT_QUALITY",
        "modes": ["WRITE", "CRITIC", "EDIT", "QUALITY"],
        "book_id": book_id,
        "payload": {
            "book_id": book_id,
            "topic": "Bohater odkrywa, że całe jego życie było eksperymentem. Krótki, mocny wstęp thrillera.",
            "max_tokens": 650,
        },
        "resume": False,
    }
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok"), data

    run_id = data["run_id"]

    latest = get_books_root() / book_id / "draft" / "latest.txt"
    assert latest.exists(), "latest.txt not created"
    txt = latest.read_text(encoding="utf-8")

    assert "\n\n" in txt, "No paragraph breaks in latest.txt"

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    banned = profile.get("banned_phrases") or []
    for b in banned:
        if not isinstance(b, str):
            continue
        assert b not in txt, f"banned phrase leaked into latest.txt: {b}"

    critic_path = get_runs_root() / run_id / "steps" / "002_CRITIC.json"
    assert critic_path.exists(), "CRITIC step missing"
    critic = json.loads(critic_path.read_text(encoding="utf-8"))
    issues = (((critic.get("result") or {}).get("payload") or {}).get("ISSUES") or [])
    seen = set()
    for it in issues:
        if isinstance(it, dict):
            k = (str(it.get("type")), str(it.get("msg")), str(it.get("fix")))
        else:
            k = ("_", str(it))
        assert k not in seen, "Duplicate issue detected in CRITIC"
        seen.add(k)

    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()
