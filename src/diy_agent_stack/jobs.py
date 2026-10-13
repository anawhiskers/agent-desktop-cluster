from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .common import encoded, identifier, load_private_json, private_directory, public_text, write_json, write_private

OUTPUT_BYTES = 16384


class Jobs:
    def __init__(self, config, state, run=subprocess.run):
        self.config, self.state, self.run = config, state, run
        self.root = private_directory(state.root / "jobs")

    def properties(self, unit):
        result = self.run(["systemctl", "--user", "show", unit, "--no-pager",
                           "--property=LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,MainPID,TasksCurrent"],
                          capture_output=True, text=True, timeout=8)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode and values.get("LoadState") != "not-found":
            raise RuntimeError("User service manager is unavailable")
        if not values.get("LoadState") or values["LoadState"] != "not-found" and not {"ActiveState", "SubState"} <= set(values):
            raise RuntimeError("User service-manager state is incomplete")
        return values

    def start(self, payload):
        action, agent = identifier(payload["action_id"]), identifier(payload["agent_id"], 48)
        script = public_text(payload["command"], 16384)
        timeout = payload.get("timeout_seconds", 180)
        if not script or type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("Use a nonempty script and timeout 1..3600 seconds")
        folder = self.root / action
        unit = "diy-stack-" + action + ".service"
        with self.state.db() as db:
            prior = self.state.existing(db, action, "machine_start", agent, payload)
            if prior:
                return prior
            self.state.admit(db, agent, payload.get("reservation_token"))
            private_directory(folder)
            write_private(folder / "script.sh", script.encode() + b"\n")
            # Persist uncertainty before the launcher can produce an external effect.
            receipt = dict(action_id=action, status="unknown_outcome", launcher_acknowledged=False, unit=unit, host=self.config.host,
                           timeout_seconds=timeout, created_at=time.time(), no_automatic_replay=True,
                           effect_verified=False, command_text_returned=False)
            self.state.begin(db, action, "machine_start", agent, payload, receipt)
        command = ["systemd-run", "--user", "--quiet", "--unit=" + unit,
                   "--property=Type=exec", "--property=RemainAfterExit=yes",
                   "--property=RuntimeMaxSec=" + str(timeout + 5), "--property=KillMode=control-group",
                   "--property=TimeoutStopSec=5", "--property=UMask=0077",
                   "--property=WorkingDirectory=" + str(Path.home()),
                   sys.executable, "-m", "diy_agent_stack.cli", "job-child", "--folder", str(folder), "--timeout", str(timeout)]
        if self.state.paused():
            receipt.update(status="not_dispatched", launcher_acknowledged=False)
            with self.state.db() as db:
                self.state.finish(db, action, receipt)
            return receipt
        try:
            launched = self.run(command, capture_output=True, timeout=12)
            receipt["launcher_acknowledged"] = launched.returncode == 0
            receipt["status"] = "running" if launched.returncode == 0 else "unknown_outcome"
        except (OSError, subprocess.TimeoutExpired):
            receipt.update(status="unknown_outcome", launcher_acknowledged=False)
        except (KeyboardInterrupt, SystemExit):
            receipt.update(status="unknown_outcome", launcher_acknowledged=False, interrupted=True)
            raise
        finally:
            with self.state.db() as db:
                self.state.finish(db, action, receipt)
        return receipt

    def receipt(self, action: str, include_output=False):
        if type(include_output) is not bool:
            raise ValueError("include_output must be boolean")
        action = identifier(action)
        result = self.state.receipt(action)
        if "unit" not in result:
            raise ValueError("This is not a machine job")
        folder = self.root / action
        if result["status"] in ("starting", "running"):
            try:
                systemd = self.properties(result["unit"])
                result["systemd"] = systemd
                if systemd.get("LoadState") == "not-found":
                    result["status"] = "unknown_outcome"
                elif systemd.get("ActiveState") in ("inactive", "failed") or systemd.get("SubState") == "exited":
                    terminal = load_private_json(folder / "child-result.json") if (folder / "child-result.json").exists() else None
                    if terminal and terminal.get("shell_exited") and terminal.get("cleanup_attempted"):
                        result.update(status="completed" if terminal["exit_code"] == 0 else "completed_nonzero",
                                      exit_code=terminal["exit_code"], output_truncated=terminal["output_truncated"])
                    else:
                        result["status"] = "unknown_outcome"
                    terminal_status = result["status"]
                    result.update(status="unknown_outcome", cleanup_acknowledged=False, service_state_unverified=True)
                    with self.state.db() as db:
                        self.state.finish(db, action, result)
                    stopped = self.run(["systemctl", "--user", "stop", result["unit"]], capture_output=True, timeout=12)
                    cleanup = self.properties(result["unit"])
                    result["cleanup_acknowledged"] = (stopped.returncode == 0 and cleanup.get("MainPID", "0") == "0"
                                                       and cleanup.get("TasksCurrent", "0") == "0")
                    result["status"] = terminal_status if result["cleanup_acknowledged"] else "unknown_outcome"
                    result["service_state_unverified"] = not result["cleanup_acknowledged"]
                    result["finished_at"] = time.time()
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                result.update(status="unknown_outcome", cleanup_acknowledged=False, service_state_unverified=True)
            except (KeyboardInterrupt, SystemExit):
                result.update(status="unknown_outcome", cleanup_acknowledged=False, service_state_unverified=True, interrupted=True)
                raise
            finally:
                with self.state.db() as db:
                    self.state.finish(db, action, result)
        result["next_step"] = ("Read this receipt later" if result["status"] in ("starting", "running") else
                               "Verify actual effects; never automatically replay an uncertain job")
        if include_output:
            from .common import SECRET, private_file
            for name in ("stdout", "stderr"):
                path = folder / (name + ".log")
                if not path.exists():
                    result[name] = ""
                    continue
                private_file(path)
                text = path.read_bytes()[:OUTPUT_BYTES].decode("utf-8", errors="replace")
                result[name] = "[credential-like output withheld]" if SECRET.search(text) else text
            result["output_filter_is_heuristic"] = True
        return result

    def cancel(self, action: str):
        result = self.state.receipt(identifier(action))
        if "unit" not in result:
            raise ValueError("This is not a machine job")
        # A lost worker must leave a fence before it asks the service manager to stop anything.
        result.update(status="unknown_outcome", cancel_acknowledged=False, cleanup_acknowledged=False,
                      service_state_unverified=True, next_step="Inspect partial effects before a new action")
        with self.state.db() as db:
            self.state.finish(db, action, result)
        try:
            stopped = self.run(["systemctl", "--user", "stop", result["unit"]], capture_output=True, timeout=12)
            result["cancel_acknowledged"] = stopped.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
        except (KeyboardInterrupt, SystemExit):
            result.update(cancel_acknowledged=False, interrupted=True)
            raise
        finally:
            with self.state.db() as db:
                self.state.finish(db, action, result)
        return result


def job_child(folder: Path, timeout: int):
    """Drain both streams while retaining only bounded private logs."""
    import signal
    from .common import private_file
    private_directory(folder)
    private_file(folder / "script.sh")
    with (folder / "script.sh").open("rb") as script:
        process = subprocess.Popen(["/bin/bash", "-s"], stdin=script,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                   env={k: v for k, v in os.environ.items() if k not in ("BW_SESSION", "BW_PASSWORD")})
    truncated = {"stdout": False, "stderr": False}

    def drain(name, stream):
        with (folder / (name + ".log")).open("xb") as target:
            os.chmod(target.name, 0o600)
            kept = 0
            while chunk := stream.read(4096):
                allowed = max(0, OUTPUT_BYTES - kept)
                target.write(chunk[:allowed])
                kept += min(len(chunk), allowed)
                if len(chunk) > allowed:
                    truncated[name] = True

    threads = [threading.Thread(target=drain, args=(name, stream), daemon=True)
               for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for thread in threads:
        thread.start()
    clean = False
    try:
        exit_code = process.wait(timeout=timeout)
        clean = exit_code >= 0
    except subprocess.TimeoutExpired:
        exit_code = -1
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if all(not thread.is_alive() for thread in threads):
                stream.close()
        write_json(folder / "child-result.json", dict(shell_exited=clean, exit_code=exit_code,
                   cleanup_attempted=True, output_truncated=truncated))
    return 0 if clean and exit_code == 0 else 1
