from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from .common import digest, file_lock, identifier, private_directory, private_file, public_text


class State:
    def __init__(self, root: Path, clock=time.time):
        self.root = private_directory(root)
        self.path = root / "state.sqlite3"
        if self.path.exists() or self.path.is_symlink():
            private_file(self.path)
            if self.path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("State quota reached; archive it privately before continuing")
        else:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        self.clock = clock
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, kind TEXT, agent TEXT, hash TEXT,
                    status TEXT, receipt TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS reservation (
                    slot INTEGER PRIMARY KEY CHECK(slot=1), agent TEXT, token TEXT,
                    generation INTEGER, expires REAL);
                CREATE TABLE IF NOT EXISTS history (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, kind TEXT,
                    agent TEXT, status TEXT, at REAL, request_hash TEXT);
                CREATE TABLE IF NOT EXISTS presence (
                    agent TEXT PRIMARY KEY, body TEXT);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        with file_lock(self.root / "state.lock"):
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def paused(self) -> bool:
        return (self.root / "PAUSE-AGENTS").exists()

    def admit(self, db, agent: str, token: str | None, *, allow_paused=False):
        identifier(agent, 48)
        if self.paused() and not allow_paused:
            raise ValueError("Owner pause is active")
        if not allow_paused and db.execute("SELECT 1 FROM actions WHERE status='unknown_outcome' LIMIT 1").fetchone():
            raise ValueError("An uncertain prior action requires owner reconciliation before new mutations")
        row = db.execute("SELECT * FROM reservation WHERE slot=1").fetchone()
        if row and row["expires"] > self.clock():
            if agent != row["agent"] or not isinstance(token, str) or not secrets.compare_digest(token, row["token"]):
                raise ValueError("Host has an exclusive work reservation")
        elif token is not None:
            raise ValueError("Reservation expired or released; explicitly claim a new one")

    def existing(self, db, action: str, kind: str, agent: str, payload: dict):
        identifier(action)
        identifier(agent, 48)
        row = db.execute("SELECT * FROM actions WHERE id=?", (action,)).fetchone()
        if row:
            if row["kind"] != kind or row["agent"] != agent or row["hash"] != digest(payload):
                raise ValueError("Action ID belongs to another request")
            return dict(json.loads(row["receipt"]), replay_suppressed=True)
        return None

    def begin(self, db, action: str, kind: str, agent: str, payload: dict, receipt: dict):
        db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?)", (action, kind, agent, digest(payload),
                   receipt["status"], json.dumps(receipt), self.clock()))
        self.finish(db, action, receipt)

    def finish(self, db, action: str, receipt: dict):
        db.execute("UPDATE actions SET status=?, receipt=? WHERE id=?", (receipt["status"], json.dumps(receipt), action))
        row = db.execute("SELECT * FROM actions WHERE id=?", (action,)).fetchone()
        db.execute("INSERT INTO history(id,kind,agent,status,at,request_hash) VALUES (?,?,?,?,?,?)",
                   (action, row["kind"], row["agent"], receipt["status"], self.clock(), row["hash"]))
        db.execute("DELETE FROM history WHERE seq NOT IN (SELECT seq FROM history ORDER BY seq DESC LIMIT 5000)")

    def receipt(self, action: str, agent: str | None = None) -> dict:
        identifier(action)
        with self.db() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (action,)).fetchone()
            if not row or agent is not None and row["agent"] != agent:
                raise ValueError("No matching action receipt")
            return json.loads(row["receipt"])

    def active(self, db) -> bool:
        return bool(db.execute("SELECT 1 FROM actions WHERE status IN ('starting','dispatching','running','unknown_outcome') LIMIT 1").fetchone())

    def work(self, operation: str, payload: dict) -> dict:
        action, agent = identifier(payload["action_id"]), identifier(payload["agent_id"], 48)
        ttl = payload.get("ttl_seconds", 300)
        if type(ttl) is not int or not 30 <= ttl <= 900:
            raise ValueError("Reservation TTL must be 30..900 seconds")
        with self.db() as db:
            prior = self.existing(db, action, "work_" + operation, agent, payload)
            if prior:
                return prior
            row = db.execute("SELECT * FROM reservation WHERE slot=1").fetchone()
            if operation == "claim":
                if self.paused() or row and row["expires"] > self.clock() or self.active(db):
                    raise ValueError("Host is paused, reserved, running work, or has an uncertain action")
                generation = (row["generation"] if row else 0) + 1
                token = secrets.token_hex(24)
                db.execute("INSERT OR REPLACE INTO reservation VALUES (1,?,?,?,?)",
                           (agent, token, generation, self.clock() + ttl))
            else:
                self.admit(db, agent, payload.get("reservation_token"), allow_paused=operation == "release")
                if not row or row["expires"] <= self.clock():
                    raise ValueError("No active reservation to renew or release")
                generation, token = row["generation"], row["token"]
                if operation == "renew":
                    db.execute("UPDATE reservation SET expires=? WHERE slot=1", (self.clock() + ttl,))
                elif operation == "release":
                    db.execute("UPDATE reservation SET expires=0 WHERE slot=1")
                else:
                    raise ValueError("Unsupported reservation operation")
            receipt = dict(action_id=action, status={"claim": "claimed", "renew": "renewed", "release": "released"}[operation],
                           generation=generation, expires_at=self.clock() + ttl if operation != "release" else 0,
                           coordination_only=True, grants_permissions=False)
            if operation != "release":
                receipt["reservation_token"] = token
            self.begin(db, action, "work_" + operation, agent, payload, receipt)
            return receipt

    def work_status(self) -> dict:
        with self.db() as db:
            row = db.execute("SELECT generation,expires FROM reservation WHERE slot=1").fetchone()
            return dict(exclusive=bool(row and row["expires"] > self.clock()),
                        generation=row["generation"] if row else 0, expires_at=row["expires"] if row else 0,
                        paused=self.paused(), active_or_uncertain_work=self.active(db), shared_by_default=True)

    def history(self, limit: int = 50, before: int | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or before is not None and (type(before) is not int or before < 1):
            raise ValueError("History uses limit 1..100 and a positive cursor")
        with self.db() as db:
            rows = db.execute("SELECT * FROM history WHERE seq < ? ORDER BY seq DESC LIMIT ?", (before or 2**63 - 1, limit)).fetchall()
            return [dict(row) for row in rows]

    def presence(self, agent: str, label: str, color: str, phase: str, point: dict | None):
        public_text(label, 32)
        if color not in {"lilac", "rose", "mint", "sky", "amber", "coral"}:
            raise ValueError("Unsupported cursor color")
        body = dict(agent_id=identifier(agent, 48), label=label, color=color, phase=phase, at=self.clock())
        if point:
            body.update(point)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO presence VALUES (?,?)", (agent, json.dumps(body)))

    def presence_status(self) -> list[dict]:
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM presence")
                    if json.loads(row[0])["at"] > self.clock() - 15]
