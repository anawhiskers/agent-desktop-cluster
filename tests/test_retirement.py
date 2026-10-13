from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_core import Temporary
from diy_agent_stack.common import private_directory, write_json
from diy_agent_stack.jobs import Jobs


def failed_state(unit, **changes):
    return dict(dict(Id=unit, LoadState="loaded", ActiveState="failed", SubState="failed", MainPID="0",
                     ControlPID="0", ControlGroup="", TasksCurrent="[not set]", Result="exit-code",
                     ExecMainCode="1", ExecMainStatus="9"), **changes)


class RetirementTests(Temporary):
    def job(self, action="job-001", status="running", report=True):
        jobs = Jobs(self.config, self.state)
        unit = "diy-stack-" + action + ".service"
        folder = private_directory(jobs.root / action)
        if report:
            write_json(folder / "child-result.json", dict(shell_exited=True, cleanup_attempted=True,
                       exit_code=9, output_truncated={"stdout": False, "stderr": False}))
        result = dict(action_id=action, status=status, unit=unit, effect_verified=False, no_automatic_replay=True)
        if status == "completed_nonzero":
            result["exit_code"] = 9
        with self.state.db() as db:
            self.state.begin(db, action, "machine_start", "fixture-agent", {}, result)
        return jobs, unit

    def simulated(self, jobs, action, states, *, stop=0, reset=0, timeout_on=None):
        commands = []
        unit = "diy-stack-" + action + ".service"
        def run(argv, **options):
            commands.append(argv)
            self.assertEqual(argv[:2], ["systemctl", "--user"])
            self.assertEqual(argv[3], unit)
            operation = argv[2]
            self.assertIn(operation, ("show", "stop", "reset-failed"))
            if operation != "show":
                self.assertEqual(self.state.receipt(action)["status"], "unknown_outcome")
            if operation == "reset-failed":
                receipt = self.state.receipt(action)
                self.assertTrue(receipt["stop_acknowledged"])
                self.assertTrue(receipt["failed_unit_retirement_precheck"]["process_group_empty"])
                self.assertFalse(receipt["failed_unit_retirement_acknowledged"])
            if operation == timeout_on:
                raise subprocess.TimeoutExpired(argv, options["timeout"])
            if operation == "show":
                self.assertIn("ControlPID", argv[-1])
                self.assertIn("ControlGroup", argv[-1])
                values = states.pop(0)
                return SimpleNamespace(returncode=0, stdout="\n".join(key + "=" + value for key, value in values.items()))
            return SimpleNamespace(returncode=stop if operation == "stop" else reset)
        jobs.run = run
        return commands

    def test_failed_unit_with_unavailable_tasks_is_retired_and_nonzero_result_preserved(self):
        jobs, unit = self.job()
        state = failed_state(unit)
        commands = self.simulated(jobs, "job-001", [state, state, {"LoadState": "not-found"}])
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "completed_nonzero")
        self.assertEqual(result["execution_status"], "completed_nonzero")
        self.assertEqual(result["exit_code"], 9)
        self.assertFalse(result["effect_verified"])
        self.assertTrue(result["cleanup_acknowledged"])
        self.assertTrue(result["failed_unit_retirement_acknowledged"])
        self.assertEqual([argv[2] for argv in commands], ["show", "stop", "show", "reset-failed", "show"])
        self.assertEqual([argv for argv in commands if argv[2] == "reset-failed"], [["systemctl", "--user", "reset-failed", unit]])
        self.assertEqual(self.state.receipt("job-001")["exit_code"], 9)

    def test_empty_group_requires_complete_stopped_shape(self):
        jobs, unit = self.job()
        accepted = [failed_state(unit), failed_state(unit, ActiveState="inactive", TasksCurrent="0"),
                    {"LoadState": "not-found"}]
        rejected = [failed_state(unit, MainPID="123"), failed_state(unit, ControlPID="456"),
                    failed_state(unit, ActiveState="active"), failed_state(unit, TasksCurrent="1"),
                    failed_state(unit, TasksCurrent="malformed"), failed_state(unit, ControlGroup=None),
                    failed_state(unit, ControlGroup="/user.slice/unrelated.service", TasksCurrent="0"),
                    failed_state(unit, ControlGroup="/user.slice/../" + unit),
                    {key: value for key, value in failed_state(unit).items() if key != "ControlPID"}]
        for values in accepted:
            with self.subTest(values=values):
                self.assertTrue(jobs.empty_group(values, unit))
        for values in rejected:
            with self.subTest(values=values):
                self.assertFalse(jobs.empty_group(values, unit))

    def test_v2_group_proof_handles_empty_populated_missing_and_malformed_events(self):
        jobs, unit = self.job()
        root = private_directory(self.root / "cgroup")
        (root / "cgroup.controllers").write_text("cpu memory\n")
        group = "/user.slice/" + unit
        directory = private_directory(private_directory(root / "user.slice") / unit)
        state = failed_state(unit, ControlGroup=group)
        with patch("diy_agent_stack.jobs.CGROUP_ROOT", root):
            for text, expected in (("populated 0\nfrozen 0\n", True), ("populated 1\n", False),
                                   ("populated 0 extra\n", False), ("populated 1\npopulated 0\n", False)):
                with self.subTest(text=text):
                    (directory / "cgroup.events").write_text(text)
                    self.assertEqual(jobs.empty_group(state, unit), expected)
            (directory / "cgroup.events").unlink()
            self.assertFalse(jobs.empty_group(state, unit))
            directory.rmdir()
            self.assertTrue(jobs.empty_group(state, unit))
            outside = private_directory(self.root / "outside-cgroup")
            (outside / "cgroup.events").write_text("populated 0\n")
            directory.symlink_to(outside, target_is_directory=True)
            self.assertFalse(jobs.empty_group(state, unit))

    def test_populated_group_never_reaches_reset_failed(self):
        jobs, unit = self.job()
        root = private_directory(self.root / "cgroup")
        (root / "cgroup.controllers").write_text("cpu\n")
        directory = private_directory(private_directory(root / "user.slice") / unit)
        (directory / "cgroup.events").write_text("populated 1\n")
        state = failed_state(unit, ControlGroup="/user.slice/" + unit)
        commands = self.simulated(jobs, "job-001", [state, state])
        with patch("diy_agent_stack.jobs.CGROUP_ROOT", root):
            result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertFalse(result["cleanup_acknowledged"])
        self.assertNotIn("reset-failed", [argv[2] for argv in commands])
        self.assertEqual(result["exit_code"], 9)

    def test_reset_timeout_is_durable_and_retry_only_cleans_up(self):
        jobs, unit = self.job()
        state = failed_state(unit)
        self.simulated(jobs, "job-001", [state, state], timeout_on="reset-failed")
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertFalse(result["cleanup_acknowledged"])
        self.assertFalse(result["failed_unit_retirement_acknowledged"])
        self.assertEqual(self.state.receipt("job-001")["execution_status"], "completed_nonzero")
        commands = self.simulated(jobs, "job-001", [state, state, {"LoadState": "not-found"}])
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertTrue(result["cleanup_only_reconciliation"])
        self.assertTrue(result["cleanup_acknowledged"])
        self.assertEqual(result["execution_status"], "completed_nonzero")
        self.assertEqual(result["exit_code"], 9)
        self.assertTrue(all(argv[:2] == ["systemctl", "--user"] for argv in commands))
        jobs.run = lambda *args, **kwargs: self.fail("No following launch until owner outcome reconciliation")
        with self.assertRaises(ValueError):
            jobs.start(dict(action_id="job-002", agent_id="fixture-agent", command="true", timeout_seconds=30))

    def test_unknown_reconciled_and_incomplete_terminal_receipts_never_replay(self):
        for index, status in enumerate(("unknown_outcome", "owner_reconciled", "completed_nonzero", "running"), 1):
            action = "job-" + str(index).zfill(3)
            with self.subTest(status=status):
                jobs, unit = self.job(action, status, report=False)
                state = failed_state(unit)
                commands = self.simulated(jobs, action, [state, state, {"LoadState": "not-found"}])
                result = jobs.receipt(action)
                self.assertEqual(result["status"], "unknown_outcome" if status == "running" else status)
                self.assertTrue(result["cleanup_acknowledged"])
                self.assertFalse(result["effect_verified"])
                self.assertTrue(all(argv[0] == "systemctl" for argv in commands))
                if status != "completed_nonzero":
                    self.assertNotIn("exit_code", result)

    def test_active_uncertain_unit_is_observed_without_stop_or_reset(self):
        jobs, unit = self.job(status="unknown_outcome", report=False)
        state = failed_state(unit, ActiveState="active", SubState="running", MainPID="123", TasksCurrent="1")
        commands = self.simulated(jobs, "job-001", [state])
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertEqual([argv[2] for argv in commands], ["show"])
        self.assertFalse(result["cleanup_acknowledged"])

    def test_stop_reset_and_post_reset_failures_never_claim_cleanup(self):
        for index, stage in enumerate(("stop", "reset", "post_reset"), 1):
            action = "job-" + str(index).zfill(3)
            with self.subTest(stage=stage):
                jobs, unit = self.job(action)
                state = failed_state(unit)
                states = [state, state, state]
                self.simulated(jobs, action, states, stop=1 if stage == "stop" else 0,
                               reset=1 if stage == "reset" else 0)
                result = jobs.receipt(action)
                self.assertEqual(result["status"], "unknown_outcome")
                self.assertFalse(result["cleanup_acknowledged"])
                self.assertEqual(result["exit_code"], 9)

    def test_receipt_identity_mismatch_never_queries_or_changes_any_unit(self):
        jobs, _unit = self.job()
        result = self.state.receipt("job-001")
        result["unit"] = "unrelated.service"
        with self.state.db() as db:
            self.state.finish(db, "job-001", result)
        jobs.run = lambda *args, **kwargs: self.fail("Receipt cannot choose an unowned unit")
        for operation in (jobs.receipt, jobs.cancel):
            with self.assertRaises(ValueError):
                operation("job-001")

    def test_manager_primary_identity_mismatch_cannot_reach_stop_or_reset(self):
        jobs, unit = self.job()
        commands = self.simulated(jobs, "job-001", [failed_state(unit, Id="unrelated.service")])
        result = jobs.receipt("job-001")
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertFalse(result["cleanup_acknowledged"])
        self.assertEqual([argv[2] for argv in commands], ["show"])


if __name__ == "__main__":
    unittest.main()
