from pathlib import Path

from app.runtime_memory_adapter import RuntimeMemoryAdapter, normalize_memory


def test_normalize_memory_contract_minimal():
    m = normalize_memory(None)
    assert isinstance(m, dict)
    assert m["version"] == "1.0"
    assert "project_id" in m
    assert "run_id" in m
    assert "entries" in m
    assert isinstance(m["entries"], list)
    assert "updated_at" in m


def test_read_missing_file_returns_contract(tmp_path: Path):
    p = tmp_path / "runtime_memory.json"
    adapter = RuntimeMemoryAdapter(p, max_entries=3)

    m = adapter.read()

    assert m["version"] == "1.0"
    assert m["entries"] == []
    assert m["project_id"] == ""
    assert m["run_id"] == ""


def test_write_then_read_roundtrip(tmp_path: Path):
    p = tmp_path / "runtime_memory.json"
    adapter = RuntimeMemoryAdapter(p, max_entries=3)

    written = adapter.write(
        {
            "project_id": "book-A",
            "run_id": "run-001",
            "entries": [{"role": "planner", "content": "x"}],
        }
    )
    read_back = adapter.read()

    assert written["project_id"] == "book-A"
    assert written["run_id"] == "run-001"
    assert len(written["entries"]) == 1
    assert read_back["project_id"] == "book-A"
    assert read_back["run_id"] == "run-001"
    assert len(read_back["entries"]) == 1
    assert read_back["entries"][0]["role"] == "planner"
    assert read_back["entries"][0]["content"] == "x"


def test_append_truncates_to_max_entries(tmp_path: Path):
    p = tmp_path / "runtime_memory.json"
    adapter = RuntimeMemoryAdapter(p, max_entries=3)

    for i in range(5):
        adapter.append({"role": "writer", "content": f"item-{i}"})

    m = adapter.read()
    assert len(m["entries"]) == 3
    assert [e["content"] for e in m["entries"]] == ["item-2", "item-3", "item-4"]
