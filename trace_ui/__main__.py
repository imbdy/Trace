"""Allow ``python -m trace_ui`` to start the desktop companion."""

from .app import TraceApp

if __name__ == "__main__":
    TraceApp().start()
