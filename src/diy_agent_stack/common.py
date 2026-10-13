from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"tskey-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,})|"
    r"(?:password|passwd|api[_ -]?key|access[_ -]?token|refresh[_ -]?token)"
    r"\s*[=:]\s*[\"']?[^\s\"']{3,}", re.I,
)


def identifier(value: str, maximum: int = 64) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", value) or len(value) > maximum:
        raise ValueError("Use a lowercase public identifier with letters, digits, dot, dash or underscore")
    return value


def host_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,31}", value):
        raise ValueError("Use a configured host ID such as HOST01")
    return value


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def expanded(value: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Path is required")
    value = os.path.expandvars(value)
    if "$" in value:
        raise ValueError("An environment variable in a configured path is unset")
    path = Path(value).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Configured paths must not traverse symlinks")
    return path


def private_file(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise ValueError("A private, owned, unlinked regular file is required")


def private_directory(path: Path) -> Path:
    expanded(str(path))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("State directory must be owned by this user and mode 700")
    return path


def load_private_json(path: Path):
    private_file(path)
    if path.stat().st_size > 65536:
        raise ValueError("Private metadata exceeds its budget")
    return json.loads(path.read_text())


def write_private(path: Path, value: bytes) -> None:
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        private_file(path)
    temporary = path.parent / (".tmp-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    write_private(path, encoded(value))


@contextmanager
def file_lock(path: Path, *, nonblocking: bool = False):
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        private_file(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(fd)


def public_text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value.encode()) > maximum or "\0" in value or SECRET.search(value):
        raise ValueError("Use bounded text without literal credentials")
    return value
