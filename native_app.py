"""Run the browser-native trajectory dashboard and optional live MATREX viewer."""

from __future__ import annotations
import argparse
import atexit
import logging
import sys
import threading
import webbrowser
from native_dashboard import create_native_app


def _args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--glob", default="", help="CSV file, folder, or recursive glob"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8060, type=int)
    parser.add_argument(
        "--telemetry",
        default="",
        help="MATREX ZMQ PUB endpoint, e.g. tcp://127.0.0.1:9880",
    )
    parser.add_argument("--telemetry-topic", default="matrex.telemetry.v2")
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Serve a read-only live viewer; disable recorded-file loading",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the local browser automatically",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Suppress automatic browser opening in standalone packages",
    )
    parser.add_argument("--replay-token", default="", help="Local recorded replay owner nonce; separate from live telemetry")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between1 and65535")
    return args


def main():
    args = _args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        app = create_native_app(
            args.glob,
            telemetry=args.telemetry,
            telemetry_topic=args.telemetry_topic,
            live_only=args.live_only,
            replay_token=args.replay_token,
        )
    except (ValueError, ImportError) as error:
        raise SystemExit(str(error))
    atexit.register(app.extensions["matrex_live"].close)
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{host}:{args.port}/" + (
        "live/" if args.live_only or args.telemetry else ""
    )
    print("Daari Deepa — " + url, flush=True)
    if (args.open_browser or getattr(sys, "frozen", False)) and not args.no_browser:
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    if args.debug:
        app.run(
            host=args.host,
            port=args.port,
            debug=True,
            threaded=True,
            use_reloader=False,
        )
    else:
        from waitress import serve

        serve(app, host=args.host, port=args.port, threads=12)


if __name__ == "__main__":
    main()
