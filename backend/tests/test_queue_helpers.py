"""Tests for queue helpers: filename sanitising and collision protection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.worker import default_output_name, sanitize_filename, unique_output_path


class FilenameSanitisingTests(unittest.TestCase):
    def test_strips_path_separators_and_invalid_characters(self) -> None:
        self.assertEqual(
            sanitize_filename('..\\evil/name:with*illegal?.mp4'), '_evil_name_with_illegal_.mp4'
        )

    def test_control_characters_are_removed(self) -> None:
        self.assertEqual(sanitize_filename("clip\x00\x1f.mp4"), "clip.mp4")

    def test_empty_name_falls_back(self) -> None:
        self.assertEqual(sanitize_filename('   '), 'output')

    def test_default_name_matches_spec(self) -> None:
        self.assertEqual(default_output_name('Holiday.MP4', 2.0, 'hevc'), 'Holiday_2x_hevc.mp4')
        self.assertEqual(default_output_name('clip.mkv', 1.25, 'h264'), 'clip_1.25x_h264.mp4')


class CollisionProtectionTests(unittest.TestCase):
    def test_existing_files_are_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / 'clip.mp4').write_bytes(b'a')
            (directory / 'clip_1.mp4').write_bytes(b'b')

            first = unique_output_path(directory, 'clip.mp4', set())
            self.assertEqual(first.name, 'clip_2.mp4')

    def test_reserved_queue_paths_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            reserved = {str((directory / 'clip.mp4')).casefold()}
            chosen = unique_output_path(directory, 'clip.mp4', reserved)
            self.assertEqual(chosen.name, 'clip_1.mp4')

    def test_source_file_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            source = directory / 'source.mp4'
            source.write_bytes(b'original')
            output = unique_output_path(directory, 'source.mp4', set())
            self.assertNotEqual(output, source)
            self.assertEqual(output.name, 'source_1.mp4')

    def test_suffix_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / 'render.mkv').write_bytes(b'a')
            self.assertEqual(unique_output_path(directory, 'render.mkv', set()).name, 'render_1.mkv')


if __name__ == '__main__':
    unittest.main()
