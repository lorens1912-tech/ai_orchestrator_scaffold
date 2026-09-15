"""Windows current-user DPAPI client store; not imported by the P20 tool registry.

DPAPI does not isolate arbitrary processes running as the same Windows user.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess


def default_secret_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "AgentPRO" / "Security" / "operator.dpapi"


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is required")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def _restrict_directory(path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows ACL is required")
    from app.p20_core.storage_paths import get_storage_root
    resolved = path.resolve()
    for root in (Path(__file__).resolve().parents[1], get_storage_root().resolve()):
        if resolved == root or root in resolved.parents:
            raise RuntimeError("Credential must be outside project and book storage")
    for parent in (path, *path.parents):
        if parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Credential directory must not traverse a reparse point")
    path.mkdir(parents=True, exist_ok=True)
    # Set a protected DACL containing only this user's full-control entry.
    script = (
        "$ErrorActionPreference='Stop'; "
        "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "$acl=[Security.AccessControl.DirectorySecurity]::new(); "
        "$acl.SetAccessRuleProtection($true,$false); $acl.SetOwner($sid); "
        "$rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',"
        "'ContainerInherit,ObjectInherit','None','Allow'); $acl.AddAccessRule($rule); "
        "[IO.Directory]::SetAccessControl($env:AGENTPRO_SECURITY_DIRECTORY,$acl)"
    )
    environment = dict(os.environ, AGENTPRO_SECURITY_DIRECTORY=str(path.resolve()))
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=environment, capture_output=True, check=True)


def write_secret(path: Path, token: str, *, replace: bool = False) -> None:
    _restrict_directory(path.parent)
    if path.is_symlink():
        raise RuntimeError("Credential file must not be a link")
    encrypted = _transform(token.encode("ascii"), decrypt=False)
    # New file inherits the already restricted directory ACL.
    with path.open("wb" if replace else "xb") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())


def read_secret(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError("Credential file must not be a link")
    return _transform(path.read_bytes(), decrypt=True).decode("ascii")
