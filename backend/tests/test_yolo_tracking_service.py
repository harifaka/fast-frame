"""Tests for the YOLO tracking math, smoothing and analysis pipeline."""

from __future__ import annotations

import unittest
from unittest import mock

from services import yolo_tracking_service as tracking
from services.yolo_tracking_service import (
    COCO_NAMES,
    Detection,
    Framing,
    FramingSmoother,
    TrackingError,
    YoloTrackingService,
    build_tracking_keyframes,
    framing_for_detection,
    level_profile,
    normalize_level,
    select_subject,
)

try:  # numpy/onnxruntime are optional extras
    import numpy as np
except ImportError:  # pragma: no cover - slim installs
    np = None


def detection(label="person", confidence=0.9, x=0.5, y=0.5, w=0.2, h=0.3) -> Detection:
    return Detection(label=label, confidence=confidence, x=x, y=y, w=w, h=h)


class LevelProfileTests(unittest.TestCase):
    def test_three_levels_are_ordered_from_smooth_to_aggressive(self) -> None:
        smooth, dynamic, aggressive = (level_profile(level) for level in (1, 2, 3))
        self.assertGreater(smooth.smoothing_seconds, dynamic.smoothing_seconds)
        self.assertGreater(dynamic.smoothing_seconds, aggressive.smoothing_seconds)
        self.assertGreater(smooth.margin, dynamic.margin)
        self.assertGreater(dynamic.margin, aggressive.margin)
        self.assertLess(smooth.max_zoom, aggressive.max_zoom)

    def test_level_is_validated(self) -> None:
        self.assertEqual(normalize_level("3"), 3)
        self.assertEqual(normalize_level(2.0), 2)
        with self.assertRaises(TrackingError):
            normalize_level(4)
        with self.assertRaises(TrackingError):
            normalize_level("fast")


class SubjectSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = level_profile(2)

    def test_ignores_untrackable_classes_and_low_confidence(self) -> None:
        subject = detection("cup", confidence=0.99, w=0.6, h=0.6)
        weak = detection("person", confidence=0.05)
        self.assertIsNone(select_subject([subject, weak], profile=self.profile))

    def test_larger_subject_wins_on_similar_confidence(self) -> None:
        small = detection("person", confidence=0.8, x=0.3, w=0.05, h=0.05)
        large = detection("person", confidence=0.8, x=0.3, w=0.3, h=0.4)
        chosen = select_subject([small, large], profile=self.profile)
        self.assertIs(chosen, large)

    def test_continuity_prefers_the_previously_tracked_subject(self) -> None:
        near = detection("person", confidence=0.8, x=0.35, w=0.2, h=0.3)
        far = detection("person", confidence=0.85, x=0.85, w=0.2, h=0.3)
        chosen = select_subject([far, near], profile=self.profile, previous=near)
        self.assertIs(chosen, near)


class FramingMathTests(unittest.TestCase):
    def test_centred_subject_has_no_offset(self) -> None:
        framing = framing_for_detection(
            detection(), mode="rectilinear", frame_aspect=16 / 9
        )
        self.assertAlmostEqual(framing.pan, 0.0)
        self.assertAlmostEqual(framing.tilt, 0.0)

    def test_spherical_pan_uses_the_full_360_degrees(self) -> None:
        framing = framing_for_detection(
            detection(x=0.75), mode="spherical", frame_aspect=2.0
        )
        self.assertAlmostEqual(framing.pan, 90.0)

    def test_rectilinear_pan_uses_frame_width_degrees(self) -> None:
        framing = framing_for_detection(
            detection(x=0.75), mode="rectilinear", frame_aspect=16 / 9
        )
        self.assertAlmostEqual(framing.pan, 45.0)

    def test_smaller_subject_zooms_further(self) -> None:
        wide = framing_for_detection(
            detection(w=0.4, h=0.5), mode="rectilinear", frame_aspect=16 / 9
        )
        tight = framing_for_detection(
            detection(w=0.1, h=0.12), mode="rectilinear", frame_aspect=16 / 9
        )
        self.assertGreater(tight.zoom, wide.zoom)
        self.assertGreaterEqual(wide.zoom, 1.0)

    def test_zoom_is_capped_by_the_level(self) -> None:
        tiny = detection(w=0.01, h=0.01)
        smooth = framing_for_detection(
            tiny, mode="rectilinear", frame_aspect=16 / 9, profile=level_profile(1)
        )
        aggressive = framing_for_detection(
            tiny, mode="rectilinear", frame_aspect=16 / 9, profile=level_profile(3)
        )
        self.assertAlmostEqual(smooth.zoom, level_profile(1).max_zoom)
        self.assertAlmostEqual(aggressive.zoom, level_profile(3).max_zoom)

    def test_aggressive_level_frames_tighter_than_smooth(self) -> None:
        subject = detection(w=0.2, h=0.3)
        smooth = framing_for_detection(
            subject, mode="rectilinear", frame_aspect=16 / 9, profile=level_profile(1)
        )
        aggressive = framing_for_detection(
            subject, mode="rectilinear", frame_aspect=16 / 9, profile=level_profile(3)
        )
        self.assertGreater(aggressive.zoom, smooth.zoom)


class SmootherTests(unittest.TestCase):
    def test_first_update_snaps_to_the_target(self) -> None:
        smoother = FramingSmoother(2)
        target = Framing(pan=30.0, tilt=-10.0, zoom=2.0)
        self.assertEqual(smoother.update(target, 0.0), target)

    def test_aggressive_level_converges_faster(self) -> None:
        target = Framing(pan=45.0, tilt=0.0, zoom=2.0)
        smooth = FramingSmoother(1, initial=Framing(0.0, 0.0, 1.0))
        aggressive = FramingSmoother(3, initial=Framing(0.0, 0.0, 1.0))
        smooth_result = smooth.update(target, 0.5)
        aggressive_result = aggressive.update(target, 0.5)
        self.assertGreater(aggressive_result.pan, smooth_result.pan)
        self.assertLess(aggressive_result.pan, 45.0)

    def test_smooth_level_ignores_tiny_nudges(self) -> None:
        smoother = FramingSmoother(1, initial=Framing(10.0, 0.0, 1.0))
        result = smoother.update(Framing(11.0, 0.0, 1.0), 0.5)
        self.assertEqual(result.pan, 10.0)

    def test_speed_limit_caps_a_long_jump(self) -> None:
        smoother = FramingSmoother(1, initial=Framing(0.0, 0.0, 1.0))
        result = smoother.update(Framing(170.0, 0.0, 1.0), 0.5)
        self.assertLessEqual(result.pan, level_profile(1).max_speed_deg_per_second * 0.5)

    def test_zoom_never_drops_below_one(self) -> None:
        smoother = FramingSmoother(3, initial=Framing(0.0, 0.0, 1.2))
        result = smoother.update(Framing(0.0, 0.0, 0.4), 1.0)
        self.assertGreaterEqual(result.zoom, 1.0)


class KeyframeTests(unittest.TestCase):
    def test_static_samples_collapse_to_two_keyframes(self) -> None:
        samples = [(index * 0.5, Framing(5.0, 0.0, 1.5)) for index in range(10)]
        frames = build_tracking_keyframes(samples, duration=4.5)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["time"], 0.0)
        self.assertEqual(frames[-1]["time"], 4.5)

    def test_moving_samples_are_kept_in_order(self) -> None:
        samples = [(float(index), Framing(index * 10.0, 0.0, 1.0)) for index in range(5)]
        frames = build_tracking_keyframes(samples, duration=4.0)
        times = [frame["time"] for frame in frames]
        self.assertEqual(times, sorted(times))
        self.assertGreaterEqual(len(frames), 4)

    def test_last_keyframe_always_reaches_the_duration(self) -> None:
        frames = build_tracking_keyframes(
            [(0.0, Framing(0.0, 0.0, 1.0)), (2.0, Framing(10.0, 0.0, 1.0))],
            duration=30.0,
        )
        self.assertEqual(frames[-1]["time"], 30.0)

    def test_values_are_clamped_and_zoomed_in_at_least_once(self) -> None:
        frames = build_tracking_keyframes(
            [(0.0, Framing(400.0, -200.0, 0.2)), (1.0, Framing(0.0, 0.0, 1.0))],
            duration=1.0,
        )
        self.assertLessEqual(max(frame["pan"] for frame in frames), 180.0)
        self.assertLessEqual(max(abs(frame["tilt"]) for frame in frames), 90.0)
        self.assertGreaterEqual(min(frame["zoom"] for frame in frames), 1.0)

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(TrackingError):
            build_tracking_keyframes([(0.0, Framing(0.0, 0.0, 1.0))], mode="flat")


class FakeDetector(tracking.DetectorBackend):
    name = "fake"
    model = "fake-model"

    def __init__(self, x_values) -> None:
        self._x_values = list(x_values)
        self.calls = 0

    def detect(self, frame, width, height):
        x = self._x_values[min(self.calls, len(self._x_values) - 1)]
        self.calls += 1
        return [detection(x=x, w=0.2, h=0.3)]


class AnalysisTests(unittest.TestCase):
    def make_service(self, detector) -> YoloTrackingService:
        service = YoloTrackingService(backend="auto")
        service._resolved = True
        service._detector = detector
        return service

    def test_analysis_returns_keyframes_and_summary(self) -> None:
        detector = FakeDetector([0.2 + index * 0.05 for index in range(12)])
        service = self.make_service(detector)
        with mock.patch.object(tracking, "extract_frame", return_value=(b"raw", 2, 2)), \
                mock.patch.object(tracking, "_frame_to_array", return_value=object()):
            summary = service.analyze("/media/clip.mp4", duration=10.0, width=1920, height=1080)

        self.assertEqual(summary["backend"], "fake")
        self.assertEqual(summary["frames_with_subject"], summary["frames_analyzed"])
        self.assertGreater(summary["keyframe_count"], 1)
        self.assertEqual(summary["keyframes"][0]["time"], 0.0)
        self.assertAlmostEqual(summary["keyframes"][-1]["time"], 10.0)

    def test_aggressive_level_tracks_faster(self) -> None:
        smooth = self.make_service(FakeDetector([0.2 + index * 0.05 for index in range(12)]))
        aggressive = self.make_service(FakeDetector([0.2 + index * 0.05 for index in range(12)]))
        with mock.patch.object(tracking, "extract_frame", return_value=(b"raw", 2, 2)), \
                mock.patch.object(tracking, "_frame_to_array", return_value=object()):
            smooth_summary = smooth.analyze("/media/a.mp4", duration=10.0, level=1)
            aggressive_summary = aggressive.analyze("/media/b.mp4", duration=10.0, level=3)

        self.assertGreater(
            aggressive_summary["keyframes"][-1]["pan"],
            smooth_summary["keyframes"][-1]["pan"],
        )

    def test_repeat_analysis_uses_the_cache(self) -> None:
        detector = FakeDetector([0.5])
        service = self.make_service(detector)
        with mock.patch.object(tracking, "extract_frame", return_value=(b"raw", 2, 2)), \
                mock.patch.object(tracking, "_frame_to_array", return_value=object()):
            service.analyze("/media/clip.mp4", duration=5.0)
            calls_after_first = detector.calls
            service.analyze("/media/clip.mp4", duration=5.0)
        self.assertEqual(detector.calls, calls_after_first)

    def test_analysis_without_a_backend_raises(self) -> None:
        service = YoloTrackingService(backend="does-not-exist")
        self.assertFalse(service.status()["available"])
        with self.assertRaises(TrackingError):
            service.analyze("/media/clip.mp4", duration=1.0)

    def test_network_sources_are_rejected_for_analysis(self) -> None:
        service = self.make_service(FakeDetector([0.5]))
        with self.assertRaises(TrackingError):
            service.analyze("rtsp://camera.example/stream", duration=5.0)

    def test_frame_probe_reports_unavailable_instead_of_raising(self) -> None:
        service = YoloTrackingService(backend="does-not-exist")
        result = service.detect_frame("/media/clip.mp4", timestamp=1.0)
        self.assertFalse(result["available"])
        self.assertIsNone(result["detection"])
        self.assertTrue(result["reason"])


@unittest.skipUnless(np is not None, "numpy is not installed")
class OnnxPostProcessingTests(unittest.TestCase):
    def test_letterbox_pads_to_the_square_input(self) -> None:
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        image, scale, pad_x, pad_y = tracking.letterbox_frame(frame, 640)
        self.assertEqual(image.shape, (640, 640, 3))
        self.assertAlmostEqual(scale, 1.0)
        self.assertEqual(pad_x, 0)
        self.assertEqual(pad_y, 140)

    def test_decode_maps_boxes_back_to_source_pixels(self) -> None:
        # Two predictions: one confident person, one weak bicycle.
        output = np.zeros((1, 84, 2), dtype=np.float32)
        output[0, :4, 0] = [320.0, 320.0, 64.0, 64.0]
        output[0, 4 + 0, 0] = 0.9  # person
        output[0, :4, 1] = [100.0, 100.0, 32.0, 32.0]
        output[0, 4 + 1, 1] = 0.05  # bicycle, below threshold

        detections = tracking.decode_yolov8_output(
            output,
            confidence=0.25,
            src_width=640,
            src_height=360,
            scale=1.0,
            pad_x=0.0,
            pad_y=0.0,
        )
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, COCO_NAMES[0])
        self.assertAlmostEqual(detections[0].x, 0.5, places=3)
        self.assertAlmostEqual(detections[0].w, 0.1, places=3)


if __name__ == "__main__":
    unittest.main()
