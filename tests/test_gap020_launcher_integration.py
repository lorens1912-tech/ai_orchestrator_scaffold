from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\AI\ai_orchestrator_scaffold")


def _powershell_probe(script: Path, cwd: Path, *, test_mode: str = "1") -> dict:
    # Intercept only the server command. The launcher's real Set-Location and
    # environment assignments still execute in the child PowerShell process.
    command = r"""
function python {
    $record = @{
        cwd = (Get-Location).Path
        mode = $env:AGENT_TEST_MODE
        fastpath = $env:PYTEST_FASTPATH
        argv = @($args)
    }
    Write-Output ("GAP020_PROBE:" + ($record | ConvertTo-Json -Compress))
}
function uvicorn {
    $record = @{
        cwd = (Get-Location).Path
        mode = $env:AGENT_TEST_MODE
        fastpath = $env:PYTEST_FASTPATH
        argv = @($args)
    }
    Write-Output ("GAP020_PROBE:" + ($record | ConvertTo-Json -Compress))
}
& '__SCRIPT__'
""".replace("__SCRIPT__", str(script).replace("'", "''"))
    env = os.environ.copy()
    env["AGENT_TEST_MODE"] = test_mode
    env["PYTEST_FASTPATH"] = "1"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    markers = [
        line.removeprefix("GAP020_PROBE:")
        for line in result.stdout.splitlines()
        if line.startswith("GAP020_PROBE:")
    ]
    assert len(markers) == 1, result.stdout
    return json.loads(markers[0])


@pytest.mark.parametrize(
    ("name", "port"),
    [
        ("run_server.ps1", "8001"),
        ("run_server_agent.ps1", "8001"),
        ("scripts/start_server_full_real.ps1", "8000"),
    ],
)
def test_powershell_production_launcher_uses_own_checkout_and_real_mode(
    tmp_path: Path, name: str, port: str
) -> None:
    record = _powershell_probe(ROOT / name, tmp_path)
    assert Path(record["cwd"]).resolve() == ROOT.resolve()
    assert Path(record["cwd"]).resolve() != OLD_ROOT.resolve()
    assert record["mode"] == "0"
    assert not record["fastpath"]
    assert record["argv"][:2] == ["-m", "uvicorn"]
    assert "app.main:app" in record["argv"]
    assert record["argv"][-2] == "--port"
    assert str(record["argv"][-1]) == port


def test_batch_production_launcher_uses_own_checkout(tmp_path: Path) -> None:
    shim = tmp_path / "python.cmd"
    shim.write_text(
        "@echo off\r\necho GAP020_PROBE:%CD%#%AGENT_TEST_MODE%#%PYTEST_FASTPATH%#%*\r\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["AGENT_TEST_MODE"] = "1"
    env["PYTEST_FASTPATH"] = "1"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(ROOT / "RUN.bat")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    markers = [line for line in result.stdout.splitlines() if line.startswith("GAP020_PROBE:")]
    assert len(markers) == 1, result.stdout
    cwd, mode, fastpath, argv = markers[0].removeprefix("GAP020_PROBE:").split("#", 3)
    assert Path(cwd).resolve() == ROOT.resolve()
    assert Path(cwd).resolve() != OLD_ROOT.resolve()
    assert mode == "0"
    assert fastpath == ""
    assert "-m uvicorn app.main:app" in argv
    assert "--port 8001" in argv


def test_offline_scripts_are_absent_from_active_runtime_imports() -> None:
    code = """
import json
import sys
from pathlib import Path
from app.main import app
from app.p20_core import executor, runtime

assert Path(sys.modules["app.main"].__file__).resolve().parents[1] == Path.cwd().resolve()
assert runtime.execute_stub is executor.execute_p20
assert any(route.path == "/agent/step" and route.endpoint.__module__ == "app.main" for route in app.routes)
assert any(route.path.endswith("/book-qa") and route.endpoint.__module__ == "app.operator_api" for route in app.routes)
assert any("/translations/" in route.path and route.endpoint.__module__ == "app.operator_api" for route in app.routes)
for name in ("app.agent_api", "app.orchestrator_stub", "app.pytest_fastpath",
             "scripts.p20_2_tune_auto_retry_policy", "scripts.p20_3_build_retry_feedback"):
    assert name not in sys.modules, name
print(json.dumps({"app": str(Path(sys.modules["app.main"].__file__).resolve()),
                  "routes": len(app.routes)}))
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["app"]).resolve() == (ROOT / "app/main.py").resolve()


def test_explicit_test_mode_remains_available_to_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import _is_test_mode

    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    assert _is_test_mode() is True
    monkeypatch.setenv("AGENT_TEST_MODE", "0")
    assert _is_test_mode() is False



@pytest.mark.parametrize(
    "name",
    [
        "run_server.ps1",
        "run_server_agent.ps1",
        "scripts/start_server_full_real.ps1",
    ],
)
def test_powershell_launcher_follows_a_second_checkout_copy(
    tmp_path: Path, name: str
) -> None:
    checkout = tmp_path / "alternate-checkout"
    target = checkout / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((ROOT / name).read_bytes())
    record = _powershell_probe(target, tmp_path)
    assert Path(record["cwd"]).resolve() == checkout.resolve()
    assert record["mode"] == "0"


def test_batch_launcher_follows_a_second_checkout_copy(tmp_path: Path) -> None:
    checkout = tmp_path / "alternate-checkout"
    checkout.mkdir()
    (checkout / "RUN.bat").write_bytes((ROOT / "RUN.bat").read_bytes())
    shim = tmp_path / "python.cmd"
    shim.write_text(
        "@echo off\r\necho GAP020_PROBE:%CD%#%AGENT_TEST_MODE%#%PYTEST_FASTPATH%#%*\r\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["AGENT_TEST_MODE"] = "1"
    env["PYTEST_FASTPATH"] = "1"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(checkout / "RUN.bat")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    markers = [line for line in result.stdout.splitlines() if line.startswith("GAP020_PROBE:")]
    assert len(markers) == 1, result.stdout
    cwd, mode, fastpath, _ = markers[0].removeprefix("GAP020_PROBE:").split("#", 3)
    assert Path(cwd).resolve() == checkout.resolve()
    assert mode == "0"
    assert fastpath == ""


def test_offline_tools_are_explicit_and_not_runtime_dependencies() -> None:
    import ast

    for name in ("p20_2_tune_auto_retry_policy.py", "p20_3_build_retry_feedback.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "Offline JSON tool" in (ast.get_docstring(ast.parse(source)) or "")
        for active in (
            "app/main.py",
            "app/operator_api.py",
            "app/p20_core/runtime.py",
            "app/p20_core/executor.py",
        ):
            assert name.removesuffix(".py") not in (ROOT / active).read_text(encoding="utf-8")


def test_active_quality_operation_survives_fresh_import_and_reopen(
    isolated_agentpro_storage: Path,
) -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    request = {
        "project_id": "PROJ-gap020-reopen-a",
        "book_id": "BOOK-gap020-reopen-a",
        "run_id": "run-gap020-reopen-a",
        "step_id": "step-gap020-reopen-a",
        "modes": ["QUALITY"],
        "payload": {"text": "A neutral synthetic sentence. " * 14},
    }
    first = TestClient(app).post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["project_id"] == request["project_id"]
    record = body["evaluation_record"]
    assert record["decision"] == "REVISE"
    assert record["execution_status"] == "COMPLETED"
    assert record["validation_status"] == "VALID"
    assert record["context_package_id"] and record["context_hash"]

    other = dict(request)
    other["project_id"] = "PROJ-gap020-reopen-b"
    other["book_id"] = "BOOK-gap020-reopen-b"
    other["run_id"] = "run-gap020-reopen-b"
    other["step_id"] = "step-gap020-reopen-b"
    second = TestClient(app).post("/agent/step", json=other)
    assert second.status_code == 200, second.text
    assert second.json()["evaluation_record"]["evaluation_id"] != record["evaluation_id"]

    child = r"""
import json
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.p20_core.project_repository import ProjectRepository, StorageResolver

request = json.loads(sys.argv[1])
expected = json.loads(sys.argv[2])
other = json.loads(sys.argv[3])
repo = ProjectRepository(StorageResolver().resolve_project(
    request["project_id"], book_id=request["book_id"]))
other_repo = ProjectRepository(StorageResolver().resolve_project(
    other["project_id"], book_id=other["book_id"]))
own_records = repo.list_evaluation_envelopes()
foreign_records = other_repo.list_evaluation_envelopes()
assert len(own_records) == len(foreign_records) == 1
assert own_records[0]["record"] == expected
assert foreign_records[0]["record"]["evaluation_id"] != expected["evaluation_id"]
retry = TestClient(app).post("/agent/step", json={**request, "technical_retry": True})
assert retry.status_code == 200, retry.text
assert retry.json()["evaluation_record"] == expected
assert len(repo.list_evaluation_envelopes()) == 1
assert Path(sys.modules["app.main"].__file__).resolve().parents[1] == Path.cwd().resolve()
print(json.dumps({"app": str(Path(sys.modules["app.main"].__file__).resolve()),
                  "evaluation_id": expected["evaluation_id"],
                  "context_hash": expected["context_hash"]}))
"""
    env = os.environ.copy()
    env["AGENTPRO_STORAGE_ROOT"] = str(isolated_agentpro_storage)
    env["AGENT_TEST_MODE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    reopened = subprocess.run(
        [sys.executable, "-c", child, json.dumps(request), json.dumps(record), json.dumps(other)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert reopened.returncode == 0, reopened.stderr
    summary = json.loads(reopened.stdout)
    assert Path(summary["app"]).resolve() == (ROOT / "app/main.py").resolve()
    assert summary["evaluation_id"] == record["evaluation_id"]
    assert summary["context_hash"] == record["context_hash"]



def test_fastpath_profile_is_explicitly_diagnostic(tmp_path: Path) -> None:
    script = ROOT / "scripts/start_server_strict_fastpath.ps1"
    source = script.read_text(encoding="utf-8")
    assert source.startswith("# Diagnostic-only launcher")
    assert "SERVER_PROFILE=DIAGNOSTIC_STRICT_FASTPATH" in source
    record = _powershell_probe(script, tmp_path)
    assert Path(record["cwd"]).resolve() == ROOT.resolve()
    assert record["mode"] == "0"
    assert record["fastpath"] == "1"
