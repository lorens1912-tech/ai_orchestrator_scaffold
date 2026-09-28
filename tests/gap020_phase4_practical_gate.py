"""GAP-020 Phase 4 process gate. Neutral isolated data; no external provider."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = {
    "run_server.ps1": 8001,
    "run_server_agent.ps1": 8001,
    "RUN.bat": 8001,
    "scripts/start_server_full_real.ps1": 8000,
}
DIAGNOSTIC = {"scripts/start_server_strict_fastpath.ps1": 8000}
OLD_ROOT = Path(r"C:\AI\ai_orchestrator_scaffold")


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def http(port: int, method: str, path: str, body: dict | None = None,
         token: str | None = None) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(f"http://127.0.0.1:{port}{path}", data=data,
                      headers=headers, method=method)
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw.decode("utf-8", errors="replace")
        return exc.code, payload


class Server:
    def __init__(self, name: str, cwd: Path, env: dict[str, str], temp: Path,
                 root: Path = ROOT):
        self.name = name
        self.cwd = cwd
        self.env = env
        self.temp = temp
        self.root = root
        self.port = (PRODUCTION | DIAGNOSTIC)[name]
        self.process: subprocess.Popen | None = None
        self.log_file = temp / ("server-" + name.replace("/", "-").replace(".", "-") +
                                "-" + str(time.time_ns()) + ".log")
        self.log_stream = None

    def start(self) -> "Server":
        with socket.socket() as sock:
            require(sock.connect_ex(("127.0.0.1", self.port)) != 0,
                    f"port {self.port} is occupied before {self.name}")
        script = self.root / self.name
        command = (["cmd.exe", "/d", "/c", str(script)] if script.suffix.lower() == ".bat"
                   else ["powershell.exe", "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "Bypass", "-File", str(script)])
        self.log_stream = self.log_file.open("wb")
        self.process = subprocess.Popen(
            command, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
            stdout=self.log_stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(
                    f"{self.name} exited {self.process.returncode}: "
                    + self.log_file.read_text(encoding="utf-8", errors="replace")[-3000:]
                )
            try:
                status, health = http(self.port, "GET", "/health")
                if status == 200 and isinstance(health, dict) and health.get("runtime") == "P20.x":
                    return self
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.3)
        raise AssertionError(f"{self.name} did not become ready: "
                             + self.log_file.read_text(encoding="utf-8", errors="replace")[-3000:])

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            require(result.returncode == 0, f"could not stop owned launcher {self.name}: {result.stderr}")
        self.process.wait(timeout=20)
        if self.log_stream is not None:
            self.log_stream.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", self.port)) != 0:
                    return
            time.sleep(0.2)
        raise AssertionError(f"owned server {self.name} still listens on {self.port}")

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.stop()


def check_routes(port: int) -> None:
    status, spec = http(port, "GET", "/openapi.json")
    require(status == 200 and isinstance(spec, dict), "OpenAPI unavailable")
    paths = spec["paths"]
    for route in (
        "/agent/step",
        "/operator/identity",
        "/operator/projects/{project_id}/books/{book_id}/source-masters/current",
        "/operator/projects/{project_id}/books/{book_id}/translations/{locale}/runs",
    ):
        require(route in paths, f"official route missing: {route}")
    require("/books" not in paths and "/agent_api" not in paths,
            "legacy router mounted")


def setup_neutral_source(storage: Path, temp: Path) -> tuple[dict, str, str, str]:
    os.environ["AGENTPRO_STORAGE_ROOT"] = str(storage)
    from app.operator_dpapi import read_secret
    from app.p20_core.local_operator import initialize_operator
    from app.p20_core.project_repository import ensure_system_repository
    from tests.test_gap018_manuscript_version import case
    from tests.test_gap019_phase2 import _source

    prepared = case.__wrapped__(storage)
    repo, _root, _actor, source = _source(prepared)
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = temp / "operator.dpapi"
    initialize_operator(system, secret)
    return source, repo.context.project_id, repo.context.book_id, read_secret(secret)


def quality_request(suffix: str, text: str) -> dict:
    return {
        "project_id": "PROJ-gap020-live-" + suffix,
        "book_id": "BOOK-gap020-live-" + suffix,
        "run_id": "run-gap020-live-" + suffix,
        "step_id": "step-gap020-live-" + suffix,
        "modes": ["QUALITY"],
        "payload": {"text": text},
    }


def expect_http(port: int, method: str, path: str, expected_status: int,
                body: dict | None = None, token: str | None = None) -> object:
    status, payload = http(port, method, path, body, token)
    require(status == expected_status,
            f"{method} {path}: expected {expected_status}, got {status}: {payload}")
    return payload


def check_live_flow(port: int, storage: Path, source: dict, project: str,
                    book: str, token: str) -> dict:
    check_routes(port)
    base = f"/operator/projects/{project}/books/{book}"
    require(expect_http(port, "GET", "/operator/identity", 200, token=token)["operator_id"],
            "operator identity missing")
    expect_http(port, "GET", base + "/source-masters/current", 401)
    current = expect_http(port, "GET", base + "/source-masters/current", 200, token=token)
    require(current["source_master_id"] == source["source_master_id"], "Source head mismatch")
    expect_http(port, "GET", base.replace(book, "BOOK-FOREIGN") +
                "/source-masters/current", 403, token=token)
    expect_http(port, "GET", f"/operator/projects/PROJ-FOREIGN/books/{book}/source-masters/current",
                403, token=token)

    text_a = ("Neutral synthetic sentence. " * 14).strip()
    text_b = "\n\n".join(
        "A neutral test character crossed the quiet archive and checked every numbered "
        "shelf against the sealed inventory while rain marked time on the high windows."
        for _ in range(10)
    )
    request_a = quality_request("a", text_a)
    request_b = quality_request("b", text_b)
    first = expect_http(port, "POST", "/agent/step", 200, request_a)
    second = expect_http(port, "POST", "/agent/step", 200, request_b)
    a_record = first["evaluation_record"]
    b_record = second["evaluation_record"]
    require(a_record["decision"] == "REVISE" and b_record["decision"] == "ACCEPT",
            "quality decisions incorrect")
    require(a_record["project_id"] == request_a["project_id"], "project A binding incorrect")
    require(b_record["project_id"] == request_b["project_id"], "project B binding incorrect")
    require(a_record["evaluation_id"] != b_record["evaluation_id"], "evaluation leaked")
    require(a_record["context_package_id"] != b_record["context_package_id"], "context leaked")
    require(a_record["context_hash"] and b_record["context_hash"], "context hash missing")
    require(a_record["execution_status"] == "COMPLETED", "execution status missing")
    require(a_record["validation_status"] == "VALID", "validation status missing")

    wrong = dict(request_a)
    wrong["project_id"] = request_b["project_id"]
    wrong["book_id"] = request_b["book_id"]
    wrong["resume"] = True
    expect_http(port, "POST", "/agent/step", 422, wrong)
    generated = expect_http(port, "POST", "/agent/step", 200,
                            {"modes": ["QUALITY"], "payload": {"text": text_a}})
    require(generated["project_id"].startswith("PROJ-runtime-") and
            generated["project_id"] not in {request_a["project_id"], request_b["project_id"]}
            and generated["domain_book_id"] == "BOOK-book_runtime_test",
            "missing context did not receive isolated explicit runtime scope")
    expect_http(port, "POST", "/agent/step", 422,
                {**request_a, "project_id": "INVALID-PROJECT-ID", "run_id": "run-gap020-invalid"})
    expect_http(port, "POST", "/agent/step", 409,
                {**request_a, "technical_retry": True,
                 "payload": {"text": text_a + " changed"}})
    expect_http(port, "POST", "/agent/step", 422,
                {**request_a, "technical_retry": True, "evaluation_intent": "REEVALUATE",
                 "reevaluation_of": a_record["evaluation_id"]})

    from app.p20_core.translation_contract import TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION
    locale_records = {}
    for locale in ("en-US", "en-GB"):
        bible = expect_http(port, "POST", base + f"/translation-bibles/{locale}/versions", 200,
                            {"request_id": "GAP020-BIBLE-" + locale,
                             "source_master_id": source["source_master_id"],
                             "source_master_hash": source["artifact_hash"],
                             "expected_head": {"record_id": None, "version": None, "head_hash": None},
                             "decision_entries": []}, token)
        started = expect_http(port, "POST", base + f"/translations/{locale}/runs", 200,
                              {"request_id": "GAP020-START-" + locale,
                               "source_master_id": source["source_master_id"],
                               "source_master_hash": source["artifact_hash"],
                               "bible_id": bible["translation_bible_id"],
                               "bible_hash": bible["record_hash"],
                               "policy_version": TRANSLATION_POLICY_VERSION,
                               "policy_hash": TRANSLATION_POLICY_HASH,
                               "expected_head": {"record_id": None, "version": None, "head_hash": None}},
                              token)
        current_bible = expect_http(port, "GET",
                                    base + f"/translation-bibles/{locale}/current", 200, token=token)
        require(current_bible["record_hash"] == bible["record_hash"], "locale Bible mismatch")
        expect_http(port, "POST", "/agent/step", 403,
                    {"mode": "TRANSLATE", "project_id": project, "book_id": book,
                     "run_id": "run-gap020-generic-" + locale,
                     "payload": {"gap019_run_id": started["run_id"], "target_locale": locale}})
        locale_records[locale] = {"bible": bible, "run": started}
    require(locale_records["en-US"]["run"]["run_id"] !=
            locale_records["en-GB"]["run"]["run_id"], "locale lineage shared")

    # The preset uses the existing bounded QUALITY -> EDIT -> QUALITY loop.
    preset = expect_http(port, "POST", "/agent/step", 200,
                         {"project_id": "PROJ-gap020-preset", "book_id": "BOOK-gap020-preset",
                          "run_id": "run-gap020-preset", "step_id": "step-gap020-preset",
                          "preset": "ORCH_RETRY_TEST",
                          "payload": {"text": "To jest lista:\n- a\n- b\n- c\n"}})
    paths = preset["artifact_paths"]
    require(len(paths) >= 3 and "QUALITY" in paths[0] and
            "EDIT" in paths[1] and "QUALITY" in paths[2],
            "bounded quality revision loop absent")

    return {
        "source": current, "request_a": request_a, "request_b": request_b,
        "record_a": a_record, "record_b": b_record, "locale": locale_records,
        "preset_artifacts": len(paths),
    }


def check_reopen(port: int, storage: Path, state: dict,
                 project: str, book: str, token: str) -> None:
    from app.p20_core.project_repository import ProjectRepository, StorageResolver
    base = f"/operator/projects/{project}/books/{book}"
    current = expect_http(port, "GET", base + "/source-masters/current", 200, token=token)
    require(current == state["source"], "Source changed on restart")
    for locale, original in state["locale"].items():
        bible = expect_http(port, "GET", base + f"/translation-bibles/{locale}/current", 200,
                            token=token)
        run = expect_http(port, "GET", base + f"/translations/{locale}/runs/" +
                          original["run"]["run_id"], 200, token=token)
        require(all(bible.get(key) == value for key, value in original["bible"].items()),
                f"{locale} Bible changed on restart")
        require(all(run.get(key) == value for key, value in original["run"].items()),
                f"{locale} run changed on restart")
    retry = expect_http(port, "POST", "/agent/step", 200,
                        {**state["request_a"], "technical_retry": True})
    require(retry["evaluation_record"] == state["record_a"], "retry created new evaluation")
    reevaluate = expect_http(port, "POST", "/agent/step", 200,
                             {**state["request_a"], "evaluation_intent": "REEVALUATE",
                              "reevaluation_of": state["record_a"]["evaluation_id"]})
    new_record = reevaluate["evaluation_record"]
    require(new_record["evaluation_id"] != state["record_a"]["evaluation_id"] and
            new_record["reevaluation_of"] == state["record_a"]["evaluation_id"],
            "reevaluation did not create distinct record")
    for suffix, expected in (("a", state["record_a"]), ("b", state["record_b"])):
        request = state["request_a"] if suffix == "a" else state["request_b"]
        repo = ProjectRepository(StorageResolver(storage).resolve_project(
            request["project_id"], book_id=request["book_id"]))
        records = repo.list_evaluation_envelopes()
        require(any(envelope.get("record") == expected for envelope in records),
                f"project {suffix} lost evaluation")
        other = state["record_b"] if suffix == "a" else state["record_a"]
        require(all(envelope.get("record", {}).get("evaluation_id") != other["evaluation_id"]
                    for envelope in records), f"project {suffix} sees foreign evaluation")
        audit = json.loads((storage / "runs" / request["run_id"] / "audit.json").read_text(
            encoding="utf-8"))
        require(audit["evaluation_record"]["evaluation_id"] in
                {item["record"]["evaluation_id"] for item in records if item.get("record")},
                "audit not bound to persisted evaluation")


def check_import_boundary(log_file: Path, root: Path = ROOT) -> None:
    text = log_file.read_text(encoding="utf-8", errors="replace")
    for module in ("app.main", "app.operator_api", "app.p20_core.runtime", "app.p20_core.executor"):
        require(f"import '{module}'" in text, f"server import trace missing {module}")
    require(str(root / "app" / "main.py").lower() in text.lower(),
            "server did not import app.main from requested worktree")
    for forbidden in ("app.agent_api", "app.orchestrator_stub",
                      "scripts.p20_2_tune_auto_retry_policy",
                      "scripts.p20_3_build_retry_feedback"):
        require(forbidden not in text, f"server imported legacy/offline module: {forbidden}")
    require(str(OLD_ROOT).lower() not in text.lower(), "server trace contains old checkout")


def main() -> int:
    require(os.name == "nt", "Windows process gate only")
    with tempfile.TemporaryDirectory(prefix="gap020-phase4-") as directory:
        temp = Path(directory).resolve()
        storage = temp / "storage"
        storage.mkdir()
        foreign = temp / "foreign"
        foreign.mkdir()
        source, project, book, token = setup_neutral_source(storage, temp)
        env = os.environ.copy()
        env.update({
            "AGENTPRO_STORAGE_ROOT": str(storage),
            "AGENT_TEST_MODE": "1",
            "PYTEST_FASTPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONVERBOSE": "1",
            "PYTHONPATH": "",
        })
        env.pop("OPENAI_API_KEY", None)
        summary = {}
        with Server("run_server.ps1", foreign, env, temp) as first:
            check_import_boundary(first.log_file)
            state = check_live_flow(first.port, storage, source, project, book, token)
            summary["foreign_cwd"] = True
            summary["quality"] = (state["record_a"]["decision"], state["record_b"]["decision"])
            summary["source_master_id"] = source["source_master_id"]
            summary["locale_runs"] = {loc: item["run"]["run_id"]
                                      for loc, item in state["locale"].items()}
        with Server("run_server.ps1", ROOT, env, temp) as restarted:
            check_routes(restarted.port)
            check_reopen(restarted.port, storage, state, project, book, token)
            summary["restart_reopen"] = True
            check_import_boundary(restarted.log_file)
        for name in ("run_server_agent.ps1", "RUN.bat",
                     "scripts/start_server_full_real.ps1"):
            with Server(name, foreign, env, temp) as server:
                check_routes(server.port)
                expect_http(server.port, "GET", f"/operator/projects/{project}/books/{book}"
                            "/source-masters/current", 200, token=token)
                summary[name] = "PASS"
                check_import_boundary(server.log_file)
        with Server("scripts/start_server_strict_fastpath.ps1", foreign, env, temp) as diagnostic:
            check_routes(diagnostic.port)
            summary["diagnostic"] = "PASS"
            check_import_boundary(diagnostic.log_file)
        print(json.dumps({"status": "PASS", "checks": summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
