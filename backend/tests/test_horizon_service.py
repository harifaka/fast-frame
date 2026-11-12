"""Tests for the 360° horizon levelling estimator."""

from __future__ import annotations

import unittest

from services.horizon_service import estimate_roll, sample_times

try:  # numpy is part of the AI runtime
    import numpy as np
except ImportError:  # pragma: no cover - slim installs
    np = None


def horizon_frame(tilt: float, *, height: int = 360, width: int = 640) -> "np.ndarray":
    """Synthetic equirect frame with a horizon tilted by ``tilt`` degrees."""

    rows, columns = np.mgrid[0:height, 0:width].astype(np.float32)
    boundary = height / 2.0 + np.tan(np.deg2rad(tilt)) * (columns - width / 2.0)
    frame = np.where(rows < boundary, 200.0, 60.0).astype(np.float32)
    noise = np.random.default_rng(7).normal(0.0, 2.0, frame.shape)
    return frame + noise.astype(np.float32)


@unittest.skipIf(np is None, "numpy is not installed")
class EstimateRollTests(unittest.TestCase):
    def test_level_horizon_needs_no_correction(self) -> None:
        roll, confidence = estimate_roll(horizon_frame(0.0))
        self.assertAlmostEqual(roll, 0.0, delta=0.6)
        self.assertGreater(confidence, 0.5)

    def test_clockwise_tilt_gets_counter_clockwise_correction(self) -> None:
        roll, _ = estimate_roll(horizon_frame(6.0))
        self.assertAlmostEqual(roll, -6.0, delta=0.6)

    def test_counter_clockwise_tilt_gets_clockwise_correction(self) -> None:
        roll, _ = estimate_roll(horizon_frame(-9.0))
        self.assertAlmostEqual(roll, 9.0, delta=0.6)

    def test_noise_has_no_reliable_roll(self) -> None:
        noise = np.random.default_rng(3).normal(100.0, 25.0, (360, 640)).astype(np.float32)
        roll, confidence = estimate_roll(noise)
        self.assertLess(confidence, 0.12)
        self.assertEqual(roll, 0.0)

    def test_flat_frame_has_no_roll(self) -> None:
        self.assertEqual(estimate_roll(np.full((360, 640), 100.0, dtype=np.float32)), (0.0, 0.0))


class SampleTimesTests(unittest.TestCase):
    def test_samples_stay_inside_the_clip(self) -> None:
        times = sample_times(10.0, 3)
        self.assertEqual(len(times), 3)
        self.assertTrue(all(0 < value < 10.0 for value in times))
        self.assertEqual(times, sorted(times))

    def test_unknown_duration_probes_the_start(self) -> None:
        self.assertEqual(sample_times(0.0, 2), [0.0, 0.0])

    def test_sample_count_is_bounded(self) -> None:
        self.assertEqual(len(sample_times(30.0, 99)), 12)
        self.assertEqual(len(sample_times(30.0, 0)), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
