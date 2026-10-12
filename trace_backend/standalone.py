"""Packaged Trace backend entrypoint used by the Electron installer."""

from __future__ import annotations

import argparse
import dataclasses

from .config import TraceConfig
from .server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="trace-backend")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    config = TraceConfig.from_env()
    if args.port:
        config = dataclasses.replace(config, bind_port=args.port)
    run_server(config)


if __name__ == "__main__":
    main()
