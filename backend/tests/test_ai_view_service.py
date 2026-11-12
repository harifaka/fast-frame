"""Tests for the AI view selector state used by the UI and MCP agents."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.ai_view_service import (
    TRACKING_MODES,
    AiViewError,
    AiViewStateManager,
    apply_patch,
    default_state,
)


class AiViewStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = AiViewStateManager()

    def test_default_state_is_neutral(self) -> None:
        state = self.manager.snapshot()
        self.assertFalse(state["active"])
        self.assertEqual(state["pan"], 0.0)
        self.assertEqual(state["tilt"], 0.0)
        self.assertEqual(state["zoom"], 1.0)
        self.assertEqual(state["tracking_mode"], "off")
        self.assertEqual(state["keyframe_offsets"], [])
        self.assertTrue(state["mcp_ready"])
        self.assertEqual(state["endpoints"]["mcp_command"], "/api/ai/mcp-command")
        self.assertEqual(tuple(state["tracking_modes"]), TRACKING_MODES)

    def test_update_clamps_and_bumps_revision(self) -> None:
        state = self.manager.update({"pan": 400, "tilt": -200, "zoom": 99, "active": True})
        self.assertEqual(state["pan"], 180.0)
        self.assertEqual(state["tilt"], -90.0)
        self.assertEqual(state["zoom"], 8.0)
        self.assertTrue(state["active"])
        self.assertEqual(state["revision"], 1)
        self.assertIsNotNone(state["updated_at"])

        again = self.manager.update({"pan": -12.5})
        self.assertEqual(again["pan"], -12.5)
        self.assertEqual(again["revision"], 2)

    def test_invalid_tracking_mode_is_rejected(self) -> None:
        with self.assertRaises(AiViewError):
            self.manager.update({"tracking_mode": "teleport"})

    def test_tracking_activation_turns_view_on(self) -> None:
        state = self.manager.update({"tracking_mode": "subject", "target": "skier"})
        self.assertTrue(state["active"])
        self.assertEqual(state["tracking_mode"], "subject")
        self.assertEqual(state["target"], "skier")

    def test_keyframes_are_sorted_and_clamped(self) -> None:
        state = self.manager.update(
            {
                "keyframe_offsets": [
                    {"time": 4.0, "pan": 30, "tilt": 10, "zoom": 2},
                    {"time": 1.5, "pan": -300, "tilt": 250, "zoom": 0.1},
                ]
            }
        )
        frames = state["keyframe_offsets"]
        self.assertEqual([frame["time"] for frame in frames], [1.5, 4.0])
        self.assertEqual(frames[0]["pan"], -180.0)
        self.assertEqual(frames[0]["tilt"], 90.0)
        self.assertEqual(frames[0]["zoom"], 0.5)

    def test_keyframes_must_be_a_list(self) -> None:
        with self.assertRaises(AiViewError):
            self.manager.update({"keyframe_offsets": {"time": 1}})

    def test_reset_clears_directives_but_keeps_revision(self) -> None:
        self.manager.update({"pan": 45, "active": True})
        state = self.manager.reset()
        self.assertEqual(state["pan"], 0.0)
        self.assertFalse(state["active"])
        self.assertEqual(state["revision"], 2)

    def test_empty_update_is_a_noop(self) -> None:
        self.assertEqual(self.manager.update({})["revision"], 0)


class AiMcpCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = AiViewStateManager()

    def test_set_view_marks_mcp_controller(self) -> None:
        state = self.manager.apply_command(
            {
                "action": "set_view",
                "view": {"pan": 25, "tilt": -10, "zoom": 1.8, "active": True},
                "source_path": "/mnt/host/media/clip.insp",
            }
        )
        self.assertEqual(state["controller"], "mcp")
        self.assertEqual(state["pan"], 25.0)
        self.assertEqual(state["zoom"], 1.8)
        self.assertEqual(state["source_path"], "/mnt/host/media/clip.insp")

    def test_set_tracking_requires_mode(self) -> None:
        with self.assertRaises(AiViewError):
            self.manager.apply_command({"action": "set_tracking"})

    def test_add_and_clear_keyframes(self) -> None:
        self.manager.apply_command(
            {"action": "add_keyframe", "keyframe": {"time": 2, "pan": 10}}
        )
        state = self.manager.snapshot()
        self.assertEqual(len(state["keyframe_offsets"]), 1)

        cleared = self.manager.apply_command({"action": "clear_keyframes"})
        self.assertEqual(cleared["keyframe_offsets"], [])

    def test_activate_deactivate_and_reset(self) -> None:
        self.assertTrue(self.manager.apply_command({"action": "activate"})["active"])
        self.assertFalse(self.manager.apply_command({"action": "deactivate"})["active"])
        self.assertEqual(self.manager.apply_command({"action": "reset"})["pan"], 0.0)

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(AiViewError):
            self.manager.apply_command({"action": "self-destruct"})


class AiViewPersistenceTests(unittest.TestCase):
    def test_state_survives_a_restart_when_file_backed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai_view_state.json"
            first = AiViewStateManager(path)
            first.update({"pan": 33.5, "zoom": 2.5, "tracking_mode": "face"})

            second = AiViewStateManager(path)
            restored = second.snapshot()

        self.assertEqual(restored["pan"], 33.5)
        self.assertEqual(restored["zoom"], 2.5)
        self.assertEqual(restored["tracking_mode"], "face")

    def test_corrupt_state_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai_view_state.json"
            path.write_text("{not json", encoding="utf-8")
            manager = AiViewStateManager(path)
        self.assertEqual(manager.snapshot()["pan"], 0.0)


class ApplyPatchTests(unittest.TestCase):
    def test_apply_patch_is_pure(self) -> None:
        original = default_state()
        updated = apply_patch(original, {"pan": 12})
        self.assertEqual(original["pan"], 0.0)
        self.assertEqual(updated["pan"], 12.0)

    def test_non_finite_values_are_rejected(self) -> None:
        with self.assertRaises(AiViewError):
            apply_patch(default_state(), {"pan": float("nan")})


class AiTrackingStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = AiViewStateManager()

    def test_default_state_has_tracking_disabled(self) -> None:
        state = self.manager.snapshot()
        self.assertFalse(state["ai_tracking_enabled"])
        self.assertEqual(state["ai_tracking_level"], 2)
        self.assertEqual(state["tracking_keyframes"], [])
        self.assertIsNone(state["tracking_analysis"])
        self.assertEqual(state["tracking_levels"], [1, 2, 3])
        self.assertIn("available", state["tracking_detector"])
        self.assertEqual(state["endpoints"]["tracking"], "/api/ai/tracking")

    def test_enabling_tracking_activates_the_preview(self) -> None:
        state = self.manager.update({"ai_tracking_enabled": True, "ai_tracking_level": 3})
        self.assertTrue(state["ai_tracking_enabled"])
        self.assertEqual(state["ai_tracking_level"], 3)
        self.assertTrue(state["active"])
        self.assertEqual(state["tracking_mode"], "subject")

    def test_level_is_validated(self) -> None:
        with self.assertRaises(AiViewError):
            self.manager.update({"ai_tracking_level": 4})

    def test_tracking_keyframes_are_sorted_and_clamped(self) -> None:
        state = self.manager.update(
            {
                "tracking_keyframes": [
                    {"time": 4.0, "pan": 30, "tilt": 10, "zoom": 2},
                    {"time": 1.5, "pan": -300, "tilt": 250, "zoom": 0.2},
                ]
            }
        )
        frames = state["tracking_keyframes"]
        self.assertEqual([frame["time"] for frame in frames], [1.5, 4.0])
        self.assertEqual(frames[0]["pan"], -180.0)
        self.assertEqual(frames[0]["tilt"], 90.0)
        self.assertEqual(frames[0]["zoom"], 1.0)

    def test_analysis_summary_is_copied_and_validated(self) -> None:
        summary = {"backend": "ultralytics", "level": 2, "keyframe_count": 3}
        state = self.manager.update({"tracking_analysis": summary})
        summary["backend"] = "mutated"
        self.assertEqual(state["tracking_analysis"]["backend"], "ultralytics")

        with self.assertRaises(AiViewError):
            self.manager.update({"tracking_analysis": "not an object"})

        cleared = self.manager.update({"tracking_analysis": None, "tracking_keyframes": []})
        self.assertIsNone(cleared["tracking_analysis"])
        self.assertEqual(cleared["tracking_keyframes"], [])

    def test_horizon_alignment_and_roll_are_tracked(self) -> None:
        alignment = {"roll": -3.2, "confidence": 0.8, "reliable": True}
        state = self.manager.update({"roll": -3.2, "horizon_alignment": alignment})
        alignment["roll"] = 99.0
        self.assertEqual(state["roll"], -3.2)
        self.assertEqual(state["horizon_alignment"]["roll"], -3.2)
        self.assertEqual(state["endpoints"]["auto_align"], "/api/ai/auto-align")

        with self.assertRaises(AiViewError):
            self.manager.update({"horizon_alignment": "nope"})

        cleared = self.manager.update({"roll": 0, "horizon_alignment": None})
        self.assertIsNone(cleared["horizon_alignment"])
        self.assertEqual(cleared["roll"], 0.0)


if __name__ == "__main__":
    unittest.main()
