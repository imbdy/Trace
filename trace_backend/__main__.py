"""Allow ``python -m trace_backend`` to start the server."""

from .server import run_server

if __name__ == "__main__":
    run_server()
