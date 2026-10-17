"""Trace window + process identity: custom icon everywhere, best-effort.

Covers the Tk desktop UI (hidden root, companion, results) AND the
background process on Windows:

- every Tk window gets the Trace PNG (iconphoto) with ICO fallback,
- the process gets an explicit AppUserModelID so taskbar grouping shows
  "Trace" instead of "python.exe",
- the console window (``python main.py`` / ``--backend-only``) gets the
  Trace title + Trace ICO via WM_SETICON, so the background console also
  carries the icon.

Everything is best-effort and never raises: missing assets, headless
environments, or non-Windows platforms simply keep the old behavior.
Stdlib only; tkinter is imported lazily so headless tests stay green.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ID = "Trace.Trace.0.1"
APP_TITLE = "Trace"
BACKEND_TITLE = "Trace Backend"

_ICON_DIR: Path | None = None


def icon_dir() -> Path:
    """Repo ``assets/icons`` directory (works from source checkout)."""
    global _ICON_DIR
    if _ICON_DIR is None:
        _ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
    return _ICON_DIR


def png_path() -> Path | None:
    for name in ("trace.png", "trace-256.png", "trace-512.png",
                 "trace-48.png", "trace-32.png"):
        p = icon_dir() / name
        if p.is_file():
            return p
    return None


def ico_path() -> Path | None:
    p = icon_dir() / "trace.ico"
    return p if p.is_file() else None


def init_process_identity(title: str = APP_TITLE) -> None:
    """Set AppUserModelID + console title + console icon (Windows only).

    Call once at startup — from the Tk app AND from backend-only mode —
    so even the background process carries the Trace identity.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
        _set_console_icon()
    except Exception:
        pass


def _set_console_icon() -> None:
    """Apply trace.ico to the owning console window. Best-effort."""
    try:
        import ctypes
    except Exception:
        return
    try:
        ico = ico_path()
        if ico is None:
            return
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        # IMAGE_ICON=1, LR_LOADFROMFILE=0x10, LR_DEFAULTSIZE=0x40
        hicon = user32.LoadImageW(None, str(ico), 1, 0, 0, 0x10 | 0x40)
        if not hicon:
            return
        WM_SETICON = 0x80
        ICON_SMALL, ICON_BIG = 0, 1
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception:
        pass


def apply_window_icon(win) -> bool:
    """Apply the Trace icon to a Tk window. Returns True on success."""
    if win is None:
        return False
    # Preferred: PNG via iconphoto (crisp, works on all platforms).
    try:
        png = png_path()
        if png is not None:
            import tkinter as tk
            img = tk.PhotoImage(file=str(png))
            # keep a reference: Tk drops icons garbage-collected by Python
            try:
                win.tk.call("wm", "iconphoto", win._w, img)
            except Exception:
                win.iconphoto(True, img)  # type: ignore[attr-defined]
            cache = getattr(win, "_trace_icon_refs", None)
            if cache is None:
                cache = []
                win._trace_icon_refs = cache  # type: ignore[attr-defined]
            cache.append(img)
            return True
    except Exception:
        pass
    # Fallback: ICO bitmap (Windows taskbar/tray grouping).
    try:
        ico = ico_path()
        if ico is not None:
            win.iconbitmap(str(ico))
            return True
    except Exception:
        pass
    return False
