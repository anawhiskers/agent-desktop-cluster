from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import signal

from .config import StackConfig, WorkerConfig


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="diy-stack")
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("server", "worker"):
        item = sub.add_parser(name)
        item.add_argument("--config", type=Path, required=True)
    owner = sub.add_parser("owner")
    owner.add_argument("--config", type=Path, required=True)
    owner.add_argument("operation", choices=["pause", "resume", "allow-input", "vault-unlock", "vault-lock", "reconcile"])
    owner.add_argument("--ttl", type=int, default=300)
    owner.add_argument("--action-id")
    owner.add_argument("--confirmed-effects", action="store_true")
    child = sub.add_parser("job-child")
    child.add_argument("--folder", type=Path, required=True)
    child.add_argument("--timeout", type=int, required=True)
    viewer = sub.add_parser("viewer")
    viewer.add_argument("--config", type=Path)
    viewer.add_argument("--fixture", action="store_true")
    viewer.add_argument("--novnc", type=Path)
    viewer.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    host = None
    try:
        if args.mode == "job-child":
            from .jobs import job_child
            return job_child(args.folder, args.timeout)
        if args.mode in ("server", "viewer"):
            from .coordinator import Coordinator
            coordinator = Coordinator(StackConfig.load(args.config)) if args.config else None
            if args.mode == "server":
                from .server import build_server
                build_server(coordinator).run(transport="stdio")
            else:
                from .viewer import Viewer
                Viewer(coordinator, fixture=args.fixture, novnc=args.novnc).serve(args.port)
            return 0
        from .worker import Worker
        config = WorkerConfig.load(args.config)
        host = config.host
        worker = Worker(config)
        if args.mode == "worker":
            def interrupted(_number, _frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGHUP, interrupted)
            body = sys.stdin.buffer.read(65537)
            if len(body) > 65536:
                raise ValueError("Worker request exceeds 64 KiB")
            result = worker.call(json.loads(body))
        elif args.operation in ("pause", "resume"):
            result = worker.owner_pause(args.operation == "pause")
        elif args.operation == "allow-input":
            result = worker.desktop.owner_allow_input(args.ttl)
        elif args.operation == "vault-unlock":
            if not sys.stdin.isatty():
                raise ValueError("Vault unlock must run privately in the owner's host terminal")
            result = worker.vault.owner_unlock(args.ttl)
        elif args.operation == "vault-lock":
            result = worker.vault.owner_lock()
        else:
            if not args.action_id or not args.confirmed_effects:
                raise ValueError("Reconcile requires the exact action ID and confirmed-effects flag after manual inspection")
            result = worker.state.receipt(args.action_id)
            if result["status"] != "unknown_outcome":
                raise ValueError("Only an uncertain action can be manually reconciled")
            result.update(status="owner_reconciled", owner_confirmation_recorded=True, effect_verified=False)
            with worker.state.db() as db:
                worker.state.finish(db, args.action_id, result)
        print(json.dumps(dict(result, host=host)), flush=True)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # Third-party errors can include terminal input or host paths; never echo them into MCP output.
        print(json.dumps(dict(host=host, status="blocked", error_class=type(error).__name__,
                              reason="Inspect the owner configuration or local host privately; no execution success is claimed")), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
