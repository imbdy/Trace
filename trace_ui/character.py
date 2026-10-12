"""Headless character animation logic for the Trace companion.

Pure Python — no tkinter dependency — so physics and stage mapping are
unit-testable. The renderer (companion.py) only draws what this module
computes.

Design contract (from the established reference):
- black rounded body, face integrated into the body, eyes follow the mouse,
  spring-smoothed motion, subtle idle bob, mood-driven arm/bounce.
- NO three-dot indicator anywhere.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


# Backend stage -> (short label, detail). Every backend stage is covered;
# the UI only ever displays labels for events actually received.
STAGE_LABELS: dict[str, tuple[str, str]] = {
    "receive": ("Sending request", "Sending your debugging request"),
    "screenshot": ("Capturing context", "Capturing screenshot and window context"),
    "context": ("Capturing context", "Collecting local context"),
    "project_root": ("Inspecting the project", "Identifying the project root"),
    "error_extract": ("Analyzing the error", "Extracting error and stack trace"),
    "read_files": ("Inspecting the project", "Reading relevant source files"),
    "diagnostics": ("Inspecting the project", "Collecting compiler and linter diagnostics"),
    "model_analysis": ("Analyzing the error", "Thinking through the evidence"),
    "validate": ("Verifying file references", "Verifying paths and line numbers"),
    "report": ("Preparing the explanation", "Preparing the explanation"),
}

TERMINAL_LABELS: dict[str, tuple[str, str]] = {
    "done": ("Done", "Investigation complete"),
    "error": ("Error", "Investigation failed"),
    "cancelled": ("Cancelled", "Investigation cancelled"),
}


def stage_label(stage: str) -> tuple[str, str]:
    """Return (label, detail) for a real backend stage or terminal state."""
    if stage in STAGE_LABELS:
        return STAGE_LABELS[stage]
    if stage in TERMINAL_LABELS:
        return TERMINAL_LABELS[stage]
    return ("Working", stage or "Working")


@dataclass
class Spring2D:
    """Damped spring toward a target — the 'polished movement' primitive."""

    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    stiffness: float = 170.0
    damping: float = 20.0

    def update(self, tx: float, ty: float, dt: float) -> tuple[float, float]:
        dt = min(max(dt, 0.0), 0.05)  # clamp: tab-switch safe
        ax = (tx - self.x) * self.stiffness - self.vx * self.damping
        ay = (ty - self.y) * self.stiffness - self.vy * self.damping
        self.vx += ax * dt
        self.vy += ay * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        return self.x, self.y


@dataclass
class EyeLook:
    """Shared cartoon look-vector for both eyes, spring-smoothed, clamped."""

    spring: Spring2D = field(default_factory=Spring2D)
    max_offset: float = 9.0
    dead_zone: float = 4.0

    def target(self, cursor: tuple[float, float],
               face_center: tuple[float, float]) -> tuple[float, float]:
        dx = cursor[0] - face_center[0]
        dy = cursor[1] - face_center[1]
        dist = math.hypot(dx, dy)
        if dist < self.dead_zone:
            return 0.0, 0.0
        scale = min(1.0, dist / 450.0) * self.max_offset
        return dx / dist * scale, dy / dist * scale

    def update(self, cursor: tuple[float, float],
               face_center: tuple[float, float],
               dt: float) -> tuple[float, float]:
        tx, ty = self.target(cursor, face_center)
        return self.spring.update(tx, ty, dt)


class Blink:
    """Eyelid openness 0..1. Randomized schedule; deterministic via reset()."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.next_in = 2.5
        self.t = -1.0  # <0 means not blinking

    def reset(self, next_in: float = 2.5):
        self.next_in = next_in
        self.t = -1.0

    def update(self, dt: float) -> float:
        if self.t < 0:
            self.next_in -= dt
            if self.next_in <= 0:
                self.t = 0.0
            return 1.0
        self.t += dt
        dur = 0.16
        if self.t >= dur:
            self.t = -1.0
            self.next_in = self.rng.uniform(2.2, 5.0)
            return 1.0
        # down then up
        half = dur / 2
        if self.t < half:
            return 1.0 - 0.9 * (self.t / half)
        return 0.1 + 0.9 * ((self.t - half) / half)


class CharacterState:
    """Mood + motion state. Moods: idle | working | done | error."""

    def __init__(self):
        self.mood = "idle"
        self.look = EyeLook()
        self.blink = Blink()
        self.time = 0.0
        self.bounce_y = 0.0
        self.bounce_v = 0.0
        self._bouncing = False

    def set_mood(self, mood: str):
        if mood not in ("idle", "working", "done", "error"):
            raise ValueError(f"unknown mood {mood!r}")
        if mood == "done" and self.mood != "done":
            self.bounce_v = -170.0  # happy hop impulse
            self._bouncing = True
        self.mood = mood

    def bob(self) -> float:
        """Idle vertical offset for the whole face group."""
        amp = 3.0 if self.mood == "idle" else 5.0 if self.mood == "working" else 2.0
        freq = 1.6 if self.mood == "idle" else 3.2
        return math.sin(self.time * freq) * amp

    def arm_angle_deg(self) -> float:
        """Rest -35deg; waves while working; raised when done."""
        if self.mood == "working":
            return -35.0 + math.sin(self.time * 6.0) * 28.0
        if self.mood == "done":
            return -80.0
        if self.mood == "error":
            return -8.0
        return -35.0 + math.sin(self.time * 1.6) * 4.0

    def eye_droop(self) -> float:
        return 4.0 if self.mood == "error" else 0.0

    def update(self, dt: float, cursor: tuple[float, float],
               face_center: tuple[float, float]) -> dict:
        dt = min(max(dt, 0.0), 0.05)
        self.time += dt
        lx, ly = self.look.update(cursor, face_center, dt)
        openness = self.blink.update(dt)
        if self._bouncing:
            self.bounce_v += 520.0 * dt
            self.bounce_y += self.bounce_v * dt
            if self.bounce_y >= 0:
                self.bounce_y = 0.0
                self._bouncing = False
        return {
            "look": (lx, ly),
            "openness": openness,
            "bob": self.bob() + self.bounce_y,
            "arm_angle": self.arm_angle_deg(),
            "droop": self.eye_droop(),
            "mood": self.mood,
        }
