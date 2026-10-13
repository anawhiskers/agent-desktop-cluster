from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from diy_agent_stack.common import private_directory, write_json
from diy_agent_stack.config import Host, StackConfig, WorkerConfig
from diy_agent_stack.coordinator import Coordinator
from diy_agent_stack.jobs import Jobs
from diy_agent_stack.state import State


class ProcessCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.configs = {}
        self.environ = patch.dict(os.environ, {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}, clear=False)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.cleanup_groups)

    def cleanup_groups(self):
        for path in self.root.rglob("processes.json"):
            pid = json.loads(path.read_text())["leader"]
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def host(self, name, mode="worker"):
        config = self.root / (name + ".json")
        write_json(config, dict(schema=1, host=name, state_directory=str(self.root / (name + "-state")),
                   desktop=dict(enabled=False, dedicated_session=True, display=":1", xauthority=str(self.root / "Xauthority"), session_id="fixture-session"),
                   vault=dict(enabled=False, bw_command="/fixture/bw", runtime_directory="$XDG_RUNTIME_DIR/fixture-vault")))
        self.configs[name] = config
        return Host(name, "local", [sys.executable, str(Path(__file__).with_name("process_fixture.py")),
                                    mode, str(config), str(self.root)])

    def request(self, action="input-001"):
        return dict(action_id=action, agent_id="fixture-agent", kind="type", text="public fixture",
                    expected_generation="fixture-generation", expected_window="123")

    async def started(self, names):
        deadline = asyncio.get_running_loop().time() + 6
        while not all((self.root / name / "started").exists() for name in names):
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("Synthetic input process did not start")
            await asyncio.sleep(.01)

    def assert_stopped(self, names):
        for name in names:
            processes = json.loads((self.root / name / "processes.json").read_text())
            for pid in processes.values():
                result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
                self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith("Z"), "Synthetic input process still active")
            self.assertFalse((self.root / name / "delayed-effect").exists())
            receipt = State(self.root / (name + "-state")).receipt("input-001")
            self.assertEqual(receipt["status"], "unknown_outcome")
            self.assertFalse(receipt["effect_verified"])

    def test_cooperative_cancel_stops_detached_process_tree_and_fences_next_input(self):
        self.check_cooperative_cancel()

    def test_cooperative_cancel_kills_sigterm_resistant_input_group(self):
        self.check_cooperative_cancel(resists_term=True)

    def check_cooperative_cancel(self, resists_term=False):
        host = self.host("HOST01")
        if resists_term:
            target = self.root / host.id
            target.mkdir(mode=0o700)
            (target / "leader-ignore-term").write_text("public fixture")
        coordinator = Coordinator(StackConfig({host.id: host}))
        async def check():
            task = asyncio.create_task(coordinator.call(host.id, "desktop_action", self.request()))
            try:
                await self.started([host.id])
                task.cancel()
                await asyncio.sleep(.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 15)
                self.assert_stopped([host.id])
                blocked = await coordinator.call(host.id, "desktop_action", self.request("input-002"))
                self.assertEqual(blocked["status"], "unknown_outcome")
                self.assertEqual((self.root / host.id / "dispatches").read_text().count("dispatch"), 1)
                await asyncio.sleep(2.1)
                self.assert_stopped([host.id])
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        asyncio.run(check())

    def test_four_cancelled_calls_have_independent_bounded_cleanup_slots(self):
        hosts = {name: self.host(name) for name in ("HOST01", "HOST02", "HOST03", "HOST04")}
        coordinator = Coordinator(StackConfig(hosts))
        async def check():
            tasks = [asyncio.create_task(coordinator.call(name, "desktop_action", self.request())) for name in hosts]
            try:
                await self.started(hosts)
                for task in tasks:
                    task.cancel()
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
                self.assertTrue(all(isinstance(value, asyncio.CancelledError) for value in results))
                self.assert_stopped(hosts)
                await asyncio.sleep(2.1)
                self.assert_stopped(hosts)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(check())

    def test_worker_sigterm_enters_finally_and_reaps_detached_input(self):
        host = self.host("HOST01")
        async def check():
            process = await asyncio.create_subprocess_exec(*host.worker_argv, stdin=asyncio.subprocess.PIPE,
                      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            communicating = asyncio.create_task(process.communicate(json.dumps(dict(method="desktop_action", payload=self.request())).encode()))
            try:
                await self.started([host.id])
                process.terminate()
                await asyncio.wait_for(communicating, 6)
                self.assert_stopped([host.id])
                await asyncio.sleep(2.1)
                self.assert_stopped([host.id])
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                await asyncio.gather(communicating, return_exceptions=True)
        asyncio.run(check())

    def test_worker_loss_during_job_mutations_preserves_fence(self):
        for index, (method, hard_kill) in enumerate((("machine_start", False), ("machine_cancel", False),
                                                    ("machine_receipt", False), ("machine_cancel", True)), 1):
            with self.subTest(method=method, hard_kill=hard_kill):
                host = self.host("HOST" + str(index).zfill(2), mode="job-worker")
                state = State(self.root / (host.id + "-state"))
                if method != "machine_start":
                    with state.db() as db:
                        state.begin(db, "job-001", "machine_start", "fixture-agent", {},
                                    dict(action_id="job-001", status="running", unit="diy-stack-job-001.service"))
                if method == "machine_receipt":
                    folder = private_directory(private_directory(state.root / "jobs") / "job-001")
                    write_json(folder / "child-result.json", dict(shell_exited=True, cleanup_attempted=True, exit_code=0, output_truncated={}))
                payload = dict(action_id="job-001")
                if method == "machine_start":
                    payload.update(agent_id="fixture-agent", command="true", timeout_seconds=30)
                async def check():
                    process = await asyncio.create_subprocess_exec(*host.worker_argv, stdin=asyncio.subprocess.PIPE,
                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    communicating = asyncio.create_task(process.communicate(json.dumps(dict(method=method, payload=payload)).encode()))
                    try:
                        await self.started([host.id])
                        process.kill() if hard_kill else process.terminate()
                        await asyncio.wait_for(communicating, 6)
                        self.assertEqual(process.returncode, -signal.SIGKILL if hard_kill else 130)
                        result = state.receipt("job-001")
                        self.assertEqual(result["status"], "unknown_outcome")
                        acknowledgement = {"machine_start": "launcher_acknowledged", "machine_cancel": "cancel_acknowledged",
                                           "machine_receipt": "cleanup_acknowledged"}[method]
                        self.assertFalse(result[acknowledgement])
                        jobs = Jobs(WorkerConfig.load(self.configs[host.id]), state,
                                    run=lambda *args, **kwargs: self.fail("Following job must not launch"))
                        with self.assertRaises(ValueError):
                            jobs.start(dict(action_id="job-002", agent_id="fixture-agent", command="true", timeout_seconds=30))
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                        await asyncio.gather(communicating, return_exceptions=True)
                asyncio.run(check())

    def test_retirement_retry_worker_loss_cannot_reuse_previous_acknowledgements(self):
        for index, (stage, hard_kill) in enumerate((("stop", False), ("stop", True),
                                                   ("reset-failed", False), ("reset-failed", True)), 1):
            with self.subTest(stage=stage, hard_kill=hard_kill):
                host = self.host("HOST" + str(index).zfill(2), mode="job-worker")
                target = private_directory(self.root / host.id)
                (target / "retirement-pause").write_text(stage)
                state = State(self.root / (host.id + "-state"))
                with state.db() as db:
                    state.begin(db, "job-001", "machine_start", "fixture-agent", {},
                                dict(action_id="job-001", status="unknown_outcome", unit="diy-stack-job-001.service",
                                     execution_status="completed_nonzero", exit_code=9, effect_verified=False,
                                     stop_acknowledged=True, cancel_acknowledged=True, cleanup_acknowledged=True,
                                     failed_unit_retirement_acknowledged=True))
                async def check():
                    process = await asyncio.create_subprocess_exec(*host.worker_argv, stdin=asyncio.subprocess.PIPE,
                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    communicating = asyncio.create_task(process.communicate(json.dumps(dict(method="machine_receipt", payload={"action_id": "job-001"})).encode()))
                    try:
                        await self.started([host.id])
                        process.kill() if hard_kill else process.terminate()
                        await asyncio.wait_for(communicating, 6)
                        result = state.receipt("job-001")
                        self.assertEqual(result["status"], "unknown_outcome")
                        self.assertEqual(result["execution_status"], "completed_nonzero")
                        self.assertEqual(result["exit_code"], 9)
                        self.assertFalse(result["cleanup_acknowledged"])
                        self.assertFalse(result["cancel_acknowledged"])
                        self.assertFalse(result["failed_unit_retirement_acknowledged"])
                        self.assertEqual(result["stop_acknowledged"], stage == "reset-failed")
                        self.assertFalse(result["effect_verified"])
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                        await asyncio.gather(communicating, return_exceptions=True)
                asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
