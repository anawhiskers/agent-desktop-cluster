"""Archive one explicitly identified closed SQLite backup. No discovery or replay."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote

CHUNK = 1024 * 1024
FILE_FIELDS = {"dev", "ino", "size", "uid", "gid", "mode", "nlink", "mtime_ns", "ctime_ns"}
DIR_FIELDS = {"dev", "ino", "uid", "gid", "mode", "mtime_ns", "ctime_ns"}
MAPPING_KEYS = {"original_path", "original_sha256", "original_bytes", "original_identity", "archive_path", "archive_sha256", "archive_bytes", "archive_identity", "compression", "sqlite_integrity", "restoration"}
KEYS = {"version", "source", "source_identity", "source_parent_identity", "sha256", "archive_dir", "archive_parent_identity", "uid", "gid", "max_input_bytes", "max_archive_bytes", "max_seconds", "reserve_bytes"}

class Refused(Exception):
    pass

def identity(s, directory=False):
    out = {"dev": s.st_dev, "ino": s.st_ino, "uid": s.st_uid, "gid": s.st_gid,
           "mode": stat.S_IMODE(s.st_mode), "mtime_ns": s.st_mtime_ns, "ctime_ns": s.st_ctime_ns}
    if not directory:
        out.update(size=s.st_size, nlink=s.st_nlink)
    return out

def _path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise Refused("absolute path required")
    if str(Path(value)) != value or any(c in (".", "..", "") for c in value.split("/")[1:]):
        raise Refused("path aliases refused")
    return value

def _int(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise Refused("invalid integer or budget")

def _validate(plan):
    if type(plan) is not dict or set(plan) != KEYS or plan["version"] != 1 or type(plan["version"]) is not int:
        raise Refused("invalid plan schema")
    _path(plan["source"]); _path(plan["archive_dir"])
    if plan["source"] == plan["archive_dir"] or Path(plan["archive_dir"]).parent == Path(plan["source"]).parent:
        raise Refused("archive operation must have a separate parent")
    for key, fields in (("source_identity", FILE_FIELDS), ("source_parent_identity", DIR_FIELDS), ("archive_parent_identity", DIR_FIELDS)):
        item = plan[key]
        if type(item) is not dict or set(item) != fields:
            raise Refused("invalid identity schema")
        for val in item.values():
            _int(val, 0, 2**64-1)
    for key in ("uid", "gid"):
        _int(plan[key], 0, 2**32-1)
    if plan["uid"] != os.geteuid() or plan["gid"] != os.getegid():
        raise Refused("plan must name current effective owner")
    if not isinstance(plan["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", plan["sha256"]) is None:
        raise Refused("invalid SHA256")
    _int(plan["max_input_bytes"], 100, 2**40)
    _int(plan["max_archive_bytes"], 100, plan["max_input_bytes"] + 65536)
    _int(plan["max_seconds"], 1, 3600)
    _int(plan["reserve_bytes"], 0, 2**40)
    expected = plan["source_identity"]
    if expected["uid"] != plan["uid"] or expected["gid"] != plan["gid"] or expected["mode"] != 0o600 or expected["nlink"] != 1:
        raise Refused("source must be a private single-link owner file")
    if not 100 <= expected["size"] <= plan["max_input_bytes"]:
        raise Refused("source size outside budget")
    for key in ("source_parent_identity", "archive_parent_identity"):
        parent = plan[key]
        if parent["uid"] != plan["uid"] or parent["gid"] != plan["gid"] or parent["mode"] != 0o700:
            raise Refused("immediate parents must be private owner directories")

def _tick(deadline):
    if time.monotonic() >= deadline:
        raise Refused("time budget exceeded")

def _chain(path):
    """Open every directory component without following links, retaining identities."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    records = [("/", identity(os.fstat(fd), True))]
    current = ""
    try:
        for component in path.split("/")[1:]:
            nxt = os.open(component, flags, dir_fd=fd)
            os.close(fd); fd = nxt
            current += "/" + component
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) & 0o022:
                raise Refused("writable ancestor refused")
            records.append((current, identity(info, True)))
        return fd, records
    except BaseException:
        os.close(fd)
        raise

def _stable_chain(records):
    fd, now = _chain(records[-1][0])
    os.close(fd)
    # Directory timestamps change when this operation creates its own directory.
    for (_, old), (_, new) in zip(records, now):
        for key in ("dev", "ino", "uid", "gid", "mode"):
            if old[key] != new[key]:
                raise Refused("canonical parent identity changed")

def _file_at(fd, name, expected=None, uid=None, gid=None):
    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
        raise Refused("file type, links or mode changed")
    if uid is not None and (info.st_uid != uid or info.st_gid != gid):
        raise Refused("file owner changed")
    actual = identity(info)
    if expected is not None and actual != expected:
        raise Refused("file identity changed")
    return actual

def _siblings(fd, name):
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            os.stat(name + suffix, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise Refused("SQLite sidecar present")

def _hash_fd(fd, deadline, max_bytes):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256(); total = 0
    while True:
        _tick(deadline)
        block = os.read(fd, CHUNK)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise Refused("byte budget exceeded")
        digest.update(block)
    return total, digest.hexdigest()

def _new(fd, name):
    out = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    os.fchmod(out, 0o600)
    return out

def _write_all(fd, block):
    view = memoryview(block)
    while view:
        wrote = os.write(fd, view)
        if wrote <= 0:
            raise OSError("short write")
        view = view[wrote:]

def _journal(fd, name, data):
    out = _new(fd, name)
    try:
        _write_all(out, (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode())
        os.fsync(out)
    finally:
        os.close(out)
    os.fsync(fd)
    return _file_at(fd, name)

def _sqlite(path, deadline):
    _tick(deadline)
    with open(path, "rb") as src:
        if src.read(16) != b"SQLite format 3\x00":
            raise Refused("SQLite header missing")
    con = sqlite3.connect("file:" + quote(path, safe="/") + "?mode=ro&immutable=1", uri=True, timeout=0)
    try:
        con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        cursor = con.execute("PRAGMA integrity_check")
        first = cursor.fetchone()
        if first != ("ok",) or cursor.fetchone() is not None:
            raise Refused("SQLite integrity failed")
    finally:
        con.close()
    _tick(deadline)

def _verify_gzip(archive_fd, work_fd, work_name, work_path, expected_bytes, expected_hash, deadline):
    out = _new(work_fd, work_name)
    try:
        os.lseek(archive_fd, 0, os.SEEK_SET)
        digest = hashlib.sha256(); total = 0
        with os.fdopen(os.dup(archive_fd), "rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
            while True:
                _tick(deadline)
                block = compressed.read(CHUNK)
                if not block:
                    break
                total += len(block)
                if total > expected_bytes:
                    raise Refused("decompressed size exceeded")
                digest.update(block); _write_all(out, block)
        if total != expected_bytes or digest.hexdigest() != expected_hash:
            raise Refused("decompressed bytes or SHA256 mismatch")
        os.fsync(out)
        before = _file_at(work_fd, work_name)
        _sqlite(work_path, deadline)
        _file_at(work_fd, work_name, before)
        return before
    finally:
        os.close(out)

def archive_backup(plan, *, _checkpoint=None):
    """Return observed state; never retry, overwrite, or remove failed archives.

    The caller must keep the source quiescent. flock is advisory, and POSIX does
    not provide compare-and-unlink against a hostile concurrent owner process.
    """
    result = {"status": "refused", "original": "untouched", "archive_verified": False, "journal_durable": False, "error": None}
    descriptors = []; src_fd = op_fd = None; retired = False; attempted_unlink = False
    verify_owned = None
    def checkpoint(stage):
        if _checkpoint:
            _checkpoint(stage)
    try:
        _validate(plan)
        deadline = time.monotonic() + plan["max_seconds"]
        source = Path(plan["source"]); operation = Path(plan["archive_dir"])
        result.update(original_path=str(source), archive_path=str(operation / "backup.sqlite.gz"))
        parent_fd, source_chain = _chain(str(source.parent)); descriptors.append(parent_fd)
        archive_parent_fd, archive_chain = _chain(str(operation.parent)); descriptors.append(archive_parent_fd)
        if identity(os.fstat(parent_fd), True) != plan["source_parent_identity"] or identity(os.fstat(archive_parent_fd), True) != plan["archive_parent_identity"]:
            raise Refused("planned parent metadata changed")
        _siblings(parent_fd, source.name)
        _file_at(parent_fd, source.name, plan["source_identity"], plan["uid"], plan["gid"])
        src_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd); descriptors.append(src_fd)
        fcntl.flock(src_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if identity(os.fstat(src_fd)) != plan["source_identity"]:
            raise Refused("opened source differs")
        if _hash_fd(src_fd, deadline, plan["max_input_bytes"]) != (plan["source_identity"]["size"], plan["sha256"]):
            raise Refused("original SHA256 mismatch")
        _file_at(parent_fd, source.name, plan["source_identity"])
        free = os.fstatvfs(archive_parent_fd)
        needed = plan["source_identity"]["size"] + plan["max_archive_bytes"] + plan["reserve_bytes"] + 65536
        if free.f_bavail * free.f_frsize < needed:
            raise Refused("insufficient free-space budget")
        os.mkdir(operation.name, 0o700, dir_fd=archive_parent_fd)
        op_fd = os.open(operation.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=archive_parent_fd); descriptors.append(op_fd)
        os.fchmod(op_fd, 0o700); op_identity = identity(os.fstat(op_fd), True)
        os.fsync(archive_parent_fd)
        archive_parent_after_create = identity(os.fstat(archive_parent_fd), True)
        _journal(op_fd, "intent.json", {"version": 1, "plan": plan, "state": "started_no_replay"})
        checkpoint("after_intent")
        archive_fd = _new(op_fd, "backup.sqlite.gz"); descriptors.append(archive_fd)
        os.lseek(src_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(archive_fd), "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            total = 0
            while True:
                _tick(deadline)
                block = os.read(src_fd, CHUNK)
                if not block:
                    break
                total += len(block)
                if total > plan["source_identity"]["size"]:
                    raise Refused("source grew")
                compressed.write(block)
                if os.fstat(archive_fd).st_size > plan["max_archive_bytes"]:
                    raise Refused("archive size budget exceeded")
        os.fsync(archive_fd)
        compressed_bytes, compressed_hash = _hash_fd(archive_fd, deadline, plan["max_archive_bytes"])
        archive_identity = _file_at(op_fd, "backup.sqlite.gz", uid=plan["uid"], gid=plan["gid"])
        checkpoint("before_verify")
        verify_owned = _verify_gzip(archive_fd, op_fd, "verify.sqlite", str(operation / "verify.sqlite"), plan["source_identity"]["size"], plan["sha256"], deadline)
        _file_at(op_fd, "verify.sqlite", verify_owned)
        os.unlink("verify.sqlite", dir_fd=op_fd); verify_owned = None; os.fsync(op_fd)
        result["archive_verified"] = True
        mapping = {"original_path": str(source), "original_sha256": plan["sha256"], "original_bytes": plan["source_identity"]["size"], "original_identity": plan["source_identity"], "archive_path": str(operation / "backup.sqlite.gz"), "archive_sha256": compressed_hash, "archive_bytes": compressed_bytes, "archive_identity": archive_identity, "compression": "gzip", "sqlite_integrity": "ok", "restoration": "decompress to a new private file, verify byte count/hash/integrity before installing"}
        result["mapping"] = mapping
        checkpoint("before_journal")
        journal_identity = _journal(op_fd, "verified.json", {"version": 1, "state": "verified_original_retirement_pending", "mapping": mapping})
        os.fsync(archive_parent_fd)
        op_after_journal = identity(os.fstat(op_fd), True)
        result.update(journal_durable=True, mapping=mapping, status="archive_verified_original_retained", original="present")
        checkpoint("before_retire")
        _tick(deadline)
        _stable_chain(source_chain); _stable_chain(archive_chain)
        if identity(os.fstat(parent_fd), True) != plan["source_parent_identity"]:
            raise Refused("source parent metadata changed")
        current_op = os.stat(operation.name, dir_fd=archive_parent_fd, follow_symlinks=False)
        for key in ("dev", "ino", "uid", "gid", "mode"):
            if identity(current_op, True)[key] != op_identity[key]:
                raise Refused("operation directory changed")
        _file_at(op_fd, "verified.json", journal_identity, plan["uid"], plan["gid"])
        _file_at(op_fd, "backup.sqlite.gz", archive_identity, plan["uid"], plan["gid"])
        if _hash_fd(archive_fd, deadline, plan["max_archive_bytes"]) != (compressed_bytes, compressed_hash):
            raise Refused("verified archive changed")
        _file_at(op_fd, "backup.sqlite.gz", archive_identity)
        _siblings(parent_fd, source.name)
        _file_at(parent_fd, source.name, plan["source_identity"], plan["uid"], plan["gid"])
        if identity(os.fstat(src_fd)) != plan["source_identity"] or _hash_fd(src_fd, deadline, plan["max_input_bytes"]) != (plan["source_identity"]["size"], plan["sha256"]):
            raise Refused("source changed before retirement")
        _tick(deadline); _siblings(parent_fd, source.name)
        _file_at(parent_fd, source.name, plan["source_identity"])
        _stable_chain(source_chain); _stable_chain(archive_chain)
        if identity(os.fstat(parent_fd), True) != plan["source_parent_identity"]:
            raise Refused("source parent metadata changed before unlink")
        if identity(os.fstat(archive_parent_fd), True) != archive_parent_after_create:
            raise Refused("archive parent metadata changed before unlink")
        if identity(os.fstat(op_fd), True) != op_after_journal:
            raise Refused("operation directory metadata changed before unlink")
        attempted_unlink = True
        os.unlink(source.name, dir_fd=parent_fd)
        retired = True; result["original"] = "absent"
        checkpoint("after_unlink")
        os.fsync(parent_fd)
        checkpoint("before_result_journal")
        _journal(op_fd, "retired.json", {"version": 1, "state": "original_retired_durable", "mapping": mapping})
        result["status"] = "retired_durable"
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
        if retired or attempted_unlink:
            result["status"] = "retirement_uncertain"
            try:
                current = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
                result["original"] = "present" if identity(current) == plan["source_identity"] else "changed"
            except FileNotFoundError:
                result["original"] = "absent"
            except Exception:
                result["original"] = "unknown"
        elif result["archive_verified"]:
            result["status"] = "archive_verified_original_retained"
            result["original"] = "not_retired_by_this_operation"
        else:
            result["status"] = "failed_original_retained"
            result["original"] = "not_retired_by_this_operation"
    finally:
        # Failed operation files remain for manual reconciliation; no replay.
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass
    return result

def restore_verified(mapping, destination, *, max_archive_bytes, max_output_bytes,
                     max_seconds, reserve_bytes):
    """Restore a verified mapping to a different new file, within explicit budgets.

    Failures raise and can leave a private partial destination for inspection.
    No existing destination is overwritten or removed.
    """
    _path(destination)
    if type(mapping) is not dict or set(mapping) != MAPPING_KEYS:
        raise Refused("invalid restoration mapping schema")
    if mapping["compression"] != "gzip" or mapping["sqlite_integrity"] != "ok" or not isinstance(mapping["restoration"], str):
        raise Refused("invalid restoration mapping format")
    original = _path(mapping["original_path"])
    archive = _path(mapping["archive_path"])
    if destination in (original, archive) or original == archive:
        raise Refused("restoration requires a separate new destination")
    _int(max_seconds, 1, 3600)
    _int(max_output_bytes, 100, 2**40)
    _int(max_archive_bytes, 100, max_output_bytes + 65536)
    _int(reserve_bytes, 0, 2**40)
    _int(mapping["original_bytes"], 100, max_output_bytes)
    _int(mapping["archive_bytes"], 1, max_archive_bytes)
    for key in ("original_sha256", "archive_sha256"):
        if not isinstance(mapping[key], str) or re.fullmatch(r"[0-9a-f]{64}", mapping[key]) is None:
            raise Refused("invalid restoration hash")
    for key, size in (("original_identity", mapping["original_bytes"]), ("archive_identity", mapping["archive_bytes"])):
        item = mapping[key]
        if type(item) is not dict or set(item) != FILE_FIELDS:
            raise Refused("invalid restoration identity schema")
        for value in item.values():
            _int(value, 0, 2**64-1)
        if item["size"] != size or item["mode"] != 0o600 or item["nlink"] != 1 or item["uid"] != os.geteuid() or item["gid"] != os.getegid():
            raise Refused("invalid restoration size, owner, mode or links")
    expected_bytes = mapping["original_bytes"]
    deadline = time.monotonic() + max_seconds
    target = Path(destination)
    dest_fd, dest_chain = _chain(str(target.parent))
    archive_parent_fd = archive_fd = None
    try:
        parent = os.fstat(dest_fd)
        if parent.st_uid != os.geteuid() or parent.st_gid != os.getegid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise Refused("restoration parent must be private and owned")
        free = os.fstatvfs(dest_fd)
        if free.f_bavail * free.f_frsize < expected_bytes + reserve_bytes + 65536:
            raise Refused("insufficient restoration free-space budget")
        archive_parent_fd, archive_chain = _chain(str(Path(archive).parent))
        _file_at(archive_parent_fd, Path(archive).name, mapping["archive_identity"], os.geteuid(), os.getegid())
        archive_fd = os.open(Path(archive).name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archive_parent_fd)
        if identity(os.fstat(archive_fd)) != mapping["archive_identity"]:
            raise Refused("opened archive differs")
        if _hash_fd(archive_fd, deadline, max_archive_bytes) != (mapping["archive_bytes"], mapping["archive_sha256"]):
            raise Refused("archive SHA256 mismatch")
        _siblings(dest_fd, target.name)
        restored = _verify_gzip(archive_fd, dest_fd, target.name, destination, expected_bytes, mapping["original_sha256"], deadline)
        _stable_chain(dest_chain); _stable_chain(archive_chain)
        _file_at(archive_parent_fd, Path(archive).name, mapping["archive_identity"])
        if _hash_fd(archive_fd, deadline, max_archive_bytes) != (mapping["archive_bytes"], mapping["archive_sha256"]):
            raise Refused("archive changed during restoration")
        _stable_chain(dest_chain); _stable_chain(archive_chain)
        _file_at(archive_parent_fd, Path(archive).name, mapping["archive_identity"])
        _file_at(dest_fd, target.name, restored)
        _siblings(dest_fd, target.name)
        os.fsync(dest_fd)
        return {"status": "restored_verified", "path": destination, "bytes": expected_bytes, "sha256": mapping["original_sha256"], "sqlite_integrity": "ok"}
    finally:
        for fd in (archive_fd, archive_parent_fd, dest_fd):
            if fd is not None:
                os.close(fd)
