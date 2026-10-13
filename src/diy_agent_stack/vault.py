from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.parse import urlsplit

from .common import file_lock, load_private_json, private_directory, private_file, write_json, write_private

ITEM = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REF = re.compile(r"vault:([0-9a-f]{48})")


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Use an exact HTTPS origin with no path, query or credentials")
    _ = parsed.port
    return "https://" + parsed.netloc.lower()


class Vault:
    def __init__(self, config, run=subprocess.run, clock=time.time):
        self.config, self.run, self.clock = config, run, clock
        self.root = config.vault.get("runtime")

    def enabled(self):
        if not self.config.vault["enabled"] or self.root is None:
            raise ValueError("Vault support is disabled in the owner configuration")
        # The runtime must be the owner's login-session directory, not a persistent home directory.
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime or Path(runtime) not in self.root.parents or str(self.root) == runtime:
            raise ValueError("Vault runtime must be inside this owner's XDG_RUNTIME_DIR")
        private_directory(self.root)

    def env(self):
        self.enabled()
        self.prune()
        session = load_private_json(self.root / "session.json")
        if session["expires_at"] <= self.clock() or not isinstance(session["session"], str):
            raise ValueError("Owner unlock expired; unlock privately on this host")
        env = {k: v for k, v in os.environ.items() if k not in ("BW_SESSION", "BW_PASSWORD")}
        env["BW_SESSION"] = session["session"]
        return env

    def prune(self):
        """Expiry prevents use immediately; stale runtime files are removed on the next vault access."""
        with file_lock(self.root / "staging.lock"):
            for path in self.root.iterdir():
                if not re.fullmatch(r"[0-9a-f]{48}\.json", path.name):
                    continue
                metadata = load_private_json(path)
                if metadata.get("used") or metadata.get("expires_at", 0) <= self.clock():
                    secret = path.with_suffix(".secret")
                    if secret.exists() or secret.is_symlink():
                        private_file(secret)
                        secret.unlink()
                    path.unlink()

    def call(self, arguments, *, env=None):
        result = self.run([self.config.vault["bw_command"], *arguments], env=env or self.env(),
                          capture_output=True, timeout=30)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError("Official Bitwarden CLI request failed; inspect it privately on the host")
        return json.loads(result.stdout)

    def owner_unlock(self, ttl_seconds=900):
        self.enabled()
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 1800:
            raise ValueError("Unlock TTL must be 60..1800 seconds")
        env = {k: v for k, v in os.environ.items() if k not in ("BW_SESSION", "BW_PASSWORD")}
        # The official CLI reads the owner's terminal. Its raw session stays in this process and a private runtime file.
        result = self.run([self.config.vault["bw_command"], "unlock", "--raw"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180)
        raw = result.stdout.decode().strip()
        if result.returncode or not re.fullmatch(r"[A-Za-z0-9+/=_-]{16,4096}", raw):
            raise ValueError("Private unlock failed; use the official CLI locally")
        write_json(self.root / "session.json", dict(session=raw, expires_at=self.clock() + ttl_seconds))
        return dict(unlocked=True, expires_in=ttl_seconds, secret_returned=False)

    def status(self):
        if not self.config.vault["enabled"]:
            return dict(enabled=False, unlocked=False, automated_passkey_assertion=False)
        try:
            self.env()
            unlocked = True
        except (OSError, ValueError, KeyError):
            unlocked = False
        return dict(enabled=True, unlocked=unlocked, automated_passkey_assertion=False,
                    passkey_assertion="Use the official browser extension or owner device")

    def find(self, query: str):
        if not isinstance(query, str) or len(query) > 120 or "\0" in query:
            raise ValueError("Use a short vault search")
        items = self.call(["list", "items", "--search", query])
        if not isinstance(items, list):
            raise ValueError("Unexpected official CLI schema")
        out = []
        for item in items[:30]:
            if not ITEM.fullmatch(item.get("id", "")):
                continue
            login = item.get("login") or {}
            origins = []
            for uri in login.get("uris") or []:
                try:
                    parsed = urlsplit(uri.get("uri") or "")
                    origins.append(https_origin("https://" + parsed.netloc) if parsed.scheme == "https" else None)
                except ValueError:
                    pass
            out.append(dict(item_id=item["id"], name=str(item.get("name", ""))[:100],
                            origins=sorted({o for o in origins if o}),
                            passkey_metadata_count=len(login.get("fido2Credentials") or []),
                            usernames_returned=False, passwords_returned=False))
        return dict(items=out, passkey_metadata_is_not_assertion=True)

    def prepare(self, item_id: str, field: str, origin: str):
        if not ITEM.fullmatch(item_id) or field not in ("username", "password"):
            raise ValueError("Use an item UUID and username or password field")
        origin = https_origin(origin)
        item = self.call(["get", "item", item_id])
        login = item.get("login") or {}
        permitted = []
        for uri in login.get("uris") or []:
            parsed = urlsplit(uri.get("uri") or "")
            if parsed.scheme == "https" and parsed.netloc:
                permitted.append(https_origin("https://" + parsed.netloc))
        if origin not in permitted:
            raise ValueError("Exact origin is absent from this vault item's HTTPS URI list")
        value = login.get(field)
        if not isinstance(value, str) or not value or len(value.encode()) > 8000 or any(c in value for c in "\0\r\n"):
            raise ValueError("The selected login field cannot be entered as a single field")
        reference = secrets.token_hex(24)
        with file_lock(self.root / "staging.lock"):
            write_private(self.root / (reference + ".secret"), value.encode())
            write_json(self.root / (reference + ".json"), dict(field=field, origin=origin,
                       expires_at=self.clock() + 90, used=False))
        return dict(credential_ref="vault:" + reference, field=field, intended_origin=origin,
                    expires_in=90, single_use=True, secret_returned=False,
                    automatic_input_requires_origin_adapter=True)

    @contextmanager
    def consume(self, reference: str, origin: str, role: str):
        self.env()
        match = REF.fullmatch(reference) if isinstance(reference, str) else None
        if not match:
            raise ValueError("Invalid protected reference")
        prefix = match[1]
        with file_lock(self.root / "staging.lock"):
            try:
                metadata = load_private_json(self.root / (prefix + ".json"))
            except OSError:
                raise ValueError("Protected reference is unavailable or expired") from None
            if metadata["used"] or metadata["expires_at"] <= self.clock() or metadata["origin"] != https_origin(origin):
                raise ValueError("Protected reference is used, expired or bound to a different origin")
            if role != ("password" if metadata["field"] == "password" else "textbox"):
                raise ValueError("Trusted origin adapter did not attest the correct focused field")
            path = self.root / (prefix + ".secret")
            private_file(path)
            secret = path.read_bytes()
            metadata["used"] = True
            write_json(self.root / (prefix + ".json"), metadata)
            path.unlink()
        try:
            yield secret
        finally:
            secret = b""

    def owner_lock(self):
        self.enabled()
        with file_lock(self.root / "staging.lock"):
            for path in self.root.iterdir():
                if path.name == "session.json" or re.fullmatch(r"[0-9a-f]{48}\.(?:json|secret)", path.name):
                    private_file(path)
                    path.unlink()
        self.run([self.config.vault["bw_command"], "lock"], stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, timeout=20)
        return dict(locked_locally=True, staged_fields_removed=True)
