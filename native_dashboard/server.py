"""Local Flask server for the browser-native dashboard."""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from .dataset import NativeDatasetError, load_native_dataset, raw_channel_binary


LOGGER = logging.getLogger("trajectory.native")
STATIC_DIR = Path(__file__).with_name("static")


def _binary_response(payload: bytes) -> Response:
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    body = gzip.compress(payload, compresslevel=3) if accepts_gzip else payload
    response = Response(body, mimetype="application/vnd.trajectory-dashboard")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if accepts_gzip:
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
    return response


def create_native_app(default_source: str = "") -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["NATIVE_DEFAULT_SOURCE"] = str(default_source or "")

    @app.after_request
    def browser_headers(response):
        # These permit future SharedArrayBuffer use while keeping the current
        # transferable-worker design safe and entirely same-origin.
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Embedder-Policy", "require-corp")
        return response

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/native-config.json")
    def config():
        return jsonify({"defaultSource": app.config["NATIVE_DEFAULT_SOURCE"]})

    @app.get("/static/<path:filename>")
    def static_file(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    @app.post("/api/load")
    def api_load():
        payload = request.get_json(silent=True) or {}
        pattern = str(payload.get("source") or "").strip()
        started = time.perf_counter()
        try:
            dataset = load_native_dataset(pattern)
        except NativeDatasetError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # terminal gets the diagnostic, UI gets clarity
            LOGGER.exception("native.load_failed source=%r", pattern)
            return jsonify({"error": f"Loading failed: {exc}"}), 500
        LOGGER.info(
            "native.load_ready source=%r rows=%d bytes=%d seconds=%.3f",
            pattern,
            dataset.header["counts"]["retainedRows"],
            len(dataset.binary),
            time.perf_counter() - started,
        )
        response = _binary_response(dataset.binary)
        response.headers["X-Dataset-Id"] = dataset.dataset_id
        return response

    @app.get("/api/channel/<dataset_id>")
    def api_channel(dataset_id: str):
        column = str(request.args.get("name") or "")
        try:
            return _binary_response(raw_channel_binary(dataset_id, column))
        except NativeDatasetError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "renderer": "browser-native", "plotly": False})

    return app
