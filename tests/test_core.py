from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diy_agent_stack.common import private_directory, write_json, write_private
from diy_agent_stack.config import Host, StackConfig, WorkerConfig
from diy_agent_stack.coordinator import Coordinator
from diy_agent_stack.desktop import Desktop, key_chord, text_commands
from diy_agent_stack.jobs import Jobs, OUTPUT_BYTES, job_child
from diy_agent_stack.state import State
from diy_agent_stack.vault import Vault
from diy_agent_stack.viewer import Viewer
from diy_agent_stack.worker import Worker


class Temporary(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)
        self.config = WorkerConfig("HOST01", self.root / "state", dict(enabled=False, dedicated_session=True,
                       display=":1", xauthority=self.root / "Xauthority", session_id="fixture-session", origin_adapter=[]),
                       dict(enabled=False, bw_command="/fixture/bw", runtime_directory="", runtime=None))
        self.state = State(self.config.state)

    def worker_file(self):
        path = self.root / "worker.json"
        write_json(path, dict(schema=1, host="HOST01", state_directory=str(self.config.state),
                   desktop=dict(enabled=False, dedicated_session=True, display=":1", xauthority=str(self.root / "Xauthority"), session_id="fixture-session"),
                   vault=dict(enabled=False, bw_command="/fixture/bw", runtime_directory="$XDG_RUNTIME_DIR/fixture-vault")))
        return path


class ConfigurationTests(Temporary):
    def test_worker_default_off_and_private_config(self):
        path = self.worker_file()
        result = WorkerConfig.load(path)
        self.assertFalse(result.desktop["enabled"])
        self.assertIsNone(result.vault["runtime"])
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            WorkerConfig.load(path)

    def test_symlink_state_is_refused(self):
        link = self.root / "link"
        link.symlink_to(self.config.state, target_is_directory=True)
        with self.assertRaises(ValueError):
            State(link)

    def test_inventory_duplicate_and_limit(self):
        path = self.root / "stack.json"
        host = dict(id="HOST01", transport="local", worker_argv=["/fixture/worker"])
        for values in ([host, host], [dict(host, id="HOST" + str(n).zfill(2)) for n in range(1, 9)]):
            write_json(path, dict(schema=1, hosts=values))
            with self.assertRaises(ValueError):
                StackConfig.load(path)

    def test_ssh_command_is_fixed_and_strict(self):
        host = Host("HOST02", "ssh", ["/fixture/path with space/worker", "worker", "--config", "/fixture/config file.json"],
                    "stack-host02", self.root / "ssh-config")
        command = Coordinator(StackConfig({host.id: host})).command(host.id)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-1], "'/fixture/path with space/worker' worker --config '/fixture/config file.json'")
        with self.assertRaises(ValueError):
            Coordinator(StackConfig({host.id: host})).command("HOST99")


class ReservationTests(Temporary):
    def claim(self, action="claim-001", agent="agent-a", ttl=300):
        return self.state.work("claim", dict(action_id=action, agent_id=agent, ttl_seconds=ttl))

    def test_claim_replay_never_reexecutes(self):
        first = self.claim()
        replay = self.claim()
        self.assertTrue(replay["replay_suppressed"])
        self.assertEqual(first["reservation_token"], replay["reservation_token"])
        with self.assertRaises(ValueError):
            self.claim(ttl=301)

    def test_exclusive_requires_matching_agent_and_token(self):
        claim = self.claim()
        with self.state.db() as db:
            for agent, token in (("agent-b", claim["reservation_token"]), ("agent-a", "wrong-token"), ("agent-a", None)):
                with self.assertRaises(ValueError):
                    self.state.admit(db, agent, token)
            self.state.admit(db, "agent-a", claim["reservation_token"])
        with self.assertRaises(ValueError):
            self.claim(action="claim-002", agent="agent-b")
        self.assertNotIn("reservation_token", self.state.work_status())

    def test_expired_cannot_renew_and_next_claim_increments(self):
        now = [1000.0]
        self.state.clock = lambda: now[0]
        claim = self.claim(ttl=30)
        now[0] += 31
        with self.assertRaises(ValueError):
            self.state.work("renew", dict(action_id="renew-001", agent_id="agent-a", reservation_token=claim["reservation_token"]))
        next_claim = self.claim(action="claim-002", agent="agent-b")
        self.assertEqual(next_claim["generation"], 2)

    def test_owner_pause_overrides_token_and_new_jobs(self):
        claim = self.claim()
        write_json(self.state.root / "PAUSE-AGENTS", dict(owner_requested=True))
        with self.state.db() as db:
            with self.assertRaises(ValueError):
                self.state.admit(db, "agent-a", claim["reservation_token"])
        with self.assertRaises(ValueError):
            self.claim(action="claim-002")

    def test_release_can_free_coordination_while_human_pause_stays(self):
        claim = self.claim()
        write_json(self.state.root / "PAUSE-AGENTS", dict(owner_requested=True))
        result = self.state.work("release", dict(action_id="release-001", agent_id="agent-a", reservation_token=claim["reservation_token"]))
        self.assertEqual(result["status"], "released")
        self.assertTrue(self.state.paused())
        self.assertFalse(self.state.work_status()["exclusive"])

    def test_running_and_unknown_jobs_block_claim(self):
        for status in ("running", "unknown_outcome"):
            with self.state.db() as db:
                self.state.begin(db, "job-" + status.replace("_", "-"), "machine_start", "agent-a", {"fixture": status},
                                 dict(action_id="job-" + status.replace("_", "-"), status=status))
            with self.assertRaises(ValueError):
                self.claim(action="claim-" + status.replace("_", "-"))

    def test_history_contains_metadata_not_request_content(self):
        payload = dict(command="printf 'fictional private text'", text="fictional typed text")
        with self.state.db() as db:
            self.state.begin(db, "action-001", "desktop_action", "agent-a", payload, dict(status="input_sent"))
        history = self.state.history()
        self.assertNotIn("fictional", json.dumps(history))
        self.assertNotIn("command", history[0])
        self.assertEqual(len(self.state.history(limit=1)), 1)
        with self.assertRaises(ValueError):
            self.state.history(limit=101)

    def test_unknown_outcome_blocks_new_mutations_until_owner_reconcile(self):
        with self.state.db() as db:
            self.state.begin(db, "uncertain-001", "machine_start", "agent-a", {}, dict(status="unknown_outcome"))
        with self.state.db() as db:
            with self.assertRaises(ValueError):
                self.state.admit(db, "agent-b", None)
            self.state.finish(db, "uncertain-001", dict(status="owner_reconciled", owner_confirmation_recorded=True))
        with self.state.db() as db:
            self.state.admit(db, "agent-b", None)


class JobTests(Temporary):
    def test_start_replay_and_conflict(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        jobs = Jobs(self.config, self.state, run=fake_run)
        payload = dict(action_id="job-001", agent_id="agent-a", command="printf 'public result\\n'", timeout_seconds=30)
        self.assertEqual(jobs.start(payload)["status"], "running")
        self.assertTrue(jobs.start(payload)["replay_suppressed"])
        self.assertEqual(len(calls), 1)
        self.assertIn("--user", calls[0])
        with self.assertRaises(ValueError):
            jobs.start(dict(payload, command="printf 'changed'"))
        self.assertEqual((jobs.root / "job-001" / "script.sh").stat().st_mode & 0o777, 0o600)

    def test_paused_job_never_launches(self):
        write_json(self.state.root / "PAUSE-AGENTS", {})
        jobs = Jobs(self.config, self.state, run=lambda *args, **kwargs: self.fail("Job must not launch"))
        with self.assertRaises(ValueError):
            jobs.start(dict(action_id="job-001", agent_id="agent-a", command="true", timeout_seconds=30))

    def test_public_child_drains_and_bounds_output(self):
        folder = private_directory(self.root / "child")
        write_private(folder / "script.sh", b"printf 'public child result\\n'; head -c 20000 /dev/zero | tr '\\000' x; printf 'public stderr' >&2\n")
        self.assertEqual(job_child(folder, 5), 0)
        self.assertEqual((folder / "stdout.log").stat().st_size, OUTPUT_BYTES)
        result = json.loads((folder / "child-result.json").read_text())
        self.assertTrue(result["shell_exited"])
        self.assertTrue(result["output_truncated"]["stdout"])
        self.assertEqual((folder / "stderr.log").read_text(), "public stderr")

    def test_terminal_receipt_checks_cleanup_and_optional_output(self):
        jobs = Jobs(self.config, self.state)
        folder = private_directory(jobs.root / "job-001")
        write_json(folder / "child-result.json", dict(shell_exited=True, cleanup_attempted=True, exit_code=0, output_truncated={}))
        write_private(folder / "stdout.log", b"public fixture")
        with self.state.db() as db:
            self.state.begin(db, "job-001", "machine_start", "agent-a", {}, dict(action_id="job-001", status="running", unit="fixture.service"))
        states = [dict(LoadState="loaded", ActiveState="active", SubState="exited"),
                  dict(LoadState="not-found", MainPID="0", TasksCurrent="0")]
        jobs.properties = lambda _: states.pop(0)
        jobs.run = lambda *args, **kwargs: SimpleNamespace(returncode=0)
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["cleanup_acknowledged"])
        self.assertNotIn("stdout", result)
        self.assertEqual(jobs.receipt("job-001", include_output=True)["stdout"], "public fixture")

    def test_manager_failure_persists_uncertainty_and_fences_following_job(self):
        jobs = Jobs(self.config, self.state)
        with self.state.db() as db:
            self.state.begin(db, "job-001", "machine_start", "agent-a", {},
                             dict(action_id="job-001", status="running", unit="fixture.service"))
        def unavailable(_unit):
            raise RuntimeError("Synthetic unavailable manager")
        jobs.properties = unavailable
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertFalse(result["cleanup_acknowledged"])
        self.assertTrue(result["service_state_unverified"])
        self.assertEqual(self.state.receipt("job-001")["status"], "unknown_outcome")
        jobs.run = lambda *args, **kwargs: self.fail("Following job must not launch")
        with self.assertRaises(ValueError):
            jobs.start(dict(action_id="job-002", agent_id="agent-a", command="true", timeout_seconds=30))

    def test_cleanup_query_failure_cannot_claim_group_stopped(self):
        jobs = Jobs(self.config, self.state)
        folder = private_directory(jobs.root / "job-001")
        write_json(folder / "child-result.json", dict(shell_exited=True, cleanup_attempted=True, exit_code=0, output_truncated={}))
        with self.state.db() as db:
            self.state.begin(db, "job-001", "machine_start", "agent-a", {},
                             dict(action_id="job-001", status="running", unit="fixture.service"))
        queried = []
        def properties(_unit):
            queried.append(True)
            if len(queried) == 1:
                return dict(LoadState="loaded", ActiveState="active", SubState="exited")
            raise RuntimeError("Synthetic cleanup query failure")
        jobs.properties = properties
        jobs.run = lambda *args, **kwargs: SimpleNamespace(returncode=0)
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertFalse(result["cleanup_acknowledged"])
        self.assertEqual(self.state.receipt("job-001")["status"], "unknown_outcome")

    def test_cancel_stop_failure_persists_uncertainty_and_fences_following_job(self):
        jobs = Jobs(self.config, self.state)
        with self.state.db() as db:
            self.state.begin(db, "job-001", "machine_start", "agent-a", {},
                             dict(action_id="job-001", status="running", unit="fixture.service"))
        def unavailable(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        jobs.run = unavailable
        result = jobs.cancel("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertFalse(result["cancel_acknowledged"])
        self.assertFalse(result["cleanup_acknowledged"])
        self.assertEqual(self.state.receipt("job-001")["status"], "unknown_outcome")
        jobs.run = lambda *args, **kwargs: self.fail("Following job must not launch")
        with self.assertRaises(ValueError):
            jobs.start(dict(action_id="job-002", agent_id="agent-a", command="true", timeout_seconds=30))

    def interrupted_mutation(self, operation):
        for exception in (KeyboardInterrupt, SystemExit):
            with self.subTest(operation=operation, exception=exception.__name__):
                state = State(self.root / (operation + "-" + exception.__name__))
                def interrupted(*args, **kwargs):
                    self.assertEqual(state.receipt("job-001")["status"], "unknown_outcome")
                    raise exception()
                jobs = Jobs(self.config, state, run=interrupted)
                if operation != "start":
                    with state.db() as db:
                        state.begin(db, "job-001", "machine_start", "agent-a", {},
                                    dict(action_id="job-001", status="running", unit="fixture.service"))
                if operation == "receipt":
                    folder = private_directory(jobs.root / "job-001")
                    write_json(folder / "child-result.json", dict(shell_exited=True, cleanup_attempted=True, exit_code=0, output_truncated={}))
                    jobs.properties = lambda _: dict(LoadState="loaded", ActiveState="active", SubState="exited")
                with self.assertRaises(exception):
                    if operation == "start":
                        jobs.start(dict(action_id="job-001", agent_id="agent-a", command="true", timeout_seconds=30))
                    else:
                        getattr(jobs, operation)("job-001")
                receipt = state.receipt("job-001")
                self.assertEqual(receipt["status"], "unknown_outcome")
                self.assertTrue(receipt["interrupted"])
                flag = {"start": "launcher_acknowledged", "cancel": "cancel_acknowledged", "receipt": "cleanup_acknowledged"}[operation]
                self.assertFalse(receipt[flag])
                jobs.run = lambda *args, **kwargs: self.fail("Following job must not launch")
                with self.assertRaises(ValueError):
                    jobs.start(dict(action_id="job-002", agent_id="agent-a", command="true", timeout_seconds=30))

    def test_interrupted_launcher_preserves_durable_uncertainty(self):
        self.interrupted_mutation("start")

    def test_interrupted_cancel_preserves_durable_uncertainty(self):
        self.interrupted_mutation("cancel")

    def test_interrupted_receipt_cleanup_preserves_durable_uncertainty(self):
        self.interrupted_mutation("receipt")


class CompletedProcess:
    pid = 424242
    returncode = 0
    def __init__(self, argv, record, **kwargs):
        self.argv, self.record = argv, record
        record.append(dict(argv=argv, inputs=[]))
    def communicate(self, input=None, timeout=None):
        self.record[-1]["inputs"].append(input)
        return None, None
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        return self.returncode


class DesktopTests(Temporary):
    def setUp(self):
        super().setUp()
        self.record = []
        cleanup = patch("diy_agent_stack.desktop.stop_input_group", lambda process: process.wait(timeout=2))
        cleanup.start()
        self.addCleanup(cleanup.stop)
        self.desktop = Desktop(self.config, self.state, Vault(self.config),
                               popen=lambda argv, **kwargs: CompletedProcess(argv, self.record, **kwargs))
        self.desktop.environment = lambda: {}
        self.desktop.status = lambda: dict(enabled=True, input_allowed=not self.state.paused(), paused=self.state.paused(),
                                           generation="fixture-generation", native_size=[1280, 800], active_window={"id": "123"})
        self.payload = dict(action_id="input-001", agent_id="agent-a", expected_generation="fixture-generation", expected_window="123", kind="type", text="public text")

    def test_literal_text_uses_stdin_and_replay_is_suppressed(self):
        result = self.desktop.action(self.payload)
        self.assertEqual(result["status"], "input_sent")
        self.assertFalse(result["effect_verified"])
        self.assertNotIn("public text", json.dumps([entry["argv"] for entry in self.record]))
        self.assertIn(b"public text", self.record[0]["inputs"])
        self.assertTrue(self.desktop.action(self.payload)["replay_suppressed"])
        self.assertEqual(len(self.record), 1)

    def test_changed_focus_or_generation_refused_before_dispatch(self):
        for key, value in (("expected_window", "999"), ("expected_generation", "stale")):
            with self.assertRaises(ValueError):
                self.desktop.action(dict(self.payload, **{key: value}))
        self.assertEqual(self.record, [])

    def test_owner_pause_and_origin_gate_refuse(self):
        write_json(self.state.root / "PAUSE-AGENTS", {})
        with self.assertRaises(ValueError):
            self.desktop.action(self.payload)
        (self.state.root / "PAUSE-AGENTS").unlink()
        with self.assertRaises(ValueError):
            self.desktop.action(dict(self.payload, kind="credential", credential_ref="vault:" + "1" * 48, expected_origin="https://example.invalid"))
        self.assertEqual(self.record, [])

    def test_unicode_newline_and_chord_validation(self):
        commands = text_commands("a\n\u03a9\u03bb")
        self.assertEqual([entry[0][0] for entry in commands], ["type", "key", "type", "type"])
        self.assertEqual(key_chord("ctrl+shift+a"), "Control_L+Shift_L+a")
        with self.assertRaises(ValueError):
            key_chord("ctrl+a; echo nope")

    def test_cancel_before_dispatch_is_fenced(self):
        self.desktop.cancel("input-001")
        with self.assertRaises(ValueError):
            self.desktop.action(self.payload)
        self.assertEqual(self.record, [])


class VaultTests(Temporary):
    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        self.runtime = self.root / "runtime"
        private_directory(self.runtime)
        self.environ = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)}, clear=False)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.config = replace(self.config, vault=dict(enabled=True, bw_command="/fixture/bw", runtime=self.runtime / "vault"))
        self.item = dict(id="00000000-0000-0000-0000-000000000001", name="Public fixture item",
                         login=dict(username="fixture-user", password="fixture-value", uris=[{"uri": "https://example.invalid/login"}],
                                    fido2Credentials=[{"fixture": True}]))
        self.calls = []
        def fake_run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[1:3] == ["unlock", "--raw"]:
                return SimpleNamespace(returncode=0, stdout=b"fixture-session-material-not-real", stderr=b"")
            body = [self.item] if argv[1:3] == ["list", "items"] else self.item
            return SimpleNamespace(returncode=0, stdout=json.dumps(body).encode(), stderr=b"")
        self.vault = Vault(self.config, run=fake_run, clock=lambda: self.now[0])
        self.vault.owner_unlock()

    def test_unlock_keeps_session_private_and_never_returns_it(self):
        result = self.vault.status()
        self.assertTrue(result["unlocked"])
        self.assertNotIn("fixture-session-material", json.dumps(result))
        self.assertEqual((self.config.vault["runtime"] / "session.json").stat().st_mode & 0o777, 0o600)
        self.now[0] += 901
        self.assertFalse(self.vault.status()["unlocked"])

    def test_find_reports_metadata_only_and_no_assertion(self):
        result = self.vault.find("Public fixture")
        self.assertEqual(result["items"][0]["passkey_metadata_count"], 1)
        self.assertNotIn("fixture-user", json.dumps(result))
        self.assertNotIn("fixture-value", json.dumps(result))
        self.assertTrue(result["passkey_metadata_is_not_assertion"])

    def test_staging_is_origin_bound_single_use_and_expires(self):
        result = self.vault.prepare(self.item["id"], "password", "https://example.invalid")
        self.assertNotIn("fixture-value", json.dumps(result))
        with self.assertRaises(ValueError):
            with self.vault.consume(result["credential_ref"], "https://different.invalid", "password"):
                pass
        with self.assertRaises(ValueError):
            with self.vault.consume(result["credential_ref"], "https://example.invalid", "textbox"):
                pass
        with self.vault.consume(result["credential_ref"], "https://example.invalid", "password") as value:
            self.assertEqual(value, b"fixture-value")
        with self.assertRaises(ValueError):
            with self.vault.consume(result["credential_ref"], "https://example.invalid", "password"):
                pass
        second = self.vault.prepare(self.item["id"], "username", "https://example.invalid")
        self.now[0] += 91
        with self.assertRaises(ValueError):
            with self.vault.consume(second["credential_ref"], "https://example.invalid", "textbox"):
                pass

    def test_wrong_origin_and_symlink_are_refused(self):
        with self.assertRaises(ValueError):
            self.vault.prepare(self.item["id"], "password", "https://different.invalid")
        result = self.vault.prepare(self.item["id"], "password", "https://example.invalid")
        secret = self.config.vault["runtime"] / (result["credential_ref"].split(":")[1] + ".secret")
        secret.unlink()
        target = self.root / "fixture-secret"
        write_private(target, b"fixture-only")
        secret.symlink_to(target)
        with self.assertRaises(ValueError):
            with self.vault.consume(result["credential_ref"], "https://example.invalid", "password"):
                pass

    def credential_action(self, field, roles, action):
        prepared = self.vault.prepare(self.item["id"], field, "https://example.invalid")
        roles, dispatched = iter(roles), []
        config = replace(self.config, desktop=dict(self.config.desktop, origin_adapter=["/fixture/origin-adapter"]))
        def adapter(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(dict(window="123", origin="https://example.invalid", role=next(roles))).encode())
        desktop = Desktop(config, self.state, self.vault, run=adapter,
                          popen=lambda argv, **kwargs: CompletedProcess(argv, dispatched, **kwargs))
        desktop.environment = lambda: {}
        desktop.status = lambda: dict(enabled=True, input_allowed=True, generation="fixture-generation", native_size=[1280, 800], active_window={"id": "123"})
        with patch("diy_agent_stack.desktop.stop_input_group", lambda process: process.wait(timeout=2)):
            result = desktop.action(dict(action_id=action, agent_id="agent-a", kind="credential",
                                   expected_generation="fixture-generation", expected_window="123", expected_origin="https://example.invalid",
                                   credential_ref=prepared["credential_ref"]))
        return result, prepared, dispatched

    def test_changed_final_field_role_refuses_and_consumes_protected_reference(self):
        result, prepared, dispatched = self.credential_action("password", ["password", "textbox"], "credential-001")
        self.assertEqual(result["status"], "not_dispatched")
        self.assertEqual(dispatched, [])
        with self.assertRaises(ValueError):
            with self.vault.consume(prepared["credential_ref"], "https://example.invalid", "password"):
                self.fail("Reference must already be consumed")

    def test_attested_password_and_username_preserve_protected_stdin_input(self):
        for field, role in (("password", "password"), ("username", "textbox")):
            result, prepared, dispatched = self.credential_action(field, [role, role], "credential-" + field)
            self.assertEqual(result["status"], "input_sent")
            self.assertFalse(result["effect_verified"])
            self.assertEqual(dispatched[0]["inputs"], [self.item["login"][field].encode()])
            self.assertNotIn(self.item["login"][field], json.dumps(result))
            self.assertNotIn(self.item["login"][field], json.dumps([entry["argv"] for entry in dispatched]))
            with self.assertRaises(ValueError):
                with self.vault.consume(prepared["credential_ref"], "https://example.invalid", role):
                    self.fail("Reference must already be consumed")

    def test_later_secret_segment_role_change_stops_input_and_records_uncertainty(self):
        self.item["login"]["password"] = "fixture-\u03a9-value"
        result, prepared, dispatched = self.credential_action("password", ["password", "password", "textbox"], "credential-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["inputs"], [b"fixture-"])
        self.assertFalse(result["effect_verified"])
        self.assertEqual(self.state.receipt("credential-001")["status"], "unknown_outcome")
        with self.assertRaises(ValueError):
            with self.vault.consume(prepared["credential_ref"], "https://example.invalid", "password"):
                self.fail("Reference must already be consumed")


class ViewerTests(Temporary):
    def setUp(self):
        super().setUp()
        self.viewer = Viewer(fixture=True)
        self.addCleanup(self.viewer.close)

    def test_fixture_is_labeled_and_refuses_control(self):
        self.assertEqual(len(self.viewer.inventory()), 7)
        self.assertTrue(self.viewer.call("HOST01", "status")["fixture"])
        with self.assertRaises(ValueError):
            self.viewer.control("HOST01")

    def test_viewer_auth_rejects_host_origin_and_missing_key(self):
        valid = {"Host": "127.0.0.1:8765", "X-Stack-Viewer-Key": self.viewer.key}
        self.assertTrue(self.viewer.authorized(valid, 8765, api=True))
        for extra in ({"Host": "example.invalid:8765"}, {"Origin": "https://example.invalid"},
                      {"X-Stack-Viewer-Key": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertFalse(self.viewer.authorized(dict(valid, **extra), 8765, api=True))

    def test_handler_blocks_path_escape_without_binding_a_socket(self):
        handler = self.viewer.handler().__new__(self.viewer.handler())
        handler.headers = {"Host": "127.0.0.1:8765"}
        handler.server = SimpleNamespace(server_port=8765)
        handler.path = "/../config.py"
        sent = []
        handler.send = lambda status, body, content_type=None: sent.append((status, body))
        handler.do_GET()
        self.assertEqual(sent[0][0], 404)


class LocalTransportTests(Temporary):
    def test_real_worker_process_status_without_native_or_service_access(self):
        config = StackConfig({"HOST01": Host("HOST01", "local", [sys.executable, "-m", "diy_agent_stack.cli", "worker", "--config", str(self.worker_file())])})
        with patch.dict(os.environ, {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}, clear=False):
            result = asyncio.run(Coordinator(config).call("HOST01", "status"))
        self.assertEqual(result["host"], "HOST01")
        self.assertFalse(result["enabled"])
        self.assertFalse(result["input_allowed"])

    def test_four_host_fanout_is_bounded(self):
        config = StackConfig({"HOST" + str(n).zfill(2): Host("HOST" + str(n).zfill(2), "local", ["/fixture/worker"]) for n in range(1, 8)})
        coordinator = Coordinator(config)
        active, peak = [0], [0]
        async def fake_call(host, method, payload=None):
            active[0] += 1; peak[0] = max(peak[0], active[0])
            await asyncio.sleep(.01)
            active[0] -= 1
            return dict(host=host)
        coordinator.call = fake_call
        results = asyncio.run(coordinator.many([dict(host=host) for host in config.hosts], "status", 4))
        self.assertEqual(peak[0], 4)
        self.assertEqual(len(results["results"]), 7)
        self.assertFalse(results["retries"])


if __name__ == "__main__":
    unittest.main()
