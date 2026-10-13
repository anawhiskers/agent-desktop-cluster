from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import time

from .common import file_lock, identifier, load_private_json, private_file, public_text, write_json

COLORS = {"lilac": "#b49cff", "rose": "#ff83ba", "mint": "#62e5c5",
          "sky": "#72c8ff", "amber": "#ffd478", "coral": "#ff9e8d"}


@contextmanager
def atomic_spawn():
    # A stop signal between Popen returning and recording its object could orphan input.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGHUP, signal.SIGINT})
    def restore_child_mask():
        # RPC workers are single-threaded. The exec child must not inherit our temporary mask.
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    try:
        yield restore_child_mask
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def stop_input_group(process):
    """Stop the group even if its leader already exited; descendants must not retain input."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=2)
        return
    try:
        # Input has no durable work to flush; a trapped TERM must not extend its input window.
        process.wait(timeout=.1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def key_chord(value):
    if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+){0,4}", value):
        raise ValueError("Use an X11 key chord, not a command")
    aliases = {"ctrl": "Control_L", "control": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L"}
    return "+".join(aliases.get(token.lower(), token) for token in value.split("+"))


def text_commands(text: str) -> list[tuple[list[str], bytes | None]]:
    commands = []
    # X11 remaps unmapped characters; flush each before a later character reuses its keycode.
    for part in re.split(r"(\r\n|\r|\n)", text):
        if part in ("\r\n", "\r", "\n"):
            commands.append((["key", "Return"], None))
        else:
            for segment in re.findall(r"[\x00-\x7f]+|[^\x00-\x7f]", part):
                commands.append((["type", "--delay", "2", "--file", "-"], segment.encode()))
    return commands


class Desktop:
    def __init__(self, config, state, vault, run=subprocess.run, popen=subprocess.Popen):
        self.config, self.state, self.vault, self.run, self.popen = config, state, vault, run, popen

    def environment(self):
        if not self.config.desktop["enabled"]:
            raise ValueError("Desktop is disabled in owner configuration")
        auth = self.config.desktop["xauthority"]
        private_file(auth)
        env = {k: v for k, v in os.environ.items() if k not in ("BW_SESSION", "BW_PASSWORD")}
        return dict(env, DISPLAY=self.config.desktop["display"], XAUTHORITY=str(auth))

    def native(self, args):
        result = self.run(["xdotool", *args], env=self.environment(), capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise ValueError("Dedicated X11 desktop is unavailable")
        return result.stdout.strip()

    def status(self):
        if not self.config.desktop["enabled"]:
            return dict(host=self.config.host, enabled=False, paused=self.state.paused(), input_allowed=False,
                        automatic_human_detection=False, native_together=False)
        size = [int(value) for value in self.native(["getdisplaygeometry"]).split()]
        if len(size) != 2 or any(not 1 <= value <= 16384 for value in size):
            raise ValueError("Invalid desktop geometry")
        window = self.native(["getactivewindow"])
        title = self.native(["getwindowname", window])[:200]
        auth = self.config.desktop["xauthority"].stat()
        generation = hashlib.sha256((self.config.desktop["session_id"] + "\0" + str(size) + "\0" + str(auth.st_mtime_ns)).encode()).hexdigest()[:24]
        try:
            grant = load_private_json(self.state.root / "owner-input-grant.json")
            allowed = grant["expires_at"] > time.time() and grant["generation"] == generation
        except (OSError, ValueError, KeyError):
            allowed = False
        return dict(host=self.config.host, enabled=True, generation=generation, native_size=size,
                    active_window=dict(id=window, title=title), paused=self.state.paused(),
                    input_allowed=allowed and not self.state.paused(), automatic_human_detection=False,
                    native_together=False, presence=self.state.presence_status())

    def owner_allow_input(self, ttl=300):
        if type(ttl) is not int or not 30 <= ttl <= 900:
            raise ValueError("Input grant TTL must be 30..900 seconds")
        state = self.status()
        if not state["enabled"] or self.state.paused():
            raise ValueError("Enable the dedicated desktop and explicitly clear an owner pause first")
        write_json(self.state.root / "owner-input-grant.json", dict(generation=state["generation"], expires_at=time.time() + ttl))
        return dict(input_grant=True, expires_in=ttl, generation=state["generation"])

    def capture(self, max_width=1280, cursor=True):
        if type(max_width) is not int or not 320 <= max_width <= 1920 or type(cursor) is not bool:
            raise ValueError("Capture width is 320..1920 and cursor must be boolean")
        from PIL import Image, ImageDraw, ImageGrab
        state = self.status()
        if not state["enabled"]:
            raise ValueError("Desktop is disabled")
        previous = os.environ.get("XAUTHORITY")
        os.environ["XAUTHORITY"] = str(self.config.desktop["xauthority"])
        try:
            picture = ImageGrab.grab(xdisplay=self.config.desktop["display"])
        finally:
            if previous is None:
                os.environ.pop("XAUTHORITY", None)
            else:
                os.environ["XAUTHORITY"] = previous
        if list(picture.size) != state["native_size"] or self.status()["generation"] != state["generation"]:
            raise ValueError("Desktop changed during capture; observe again")
        if picture.width > max_width:
            picture = picture.resize((max_width, max(1, round(picture.height * max_width / picture.width))), Image.Resampling.LANCZOS)
        raw = io.BytesIO()
        picture.convert("RGB").save(raw, format="JPEG", quality=70)
        state["native_image_sha256"] = hashlib.sha256(raw.getvalue()).hexdigest()
        if cursor:
            draw = ImageDraw.Draw(picture)
            sx, sy = picture.width / state["native_size"][0], picture.height / state["native_size"][1]
            for actor in state["presence"]:
                if "x" not in actor or "y" not in actor:
                    continue
                x, y = round(actor["x"] * sx), round(actor["y"] * sy)
                color = COLORS[actor["color"]]
                draw.polygon([(x, y), (x + 3, y + 16), (x + 7, y + 11), (x + 14, y + 11)], fill=color, outline="black")
                draw.text((x + 17, y), actor["label"], fill=color, stroke_width=1, stroke_fill="black")
        output = io.BytesIO()
        picture.convert("RGB").save(output, format="JPEG", quality=70)
        if len(output.getvalue()) > 2 * 1024 * 1024:
            raise ValueError("Capture exceeds image budget; use a smaller width")
        return dict(state, image=base64.b64encode(output.getvalue()).decode(), image_mime="image/jpeg",
                    image_size=list(picture.size), coordinate_space="native",
                    image_to_native_scale=[state["native_size"][0] / picture.width, state["native_size"][1] / picture.height],
                    image_sha256=hashlib.sha256(output.getvalue()).hexdigest(), cursor_overlay=cursor)

    def point(self, x, y, state):
        if type(x) is not int or type(y) is not int or not 0 <= x < state["native_size"][0] or not 0 <= y < state["native_size"][1]:
            raise ValueError("Coordinates must be integers inside the native desktop")
        return [str(x), str(y)]

    def check(self, payload):
        state = self.status()
        if (not state.get("input_allowed") or state.get("generation") != payload.get("expected_generation")
                or state.get("active_window", {}).get("id") != str(payload.get("expected_window"))):
            raise ValueError("Owner input grant, generation or focus changed; no new input")
        if (self.state.root / ("cancel-" + payload["action_id"])).exists():
            raise ValueError("Cancellation requested; inspect receipt before a new action")
        with self.state.db() as db:
            self.state.admit(db, payload["agent_id"], payload.get("reservation_token"))
            if db.execute("SELECT 1 FROM actions WHERE kind='desktop_action' AND status='dispatching' AND id<>? LIMIT 1", (payload["action_id"],)).fetchone():
                raise ValueError("A prior native dispatch remains unresolved; inspect it before new input")
        return state

    def origin(self, payload, state):
        adapter = self.config.desktop.get("origin_adapter")
        if not adapter:
            raise ValueError("Automated credential input is disabled until a trusted origin/focus adapter is configured")
        result = self.run(adapter, input=json.dumps(dict(window=state["active_window"]["id"], generation=state["generation"])).encode(),
                          env=self.environment(), capture_output=True, timeout=3)
        if result.returncode or len(result.stdout) > 8192:
            raise ValueError("Trusted origin adapter unavailable")
        proof = json.loads(result.stdout)
        if proof.get("window") != state["active_window"]["id"] or proof.get("origin") != payload.get("expected_origin"):
            raise ValueError("Trusted origin adapter disagrees with the intended window or HTTPS origin")
        return proof

    def action(self, payload):
        action, agent = identifier(payload["action_id"]), identifier(payload["agent_id"], 48)
        label, color = public_text(payload.get("agent_label", agent), 32), payload.get("cursor_color", "lilac")
        if color not in COLORS:
            raise ValueError("Unsupported cursor color")
        process, dispatched, prepared, dragging = None, False, False, False
        owned_processes = []
        receipt = dict(action_id=action, host=self.config.host, kind=payload["kind"], status="dispatching", effect_verified=False)
        with file_lock(self.state.root / "input.lock", nonblocking=True):
            with self.state.db() as db:
                prior = self.state.existing(db, action, "desktop_action", agent, payload)
                if prior:
                    return prior
            state = self.check(payload)
            kind = payload["kind"]
            if kind in ("move", "click"):
                command = ["mousemove", *self.point(payload.get("x"), payload.get("y"), state)]
                if kind == "click":
                    button = payload.get("button", 1)
                    if type(button) is not int or button not in (1, 2, 3):
                        raise ValueError("Button must be 1, 2 or 3")
                    command += ["click", str(button)]
                commands, secret_context = [(command, None)], nullcontext(None)
            elif kind == "type":
                text = public_text(payload.get("text"), 8000)
                if not text:
                    raise ValueError("Type requires text")
                commands, secret_context = text_commands(text), nullcontext(None)
            elif kind == "key":
                commands, secret_context = [(["key", key_chord(payload.get("key"))], None)], nullcontext(None)
            elif kind == "scroll":
                amount = payload.get("amount")
                if type(amount) is not int or amount == 0 or not -20 <= amount <= 20:
                    raise ValueError("Scroll amount must be -20..20 excluding zero")
                commands, secret_context = [(["click", "--repeat", str(abs(amount)), "--delay", "20", "4" if amount < 0 else "5"], None)], nullcontext(None)
            elif kind == "drag":
                commands = [(["mousemove", *self.point(payload.get("x"), payload.get("y"), state)], None),
                            (["mousedown", "1"], None),
                            (["mousemove", *self.point(payload.get("to_x"), payload.get("to_y"), state)], None),
                            (["mouseup", "1"], None)]
                secret_context = nullcontext(None)
            elif kind == "credential":
                proof = self.origin(payload, state)
                credential_role = proof.get("role")
                secret_context = self.vault.consume(payload.get("credential_ref"), proof["origin"], proof.get("role"))
                commands = []
            else:
                raise ValueError("Use move, click, type, key, scroll, drag or credential")
            with self.state.db() as db:
                self.state.admit(db, agent, payload.get("reservation_token"))
                self.state.begin(db, action, "desktop_action", agent, payload, receipt)
            prepared = True
            try:
                with secret_context as secret:
                    if secret is not None:
                        commands = text_commands(secret.decode())
                    self.state.presence(agent, label, color, "working", {"x": payload["x"], "y": payload["y"]} if "x" in payload else None)
                    deadline = time.monotonic() + 8
                    for argv, input_bytes in commands:
                        fresh = self.check(payload)
                        if kind == "credential":
                            refreshed = self.origin(payload, fresh)
                            if refreshed.get("role") != credential_role:
                                raise ValueError("Focused field role changed; protected input was not dispatched")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Native input budget reached")
                        with atomic_spawn() as restore_child_mask:
                            process = self.popen(["xdotool", *argv], env=self.environment(), stdin=subprocess.PIPE,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                                                 preexec_fn=restore_child_mask)
                            owned_processes.append(process)
                            dispatched = True
                            if argv[:1] == ["mousedown"]:
                                dragging = True
                        first = True
                        while True:
                            try:
                                process.communicate(input=input_bytes if first else None, timeout=min(.05, max(.001, deadline - time.monotonic())))
                                break
                            except subprocess.TimeoutExpired:
                                first = False
                                if time.monotonic() >= deadline or self.state.paused() or (self.state.root / ("cancel-" + action)).exists():
                                    raise TimeoutError("Input paused, cancelled or timed out")
                        if process.returncode:
                            raise RuntimeError("Native input result is uncertain")
                        if input_bytes is not None and not input_bytes.isascii():
                            time.sleep(.04)
                        if argv[:1] == ["mouseup"]:
                            dragging = False
                    receipt["status"] = "input_sent"
                    receipt["ui_effect_verified"] = False
            except BaseException as error:
                receipt.update(status="unknown_outcome" if dispatched else "not_dispatched", error_class=type(error).__name__)
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
            finally:
                # The receipt remains uncertain if cleanup itself cannot be confirmed.
                cleanup_confirmed = True
                for owned in owned_processes:
                    try:
                        stop_input_group(owned)
                    except (OSError, subprocess.TimeoutExpired):
                        cleanup_confirmed = False
                receipt["input_group_cleanup_acknowledged"] = cleanup_confirmed
                if not cleanup_confirmed:
                    receipt["status"] = "unknown_outcome"
                if dragging:
                    try:
                        self.native(["mouseup", "1"])
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        receipt["status"] = "unknown_outcome"
                self.state.presence(agent, label, color, "finished" if receipt["status"] == "input_sent" else "stopped",
                                    {"x": payload["x"], "y": payload["y"]} if "x" in payload else None)
                if prepared:
                    with self.state.db() as db:
                        self.state.finish(db, action, receipt)
        return receipt

    def cancel(self, action_id):
        action_id = identifier(action_id)
        write_json(self.state.root / ("cancel-" + action_id), dict(requested_at=time.time()))
        return dict(action_id=action_id, cancel_requested=True, effect_verified=False,
                    next_step="Read its receipt and a fresh screen; do not replay")
