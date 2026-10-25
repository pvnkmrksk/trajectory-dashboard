"""Bounded, read-only MATREX telemetry receiver and browser event stream."""

from __future__ import annotations

from copy import deepcopy
import importlib
import ipaddress
import json
import math
import re
import threading
import time
from typing import Any

from flask import Response, jsonify, request, send_from_directory

_ENDPOINT = re.compile(r"^tcp://(?:[A-Za-z0-9_.-]+|\[[A-Fa-f0-9:]+\]):([0-9]{1,5})$")


def validate_endpoint(value: str) -> str:
    value = str(value).strip()
    match = _ENDPOINT.fullmatch(value)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise ValueError("Use tcp://hostname:port, for example tcp://127.0.0.1:9880")
    return value


def _finite_tree(value: Any, depth: int = 0) -> bool:
    if depth > 30:
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return len(value) <= 512 and all(
            _finite_tree(v, depth + 1) for v in value.values()
        )
    if isinstance(value, list):
        return len(value) <= 512 and all(_finite_tree(v, depth + 1) for v in value)
    return True


class LiveHub:
    """One socket-owning worker; slow browsers consume a replaceable latest slot."""

    def __init__(self, endpoint: str = "", topic: str = "matrex.telemetry.v2"):
        self.condition = threading.Condition()
        self.lifecycle = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.endpoint = ""
        self.topic = str(topic)
        self.frame = None
        self.context = None
        self.version = 0
        self.received = self.contexts = self.invalid = self.gaps = self.restarts = 0
        self.last_received = self.last_frame_received = self.last_context_received = 0.0
        self.last_sequence = None
        self.error = None
        if endpoint:
            self.connect(endpoint)

    def connect(self, endpoint: str, topic: str | None = None) -> None:
        endpoint = validate_endpoint(endpoint)
        importlib.import_module("zmq")  # Fail before changing an existing connection.
        if topic is not None and (
            not topic or len(topic) > 160 or any(ord(c) < 32 for c in topic)
        ):
            raise ValueError("Telemetry topic must be a nonempty printable string")
        with self.lifecycle:
            self._stop()
            with self.condition:
                self.endpoint, self.topic = endpoint, topic or self.topic
                self.frame = self.context = None
                self.error = None
                self.last_sequence = None
                self.last_received = self.last_frame_received = (
                    self.last_context_received
                ) = 0.0
                self.version += 1
                self.condition.notify_all()
            self.stop_event = threading.Event()
            self.thread = threading.Thread(
                target=self._receive, name="matrex-telemetry", daemon=True
            )
            self.thread.start()

    def _stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError("The previous telemetry receiver has not stopped")
        self.thread = None

    def close(self) -> None:
        with self.lifecycle:
            self._stop()
        with self.condition:
            self.condition.notify_all()

    def ingest(self, topic: str, payload: bytes) -> bool:
        try:
            if len(payload) > 2_000_000:
                raise ValueError("Telemetry packet exceeds 2MB")
            if topic not in {self.topic, self.topic + ".context"}:
                raise ValueError("Unexpected telemetry topic")
            data = json.loads(payload)
            if not isinstance(data, dict) or not _finite_tree(data):
                raise ValueError(
                    "Telemetry must be a bounded JSON object with finite numbers"
                )
            context = (
                topic == self.topic + ".context" or data.get("kind") == "matrex.context"
            )
            if context:
                if (
                    data.get("schemaVersion") != 1
                    or data.get("kind") != "matrex.context"
                ):
                    raise ValueError("Unsupported experiment-context schema")
            elif data.get("schemaVersion") != 2 or not isinstance(
                data.get("rigs"), list
            ):
                raise ValueError("Expected MATREX telemetry schemaVersion 2")
            with self.condition:
                if context:
                    self.context = data
                    self.contexts += 1
                    self.last_context_received = time.monotonic()
                else:
                    sequence = data.get("sequence")
                    if isinstance(sequence, int) and not isinstance(sequence, bool):
                        if self.last_sequence is not None:
                            if sequence < self.last_sequence:
                                self.restarts += 1
                            elif sequence > self.last_sequence + 1:
                                self.gaps += sequence - self.last_sequence - 1
                        self.last_sequence = sequence
                    self.frame = data
                    self.received += 1
                    self.last_frame_received = time.monotonic()
                self.last_received = time.monotonic()
                self.error = None
                self.version += 1
                self.condition.notify_all()
            return True
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as exc:
            with self.condition:
                self.invalid += 1
                self.error = str(exc)
            return False

    def _receive(self) -> None:
        zmq = importlib.import_module("zmq")
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        try:
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.RCVHWM, 8)
            socket.setsockopt(zmq.MAXMSGSIZE, 2_000_000)
            socket.setsockopt_string(zmq.SUBSCRIBE, self.topic)
            socket.connect(self.endpoint)
            while not self.stop_event.is_set():
                if not socket.poll(200):
                    continue
                frames = socket.recv_multipart()
                if len(frames) == 2:
                    self.ingest(frames[0].decode("utf-8", errors="replace"), frames[1])
                elif len(frames) == 1:
                    self.ingest(self.topic, frames[0])
                else:
                    with self.condition:
                        self.invalid += 1
                        self.error = "Expected topic + JSON frames"
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
                self.version += 1
                self.condition.notify_all()
        finally:
            socket.close(linger=0)
            context.term()

    def snapshot(self) -> dict[str, Any]:
        with self.condition:
            age = time.monotonic() - self.last_received if self.last_received else None
            state = (
                "error"
                if self.error
                else "disconnected"
                if not self.endpoint
                else "waiting"
                if age is None
                else "stale"
                if age > 3
                else "receiving"
            )
            return deepcopy(
                {
                    "version": self.version,
                    "receiver": {
                        "state": state,
                        "endpoint": self.endpoint,
                        "topic": self.topic,
                        "ageSeconds": age,
                        "snapshotAgeSeconds": time.monotonic()
                        - self.last_frame_received
                        if self.last_frame_received
                        else None,
                        "contextAgeSeconds": time.monotonic()
                        - self.last_context_received
                        if self.last_context_received
                        else None,
                        "snapshots": self.received,
                        "contexts": self.contexts,
                        "invalidPackets": self.invalid,
                        "sequenceGaps": self.gaps,
                        "publisherRestarts": self.restarts,
                        "error": self.error,
                    },
                    "context": self.context,
                    "telemetry": self.frame,
                }
            )

    def events(self):
        previous = -1
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.version != previous, timeout=2)
            data = self.snapshot()
            previous = data["version"]
            yield (
                "event: snapshot\ndata: "
                + json.dumps(data, separators=(",", ":"), allow_nan=False)
                + "\n\n"
            )
            time.sleep(
                0.1
            )  # Max10 browser updates/s, independent of acquisition/logging.


def install_live_routes(
    app,
    static_dir,
    endpoint: str = "",
    topic: str = "matrex.telemetry.v2",
    live_only: bool = False,
):
    hub = LiveHub(endpoint, topic)
    app.extensions["matrex_live"] = hub
    app.config["LIVE_ONLY"] = bool(live_only)

    def local_operator() -> bool:
        try:
            return ipaddress.ip_address(request.remote_addr or "").is_loopback
        except ValueError:
            return False

    @app.before_request
    def live_only_boundary():
        if live_only and request.path in {
            "/api/load",
            "/api/resolve-drop",
            "/native-config.json",
        }:
            return jsonify(
                {
                    "error": "This server is a read-only live viewer. Use a local analysis server to open recorded files."
                }
            ), 403

    @app.get("/live/")
    def live_index():
        return send_from_directory(static_dir, "live.html")

    @app.get("/api/live/snapshot")
    def live_snapshot():
        data = hub.snapshot()
        data["controlAvailable"] = local_operator()
        response = jsonify(data)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/live/connect")
    def live_connect():
        if not local_operator():
            return jsonify(
                {
                    "error": "Only the operator on the server computer can change the telemetry connection."
                }
            ), 403
        data = request.get_json(silent=True) or {}
        try:
            hub.connect(
                str(data.get("endpoint") or ""),
                str(data.get("topic") or "matrex.telemetry.v2"),
            )
        except (ValueError, RuntimeError, ImportError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "endpoint": hub.endpoint})

    @app.get("/api/live/stream")
    def live_stream():
        response = Response(hub.events(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-store, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return hub
