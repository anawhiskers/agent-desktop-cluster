from __future__ import annotations

import asyncio
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

from .common import encoded

WEB = Path(__file__).with_name("web")


def public_inventory(config):
    return [dict(id=item.id, label=item.label or item.id, description=item.description,
                 vnc_websocket=item.vnc_websocket) for item in config.hosts.values()]


class Viewer:
    def __init__(self, coordinator=None, *, fixture=False, novnc=None):
        if coordinator is None and not fixture:
            raise ValueError("Real viewer requires an owner inventory")
        self.coordinator, self.fixture = coordinator, fixture
        self.novnc = Path(novnc).resolve() if novnc else None
        if self.novnc and not (self.novnc / "core" / "rfb.js").is_file():
            raise ValueError("Choose the installed noVNC root containing core/rfb.js")
        self.key = secrets.token_urlsafe(32)
        self.mode = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)
        self.loop.close()

    def inventory(self):
        if self.fixture:
            return [dict(id="HOST" + str(index).zfill(2), label="HOST" + str(index).zfill(2),
                         description="Fixture desktop; no host connection", vnc_websocket=None) for index in range(1, 8)]
        return public_inventory(self.coordinator.config)

    def call(self, host, method, payload=None):
        if host not in {item["id"] for item in self.inventory()}:
            raise ValueError("Unknown configured host")
        if self.fixture:
            if method == "status":
                return dict(host=host, enabled=True, generation="fixture-generation", native_size=[1280, 800],
                            active_window=dict(id="fixture-window", title="Public fixture"), input_allowed=False,
                            paused=True, native_together=False, fixture=True,
                            presence=[dict(agent_id="demo-agent", label="Demo agent", color="mint", phase="idle", x=510, y=310)],
                            work=dict(exclusive=False, shared_by_default=True, paused=True, active_or_uncertain_work=False))
            if method == "capture":
                return dict(self.call(host, "status"), image=base64.b64encode((WEB / "fixture.svg").read_bytes()).decode(),
                            image_mime="image/svg+xml", image_size=[1280, 800])
            if method == "history":
                return dict(host=host, fixture=True, content_logged=False,
                            events=[dict(seq=2, id="demo-observe", kind="capture", agent="demo-agent", status="fixture_only", at=0),
                                    dict(seq=1, id="demo-input", kind="desktop_action", agent="demo-agent", status="not_dispatched", at=0)])
            raise ValueError("Fixture mode does not send input or change host state")
        future = asyncio.run_coroutine_threadsafe(self.coordinator.call(host, method, payload or {}), self.loop)
        return future.result(timeout=32)

    def control(self, host):
        item = next((item for item in self.inventory() if item["id"] == host), None)
        if self.fixture or not item or not item["vnc_websocket"]:
            raise ValueError("Take control requires a real authenticated VNC tunnel")
        result = self.call(host, "owner_pause", dict(paused=True))
        if not result.get("paused"):
            raise ValueError("Owner pause could not be confirmed; controls stay disabled")
        return dict(mode="control", paused=True, automatic_resume=False)

    def authorized(self, headers, port, *, api=False):
        allowed = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
        authority = headers.get("Host", "")
        if authority not in allowed:
            return False
        if headers.get("Origin") not in (None, "http://" + authority):
            return False
        if headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        return not api or hmac.compare_digest(headers.get("X-Stack-Viewer-Key", ""), self.key)

    def handler(self):
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlsplit(self.path)
                if not viewer.authorized(self.headers, self.server.server_port, api=parsed.path.startswith("/api/")):
                    self.send(403, encoded(dict(error="Owner viewer authorization required")))
                    return
                try:
                    if parsed.path == "/api/config":
                        self.send(200, encoded(dict(hosts=viewer.inventory(), fixture=viewer.fixture, together_available=False,
                                                   novnc_available=bool(viewer.novnc), max_views=4)))
                        return
                    if parsed.path in ("/api/status", "/api/frame", "/api/history"):
                        query = parse_qs(parsed.query)
                        host = query.get("host", [None])[0]
                        method = {"/api/status": "status", "/api/frame": "capture", "/api/history": "history"}[parsed.path]
                        self.send(200, encoded(viewer.call(host, method)))
                        return
                    if parsed.path.startswith("/novnc/") and viewer.novnc:
                        root, relative = viewer.novnc, parsed.path.removeprefix("/novnc/")
                        if Path(relative).suffix not in {".js", ".css", ".svg", ".png", ".wasm"} and Path(relative).name != "LICENSE":
                            self.send(404, encoded(dict(error="Asset unavailable")))
                            return
                    else:
                        root, relative = WEB, "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                    candidate = (root / relative).resolve()
                    if root.resolve() not in candidate.parents or not candidate.is_file() or candidate.stat().st_size > 4 * 1024 * 1024:
                        self.send(404, encoded(dict(error="Asset unavailable")))
                        return
                    self.send(200, candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
                except Exception:
                    self.send(400, encoded(dict(error="Request unavailable; inspect the host privately")))

            def do_POST(self):
                if not viewer.authorized(self.headers, self.server.server_port, api=True):
                    self.send(403, encoded(dict(error="Owner viewer authorization required")))
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Content-Type") != "application/json" or not 1 <= length <= 2048 or self.path != "/api/mode":
                        raise ValueError("Unsupported viewer mutation")
                    value = json.loads(self.rfile.read(length))
                    if set(value) != {"host", "mode"} or value["mode"] not in ("watch", "control"):
                        raise ValueError("Unsupported mode")
                    result = viewer.control(value["host"]) if value["mode"] == "control" else dict(mode="watch", automatic_resume=False)
                    self.send(200, encoded(result))
                except Exception:
                    self.send(400, encoded(dict(error="Control was not granted. Agent pause remains; inspect state before proceeding")))

        return Handler

    def serve(self, port=8765):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Use a loopback port 1024..65535")
        server = ThreadingHTTPServer(("127.0.0.1", port), self.handler())
        print("Open privately: http://127.0.0.1:" + str(port) + "/#" + self.key, flush=True)
        print("Fixture mode: no connected host" if self.fixture else "Loopback owner viewer. No public listener or TLS endpoint.", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self.close()
