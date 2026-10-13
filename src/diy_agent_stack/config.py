from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .common import expanded, host_id, load_private_json, public_text


def exact(value: dict, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError("Missing or unknown configuration keys")
    return value


def argv(value) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 12
            or any(not isinstance(item, str) or not item or "\0" in item for item in value)):
        raise ValueError("Configured executable arguments must be a short string array")
    if not value[0].startswith("/"):
        raise ValueError("Use an absolute executable path")
    return value


@dataclass(frozen=True)
class Host:
    id: str
    transport: str
    worker_argv: list[str]
    ssh_target: str | None = None
    ssh_config: Path | None = None
    label: str = ""
    description: str = ""
    vnc_websocket: str | None = None


@dataclass(frozen=True)
class StackConfig:
    hosts: dict[str, Host]

    @classmethod
    def load(cls, path: Path):
        body = exact(load_private_json(path), {"schema", "hosts"}, {"schema", "hosts"})
        if body["schema"] != 1 or not isinstance(body["hosts"], list) or not 1 <= len(body["hosts"]) <= 7:
            raise ValueError("Configure schema 1 and one to seven hosts")
        hosts = {}
        for item in body["hosts"]:
            exact(item, {"id", "transport", "worker_argv", "ssh_target", "ssh_config", "label", "description", "vnc_websocket"}, {"id", "transport", "worker_argv"})
            name = host_id(item["id"])
            if name in hosts or item["transport"] not in ("local", "ssh"):
                raise ValueError("Duplicate host or unsupported transport")
            target, ssh_config = item.get("ssh_target"), item.get("ssh_config")
            if item["transport"] == "ssh":
                if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", target):
                    raise ValueError("Use a fixed SSH alias, without a user or command")
                if not ssh_config:
                    raise ValueError("SSH configuration path is required")
                ssh_config = expanded(ssh_config)
            label, description = public_text(item.get("label", name), 40), public_text(item.get("description", "Linux desktop"), 100)
            websocket = item.get("vnc_websocket")
            if websocket is not None and (not isinstance(websocket, str) or not re.fullmatch(r"ws://(?:127\.0\.0\.1|localhost):[0-9]{1,5}", websocket)):
                raise ValueError("The baseline viewer only connects to an owner-run loopback WebSocket tunnel")
            hosts[name] = Host(name, item["transport"], argv(item["worker_argv"]), target, ssh_config, label, description, websocket)
        return cls(hosts)


@dataclass(frozen=True)
class WorkerConfig:
    host: str
    state: Path
    desktop: dict
    vault: dict

    @classmethod
    def load(cls, path: Path):
        body = exact(load_private_json(path), {"schema", "host", "state_directory", "desktop", "vault"},
                     {"schema", "host", "state_directory", "desktop", "vault"})
        if body["schema"] != 1:
            raise ValueError("Unsupported schema")
        desktop = exact(body["desktop"], {"enabled", "dedicated_session", "display", "xauthority", "session_id", "origin_adapter"},
                        {"enabled", "dedicated_session", "display", "xauthority", "session_id"})
        vault = exact(body["vault"], {"enabled", "bw_command", "runtime_directory"}, {"enabled", "bw_command", "runtime_directory"})
        for value in (desktop["enabled"], desktop["dedicated_session"], vault["enabled"]):
            if type(value) is not bool:
                raise ValueError("Feature flags must be booleans")
        if not re.fullmatch(r":[0-9]{1,4}(?:\.[0-9])?", desktop["display"]):
            raise ValueError("Use a local X11 display")
        if desktop["enabled"] and not desktop["dedicated_session"]:
            raise ValueError("The baseline only controls a dedicated X11 session")
        public_text(desktop["session_id"], 64)
        if not desktop["session_id"]:
            raise ValueError("Set a fresh session ID whenever the desktop restarts")
        desktop = dict(desktop, xauthority=expanded(desktop["xauthority"]))
        if desktop.get("origin_adapter"):
            desktop["origin_adapter"] = argv(desktop["origin_adapter"])
        if not isinstance(vault["bw_command"], str) or not vault["bw_command"].startswith("/"):
            raise ValueError("Use an absolute official Bitwarden CLI path")
        # A disabled vault does not require a runtime directory to exist yet.
        runtime = expanded(vault["runtime_directory"]) if vault["enabled"] else None
        return cls(host_id(body["host"]), expanded(body["state_directory"]), desktop, dict(vault, runtime=runtime))
