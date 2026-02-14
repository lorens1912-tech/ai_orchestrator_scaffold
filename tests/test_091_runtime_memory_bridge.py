from pathlib import Path

from app.runtime_memory_adapter import RuntimeMemoryAdapter
from app.runtime_memory_bridge import build_runtime_context, load_and_bind_runtime_memory


def test_build_runtime_context_defaults():
    ctx = build_runtime_context(None, None, tail_size=20)

    assert isinstance(ctx, dict)
    assert ctx["project_id"] == ""
    assert ctx["run_id"] == ""
    assert ctx["memory_version"] == "1.0"
    assert ctx["memory_entries_count"] == 0
    assert isinstance(ctx["memory_tail"], list)
    assert ctx["state"]["mode"] == ""
    assert ctx["state"]["step_index"] == 0
    assert ctx["state"]["profile"] == ""


def test_build_runtime_context_prefers_run_state_ids():
    memory = {
        "version": "1.0",
        "project_id": "book-memory",
        "run_id": "run-memory",
        "entries": [{"role": "writer", "content": "x"}],
        "updated_at": "2026-01-01T00:00:00Z",
    }
    run_state = {"project_id": "book-run", "run_id": "run-123", "mode": "WRITE", "step_index": 7, "profile": "NOVEL_STRICT_SINGLE"}

    ctx = build_runtime_context(memory, run_state, tail_size=20)

    assert ctx["project_id"] == "book-run"
    assert ctx["run_id"] == "run-123"
    assert ctx["memory_entries_count"] == 1
    assert ctx["state"]["mode"] == "WRITE"
    assert ctx["state"]["step_index"] == 7
    assert ctx["state"]["profile"] == "NOVEL_STRICT_SINGLE"


def test_build_runtime_context_tail_truncation():
    entries = [{"role": "writer", "content": f"item-{i}"} for i in range(10)]
    memory = {"entries": entries}

    ctx = build_runtime_context(memory, {"run_id": "r1"}, tail_size=3)

    assert ctx["memory_entries_count"] == 10
    assert [e["content"] for e in ctx["memory_tail"]] == ["item-7", "item-8", "item-9"]


def test_load_and_bind_runtime_memory(tmp_path: Path):
    path = tmp_path / "runtime_memory.json"
    adapter = RuntimeMemoryAdapter(path, max_entries=10)

    adapter.append({"role": "planner", "content": "plan-1"})
    adapter.append({"role": "writer", "content": "draft-1"})

    out = load_and_bind_runtime_memory(
        adapter=adapter,
        run_state={"project_id": "book-A", "run_id": "run-001", "mode": "WRITE", "step_index": 2, "profile": "GUIDE_MULTI_PROJECT"},
        tail_size=1,
    )

    assert "runtime_memory" in out
    rm = out["runtime_memory"]
    assert rm["project_id"] == "book-A"
    assert rm["run_id"] == "run-001"
    assert rm["memory_entries_count"] == 2
    assert len(rm["memory_tail"]) == 1
    assert rm["memory_tail"][0]["content"] == "draft-1"
