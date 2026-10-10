"""Trace launcher: start the independent debugging backend (loopback only)."""

from trace_backend.config import TraceConfig
from trace_backend.server import run_server


def main() -> None:
    config = TraceConfig.from_env()
    problems = config.validate()
    if problems:
        raise SystemExit("Invalid Trace configuration: " + "; ".join(problems))
    run_server(config)


if __name__ == "__main__":
    main()
