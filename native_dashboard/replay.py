"""Loopback-only recorded replay clock; independent from acquisition telemetry."""
from __future__ import annotations
from collections import deque
import hashlib
import hmac
import math
from pathlib import Path
import re
import threading
import time
from flask import jsonify, request, send_from_directory

PROTOCOL = "matrex.replay.v1"


class ReplayHub:
    def __init__(self, token: str):
        if token and not re.fullmatch(r"[0-9a-f]{32,64}", token):
            raise ValueError("Replay owner token must be 32–64 lowercase hex digits")
        self.token = token
        self.lock = threading.RLock()
        self.state = None
        self.updated = 0.0
        self.commands = deque(maxlen=64)
        self.ids = deque(maxlen=128)
        self.sequence = 0

    def snapshot(self):
        with self.lock:
            return {"protocol": PROTOCOL, "version": 1,
                    "state": dict(self.state) if self.state else None,
                    "connected": bool(self.state) and time.monotonic() - self.updated < 3,
                    "ageSeconds": max(0, time.monotonic() - self.updated) if self.state else None}

    def publish(self, payload):
        session = str(payload.get("sessionId") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", session):
            raise ValueError("A replay session identity is required")
        directory = str(Path(str(payload.get("runDirectory") or "")).resolve())
        if not Path(directory).is_dir():
            raise ValueError("Replay run directory does not exist")
        origin = str(payload.get("originTicks") or "")
        if not origin.isdecimal() or int(origin) <= 0:
            raise ValueError("Explicit recorded wall-clock origin ticks are required")
        seconds, duration, rate = (float(payload.get(key, 0)) for key in ("seconds", "duration", "rate"))
        if not all(map(math.isfinite, (seconds, duration, rate))) or seconds < 0 or duration < 0 or seconds > duration + .001 or not 0 < rate <= 64:
            raise ValueError("Invalid replay clock")
        revision = int(payload.get("revision", -1))
        ack = int(payload.get("ackCommand", 0))
        with self.lock:
            if self.state and self.state["sessionId"] == session:
                if revision < self.state["revision"]:
                    raise ValueError("Replay clock revision moved backwards")
                if directory != self.state["runDirectory"] or origin != self.state["originTicks"]:
                    raise ValueError("A new run/origin requires a new replay session")
            else:
                self.commands.clear(); self.ids.clear()
            self.state = {"sessionId": session, "runDirectory": directory,
                          "runName": Path(directory).name, "originTicks": origin,
                          "seconds": seconds, "duration": duration, "rate": rate,
                          "playing": bool(payload.get("playing")), "revision": revision,
                          "ackCommand": ack}
            self.updated = time.monotonic()
            while self.commands and self.commands[0]["sequence"] <= ack:
                self.commands.popleft()
            return {"protocol": PROTOCOL, "commands": list(self.commands)}

    def command(self, payload):
        with self.lock:
            if not self.state or time.monotonic() - self.updated >= 3:
                raise ValueError("Unity replay is disconnected; no command was queued")
            if payload.get("sessionId") != self.state["sessionId"]:
                raise ValueError("Replay session changed; refresh the linked view")
            kind = payload.get("kind")
            if kind not in {"seek", "play", "pause"}:
                raise ValueError("Unknown replay command")
            request_id = str(payload.get("requestId") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", request_id):
                raise ValueError("A bounded replay request identity is required")
            if request_id in self.ids:
                return {"queued": True, "duplicate": True}
            seconds = float(payload.get("seconds", self.state["seconds"]))
            if not math.isfinite(seconds) or not 0 <= seconds <= self.state["duration"]:
                raise ValueError("Seek must stay within the recorded run")
            self.sequence += 1
            # Scrubbing coalesces unsent seeks rather than creating an unbounded queue.
            if kind == "seek":
                self.commands = deque((item for item in self.commands if item["kind"] != "seek"), maxlen=64)
            self.commands.append({"sequence": self.sequence, "sessionId": self.state["sessionId"],
                                  "requestId": request_id, "kind": kind, "seconds": seconds})
            self.ids.append(request_id)
            return {"queued": True, "sequence": self.sequence}


def install_replay_routes(app, static_dir, token, binary_response, loader):
    hub = ReplayHub(token)
    app.extensions["matrex_replay"] = hub

    def authorized():
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            return jsonify({"error": "Recorded replay control is loopback only"}), 403
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify({"error": "Cross-origin replay control is disabled"}), 403
        supplied = request.headers.get("X-Matrex-Replay-Token", "")
        if not hub.token or not hmac.compare_digest(supplied, hub.token):
            return jsonify({"error": "This viewer is not the requested replay owner"}), 403
        return None

    @app.get("/api/replay/capabilities")
    def replay_capabilities():
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            return jsonify({"error": "Loopback only"}), 403
        return jsonify({"protocol": PROTOCOL, "version": 1, "enabled": bool(hub.token),
                        "instanceId": hashlib.sha256(hub.token.encode()).hexdigest()[:16] if hub.token else None})

    @app.get("/replay/")
    def replay_index():
        return send_from_directory(static_dir, "replay.html")

    @app.get("/api/replay/snapshot")
    def replay_snapshot():
        error = authorized()
        return error if error else jsonify(hub.snapshot())

    @app.post("/api/replay/state")
    def replay_state():
        error = authorized()
        if error: return error
        # Only the native owner posts the authoritative clock; browsers send commands.
        if request.headers.get("Origin"):
            return jsonify({"error": "Only the native replay owner may publish its clock"}), 403
        try: return jsonify(hub.publish(request.get_json(silent=True) or {}))
        except (ValueError, TypeError, OverflowError) as exc: return jsonify({"error": str(exc)}), 400

    @app.post("/api/replay/command")
    def replay_command():
        error = authorized()
        if error: return error
        try: return jsonify(hub.command(request.get_json(silent=True) or {}))
        except (ValueError, TypeError, OverflowError) as exc: return jsonify({"error": str(exc)}), 409

    @app.get("/api/replay/dataset")
    def replay_dataset():
        error = authorized()
        if error: return error
        with hub.lock:
            state = dict(hub.state) if hub.state else None
        if not state or request.args.get("sessionId") != state["sessionId"]:
            return jsonify({"error": "No matching Unity replay session"}), 409
        try:
            dataset = loader(state["runDirectory"])
            return binary_response(dataset.binary)
        except Exception as exc:
            app.logger.exception("Recorded replay dataset failed")
            return jsonify({"error": str(exc)}), 400
