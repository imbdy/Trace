"""Screenshot service: capture active window or full screen.

Backends tried in order: ``mss`` → ``PIL.ImageGrab``. No hard dependency;
when neither is installed a clear ScreenshotCaptureError is raised and the
investigation continues with code/diagnostics only.

Captures are kept in memory (PNG bytes) with an optional temp file that the
caller MUST dispose via :func:`dispose` or the :func:`temporary_capture`
context manager. Files are never retained indefinitely.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import tempfile
import time
from dataclasses import dataclass


class ScreenshotCaptureError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class ScreenshotResult:
    width: int
    height: int
    source: str  # "mss-active" | "mss-full" | "pillow" | ...
    png_bytes: bytes
    temp_path: str | None = None
    captured_at: float = 0.0
    note: str = ""

    def to_base64(self) -> str:
        return base64.b64encode(self.png_bytes).decode("ascii")

    def size(self) -> int:
        return len(self.png_bytes)


def _downscale_png(png_bytes: bytes, max_dim: int) -> tuple[bytes, int, int]:
    """Downscale PNG bytes if Pillow is available; else return as-is."""
    try:
        from PIL import Image
    except ImportError:
        return png_bytes, -1, -1
    try:
        img = Image.open(io.BytesIO(png_bytes))
        w, h = img.size
        scale = min(1.0, max_dim / max(w, h)) if max(w, h) > 0 else 1.0
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), img.size[0], img.size[1]
        return png_bytes, w, h
    except Exception:
        return png_bytes, -1, -1


def _grab_mss(full: bool) -> tuple[bytes, int, int, str]:
    import mss  # type: ignore

    with mss.mss() as sct:
        monitor = sct.monitors[0] if full else _pick_active_mss_monitor(sct)
        shot = sct.grab(monitor)
        w, h = shot.width, shot.height
        # Convert raw BGRA to PNG (Pillow preferred, else raw portable fallback).
        try:
            from PIL import Image
            img = Image.frombytes("RGB", (w, h), shot.rgb)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), w, h, ("mss-full" if full else "mss-active")
        except ImportError:
            # Minimal PNG encoding without Pillow is complex; raise so the
            # Pillow/ImageGrab path can be attempted instead.
            raise ScreenshotCaptureError(
                "mss captured pixels but Pillow is missing for PNG encoding.",
                hint="pip install pillow (and mss) for screenshot support.",
            )


def _pick_active_mss_monitor(sct):
    """Best effort: monitor containing the mouse cursor; fallback: primary."""
    try:
        import ctypes

        class _Pt(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _Pt()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            for mon in sct.monitors[1:]:
                if (mon["left"] <= pt.x < mon["left"] + mon["width"]
                        and mon["top"] <= pt.y < mon["top"] + mon["height"]):
                    return mon
    except Exception:
        pass
    monitors = sct.monitors
    return monitors[1] if len(monitors) > 1 else monitors[0]


def _grab_pillow() -> tuple[bytes, int, int, str]:
    try:
        from PIL import ImageGrab
    except ImportError as e:
        raise ScreenshotCaptureError(
            "No screenshot backend available (tried mss, Pillow).",
            hint="Install screenshot support with: pip install mss pillow",
        ) from e
    try:
        img = ImageGrab.grab(all_screens=True)
    except TypeError:
        img = ImageGrab.grab()  # older Pillow without all_screens
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    w, h = img.size
    return buf.getvalue(), w, h, "pillow-full"


def capture_screenshot(mode: str = "active",
                       max_dim: int = 1280,
                       write_temp_file: bool = True) -> ScreenshotResult:
    """Capture a screenshot. ``mode`` is ``active`` or ``full``.

    Active-window capture is best-effort: on multi-monitor Windows setups we
    capture the monitor holding the cursor (avoiding Trace's own window where
    practical is the caller's job — e.g. minimise Trace before capture).
    Raises ScreenshotCaptureError on failure (caller should continue without
    visual evidence).
    """
    if mode not in ("active", "full"):
        raise ValueError("mode must be 'active' or 'full'")
    full = mode == "full"
    png: bytes | None = None
    w = h = -1
    source = ""
    errors: list[str] = []
    for grabber in (_grab_mss_with_flag(full), _grab_pillow_fn()):
        try:
            png, w, h, source = grabber()
            break
        except ScreenshotCaptureError as e:
            errors.append(str(e))
        except ImportError as e:
            errors.append(f"backend missing: {e}")
        except Exception as e:  # never crash the pipeline on capture failure
            errors.append(f"backend failed: {e}")
    if png is None:
        raise ScreenshotCaptureError(
            "Screenshot capture failed: " + ("; ".join(errors) or "no backend"),
            hint="pip install mss pillow. Investigations continue without "
                 "screenshots when capture is unavailable.",
        )
    png, sw, sh = _downscale_png(png, max_dim)
    if sw > 0:
        w, h = sw, sh
    temp_path: str | None = None
    if write_temp_file:
        fd, temp_path = tempfile.mkstemp(prefix="trace-shot-", suffix=".png")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(png)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            temp_path = None
    return ScreenshotResult(width=w, height=h, source=source,
                            png_bytes=png, temp_path=temp_path,
                            captured_at=time.time())


def _grab_mss_with_flag(full: bool):
    def _run():
        return _grab_mss(full)
    return _run


def _grab_pillow_fn():
    def _run():
        return _grab_pillow()
    return _run


def dispose(result: ScreenshotResult | None) -> None:
    """Delete the temp file (if any) and drop image bytes from memory."""
    if result is None:
        return
    if result.temp_path:
        try:
            os.unlink(result.temp_path)
        except OSError:
            pass
        result.temp_path = None
    result.png_bytes = b""


@contextlib.contextmanager
def temporary_capture(mode: str = "active", max_dim: int = 1280):
    """Context manager that auto-deletes the temp screenshot file."""
    result = capture_screenshot(mode=mode, max_dim=max_dim, write_temp_file=True)
    try:
        yield result
    finally:
        dispose(result)
