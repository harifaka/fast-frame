"""Tests for the Phase 11 monocular-depth point cloud exporter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import point_cloud_service as cloud
from services.point_cloud_service import (
    PointAccumulator,
    PointCloudBusyError,
    PointCloudError,
    PointCloudService,
    limit_points,
    metric_depth,
    project_equirectangular,
    project_rectilinear,
    sample_timestamps,
    voxel_downsample,
    write_ply,
)

try:  # numpy is an optional runtime extra
    import numpy as np
except ImportError:  # pragma: no cover - slim installs
    np = None


@unittest.skipUnless(np is not None, "numpy is not installed")
class SamplingTests(unittest.TestCase):
    def test_still_sources_sample_once(self) -> None:
        self.assertEqual(sample_timestamps(0.0), [0.0])

    def test_rate_and_frame_cap_are_applied(self) -> None:
        self.assertEqual(
            sample_timestamps(4.0, sample_rate=0.5, max_frames=100), [0.0, 2.0]
        )
        capped = sample_timestamps(100.0, sample_rate=5.0, max_frames=10)
        self.assertEqual(len(capped), 10)
        self.assertLessEqual(capped[-1], 100.0)


@unittest.skipUnless(np is not None, "numpy is not installed")
class ProjectionTests(unittest.TestCase):
    def test_metric_depth_puts_the_closest_sample_at_the_near_plane(self) -> None:
        closest = metric_depth(np.array([[0.0, 5.0, 10.0]], dtype=np.float32))
        self.assertAlmostEqual(float(closest[0, 2]), cloud.MIN_DEPTH_METERS, places=3)
        self.assertAlmostEqual(float(closest[0, 0]), cloud.MAX_DEPTH_METERS, places=3)

    def test_rectilinear_centre_pixel_is_on_the_optical_axis(self) -> None:
        depth = np.full((9, 15), 2.0, dtype=np.float32)
        points = project_rectilinear(depth, horizontal_fov=90.0).reshape(9, 15, 3)
        centre = points[4, 7]
        self.assertAlmostEqual(float(centre[0]), 0.0, places=5)
        self.assertAlmostEqual(float(centre[1]), 0.0, places=5)
        self.assertAlmostEqual(float(centre[2]), 2.0, places=5)
        # Y points up: the top row is above the axis, the bottom row below it.
        self.assertGreater(float(points[0, 0, 1]), 0.0)
        self.assertLess(float(points[-1, 0, 1]), 0.0)

    def test_equirectangular_points_keep_their_depth_as_radius(self) -> None:
        depth = np.linspace(1.0, 5.0, 32 * 16, dtype=np.float32).reshape(16, 32)
        points = project_equirectangular(depth).reshape(16, 32, 3)
        radius = np.linalg.norm(points, axis=-1)
        np.testing.assert_allclose(radius, depth, rtol=1e-5, atol=1e-5)


@unittest.skipUnless(np is not None, "numpy is not installed")
class DownsamplingTests(unittest.TestCase):
    def test_voxel_filter_keeps_one_point_per_cell(self) -> None:
        points = np.array(
            [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float32
        )
        colors = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], dtype=np.uint8)
        filtered_points, filtered_colors = voxel_downsample(points, colors, 0.1)
        self.assertEqual(len(filtered_points), 2)
        self.assertEqual(len(filtered_colors), 2)

    def test_empty_cloud_and_soft_limit_are_handled(self) -> None:
        empty_points, empty_colors = voxel_downsample(
            np.empty((0, 3), np.float32), np.empty((0, 3), np.uint8), 0.1
        )
        self.assertEqual(len(empty_points), 0)
        self.assertEqual(len(empty_colors), 0)

        points = np.arange(300, dtype=np.float32).reshape(-1, 3)
        colors = np.zeros((100, 3), dtype=np.uint8)
        limited_points, limited_colors = limit_points(points, colors, 10)
        self.assertEqual(len(limited_points), 10)
        self.assertEqual(len(limited_colors), 10)

    def test_accumulator_compacts_while_frames_arrive(self) -> None:
        accumulator = PointAccumulator(
            max_points=10, voxel_size=0.001, buffer_limit=10
        )
        for _ in range(4):
            accumulator.add(
                np.zeros((5, 3), dtype=np.float32), np.zeros((5, 3), dtype=np.uint8)
            )
        self.assertLessEqual(accumulator.count, 10)
        points, colors = accumulator.finalize()
        self.assertLessEqual(len(points), 10)
        self.assertEqual(len(points), len(colors))


@unittest.skipUnless(np is not None, "numpy is not installed")
class PlyTests(unittest.TestCase):
    def test_writer_emits_a_binary_ply_with_colours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cloud.ply"
            points = np.array([[1.0, 2.0, 3.0], [-1.0, 0.0, 4.0]], dtype=np.float32)
            colors = np.array([[255, 0, 0], [0, 255, 0]], dtype=np.uint8)
            write_ply(target, points, colors, comments=("unit test",))

            data = target.read_bytes()
            self.assertTrue(data.startswith(b"ply\nformat binary_little_endian 1.0"))
            self.assertIn(b"comment unit test", data)
            self.assertIn(b"element vertex 2", data)
            _, _, body = data.partition(b"end_header\n")
            self.assertEqual(len(body), 2 * 15)
            self.assertFalse(target.with_suffix(".ply.part").exists())


class FakeEstimator:
    name = "onnxruntime"
    model = "fake-depth.onnx"

    def predict(self, frame):
        height, width = frame.shape[:2]
        return np.linspace(0.0, 1.0, height * width, dtype=np.float32).reshape(
            height, width
        )


@unittest.skipUnless(np is not None, "numpy is not installed")
class PipelineTests(unittest.TestCase):
    def make_service(self) -> PointCloudService:
        service = PointCloudService(estimator=FakeEstimator())
        service._resolved = True
        return service

    def test_export_writes_ply_and_reports_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"not-a-real-video")
            output = Path(tmp) / "cloud"  # extension is added automatically
            frame = np.zeros((36, 64, 3), dtype=np.uint8)
            service = self.make_service()

            with mock.patch.object(
                cloud, "extract_frame", return_value=(frame.tobytes(), 64, 36)
            ):
                service.start(
                    source,
                    output,
                    duration=2.0,
                    width=1280,
                    height=720,
                    sample_rate=1.0,
                    max_points=500,
                )
                status = service.wait(5.0)

            self.assertEqual(status["state"], "completed", status.get("error"))
            self.assertEqual(status["frames_processed"], 2)
            self.assertEqual(status["frames_total"], 2)
            self.assertTrue(status["output"].endswith(".ply"))
            self.assertTrue(Path(status["output"]).is_file())
            self.assertGreater(status["points"], 0)
            self.assertAlmostEqual(status["progress"], 1.0)

    def test_network_sources_are_rejected(self) -> None:
        service = self.make_service()
        with self.assertRaises(PointCloudError):
            service.start("rtsp://camera.example/stream", "cloud.ply")

    def test_concurrent_exports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"x")
            service = self.make_service()
            service._status["running"] = True
            with self.assertRaises(PointCloudBusyError):
                service.start(source, Path(tmp) / "cloud.ply")

    def test_missing_output_folder_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"x")
            service = self.make_service()
            with self.assertRaises(PointCloudError):
                service.start(source, Path(tmp) / "missing" / "cloud.ply")


if __name__ == "__main__":
    unittest.main()
