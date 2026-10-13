from __future__ import annotations

from .common import identifier, write_json
from .desktop import Desktop
from .jobs import Jobs
from .state import State
from .vault import Vault

METHODS = {
    "status", "capture", "desktop_action", "desktop_cancel", "receipt", "history", "work_status",
    "work_claim", "work_renew", "work_release", "machine_start", "machine_receipt", "machine_cancel",
    "vault_status", "vault_find", "vault_prepare", "owner_pause",
}


class Worker:
    def __init__(self, config):
        self.config = config
        self.state = State(config.state)
        self.vault = Vault(config)
        self.desktop = Desktop(config, self.state, self.vault)
        self.jobs = Jobs(config, self.state)

    def owner_pause(self, paused=True):
        if paused:
            write_json(self.state.root / "PAUSE-AGENTS", dict(owner_requested=True))
            (self.state.root / "owner-input-grant.json").unlink(missing_ok=True)
        else:
            (self.state.root / "PAUSE-AGENTS").unlink(missing_ok=True)
        return dict(host=self.config.host, paused=paused, input_grant=False)

    def call(self, request):
        if not isinstance(request, dict) or set(request) != {"method", "payload"} or request["method"] not in METHODS:
            raise ValueError("Use a fixed worker method and payload")
        method, payload = request["method"], request["payload"]
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object")
        if method == "owner_pause":
            if payload != {"paused": True}:
                raise ValueError("The viewer may request a pause only; resume must happen privately on the host")
            result = self.owner_pause(True)
        elif method == "status":
            result = self.desktop.status()
            result["work"] = self.state.work_status()
        elif method == "capture":
            result = self.desktop.capture(payload.get("max_width", 1280), payload.get("cursor", True))
        elif method == "desktop_action":
            result = self.desktop.action(payload)
        elif method == "desktop_cancel":
            result = self.desktop.cancel(payload["action_id"])
        elif method == "receipt":
            result = self.state.receipt(payload["action_id"])
        elif method == "history":
            result = dict(events=self.state.history(payload.get("limit", 50), payload.get("before")), content_logged=False)
        elif method == "work_status":
            result = self.state.work_status()
        elif method.startswith("work_"):
            # Reconcile pending jobs before admitting a new whole-host reservation.
            if method == "work_claim":
                with self.state.db() as db:
                    pending = [row[0] for row in db.execute("SELECT id FROM actions WHERE kind='machine_start' AND status IN ('starting','running')")]
                for action in pending:
                    self.jobs.receipt(action)
            result = self.state.work(method.removeprefix("work_"), payload)
        elif method == "machine_start":
            result = self.jobs.start(payload)
        elif method == "machine_receipt":
            result = self.jobs.receipt(payload["action_id"], payload.get("include_output", False))
        elif method == "machine_cancel":
            result = self.jobs.cancel(payload["action_id"])
        elif method == "vault_status":
            result = self.vault.status()
        elif method == "vault_find":
            result = self.vault.find(payload["query"])
        else:
            with self.state.db() as db:
                self.state.admit(db, identifier(payload["agent_id"], 48), payload.get("reservation_token"))
            result = self.vault.prepare(payload["item_id"], payload["field"], payload["origin"])
        return dict(result, host=self.config.host)
