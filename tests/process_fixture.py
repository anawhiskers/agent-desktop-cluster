"""Synthetic process-tree fixture only. Never configure this as a production worker."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace


def main():
    mode, root = sys.argv[1], Path(sys.argv[2])
    if mode == "grandchild":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / "grandchild-started").write_text("public fixture")
        time.sleep(2)
        (root / "delayed-effect").write_text("public fixture effect")
        return 0
    if mode == "child":
        if (root / "leader-ignore-term").exists():
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child = subprocess.Popen([sys.executable, __file__, "grandchild", str(root)])
        (root / "processes.json").write_text(json.dumps(dict(leader=__import__("os").getpid(), descendant=child.pid)))
        deadline = time.monotonic() + 3
        while not (root / "grandchild-started").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        (root / "started").write_text("public fixture")
        time.sleep(3)
        return 0
    if mode not in ("worker", "job-worker"):
        raise ValueError("Unknown fixture mode")
    config_path, fixture_root = root, Path(sys.argv[3])
    from diy_agent_stack.desktop import Desktop
    from diy_agent_stack.cli import main as worker_main
    if mode == "job-worker":
        from diy_agent_stack.jobs import Jobs
        original_jobs = Jobs.__init__
        def job_initialized(self, config, state, **kwargs):
            original_jobs(self, config, state, **kwargs)
            target = fixture_root / config.host
            target.mkdir(mode=0o700, exist_ok=True)
            def simulated(argv, **options):
                if argv[:3] == ["systemctl", "--user", "show"]:
                    return SimpleNamespace(returncode=0, stdout="LoadState=loaded\nActiveState=active\nSubState=exited\n")
                (target / "started").write_text("public simulated job mutation")
                time.sleep(4)
                return SimpleNamespace(returncode=0, stdout="")
            self.run = simulated
        Jobs.__init__ = job_initialized
        return worker_main(["worker", "--config", str(config_path)])
    original = Desktop.__init__

    def initialized(self, config, state, vault, **kwargs):
        original(self, config, state, vault, **kwargs)
        target = fixture_root / config.host
        target.mkdir(mode=0o700, exist_ok=True)
        self.environment = lambda: {}
        self.status = lambda: dict(host=config.host, enabled=True, input_allowed=not state.paused(),
                                  generation="fixture-generation", native_size=[1280, 800], active_window={"id": "123"})
        def native(_argv, **options):
            with (target / "dispatches").open("a") as stream:
                stream.write("public fixture dispatch\n")
            return subprocess.Popen([sys.executable, __file__, "child", str(target)], **options)
        self.popen = native

    Desktop.__init__ = initialized
    return worker_main(["worker", "--config", str(config_path)])


if __name__ == "__main__":
    raise SystemExit(main())
