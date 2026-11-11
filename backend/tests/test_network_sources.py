"""Tests for network URL sources and SMB/CIFS share handling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config import (
    ffmpeg_network_url,
    is_network_url,
    mask_credentials,
    network_input_args,
    network_source_name,
)
from services import network_service
from services.ffmpeg_builder import RenderOptions, build_ffmpeg_command
from services.network_service import NetworkSourceError
from services.worker import (
    default_output_name,
    plan_destination,
    plan_source,
    redact_credentials,
)


class NetworkUrlDetectionTests(unittest.TestCase):
    def test_supported_schemes_are_recognised(self) -> None:
        for url in (
            "http://192.168.1.10:8080/video.mp4",
            "https://nas.local/media/clip.mkv",
            "rtsp://cam.local:554/stream",
            "webdavs://dav.example.com/files/video.avi",
        ):
            self.assertTrue(is_network_url(url), url)

    def test_local_paths_are_not_urls(self) -> None:
        for value in (
            "/mnt/host/media/clip.mp4",
            r"\\server\share\clip.mp4",
            "C:\\videos\\clip.mp4",
            "smb://server/share",
            "",
        ):
            self.assertFalse(is_network_url(value), value)

    def test_webdav_is_translated_to_http_for_ffmpeg(self) -> None:
        self.assertEqual(
            ffmpeg_network_url("webdav://nas.local/files/clip.mp4"),
            "http://nas.local/files/clip.mp4",
        )
        self.assertEqual(
            ffmpeg_network_url("webdavs://nas.local/files/clip.mp4"),
            "https://nas.local/files/clip.mp4",
        )
        self.assertEqual(
            ffmpeg_network_url("https://nas.local/clip.mp4"),
            "https://nas.local/clip.mp4",
        )

    def test_source_name_uses_url_basename(self) -> None:
        self.assertEqual(
            network_source_name("http://192.168.1.5:8080/media/stream%20video.mp4"),
            "stream_video.mp4",
        )
        self.assertEqual(
            network_source_name("http://host/live/channel.m3u8?token=abc"),
            "channel.mp4",
        )
        self.assertEqual(network_source_name("http://host/"), "host_network_stream.mp4")

    def test_credentials_are_masked_for_display(self) -> None:
        self.assertEqual(
            mask_credentials("https://user:secret@nas.local/clip.mp4"),
            "https://user:***@nas.local/clip.mp4",
        )
        self.assertEqual(
            mask_credentials("https://nas.local/clip.mp4"), "https://nas.local/clip.mp4"
        )


class NetworkInputArgsTests(unittest.TestCase):
    def test_http_urls_get_reconnect_flags(self) -> None:
        args = network_input_args("http://nas.local/clip.mp4")
        self.assertEqual(
            args,
            [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-i",
                "http://nas.local/clip.mp4",
            ],
        )

    def test_rtsp_uses_tcp_and_reconnect(self) -> None:
        args = network_input_args("rtsp://cam.local/stream")
        self.assertIn("-rtsp_transport", args)
        self.assertIn("tcp", args)
        self.assertEqual(args[-1], "rtsp://cam.local/stream")

    def test_local_paths_are_passed_through(self) -> None:
        self.assertEqual(network_input_args("/mnt/host/media/in.mp4"), ["-i", "/mnt/host/media/in.mp4"])

    def test_builder_inserts_flags_before_input(self) -> None:
        command = build_ffmpeg_command(
            RenderOptions(input_path="https://nas.local/clip.mp4", output_path="/tmp/out.mp4")
        )
        index = command.index("-i")
        self.assertEqual(command[index + 1], "https://nas.local/clip.mp4")
        self.assertEqual(
            command[index - 6 : index],
            ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"],
        )

    def test_builder_leaves_local_inputs_untouched(self) -> None:
        command = build_ffmpeg_command(
            RenderOptions(input_path="/mnt/host/in.mp4", output_path="/tmp/out.mp4")
        )
        self.assertNotIn("-reconnect", command)
        self.assertEqual(command[command.index("-i") + 1], "/mnt/host/in.mp4")

    def test_command_redacts_stream_credentials(self) -> None:
        self.assertEqual(
            redact_credentials("ffmpeg -i https://user:pw@nas.local/clip.mp4"),
            "ffmpeg -i https://***@nas.local/clip.mp4",
        )


class SharePathTests(unittest.TestCase):
    def test_unc_flavours_are_accepted(self) -> None:
        for raw in (
            r"\\192.168.1.100\videos",
            "//192.168.1.100/videos",
            "smb://192.168.1.100/videos",
        ):
            server, share, username, password = network_service.parse_share_path(raw)
            self.assertEqual((server, share), ("192.168.1.100", "videos"))
            self.assertIsNone(username)
            self.assertIsNone(password)

    def test_credentials_can_come_from_the_url(self) -> None:
        server, share, username, password = network_service.parse_share_path(
            "smb://alice:s3cret@nas.local/media"
        )
        self.assertEqual((server, share), ("nas.local", "media"))
        self.assertEqual((username, password), ("alice", "s3cret"))

    def test_incomplete_paths_are_rejected(self) -> None:
        with self.assertRaises(NetworkSourceError):
            network_service.parse_share_path("//server-only")
        with self.assertRaises(NetworkSourceError):
            network_service.parse_share_path("")

    def test_mapped_drives_are_not_smb_shares(self) -> None:
        with self.assertRaises(NetworkSourceError):
            network_service.parse_share_path("Z:\\videos")


class PlannedSourceTests(unittest.TestCase):
    def test_url_source_is_planned_without_touching_the_disk(self) -> None:
        plan = plan_source("http://192.168.1.5:8080/stream/video.mp4")
        self.assertEqual(plan["kind"], "url")
        self.assertEqual(plan["render"], "http://192.168.1.5:8080/stream/video.mp4")
        self.assertEqual(plan["name"], "video.mp4")
        self.assertEqual(
            default_output_name(plan["name"], 3, "hevc"), "video_3x_hevc.mp4"
        )

    def test_rtsp_source_is_accepted(self) -> None:
        self.assertEqual(plan_source("rtsp://cam.local/live")["kind"], "url")

    def test_credentials_are_masked_in_the_display_path(self) -> None:
        plan = plan_source("https://user:pw@nas.local/video.mp4")
        self.assertEqual(plan["display"], "https://user:***@nas.local/video.mp4")
        self.assertIn("user:pw@", plan["render"])

    def test_local_source_inside_the_mount_is_planned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            video = Path(raw) / "clip.mp4"
            video.write_bytes(b"data")
            from config import set_host_mount_path

            previous = set_host_mount_path(raw)
            try:
                plan = plan_source(str(video))
            finally:
                set_host_mount_path(previous)
            self.assertEqual(plan["kind"], "local")
            self.assertEqual(plan["render"], str(video))


class NativeLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(network_service._locations)
        network_service._locations.clear()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "sub").mkdir()
        (self.root / "clip.mp4").write_bytes(b"0123456789")
        (self.root / "notes.txt").write_text("hello", encoding="utf-8")
        self.location = network_service.register_native_path(str(self.root), name="nas")

    def tearDown(self) -> None:
        network_service._locations.clear()
        network_service._locations.update(self._saved)
        self._temp.cleanup()

    def test_listing_marks_media_and_folders(self) -> None:
        listing = network_service.list_directory(str(self.root))
        names = {entry["name"]: entry["kind"] for entry in listing["entries"]}
        self.assertEqual(names["clip.mp4"], "video")
        self.assertEqual(names["sub"], "directory")
        self.assertEqual(names["notes.txt"], "other")
        self.assertTrue(listing["is_root"])
        self.assertIsNone(listing["parent"])

    def test_subdirectory_has_parent_and_breadcrumbs(self) -> None:
        listing = network_service.list_directory(str(self.root / "sub"))
        self.assertFalse(listing["is_root"])
        self.assertEqual(listing["parent"], self.location.root)
        self.assertEqual(len(listing["breadcrumbs"]), 2)

    def test_stat_and_file_helpers(self) -> None:
        video = str(self.root / "clip.mp4")
        self.assertTrue(network_service.exists(video))
        self.assertTrue(network_service.is_file(video))
        self.assertFalse(network_service.is_dir(video))
        self.assertEqual(network_service.stat_path(video)["size_bytes"], 10)

    def test_unique_output_path_avoids_collisions(self) -> None:
        rendered = network_service.unique_output_path(str(self.root), "clip.mp4")
        self.assertEqual(Path(rendered).name, "clip_1.mp4")
        reserved = rendered.casefold()
        self.assertEqual(
            Path(
                network_service.unique_output_path(str(self.root), "clip.mp4", {reserved})
            ).name,
            "clip_2.mp4",
        )

    def test_upload_writes_into_the_share(self) -> None:
        staged = self.root / "staged.bin"
        staged.write_bytes(b"render")
        target = Path(self.root) / "out" / "render.mp4"
        size = network_service.upload(staged, str(target))
        self.assertEqual(size, 6)
        self.assertEqual(target.read_bytes(), b"render")

    def test_destination_folder_plans_as_local_for_native_paths(self) -> None:
        plan = plan_destination(str(self.root / "sub"))
        self.assertEqual(plan["mode"], "local")
        self.assertEqual(plan["display"], str(self.root / "sub"))

    def test_unknown_locations_are_not_matched(self) -> None:
        self.assertIsNone(network_service.match_location("/mnt/host/media"))
        with self.assertRaises(NetworkSourceError):
            network_service.list_directory("/mnt/host/elsewhere")


if __name__ == "__main__":
    unittest.main()
