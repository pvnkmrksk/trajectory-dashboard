"""Local Flask server for the browser-native dashboard."""

from __future__ import annotations

import gzip
import glob
import logging
import os
from pathlib import Path
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from .dataset import NativeDatasetError, load_native_dataset, raw_channel_binary


LOGGER = logging.getLogger("trajectory.native")
STATIC_DIR = Path(__file__).with_name("static")
_DROP_PRUNE = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".cache", "Library", ".Trash",
}


def _drop_search_roots() -> list[str]:
    cwd = os.path.abspath(os.getcwd())
    roots = [cwd, os.path.dirname(cwd), os.path.dirname(os.path.dirname(cwd))]
    configured = os.environ.get("TRAJ_DATA_ROOT")
    if configured:
        roots.insert(0, os.path.abspath(os.path.expanduser(configured)))
    result: list[str] = []
    for root in roots:
        if root not in result and os.path.isdir(root):
            result.append(root)
    return result


def _resolve_dropped_folder(folder: str, files: list[str]) -> str | None:
    """Resolve the browser-visible relative names to a bounded local glob."""

    csv_files = [
        str(path).replace("\\", "/").lstrip("/")
        for path in files
        if str(path).lower().endswith(".csv")
    ]
    folder = os.path.basename(str(folder or "").strip())
    if not folder or not csv_files:
        return None
    names = [path.rsplit("/", 1)[-1] for path in csv_files]
    sample = csv_files[0].split("/", 1)[1] if "/" in csv_files[0] else names[0]
    visited = 0
    for root in _drop_search_roots():
        root_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, _ in os.walk(root):
            visited += 1
            if visited > 120_000:
                return None
            if dirpath.count(os.sep) - root_depth >= 8:
                dirnames[:] = []
                continue
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and name not in _DROP_PRUNE
            ]
            if os.path.basename(dirpath) != folder:
                continue
            if not os.path.isfile(os.path.join(dirpath, sample)):
                continue
            suffix = "*_VR*.csv" if any("_VR" in name for name in names) else "*.csv"
            pattern = os.path.join(dirpath, "**", suffix)
            if not glob.glob(pattern, recursive=True):
                pattern = os.path.join(dirpath, "**", "*.csv")
            return os.path.relpath(pattern, os.getcwd()) if pattern.startswith(os.getcwd() + os.sep) else pattern
    return None


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

    @app.post("/api/resolve-drop")
    def api_resolve_drop():
        payload = request.get_json(silent=True) or {}
        pattern = _resolve_dropped_folder(
            str(payload.get("folder") or ""),
            list(payload.get("files") or []),
        )
        if not pattern:
            return jsonify({
                "error": "The dropped folder could not be located on this computer. "
                         "Set TRAJ_DATA_ROOT when the data lives outside the project tree."
            }), 404
        return jsonify({"source": pattern})

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
