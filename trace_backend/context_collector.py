"""Local context collection: active app, window title, selection, terminal output.

Everything here is best-effort and never raises: missing OS APIs simply
yield ``None`` fields. Clipboard is read ONLY when explicitly authorized
(``allow_clipboard=True``).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass
class LocalContext:
    app_name: str | None = None
    window_title: str | None = None
    editor_selection: str | None = None
    clipboard: str | None = None
    terminal_output: str | None = None
    cwd: str | None = None
    project_hint: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = []
        if self.app_name:
            lines.append(f"Active app: {self.app_name}")
        if self.window_title:
            lines.append(f"Window title: {self.window_title}")
        if self.cwd:
            lines.append(f"CWD: {self.cwd}")
        if self.editor_selection:
            lines.append(f"Editor selection ({len(self.editor_selection)} chars) included.")
        if self.terminal_output:
            lines.append(f"Terminal output ({len(self.terminal_output)} chars) included.")
        if self.clipboard:
            lines.append(f"Clipboard ({len(self.clipboard)} chars) included (authorized).")
        return "\n".join(lines) if lines else "No local context available."


def _foreground_window_win() -> tuple[str | None, str | None]:
    """Return (app/exe name, window title) on Windows, else (None, None)."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None, None
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, None
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or None
        # Process exe name best-effort.
        exe = None
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi if hasattr(ctypes.windll, "psapi") else None
            hproc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if hproc:
                try:
                    name_buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, name_buf, ctypes.byref(size)):
                        exe = os.path.basename(name_buf.value) or None
                finally:
                    kernel32.CloseHandle(hproc)
        except Exception:
            pass
        return exe, title
    except Exception:
        return None, None


def _read_clipboard() -> str | None:
    """Read clipboard via tkinter (stdlib). Returns None on any failure."""
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
        finally:
            try:
                root.destroy()
            except Exception:
                pass
        return text[:8000] if text else None
    except Exception:
        return None


def collect_local_context(*,
                          editor_selection: str | None = None,
                          terminal_output: str | None = None,
                          allow_clipboard: bool = False,
                          project_hint: str | None = None,
                          max_chars: int = 8000) -> LocalContext:
    app, title = _foreground_window_win()
    clipboard = _read_clipboard() if allow_clipboard else None
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    if editor_selection and len(editor_selection) > max_chars:
        editor_selection = editor_selection[:max_chars] + "\n…[truncated]"
    if terminal_output and len(terminal_output) > max_chars:
        # Keep the tail: errors usually appear at the end.
        terminal_output = "…[truncated]\n" + terminal_output[-max_chars:]
    return LocalContext(
        app_name=app, window_title=title,
        editor_selection=editor_selection,
        clipboard=clipboard,
        terminal_output=terminal_output,
        cwd=cwd, project_hint=project_hint,
    )
