"""Local Flask server for the browser-native dashboard."""

from __future__ import annotations

import gzip
import logging
import os
from pathlib import Path
import time
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from .dataset import NativeDatasetError, load_native_dataset


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


def _normalise_drop_manifest(folder: str, files: list[Any]) -> dict[str, int | None]:
    manifest: dict[str, int | None] = {}
    for item in files:
        raw_path = item.get("path") if isinstance(item, dict) else item
        raw_size = item.get("size") if isinstance(item, dict) else None
        path = str(raw_path or "").replace("\\", "/").lstrip("/")
        if not path.lower().endswith(".csv"):
            continue
        parts = [part for part in path.split("/") if part not in ("", ".")]
        if parts and parts[0] == folder:
            parts = parts[1:]
        if not parts or any(part == ".." for part in parts):
            continue
        manifest["/".join(parts)] = int(raw_size) if raw_size is not None else None
    return manifest


def _candidate_manifest(folder: str) -> dict[str, int]:
    root = Path(folder)
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*.csv"))
        if path.is_file()
    }


def _resolve_dropped_folder(folder: str, files: list[Any]) -> str | None:
    """Resolve a dropped folder by its complete relative CSV manifest.

    Browsers hide absolute paths. Matching every relative CSV path (and file
    size when available) prevents a same-named sibling folder from being chosen
    merely because one filename happens to match.
    """

    folder = os.path.basename(str(folder or "").strip())
    expected = _normalise_drop_manifest(folder, files)
    if not folder or not expected:
        return None
    visited = 0
    matches: list[str] = []
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
            actual = _candidate_manifest(dirpath)
            if set(actual) != set(expected):
                continue
            if any(size is not None and actual[path] != size
                   for path, size in expected.items()):
                continue
            resolved = os.path.realpath(dirpath)
            if resolved not in matches:
                matches.append(resolved)
    if len(matches) != 1:
        return None
    resolved = matches[0]
    return (os.path.relpath(resolved, os.getcwd())
            if resolved.startswith(os.path.realpath(os.getcwd()) + os.sep)
            else resolved)


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

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "renderer": "browser-native", "plotly": False})

    return app
