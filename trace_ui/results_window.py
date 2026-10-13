"""Dedicated investigation results window (separate from the companion).

Single instance per app (ResultsWindowManager): repeated investigations
update the same window instead of spawning duplicates. Dark theme matching
the companion. All long text is selectable; code excerpts get lightweight
regex syntax highlighting (no dependencies). The only editing control is
"Apply fix": it writes the VERIFIED patch to disk (with .trace-bak backups)
via the backend apply-fix endpoint. Unverified patches stay review-only.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from .result_model import ResultView, build_result_view

BG = "#101014"
PANEL = "#17171d"
FG = "#ececf1"
MUTED = "#9a9aa3"
ACCENT = "#4fd1c5"
GREEN = "#43d17c"
AMBER = "#e8a13c"
RED = "#e5484d"
CODE_BG = "#0b0b0f"
CONF_COLORS = {"high": GREEN, "medium": AMBER, "low": MUTED}

KEYWORDS = ("def|class|return|if|elif|else|for|while|import|from|as|with|try|"
            "except|finally|raise|assert|pass|break|continue|lambda|yield|await|"
            "async|None|True|False|new|function|const|let|var|interface|type|"
            "struct|fn|pub|match|switch|case|sizeof|null|this|self|static|void|"
            "int|string|bool|public|private|using|namespace")

_HIGHLIGHT_PATTERNS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"#[^\n]*|//[^\n]*"), "com"),
    (re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\''), "str"),
    (re.compile(rf"\b({KEYWORDS})\b"), "kw"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "num"),
)


def highlight(node: tk.Text):
    """Apply lightweight syntax tags to a code Text widget's content."""
    text = node.get("1.0", "end")
    node.tag_configure("kw", foreground="#7dd3fc")
    node.tag_configure("str", foreground="#a7f3d0")
    node.tag_configure("com", foreground="#6b7280")
    node.tag_configure("num", foreground="#fbbf24")
    for rx, tag in _HIGHLIGHT_PATTERNS:
        try:
            for m in rx.finditer(text):
                s = f"1.0+{m.start()}c"
                e = f"1.0+{m.end()}c"
                node.tag_add(tag, s, e)
        except Exception:
            pass


class ResultsWindowManager:
    """Owns the single results window; safe to call from the UI thread only."""

    def __init__(self, root: tk.Tk, api_client=None):
        self.root = root
        self.api = api_client
        self.win: tk.Toplevel | None = None
        self.open_count = 0  # introspection for tests/smoke checks

    @property
    def is_open(self) -> bool:
        return self.win is not None and bool(self.win.winfo_exists())

    def show(self, status: dict):
        view = build_result_view(status)
        if not self.is_open:
            self._create()
        self._render(view)
        self.open_count += 1
        try:
            self.win.deiconify()
            self.win.lift()  # no focus_force: never steals editor focus
        except Exception:
            pass

    def close(self):
        try:
            if self.win is not None:
                self.win.destroy()
        except Exception:
            pass
        finally:
            self.win = None

    # -- construction -------------------------------------------------------
    def _create(self):
        w = tk.Toplevel(self.root)
        w.title("Trace — Investigation results")
        w.geometry("780x640")
        w.minsize(560, 420)
        w.configure(bg=BG)
        try:  # don't steal focus when auto-opening on completion
            import ctypes
            hwnd = w.winfo_id()
            if not ctypes.windll.user32.IsWindow(hwnd):
                parent = ctypes.windll.user32.GetParent(hwnd)
                if parent:
                    hwnd = parent
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x08000000)
        except Exception:
            pass
        w.protocol("WM_DELETE_WINDOW", self.close)
        outer = tk.Frame(w, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _sync(_e=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas.itemconfigure(win_id, width=canvas.winfo_width())
            except Exception:
                pass

        body.bind("<Configure>", _sync)
        canvas.bind("<Configure>", _sync)
        try:
            canvas.bind_all("<MouseWheel>",
                            lambda e: canvas.yview_scroll(-e.delta // 120, "units"),
                            add="+")
        except Exception:
            pass
        self.win = w
        self.body = body

    # -- rendering ----------------------------------------------------------
    def _render(self, view: ResultView):
        for child in self.body.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        B = self.body
        if not view.complete:
            self._banner(B, view.banner, RED if view.status == "error" else AMBER)
            return
        # header
        self._h1(B, "Diagnosis")
        self._badge_row(B, f"confidence: {view.confidence}",
                        CONF_COLORS.get(view.confidence, MUTED))
        self._para(B, view.summary)
        self._h2(B, "Most likely root cause")
        self._para(B, view.root_cause)
        if view.confidence_rationale:
            self._h2(B, "Why — evidence")
            self._para(B, view.confidence_rationale)
        # B. locations
        self._h1(B, "Exact source locations")
        if view.verified_findings:
            for f in view.verified_findings:
                self._finding(B, f, verified=True)
        else:
            self._para(B, "No verified source locations. The evidence was not "
                          "sufficient to pin the bug to a file and line.",
                       muted=True)
        if view.hypotheses:
            self._h2(B, "Unverified hypotheses — not confirmed")
            for f in view.hypotheses:
                self._finding(B, f, verified=False)
        # C. fix
        self._h1(B, "Proposed solution")
        if view.fix_description:
            self._para(B, view.fix_description)
        else:
            self._para(B, "No fix proposed — the evidence does not support one yet.",
                       muted=True)
        if view.fix_applied and view.fix_applied.get("applied"):
            files = ", ".join(view.fix_applied.get("applied_files", []))
            self._para(B, f"Fixed directly in: {files or '(unknown files)'} "
                          "Backups kept as *.trace-bak.")
        if view.fix_diff:
            if view.fix_diff_verified:
                self._h2(B, "Proposed patch (verified — safe to apply)")
            else:
                self._h2(B, "Proposed patch (unverified — review only, cannot auto-apply)")
            self._code(B, view.fix_diff, lang="diff")
            self._fix_buttons(B, view)
        if view.fix_rationale:
            self._para(B, view.fix_rationale)
        if view.alternative_hypotheses:
            self._h2(B, "Alternative hypotheses")
            for h in view.alternative_hypotheses:
                self._bullet(B, h)
        # D. evidence
        self._h1(B, "Investigation evidence")
        if view.evidence_summary:
            self._para(B, view.evidence_summary)
        if view.needs_more_info:
            self._h2(B, "Missing information")
            for n in view.needs_more_info:
                self._bullet(B, n)
        if view.warnings:
            self._h2(B, "Warnings")
            for x in view.warnings:
                self._bullet(B, x, color=AMBER)

    # -- widgets ------------------------------------------------------------
    def _h1(self, parent, text: str):
        tk.Label(parent, text=text, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold"), anchor="w").pack(
                     fill="x", padx=16, pady=(16, 4))

    def _h2(self, parent, text: str):
        tk.Label(parent, text=text, bg=BG, fg=FG,
                 font=("Segoe UI", 11, "bold"), anchor="w",
                 wraplength=720, justify="left").pack(
                     fill="x", padx=16, pady=(10, 2))

    def _para(self, parent, text: str, muted: bool = False):
        t = tk.Text(parent, wrap="word", bg=BG, fg=MUTED if muted else FG,
                    relief="flat", highlightthickness=0,
                    font=("Segoe UI", 10), height=1, padx=16)
        t.insert("1.0", text or "(none)")
        t.configure(state="disabled")
        # auto-height
        try:
            lines = max(1, int(t.index("end-1c").split(".")[0]))
            t.configure(height=min(lines, 40))
        except Exception:
            pass
        t.pack(fill="x", padx=0, pady=2)

    def _bullet(self, parent, text: str, color: str = FG):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=16, pady=1)
        tk.Label(row, text="•", bg=BG, fg=color,
                 font=("Segoe UI", 10)).pack(side="left", anchor="n")
        tk.Label(row, text=text, bg=BG, fg=color,
                 font=("Segoe UI", 10), wraplength=680,
                 justify="left", anchor="w").pack(side="left", fill="x",
                                                 expand=True)

    def _banner(self, parent, text: str, color: str):
        f = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                     highlightbackground=color)
        f.pack(fill="x", padx=16, pady=16)
        tk.Label(f, text=text, bg=PANEL, fg=color, wraplength=680,
                 justify="left", font=("Segoe UI", 11)).pack(
                     padx=12, pady=12, anchor="w")

    def _badge_row(self, parent, text: str, color: str):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(row, text=text, bg=BG, fg=color,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

    def _finding(self, parent, f, verified: bool):
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground="#2a2a33")
        card.pack(fill="x", padx=16, pady=6)
        top = tk.Frame(card, bg=PANEL)
        top.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(top, text=f.title or "Finding", bg=PANEL, fg=FG,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(side="left")
        tk.Label(top, text="verified" if verified else "unverified — hypothesis",
                 bg=PANEL, fg=GREEN if verified else AMBER,
                 font=("Segoe UI", 9, "bold")).pack(side="right")
        loc = tk.Frame(card, bg=PANEL)
        loc.pack(fill="x", padx=10)
        link = tk.Label(loc, text=f.location_label, bg=PANEL, fg=ACCENT,
                        font=("Segoe UI", 10, "underline"), cursor="hand2",
                        anchor="w")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _e, p=f.file_path, ln=f.start_line:
                  self._open(p, ln))
        if f.file_path:
            btn = tk.Button(loc, text=f"Open at line {f.start_line or '?'}",
                            command=lambda p=f.file_path, ln=f.start_line:
                            self._open(p, ln),
                            bg="#26262e", fg=FG, relief="flat",
                            font=("Segoe UI", 9))
            btn.pack(side="right", padx=(8, 0))
        note = f.verification_note
        if note:
            tk.Label(card, text=note, bg=PANEL,
                     fg=GREEN if verified else AMBER,
                     font=("Segoe UI", 9, "italic"), wraplength=680,
                     justify="left", anchor="w").pack(fill="x", padx=10)
        if f.explanation:
            self._card_text(card, f.explanation)
        if f.excerpt:
            self._code(card, f.excerpt)
        elif verified:
            tk.Label(card, text="Excerpt unavailable.", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9, "italic")).pack(anchor="w", padx=10)
        if f.evidence:
            tk.Label(card, text="Evidence:", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10,
                                                       pady=(6, 0))
            self._card_text(card, f.evidence)

    def _card_text(self, parent, text: str):
        t = tk.Text(parent, wrap="word", bg=PANEL, fg=FG, relief="flat",
                    highlightthickness=0, font=("Segoe UI", 10), height=1)
        t.insert("1.0", text)
        t.configure(state="disabled")
        try:
            lines = max(1, int(t.index("end-1c").split(".")[0]))
            t.configure(height=min(lines, 30))
        except Exception:
            pass
        t.pack(fill="x", padx=10, pady=2)

    def _code(self, parent, code: str, lang: str = ""):
        f = tk.Frame(parent, bg=CODE_BG, highlightthickness=1,
                     highlightbackground="#2a2a33")
        f.pack(fill="x", padx=10, pady=6)
        t = tk.Text(f, wrap="none", bg=CODE_BG, fg="#e5e7eb", relief="flat",
                    highlightthickness=0, font=("Consolas", 10), height=1,
                    padx=8, pady=8)
        hbar = tk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(xscrollcommand=hbar.set)
        t.insert("1.0", code)
        try:
            highlight(t)
        except Exception:
            pass
        t.configure(state="disabled")
        try:
            lines = max(1, int(t.index("end-1c").split(".")[0]))
            t.configure(height=min(lines, 24))
        except Exception:
            pass
        t.pack(fill="x", expand=True)
        hbar.pack(fill="x")

    def _fix_buttons(self, parent, view: ResultView):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=16, pady=4)
        status = tk.Label(row, text="", bg=BG, fg=MUTED,
                          font=("Segoe UI", 9), anchor="w")
        status.pack(side="left", fill="x", expand=True)

        def _apply():
            if self.api is None or not view.investigation_id:
                status.configure(text="Backend not connected.", fg=AMBER)
                return
            if not view.fix_diff_verified:
                status.configure(
                    text="Unverified patch: refusing to write to disk.", fg=AMBER)
                return
            try:
                res = self.api.apply_fix(view.investigation_id)
            except Exception as e:
                status.configure(text=f"Apply failed: {e}", fg=AMBER)
                return
            if res.get("ok") or res.get("applied"):
                files = ", ".join(res.get("applied_files", []))
                status.configure(
                    text=f"Applied to {files or 'files'}. Backups kept (*.trace-bak).",
                    fg=GREEN)
            else:
                reason = res.get("reason") or res.get("error", {}).get("message", "")
                status.configure(text=f"Not applied: {reason or 'see warnings'}",
                                 fg=AMBER)

        tk.Button(row, text="Apply fix to files (backup kept)",
                  command=_apply, bg="#26262e", fg=FG, relief="flat",
                  font=("Segoe UI", 9, "bold"),
                  state="normal" if view.fix_diff_verified else "disabled").pack(
                      side="right")

    def _open(self, path: str, line: int):
        if self.api is None or not path:
            return
        try:
            res = self.api.open_file(path, line if line and line > 0 else None)
            if not res.get("ok"):
                self._banner(self.body, f"Could not open file: {res.get('error')}",
                             AMBER)
        except Exception as e:
            try:
                self._banner(self.body, f"Could not open file: {e}", AMBER)
            except Exception:
                pass
