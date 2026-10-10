"""Trace launcher: desktop companion (default) or backend-only server.

- ``python main.py`` → starts the loopback backend (in-process) + desktop
  companion + results window.
- ``python main.py --backend-only`` → previous behavior: backend server only.
- ``python -m trace_backend`` → backend server only.
- ``python -m trace_ui`` → companion UI only (expects a backend already).
"""

import argparse

from trace_backend.config import TraceConfig
from trace_backend.server import run_server


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="trace",
                                     description="Trace debugging companion")
    parser.add_argument("--backend-only", action="store_true",
                        help="run only the loopback backend server")
    parser.add_argument("--port", type=int, default=None,
                        help="backend port (default from env/TRACE_BIND_PORT)")
    parser.add_argument("--project-dir", default=None,
                        help="default project folder for investigations")
    args = parser.parse_args(argv)

    config = TraceConfig.from_env()
    if args.port:
        import dataclasses
        config = dataclasses.replace(config, bind_port=args.port)
    problems = config.validate()
    if problems:
        raise SystemExit("Invalid Trace configuration: " + "; ".join(problems))

    if args.backend_only:
        run_server(config)
        return

    from trace_ui.app import TraceApp
    base_url = f"http://{config.bind_host}:{config.bind_port}/api/v1"
    TraceApp(base_url=base_url, project_dir=args.project_dir).start()


if __name__ == "__main__":
    main()
