"""Tests for Insta360 ``.insp``/``.insv`` discovery and metadata shaping."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import config
from config import is_spherical_file, is_video_file
from routers.filesystem import _entry_for, _media_type
from services import insp_service
from services.insp_service import (
    DUAL_FISHEYE,
    EQUIRECTANGULAR,
    build_spherical_info,
    build_still_metadata,
    projection_from_filename,
    projection_from_metadata,
)


class SphericalDiscoveryTests(unittest.TestCase):
    def test_insp_and_insv_are_supported_video_sources(self) -> None:
        self.assertTrue(is_video_file(Path("clip.insp")))
        self.assertTrue(is_video_file(Path("clip.INSV")))
        self.assertTrue(is_spherical_file(Path("clip.insp")))
        self.assertFalse(is_spherical_file(Path("clip.mp4")))

    def test_directory_entries_flag_spherical_containers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spherical = root / "VID_20240101_00_001.insp"
            plain = root / "regular.mp4"
            spherical.write_bytes(b"\xff\xd8\xff\xe0")
            plain.write_bytes(b"\x00")

            spherical_entry = _entry_for(spherical)
            plain_entry = _entry_for(plain)

        self.assertEqual(spherical_entry["kind"], "video")
        self.assertTrue(spherical_entry["spherical"])
        self.assertEqual(spherical_entry["extension"], ".insp")
        self.assertEqual(plain_entry["kind"], "video")
        self.assertFalse(plain_entry["spherical"])

    def test_media_types_cover_spherical_containers(self) -> None:
        self.assertEqual(_media_type(Path("a.insp")), "image/jpeg")
        self.assertEqual(_media_type(Path("a.insv")), "video/mp4")


class ProjectionDetectionTests(unittest.TestCase):
    def test_projection_from_xmp_metadata(self) -> None:
        metadata = {"xmp": {"GPano": {"ProjectionType": "equirectangular"}}}
        self.assertEqual(projection_from_metadata(metadata), EQUIRECTANGULAR)

    def test_projection_from_fisheye_metadata(self) -> None:
        self.assertEqual(
            projection_from_metadata({"tags": {"comment": "Dual-Fisheye lens"}}),
            DUAL_FISHEYE,
        )

    def test_dual_lens_filename_is_dual_fisheye(self) -> None:
        self.assertEqual(
            projection_from_filename("VID_20240101_00_001.insp"), DUAL_FISHEYE
        )
        self.assertIsNone(projection_from_filename("holiday.insp"))


class SphericalMetadataTests(unittest.TestCase):
    def test_still_info_uses_image_dimensions(self) -> None:
        info = build_spherical_info(
            "holiday.insp",
            image={"width": 6080, "height": 3040, "format": "jpeg", "xmp": ""},
        )
        self.assertTrue(info.is_still)
        self.assertEqual(info.container, "jpeg")
        self.assertEqual((info.width, info.height), (6080, 3040))
        self.assertEqual(info.projection, EQUIRECTANGULAR)
        self.assertEqual(info.projection_source, "default")

    def test_xmp_wins_over_filename_heuristics(self) -> None:
        info = build_spherical_info(
            "VID_20240101_00_001.insp",
            image={
                "width": 6080,
                "height": 3040,
                "format": "jpeg",
                "xmp": 'GPano:ProjectionType="equirectangular"',
            },
        )
        self.assertEqual(info.projection, EQUIRECTANGULAR)
        self.assertEqual(info.projection_source, "xmp")

    def test_video_probe_produces_video_container(self) -> None:
        probe = {
            "format": {"duration": "12.5", "tags": {"comment": "Insta360 X3"}},
            "streams": [
                {"codec_type": "video", "width": 5760, "height": 2880, "index": 0}
            ],
        }
        info = build_spherical_info("clip.insv", probe=probe)
        self.assertFalse(info.is_still)
        self.assertEqual(info.container, "video")
        self.assertEqual(info.duration, 12.5)

    def test_jpeg_pipe_probe_is_treated_as_a_still(self) -> None:
        probe = {
            "format": {"format_name": "jpeg_pipe", "duration": "0.000000"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 6080,
                    "height": 3040,
                    "avg_frame_rate": "25/1",
                    "index": 0,
                }
            ],
        }
        info = build_spherical_info(
            "holiday.insp",
            probe=probe,
            image={"width": 6080, "height": 3040, "format": "jpeg"},
        )
        self.assertTrue(info.is_still)
        self.assertEqual(info.container, "jpeg")
        self.assertEqual(info.duration, 0.0)
        self.assertEqual((info.width, info.height), (6080, 3040))

    def test_still_metadata_is_render_compatible(self) -> None:
        info = build_spherical_info(
            "holiday.insp", image={"width": 6080, "height": 3040, "format": "jpeg"}
        )
        metadata = build_still_metadata("holiday.insp", info)
        self.assertTrue(metadata["is_still"])
        self.assertTrue(metadata["preview_supported"])
        self.assertFalse(metadata["has_audio"])
        self.assertEqual(metadata["duration"], 0.0)
        self.assertEqual(metadata["spherical"]["preview_mode"], "equirectangular")
        for key in ("path", "name", "width", "height", "codec", "size_bytes"):
            self.assertIn(key, metadata)

    def test_inspect_never_raises_on_unknown_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.insp"
            path.write_bytes(b"not an insta360 container")
            result = asyncio.run(insp_service.inspect_spherical(path))

        self.assertIn("metadata", result)
        self.assertIn("spherical", result)
        self.assertIn("projection", result["spherical"])
        self.assertIn("size_bytes", result["metadata"])


class SphericalConfigRegistrationTests(unittest.TestCase):
    def test_spherical_extensions_are_part_of_supported_inputs(self) -> None:
        self.assertIn(".insp", config.VIDEO_EXTENSIONS)
        self.assertIn(".insv", config.SPHERICAL_EXTENSIONS)
        self.assertIn(".insp", config.SUPPORTED_INPUT_LABEL)
        self.assertIn(".insv", config.SUPPORTED_INPUT_LABEL)


if __name__ == "__main__":
    unittest.main()
