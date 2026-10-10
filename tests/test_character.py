"""Tests: character physics, blink, moods, and backend-stage label coverage."""

import unittest

from trace_ui.character import (
    STAGE_LABELS,
    Blink,
    CharacterState,
    EyeLook,
    Spring2D,
    stage_label,
)


class TestSpring(unittest.TestCase):
    def test_converges_to_target(self):
        s = Spring2D()
        for _ in range(600):
            s.update(10.0, -5.0, 1 / 60)
        self.assertAlmostEqual(s.x, 10.0, places=1)
        self.assertAlmostEqual(s.y, -5.0, places=1)

    def test_clamped_dt_is_stable(self):
        s = Spring2D()
        s.update(100.0, 100.0, 10.0)  # huge dt must not explode
        self.assertTrue(abs(s.x) < 1e6 and abs(s.y) < 1e6)


class TestEyeLook(unittest.TestCase):
    def test_centered_cursor_rests(self):
        e = EyeLook()
        self.assertEqual(e.target((500, 500), (500, 500)), (0.0, 0.0))

    def test_offset_clamped_to_max(self):
        e = EyeLook(max_offset=9.0)
        dx, dy = e.target((5000, 500), (500, 500))
        self.assertAlmostEqual((dx * dx + dy * dy) ** 0.5, 9.0, places=5)

    def test_tracks_direction(self):
        e = EyeLook()
        dx, dy = e.target((600, 500), (500, 500))
        self.assertGreater(dx, 0)
        self.assertAlmostEqual(dy, 0.0, places=5)


class TestBlink(unittest.TestCase):
    def test_blink_cycle_returns_to_open(self):
        b = Blink()
        b.reset(next_in=0.0)
        vals = [b.update(0.02) for _ in range(20)]
        self.assertLess(min(vals), 1.0)  # lid closed at some point
        self.assertAlmostEqual(vals[-1], 1.0)

    def test_stays_open_before_schedule(self):
        b = Blink()
        b.reset(next_in=99.0)
        self.assertEqual(b.update(0.05), 1.0)


class TestCharacterState(unittest.TestCase):
    def test_bad_mood_rejected(self):
        with self.assertRaises(ValueError):
            CharacterState().set_mood("party")

    def test_done_triggers_bounce(self):
        c = CharacterState()
        c.set_mood("working")
        c.set_mood("done")
        ys = []
        for _ in range(120):
            out = c.update(1 / 60, (0, 0), (500, 500))
            ys.append(out["bob"])
        self.assertLess(min(ys), 0)  # hopped upward

    def test_arm_poses(self):
        c = CharacterState()
        c.set_mood("idle")
        idle = c.arm_angle_deg()
        c.set_mood("done")
        self.assertLess(c.arm_angle_deg(), idle)  # arm raised


class TestStageLabels(unittest.TestCase):
    def test_all_backend_stages_covered(self):
        backend_stages = ["receive", "screenshot", "context", "project_root",
                          "error_extract", "read_files", "diagnostics",
                          "model_analysis", "validate", "report"]
        for stage in backend_stages:
            label, detail = stage_label(stage)
            self.assertTrue(label and detail, stage)
            self.assertEqual(STAGE_LABELS[stage], (label, detail))

    def test_unknown_stage_never_empty(self):
        label, _ = stage_label("something_future")
        self.assertTrue(label)


if __name__ == "__main__":
    unittest.main()
