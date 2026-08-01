"""Run the Plotly-free browser-native trajectory dashboard."""

from __future__ import annotations

import argparse
import logging

from native_dashboard import create_native_app


def _args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glob", default="", help="CSV file, folder, or recursive glob")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8060, type=int)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


if __name__ == "__main__":
    args = _args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    application = create_native_app(args.glob)
    application.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        use_reloader=args.debug,
    )
