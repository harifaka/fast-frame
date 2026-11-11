"""Tests for host drive mounts, path sandboxing and drive-bar shortcuts."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import config
from config import (
    PathSecurityError,
    discover_drive_shortcuts,
    is_hidden_drive_entry,
    resolve_host_path,
)


def _symlink(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        # Junctions need no elevation and exercise the same resolution path.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    return False


class HostPathSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_root = config.host_root()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        config.set_host_mount_path(self.root)
        (self.root / "media").mkdir()
        (self.root / "drives" / "c").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        config.set_host_mount_path(self._original_root)

    def test_paths_inside_the_root_are_normalised(self) -> None:
        resolved = resolve_host_path(str(self.root / "drives" / "c"))
        self.assertEqual(resolved, self.root / "drives" / "c")

    def test_relative_paths_are_anchored_at_the_root(self) -> None:
        resolved = resolve_host_path("media")
        self.assertEqual(resolved, self.root / "media")

    def test_parent_traversal_is_rejected(self) -> None:
        with self.assertRaises(PathSecurityError):
            resolve_host_path(str(self.root / ".." / "outside"))

    def test_missing_paths_raise_when_required(self) -> None:
        with self.assertRaises(PathSecurityError):
            resolve_host_path(str(self.root / "media" / "missing.mp4"), must_exist=True)

    def test_symlink_outside_the_root_is_rejected(self) -> None:
        outside = Path(tempfile.mkdtemp())
        try:
            if not _symlink(self.root / "media" / "usb", outside):
                self.skipTest("symlinks are not permitted on this host")
            with self.assertRaises(PathSecurityError):
                resolve_host_path(str(self.root / "media" / "usb" / "clip.mp4"))
        finally:
            outside.rmdir()

    def test_symlink_inside_a_drive_mount_may_resolve_outside(self) -> None:
        outside = Path(tempfile.mkdtemp())
        try:
            if not _symlink(self.root / "drives" / "usb", outside):
                self.skipTest("symlinks are not permitted on this host")
            resolved = resolve_host_path(str(self.root / "drives" / "usb" / "clip.mp4"))
            self.assertEqual(resolved, self.root / "drives" / "usb" / "clip.mp4")

            # ... but it is still not a way to walk out of the mount lexically.
            with self.assertRaises(PathSecurityError):
                resolve_host_path(str(self.root / "drives" / "usb" / ".." / ".." / ".." / "x"))
        finally:
            outside.rmdir()


class DriveShortcutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_root = config.host_root()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        config.set_host_mount_path(self.root)
        for name in ("c", "d", "wslg", "shm"):
            (self.root / "drives" / name).mkdir(parents=True)
        (self.root / "drives" / "c" / "Users").mkdir()
        (self.root / "c").mkdir()
        (self.root / "media").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        config.set_host_mount_path(self._original_root)

    def test_internal_wsl_folders_are_hidden(self) -> None:
        self.assertTrue(is_hidden_drive_entry(self.root / "drives" / "wslg"))
        self.assertTrue(is_hidden_drive_entry(self.root / "drives" / "shm"))
        self.assertFalse(is_hidden_drive_entry(self.root / "drives" / "c"))

    def test_shortcuts_cover_drives_users_and_media(self) -> None:
        shortcuts = discover_drive_shortcuts()
        labels = [shortcut["label"] for shortcut in shortcuts]
        self.assertEqual(labels, ["C:", "D:", "Users", "Media"])
        self.assertEqual(shortcuts[0]["path"], str(self.root / "drives" / "c"))
        self.assertEqual(shortcuts[2]["path"], str(self.root / "drives" / "c" / "Users"))
        self.assertNotIn("wslg", " ".join(labels))

    def test_directory_listing_skips_internal_wsl_folders(self) -> None:
        from routers import filesystem

        payload = asyncio.run(filesystem.list_directory(path=str(self.root / "drives")))
        names = {entry["name"] for entry in payload["entries"]}
        self.assertIn("c", names)
        self.assertNotIn("wslg", names)
        self.assertNotIn("shm", names)

    def test_drives_endpoint_returns_shortcuts(self) -> None:
        from routers import filesystem

        payload = asyncio.run(filesystem.list_drives())
        self.assertEqual(payload["root"], str(self.root))
        self.assertEqual(len(payload["shortcuts"]), 4)

    def test_unreadable_entries_do_not_break_listing(self) -> None:
        from unittest import mock

        from routers import filesystem

        drive = self.root / "drives" / "c"
        (drive / "clip.mp4").write_bytes(b"x")
        locked = drive / "System Volume Information"
        locked.mkdir()

        real_is_dir = Path.is_dir

        def guarded(path: Path) -> bool:
            if path.name == locked.name:
                raise PermissionError(13, "Permission denied", str(path))
            return real_is_dir(path)

        with mock.patch.object(Path, "is_dir", guarded):
            payload = asyncio.run(filesystem.list_directory(path=str(drive)))

        self.assertIn("clip.mp4", {entry["name"] for entry in payload["entries"]})


if __name__ == "__main__":
    unittest.main()
