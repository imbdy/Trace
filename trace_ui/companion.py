"""Top-center desktop companion: borderless bar with the Trace character.

Faithful to the established design: black horizontal rounded body hanging
from the top screen edge, face integrated into the body, mouse-following
eyes, spring motion, no three-dot indicator. Top-left controls mirror the
reference (home / message / new). Behavior: always-on-top, never steals
focus (WS_EX_NOACTIVATE), pinned top-center across resolution changes,
fold/restore, input panel with send + keyboard submit + multiline.
"""

from __future__ import annotations

import time
import tkinter as tk

from .character import CharacterState, stage_label

BG_KEY = "#ff00ff"  # transparent color key
BODY = "#0b0b0e"
BODY_EDGE = "#232329"
FACE = "#f5f5f7"
EYE = "#0b0b0e"
MUTED = "#8e8e96"
TEXT_FG = "#ececf1"
TEAL = "#4fd1c5"
GREEN = "#43d17c"
RED = "#e5484d"
AMBER = "#e8a13c"
PANEL_BG = "#141419"

BODY_W = 880
BODY_H = 150
FOLDED_W = 320
FOLDED_H = 64
PAD = 10
PANEL_H = 176
GAP = 8


def _cursor_pos() -> tuple[int, int]:
    try:
        import ctypes

        class _Pt(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _Pt()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        pass
    return -10_000, -10_000  # off-screen: eyes rest centered


def _screen_size(root: tk.Tk) -> tuple[int, int]:
    try:
        return int(root.winfo_screenwidth()), int(root.winfo_screenheight())
    except Exception:
        return 1920, 1080


def _apply_noactivate(root: tk.Tk) -> None:
    """WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW so showing/focusing never steals
    the user's editor focus. Best-effort: failure degrades to normal focus."""
    try:
        import ctypes
        hwnd = root.winfo_id()
        if not ctypes.windll.user32.IsWindow(hwnd):
            parent = ctypes.windll.user32.GetParent(hwnd)
            if parent:
                hwnd = parent
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def _rounded(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r,
           x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r,
           x1, y1, x1 + r, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Companion:
    """Borderless top-center character window + expandable input panel."""

    def __init__(self, root: tk.Tk, *,
                 on_submit=None, on_cancel=None, on_close=None):
        self.root = root
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.on_close_cb = on_close
        self.face = CharacterState()
        self.folded = False
        self.panel_open = False
        self.running = False
        self.progress = 0.0
        self.state_color = TEAL
        self.stage_text = "Idle — click the face or + to ask Trace"
        self._last_frame = time.monotonic()
        self._last_screen = (0, 0)
        self._screen_poll = 0.0
        self._destroyed = False
        self._was_folded = False

        try:  # per-monitor DPI awareness; ignore failures
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG_KEY)
        try:
            root.attributes("-transparentcolor", BG_KEY)
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", self.close)
        _apply_noactivate(root)

        self.W, self.H = BODY_W + 2 * PAD, BODY_H + 2 * PAD
        self.canvas = tk.Canvas(root, width=self.W, height=self.H,
                                bg=BG_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._build_scene()
        self._build_panel_widgets()
        self._recenter(force=True)

        self.root.after(33, self._tick)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._hover)

    # -- scene --------------------------------------------------------------
    def _build_scene(self):
        c = self.canvas
        x0, y0 = PAD, PAD
        x1, y1 = PAD + BODY_W, PAD + BODY_H
        self._body = _rounded(c, x0, y0, x1, y1, 34, fill=BODY,
                              outline=BODY_EDGE, width=2)
        # halo: layered dark outlines suggesting the reference glow
        self._halos = [
            c.create_oval(0, 0, 1, 1, outline="#1c1c22", width=6),
            c.create_oval(0, 0, 1, 1, outline="#26262e", width=3),
        ]
        self._blob = _rounded(c, 0, 0, 1, 1, 40, fill=FACE, outline="")
        self._arm = c.create_line(0, 0, 1, 1, fill=FACE, width=22,
                                  capstyle="round")
        self._dot = c.create_oval(0, 0, 1, 1, fill=FACE, outline="")
        self._eye_l = c.create_oval(0, 0, 1, 1, fill=EYE, outline="")
        self._eye_r = c.create_oval(0, 0, 1, 1, fill=EYE, outline="")
        # progress line (thin bar, NOT a three-dot indicator)
        self._pbg = c.create_line(0, 0, 1, 1, fill="#26262e", width=3)
        self._pfg = c.create_line(0, 0, 1, 1, fill=TEAL, width=3)
        self._stage = c.create_text(0, 0, text="", fill=MUTED,
                                    font=("Segoe UI", 10), anchor="sw")
        # top-left controls: home / message / new (drawn shapes, no fonts)
        self._btns: dict[str, dict] = {}
        bx, by, bs, bgap = PAD + 22, PAD + 16, 40, 12
        self._make_button("home", bx, by, bs)
        self._make_button("chat", bx + bs + bgap, by, bs)
        self._make_button("new", bx + 2 * (bs + bgap), by, bs)
        # fold + close, top-right
        self._make_button("fold", PAD + BODY_W - 22 - 2 * (bs + bgap), by, bs)
        self._make_button("close", PAD + BODY_W - 22 - bs, by, bs)

    def _make_button(self, name: str, x: int, y: int, s: int):
        c = self.canvas
        bg = _rounded(c, x, y, x + s, y + s, 13, fill="#17171c", outline="")
        glyphs = []
        cx, cy = x + s / 2, y + s / 2
        if name == "home":  # house: square + roof
            glyphs.append(c.create_polygon(cx - 8, cy + 1, cx - 8, cy + 8,
                                           cx + 8, cy + 8, cx + 8, cy + 1,
                                           fill=MUTED))
            glyphs.append(c.create_polygon(cx - 11, cy + 2, cx, cy - 8,
                                           cx + 11, cy + 2, fill=MUTED))
        elif name == "chat":  # speech bubble: oval + tail
            glyphs.append(c.create_oval(cx - 9, cy - 7, cx + 9, cy + 6,
                                        fill=MUTED, outline=""))
            glyphs.append(c.create_polygon(cx - 5, cy + 5, cx - 8, cy + 11,
                                           cx, cy + 5, fill=MUTED))
        elif name == "new":  # plus
            glyphs.append(c.create_line(cx - 8, cy, cx + 8, cy, fill=MUTED,
                                        width=3, capstyle="round"))
            glyphs.append(c.create_line(cx, cy - 8, cx, cy + 8, fill=MUTED,
                                        width=3, capstyle="round"))
        elif name == "fold":  # chevron up
            glyphs.append(c.create_line(cx - 7, cy + 3, cx, cy - 4, fill=MUTED,
                                        width=3, capstyle="round"))
            glyphs.append(c.create_line(cx, cy - 4, cx + 7, cy + 3, fill=MUTED,
                                        width=3, capstyle="round"))
        elif name == "close":  # x
            glyphs.append(c.create_line(cx - 6, cy - 6, cx + 6, cy + 6,
                                        fill=MUTED, width=3, capstyle="round"))
            glyphs.append(c.create_line(cx - 6, cy + 6, cx + 6, cy - 6,
                                        fill=MUTED, width=3, capstyle="round"))
        for g in glyphs:
            c.addtag_withtag(f"btn-{name}", g)
        c.addtag_withtag(f"btn-{name}", bg)
        c.addtag_withtag("btn", bg)
        for g in glyphs:
            c.addtag_withtag("btn", g)
        self._btns[name] = {"bg": bg, "glyphs": glyphs, "box": (x, y, x + s, y + s)}

    def _build_panel_widgets(self):
        r = self.root
        self.panel_frame = tk.Frame(r, bg=PANEL_BG, bd=0,
                                    highlightthickness=1,
                                    highlightbackground=BODY_EDGE)
        self.input_text = tk.Text(self.panel_frame, height=4, wrap="word",
                                  bg=PANEL_BG, fg=TEXT_FG,
                                  insertbackground=TEXT_FG,
                                  relief="flat", bd=6,
                                  font=("Segoe UI", 11),
                                  highlightthickness=0)
        self.input_text.pack(side="left", fill="both", expand=True,
                             padx=(10, 4), pady=8)
        side = tk.Frame(self.panel_frame, bg=PANEL_BG)
        side.pack(side="right", fill="y", padx=(4, 10), pady=8)
        self.send_btn = tk.Button(side, text="Send", command=self._send,
                                  bg=TEAL, fg="#062a28", relief="flat",
                                  font=("Segoe UI", 10, "bold"),
                                  activebackground=TEAL, width=9)
        self.send_btn.pack(fill="x", pady=(0, 6))
        self.cancel_btn = tk.Button(side, text="Cancel", command=self._cancel,
                                    bg="#2a2a31", fg=TEXT_FG, relief="flat",
                                    font=("Segoe UI", 10), activebackground="#3a3a44",
                                    width=9, state="disabled")
        self.cancel_btn.pack(fill="x")
        self.auto_fix_var = tk.BooleanVar(value=False)
        self.auto_fix_check = tk.Checkbutton(
            side, text="Fix files\ndirectly", variable=self.auto_fix_var,
            bg=PANEL_BG, fg=MUTED, selectcolor=PANEL_BG,
            activebackground=PANEL_BG, font=("Segoe UI", 8),
            justify="left")
        self.auto_fix_check.pack(fill="x", pady=(6, 0))
        self.hint = tk.Label(self.panel_frame,
                             text="Enter to send · Shift+Enter newline · Esc folds panel",
                             bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 9))
        self.input_text.bind("<Return>", self._on_return)
        self.input_text.bind("<Escape>", lambda e: self.set_panel(False))

    # -- geometry -----------------------------------------------------------
    def _recenter(self, force: bool = False):
        sw, sh = _screen_size(self.root)
        if not force and (sw, sh) == self._last_screen:
            return
        self._last_screen = (sw, sh)
        x = max(0, (sw - self.W) // 2)
        self.root.geometry(f"{self.W}x{self.H}+{x}+0")

    def _resize(self, w: int, h: int):
        if (w, h) == (self.W, self.H):
            return
        self.W, self.H = w, h
        self.canvas.configure(width=w, height=h)
        self._recenter(force=True)
        self._layout_panel()

    def _layout_panel(self):
        if self.panel_open and not self.folded:
            y = PAD + BODY_H + GAP
            self.panel_frame.place(x=PAD, y=y,
                                   width=BODY_W, height=PANEL_H)
            self.hint.place(in_=self.panel_frame, relx=0.0, rely=1.0,
                            x=10, y=-22, anchor="sw")
        else:
            self.panel_frame.place_forget()

    # -- public state API (used by the app controller) ----------------------
    def set_panel(self, open_: bool):
        self.panel_open = bool(open_)
        if self.folded and open_:
            self.set_folded(False)
        if open_:
            self._resize(BODY_W + 2 * PAD, BODY_H + GAP + PANEL_H + 2 * PAD)
        else:
            self._resize(BODY_W + 2 * PAD, BODY_H + 2 * PAD)
        if open_:
            try:
                self.input_text.focus_set()
            except Exception:
                pass

    def set_folded(self, folded: bool):
        self.folded = bool(folded)
        if folded:
            self.panel_frame.place_forget()
            self._resize(FOLDED_W + 2 * PAD, FOLDED_H + 2 * PAD)
        else:
            self._resize(BODY_W + 2 * PAD,
                         (BODY_H + GAP + PANEL_H if self.panel_open else BODY_H)
                         + 2 * PAD)

    def toggle_fold(self):
        self.set_folded(not self.folded)

    def hide(self):
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show(self):
        try:
            self.root.deiconify()
            self._recenter(force=True)
        except Exception:
            pass

    def set_running(self, running: bool):
        self.running = running
        try:
            self.cancel_btn.configure(state="normal" if running else "disabled")
            self.send_btn.configure(state="disabled" if running else "normal")
        except Exception:
            pass
        if running:
            self.face.set_mood("working")
        elif self.face.mood == "working":
            self.face.set_mood("idle")

    def set_stage(self, stage: str, message: str = "", progress: float = 0.0):
        """Display a REAL backend event. Never called with simulated data."""
        label, detail = stage_label(stage)
        self.progress = max(0.0, min(1.0, float(progress)))
        self.stage_text = f"{label} — {message}" if message else detail
        if stage in ("done",):
            self.face.set_mood("done")
            self.state_color = GREEN
        elif stage in ("error",):
            self.face.set_mood("error")
            self.state_color = RED
        elif stage in ("cancelled",):
            self.face.set_mood("idle")
            self.state_color = TEAL
        else:
            self.face.set_mood("working")
            self.state_color = TEAL

    def set_idle(self, note: str = ""):
        self.progress = 0.0
        self.face.set_mood("idle")
        self.state_color = TEAL
        self.stage_text = note or "Idle — click the face or + to ask Trace"

    def set_error(self, message: str):
        self.face.set_mood("error")
        self.state_color = RED
        self.stage_text = message[:160]

    # -- events -------------------------------------------------------------
    @property
    def auto_fix(self) -> bool:
        try:
            return bool(self.auto_fix_var.get())
        except Exception:
            return False

    def _send(self):
        if self.on_submit is None or self.running:
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            self.stage_text = "Type a debugging question first"
            return
        self.input_text.delete("1.0", "end")
        try:
            self.on_submit(text, self.auto_fix)
        except TypeError:
            self.on_submit(text)

    def _cancel(self):
        if self.on_cancel is not None and self.running:
            self.on_cancel()

    def _on_return(self, event):
        if event.state & 0x1:  # Shift held -> newline
            return None
        self._send()
        return "break"

    def _click(self, event):
        if self.folded:
            self.set_folded(False)
            return
        clicked = self.canvas.find_withtag("current")
        if not clicked:
            return
        tags = self.canvas.gettags(clicked[0])
        for name in ("home", "chat", "new", "fold", "close"):
            if f"btn-{name}" in tags:
                self._press(name)
                return
        # face click toggles the input panel (main interaction)
        self.set_panel(not self.panel_open)

    def _press(self, name: str):
        if name == "home":
            self.set_panel(False)
            self.set_folded(False)
            self.set_idle()
        elif name == "chat":
            self.set_panel(not self.panel_open)
        elif name == "new":
            try:
                self.input_text.delete("1.0", "end")
            except Exception:
                pass
            self.set_panel(True)
        elif name == "fold":
            self.toggle_fold()
        elif name == "close":
            self.close()

    def _hover(self, event):
        try:
            hovered = self.canvas.find_withtag("current")
            tags = self.canvas.gettags(hovered[0]) if hovered else ()
            for name, b in self._btns.items():
                active = f"btn-{name}" in tags
                self.canvas.itemconfigure(b["bg"],
                                          fill="#26262e" if active else "#17171c")
            self.canvas.configure(cursor="hand2" if any(
                t.startswith("btn-") for t in tags) else "")
        except Exception:
            pass

    def close(self):
        if self._destroyed:
            return
        self._destroyed = True
        cb = self.on_close_cb
        try:
            self.root.destroy()
        except Exception:
            pass
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    # -- frame loop ---------------------------------------------------------
    def _tick(self):
        if self._destroyed:
            return
        try:
            now = time.monotonic()
            dt = now - self._last_frame
            self._last_frame = now
            self._screen_poll += dt
            if self._screen_poll > 2.0:  # resolution / monitor changes
                self._screen_poll = 0.0
                self._recenter()
            cx, cy = _cursor_pos()
            if self.folded:
                self._draw_folded()
            else:
                # face center in screen coords (window is at top, x known)
                try:
                    wx = self.root.winfo_x() + PAD + BODY_W / 2
                    wy = self.root.winfo_y() + PAD + BODY_H / 2
                except Exception:
                    wx, wy = 960, 75
                st = self.face.update(dt, (cx, cy), (wx, wy))
                self._draw_full(st)
        except Exception:
            pass
        finally:
            if not self._destroyed:
                try:
                    self.root.after(33, self._tick)
                except Exception:
                    pass

    def _draw_full(self, st: dict):
        c = self.canvas
        if self._was_folded:
            # restore static items hidden while folded
            self._was_folded = False
            c.coords(self._body, PAD, PAD, PAD + BODY_W, PAD + BODY_H)
            for b in self._btns.values():
                x, y, x2, y2 = b["box"]
                c.coords(b["bg"], x, y, x2, y2)
                c.itemconfigure(b["bg"], state="normal")
                for g in b["glyphs"]:
                    c.itemconfigure(g, state="normal")
        cx = PAD + BODY_W / 2
        cy = PAD + BODY_H / 2 + st["bob"]
        lx, ly = st["look"]
        droop = st["droop"]
        # halo
        for i, h in enumerate(self._halos):
            pad = 26 - i * 10
            c.coords(h, cx - 105 - pad, cy - 80 - pad,
                     cx + 105 + pad, cy + 80 + pad)
        # blob + arm + dot
        c.coords(self._blob, cx - 88, cy - 60, cx + 88, cy + 60)
        import math
        ang = math.radians(st["arm_angle"])
        ax, ay = cx + 84, cy + 16
        c.coords(self._arm, ax, ay, ax + 46 * math.cos(ang),
                 ay + 46 * math.sin(ang))
        c.coords(self._dot, cx - 128, cy + 2, cx - 108, cy + 22)
        # eyes
        op = max(0.08, st["openness"])
        rx, ry = 9, 12 * op
        for i, item in enumerate((self._eye_l, self._eye_r)):
            ex = cx + (-30 if i == 0 else 30) + lx
            ey = cy - 6 + ly + droop
            c.coords(item, ex - rx, ey - ry, ex + rx, ey + ry)
        # progress + stage text
        y = PAD + BODY_H - 16
        c.coords(self._pbg, PAD + 20, y, PAD + BODY_W - 20, y)
        w = (BODY_W - 40) * self.progress
        if w > 1 and (self.running or self.progress > 0):
            c.coords(self._pfg, PAD + 20, y, PAD + 20 + w, y)
            c.itemconfigure(self._pfg, fill=self.state_color)
        else:
            c.coords(self._pfg, -10, -10, -9, -9)
        c.coords(self._stage, PAD + 22, PAD + BODY_H - 24)
        c.itemconfigure(self._stage, text=self.stage_text[:110])

    def _draw_folded(self):
        c = self.canvas
        if not self._was_folded:
            self._was_folded = True
            for b in self._btns.values():
                c.itemconfigure(b["bg"], state="hidden")
                for g in b["glyphs"]:
                    c.itemconfigure(g, state="hidden")
        # compact pill: reuse body item + mini face (blob + eyes only)
        c.coords(self._body, PAD, PAD, PAD + FOLDED_W, PAD + FOLDED_H)
        cx = PAD + FOLDED_W / 2
        cy = PAD + FOLDED_H / 2
        try:
            wx = self.root.winfo_x() + cx
            wy = self.root.winfo_y() + cy
        except Exception:
            wx, wy = 960, 32
        st = self.face.update(0.033, _cursor_pos(), (wx, wy))
        lx, ly = st["look"]
        c.coords(self._blob, cx - 44, cy - 20, cx + 44, cy + 20)
        op = max(0.08, st["openness"])
        for i, item in enumerate((self._eye_l, self._eye_r)):
            ex = cx + (-16 if i == 0 else 16) + lx * 0.6
            ey = cy + ly * 0.6
            c.coords(item, ex - 6, ey - 8 * op, ex + 6, ey + 8 * op)
        for h in self._halos:
            c.coords(h, -10, -10, -9, -9)
        c.coords(self._arm, -10, -10, -9, -9)
        c.coords(self._dot, -10, -10, -9, -9)
        c.coords(self._pbg, -10, -10, -9, -9)
        c.coords(self._pfg, -10, -10, -9, -9)
        c.coords(self._stage, -100, -100)
        # buttons already hidden (see top of _draw_folded)
