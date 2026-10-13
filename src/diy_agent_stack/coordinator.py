from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import shlex

from .common import encoded, identifier


class Coordinator:
    def __init__(self, config):
        self.config = config
        self.semaphore = asyncio.Semaphore(4)
        self.cleanup_semaphore = asyncio.Semaphore(4)

    def command(self, host):
        if host not in self.config.hosts:
            raise ValueError("Host is not in the owner inventory")
        item = self.config.hosts[host]
        if item.transport == "local":
            return item.worker_argv
        return ["ssh", "-T", "-F", str(item.ssh_config), "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
                "-o", "ServerAliveCountMax=2", item.ssh_target, shlex.join(item.worker_argv)]

    @asynccontextmanager
    async def _slot(self, cleanup):
        if cleanup:
            async with self.cleanup_semaphore:
                yield
        else:
            async with self.semaphore:
                yield

    async def _input_cleanup(self, host, payload, process, communication):
        # Keep the original worker alive while its exact cancel marker is delivered.
        # Cleanup RPCs have a separate slot so four cancelled calls cannot deadlock.
        try:
            await asyncio.wait_for(self._invoke(host, "desktop_cancel", {"action_id": identifier(payload["action_id"])},
                                                timeout=4, _cleanup=True), 4)
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        try:
            await asyncio.wait_for(asyncio.shield(communication), 6)
            return
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(communication), 4)
        except (OSError, ValueError, asyncio.TimeoutError):
            # Forced worker termination is uncertain; the durable action remains fenced.
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def _invoke(self, host, method, payload, timeout=25, *, _cleanup=False):
        if _cleanup and method != "desktop_cancel":
            raise ValueError("The cleanup slot is only for exact input cancellation")
        async with self._slot(_cleanup):
            process = await asyncio.create_subprocess_exec(*self.command(host), stdin=asyncio.subprocess.PIPE,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            communication = asyncio.create_task(process.communicate(encoded(dict(method=method, payload=payload))))
            try:
                output, _ = await asyncio.wait_for(asyncio.shield(communication), timeout)
                if len(output) > 4 * 1024 * 1024:
                    raise ValueError("Worker output exceeds its budget")
                body = json.loads(output)
                if process.returncode or body.get("host") != host:
                    raise ValueError("Worker failed or returned a different host ID")
                return body
            except (asyncio.CancelledError, asyncio.TimeoutError):
                if method == "desktop_action" and payload.get("action_id"):
                    cleanup = asyncio.create_task(self._input_cleanup(host, payload, process, communication))
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    cleanup.result()
                raise
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                if not communication.done():
                    communication.cancel()
                await asyncio.gather(communication, return_exceptions=True)

    async def call(self, host, method, payload=None):
        payload = payload or {}
        try:
            return await self._invoke(host, method, payload)
        except (OSError, ValueError, asyncio.TimeoutError):
            return dict(host=host, status="unknown_outcome", effect_verified=False, no_automatic_replay=True,
                        next_step="Read the matching receipt and actual state before another mutation")
        except asyncio.CancelledError:
            raise

    async def many(self, requests, method, parallel=4):
        if type(parallel) is not int or not 1 <= parallel <= 4 or not isinstance(requests, list) or not 1 <= len(requests) <= 7:
            raise ValueError("Use 1..7 explicit hosts and 1..4 parallel calls")
        hosts = [item["host"] for item in requests]
        if len(set(hosts)) != len(hosts) or any(host not in self.config.hosts for host in hosts):
            raise ValueError("Hosts must be unique and configured")
        local_limit = asyncio.Semaphore(parallel)

        async def one(item):
            async with local_limit:
                return await self.call(item["host"], method, {key: value for key, value in item.items() if key != "host"})
        return dict(results=await asyncio.gather(*(one(item) for item in requests)), parallel_limit=parallel,
                    retries=False, shared_by_default=True)
