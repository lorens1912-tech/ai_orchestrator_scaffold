from __future__ import annotations

import builtins
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_STORAGE_ROOT = REPO_ROOT / ".test_storage"
TEST_SESSION_STORAGE_ROOT = TEST_STORAGE_ROOT / "sessions" / uuid.uuid4().hex
REAL_STORAGE_ROOTS = tuple(
    (REPO_ROOT / name).resolve() for name in ("books", "runs", "novel_runs", "audit")
)

os.environ["AGENTPRO_STORAGE_ROOT"] = str(TEST_SESSION_STORAGE_ROOT)


@pytest.fixture(autouse=True)
def controlled_memory_transport_sdk_boundary(monkeypatch):
    """Keep test runs at the external SDK boundary when P20 promotes a WRITE."""
    from openai.resources.responses.responses import Responses

    import app.llm_provider_openai as openai_transport

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-agentpro-controlled-boundary")
    monkeypatch.setenv("OPENAI_API_MODE", "responses")
    monkeypatch.setattr(openai_transport, "_client", None)

    def create(_self, **kwargs):
        prompt = json.loads(kwargs["input"])
        if prompt.get("protocol") == "AGENTPRO_P20_WRITER_V1":
            text = (
                "Światło przesuwało się po pustym placu, gdy Marta zatrzymała rower przy starej "
                "fontannie. Kamienne obrzeże było chłodne, a woda drżała od wiatru i rozbijała "
                "odbicia okien na drobne, srebrne plamy. Czekała spokojnie, licząc uderzenia zegara "
                "na wieży, lecz po trzecim dźwięku usłyszała szybkie kroki w wąskiej uliczce.\n\n"
                "Jan wyszedł zza narożnika z mokrym płaszczem przewieszonym przez ramię. Nie przywitał "
                "się od razu. Położył na kamieniu małe drewniane pudełko i cofnął dłoń, jakby przedmiot "
                "parzył. Marta spojrzała na wyblakły znak na wieku. Znała go z listu, który matka ukryła "
                "przed laty w kuchennej szufladzie.\n\n"
                "— Skąd to masz? — zapytała.\n\n"
                "— Z warsztatu przy moście. Właściciel kazał oddać je tobie przed zachodem słońca.\n\n"
                "Marta uniosła wieko. W środku leżał mosiężny klucz i złożona kartka. Papier pachniał "
                "dymem. Jedno zdanie, zapisane równym pismem, wskazywało zamknięte od dawna drzwi w "
                "północnej części dworca. Jan obserwował jej twarz, ale nie zadawał pytań.\n\n"
                "Z wieży popłynęło czwarte uderzenie. Marta schowała klucz do kieszeni, zapięła płaszcz "
                "i poprowadziła rower w stronę mostu. Jan ruszył obok niej. Na drugim brzegu miasta "
                "zapalały się pierwsze lampy, a ciemne okna dworca wyglądały tak, jakby ktoś już na nich "
                "czekał."
            )
            return SimpleNamespace(
                output_text=text,
                model=kwargs["model"],
                output=[],
                id="response-writer-controlled",
                _request_id="request-writer-controlled",
                usage=SimpleNamespace(input_tokens=120, output_tokens=260, total_tokens=380),
            )
        package = prompt["context_package"]
        task = next(item for item in package["included_items"] if item["layer"] == "TASK")
        payload = json.loads(task["content"])["input"]
        if payload["role"] == "EXTRACTOR":
            source = payload["source"]
            record_id = "FACT-sdk-" + hashlib.sha256(
                source["scene_id"].encode("utf-8")
            ).hexdigest()[:24]
            record = {
                "fact_id": record_id,
                "project_id": payload["project_id"],
                "subject_id": record_id,
                "predicate": "description",
                "object_type": "TEXT",
                "object_id": None,
                "object_value": "controlled SDK boundary record",
                "reality_status": "TRUE",
                "verification_status": "VERIFIED",
                "confidence": 1,
                "frozen": False,
                "author_locked": False,
                "valid_from": "test-start",
                "valid_to": None,
                "established_event_id": None,
                "established_scene_id": source["scene_id"],
                "source_artifact_ref": source["artifact_ref"],
                "source_refs": [source["scene_id"]],
                "canon_version": 1,
                "version": 1,
                "created_at": "2026-09-15T00:00:00Z",
                "updated_at": "2026-09-15T00:00:00Z",
            }
            text = json.dumps({"records": [{"record_type": "FACT", "payload": record}]})
        else:
            text = json.dumps({
                "precision_status": "ACCEPT",
                "completeness_status": "ACCEPT",
            })
        return SimpleNamespace(
            output_text=text,
            model=kwargs["model"],
            output=[],
            id="response-memory-controlled",
            _request_id="request-memory-controlled",
            usage=SimpleNamespace(input_tokens=50, output_tokens=80, total_tokens=130),
        )

    monkeypatch.setattr(Responses, "create", create)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _coerce_path(path_value) -> Path | None:
    if isinstance(path_value, int):
        return None
    try:
        return Path(os.fsdecode(path_value)).resolve()
    except TypeError:
        return None


def _is_write_mode(mode: str) -> bool:
    return any(flag in str(mode) for flag in ("w", "a", "x", "+"))


def _assert_not_real_storage(path_value, operation: str) -> None:
    path = _coerce_path(path_value)
    if path is None:
        return
    for root in REAL_STORAGE_ROOTS:
        if path == root or _is_relative_to(path, root):
            raise AssertionError(f"Blocked {operation} on real AgentPRO storage path: {path}")


_ORIGINAL_OPEN = builtins.open
_ORIGINAL_PATH_OPEN = Path.open
_ORIGINAL_PATH_MKDIR = Path.mkdir
_ORIGINAL_PATH_UNLINK = Path.unlink
_ORIGINAL_PATH_RMDIR = Path.rmdir
_ORIGINAL_PATH_TOUCH = Path.touch
_ORIGINAL_OS_MAKEDIRS = os.makedirs
_ORIGINAL_OS_MKDIR = os.mkdir
_ORIGINAL_OS_REMOVE = os.remove
_ORIGINAL_OS_UNLINK = os.unlink
_ORIGINAL_OS_RMDIR = os.rmdir
_ORIGINAL_OS_RENAME = os.rename
_ORIGINAL_OS_REPLACE = os.replace
_ORIGINAL_SHUTIL_RMTREE = shutil.rmtree
_ORIGINAL_SHUTIL_MOVE = shutil.move
_ORIGINAL_SHUTIL_COPY = shutil.copy
_ORIGINAL_SHUTIL_COPY2 = shutil.copy2
_ORIGINAL_SHUTIL_COPYFILE = shutil.copyfile
_ORIGINAL_SHUTIL_COPYTREE = shutil.copytree


def _guarded_open(file, mode="r", *args, **kwargs):
    if _is_write_mode(mode):
        _assert_not_real_storage(file, f"open({mode!r})")
    return _ORIGINAL_OPEN(file, mode, *args, **kwargs)


def _guarded_path_open(self, mode="r", *args, **kwargs):
    if _is_write_mode(mode):
        _assert_not_real_storage(self, f"Path.open({mode!r})")
    return _ORIGINAL_PATH_OPEN(self, mode, *args, **kwargs)


def _guarded_path_mkdir(self, *args, **kwargs):
    _assert_not_real_storage(self, "Path.mkdir")
    return _ORIGINAL_PATH_MKDIR(self, *args, **kwargs)


def _guarded_path_unlink(self, *args, **kwargs):
    _assert_not_real_storage(self, "Path.unlink")
    return _ORIGINAL_PATH_UNLINK(self, *args, **kwargs)


def _guarded_path_rmdir(self, *args, **kwargs):
    _assert_not_real_storage(self, "Path.rmdir")
    return _ORIGINAL_PATH_RMDIR(self, *args, **kwargs)


def _guarded_path_touch(self, *args, **kwargs):
    _assert_not_real_storage(self, "Path.touch")
    return _ORIGINAL_PATH_TOUCH(self, *args, **kwargs)


def _guarded_os_makedirs(name, *args, **kwargs):
    _assert_not_real_storage(name, "os.makedirs")
    return _ORIGINAL_OS_MAKEDIRS(name, *args, **kwargs)


def _guarded_os_mkdir(path, *args, **kwargs):
    _assert_not_real_storage(path, "os.mkdir")
    return _ORIGINAL_OS_MKDIR(path, *args, **kwargs)


def _guarded_os_remove(path, *args, **kwargs):
    _assert_not_real_storage(path, "os.remove")
    return _ORIGINAL_OS_REMOVE(path, *args, **kwargs)


def _guarded_os_unlink(path, *args, **kwargs):
    _assert_not_real_storage(path, "os.unlink")
    return _ORIGINAL_OS_UNLINK(path, *args, **kwargs)


def _guarded_os_rmdir(path, *args, **kwargs):
    _assert_not_real_storage(path, "os.rmdir")
    return _ORIGINAL_OS_RMDIR(path, *args, **kwargs)


def _guarded_os_rename(src, dst, *args, **kwargs):
    _assert_not_real_storage(src, "os.rename source")
    _assert_not_real_storage(dst, "os.rename destination")
    return _ORIGINAL_OS_RENAME(src, dst, *args, **kwargs)


def _guarded_os_replace(src, dst, *args, **kwargs):
    _assert_not_real_storage(src, "os.replace source")
    _assert_not_real_storage(dst, "os.replace destination")
    return _ORIGINAL_OS_REPLACE(src, dst, *args, **kwargs)


def _guarded_shutil_rmtree(path, *args, **kwargs):
    _assert_not_real_storage(path, "shutil.rmtree")
    return _ORIGINAL_SHUTIL_RMTREE(path, *args, **kwargs)


def _guarded_shutil_move(src, dst, *args, **kwargs):
    _assert_not_real_storage(src, "shutil.move source")
    _assert_not_real_storage(dst, "shutil.move destination")
    return _ORIGINAL_SHUTIL_MOVE(src, dst, *args, **kwargs)


def _guarded_shutil_copy(src, dst, *args, **kwargs):
    _assert_not_real_storage(dst, "shutil.copy destination")
    return _ORIGINAL_SHUTIL_COPY(src, dst, *args, **kwargs)


def _guarded_shutil_copy2(src, dst, *args, **kwargs):
    _assert_not_real_storage(dst, "shutil.copy2 destination")
    return _ORIGINAL_SHUTIL_COPY2(src, dst, *args, **kwargs)


def _guarded_shutil_copyfile(src, dst, *args, **kwargs):
    _assert_not_real_storage(dst, "shutil.copyfile destination")
    return _ORIGINAL_SHUTIL_COPYFILE(src, dst, *args, **kwargs)


def _guarded_shutil_copytree(src, dst, *args, **kwargs):
    _assert_not_real_storage(dst, "shutil.copytree destination")
    return _ORIGINAL_SHUTIL_COPYTREE(src, dst, *args, **kwargs)


builtins.open = _guarded_open
Path.open = _guarded_path_open
Path.mkdir = _guarded_path_mkdir
Path.unlink = _guarded_path_unlink
Path.rmdir = _guarded_path_rmdir
Path.touch = _guarded_path_touch
os.makedirs = _guarded_os_makedirs
os.mkdir = _guarded_os_mkdir
os.remove = _guarded_os_remove
os.unlink = _guarded_os_unlink
os.rmdir = _guarded_os_rmdir
os.rename = _guarded_os_rename
os.replace = _guarded_os_replace
shutil.rmtree = _guarded_shutil_rmtree
shutil.move = _guarded_shutil_move
shutil.copy = _guarded_shutil_copy
shutil.copy2 = _guarded_shutil_copy2
shutil.copyfile = _guarded_shutil_copyfile
shutil.copytree = _guarded_shutil_copytree


def _storage_slug(nodeid: str) -> str:
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")
    return f"{slug[:80]}_{digest}"


@pytest.fixture
def isolated_agentpro_storage(request, monkeypatch) -> Path:
    storage_root = TEST_SESSION_STORAGE_ROOT / "isolated" / _storage_slug(request.node.nodeid)
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))
    return storage_root
