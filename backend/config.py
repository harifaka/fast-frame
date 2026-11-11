"""Central configuration and path-safety helpers for Fast-Frame.

The application never uploads media: it reads and writes files directly on
host drives that are bind-mounted into the container at ``HOST_MOUNT_PATH``.
Every path coming from the browser therefore has to be validated against that
single root before it is handed to ``ffmpeg``/``ffprobe``.
"""

from __future__ import annotations

import os
from pathlib import Path

VIDEO_EXTENSIONS: tuple[str, ...] = (".mp4", ".mkv", ".avi")
SUBTITLE_EXTENSIONS: tuple[str, ...] = (".srt",)
OUTPUT_EXTENSIONS: tuple[str, ...] = (".mp4", ".mkv")

SUPPORTED_SPEED_MIN = 0.25
SUPPORTED_SPEED_MAX = 4.0

SUBTITLE_FONT_SIZES = {"small": 16, "medium": 24, "large": 32}
DEFAULT_SUBTITLE_FONT_SIZE = SUBTITLE_FONT_SIZES["medium"]

DEFAULT_OUTPUT_CODEC = "h264"
NVENC_PRESET = "p6"
NVENC_H264_CQ = 19
NVENC_HEVC_CQ = 21
X264_CRF = 19
X265_CRF = 21
AUDIO_BITRATE = "192k"

# How many bytes of ffmpeg stderr are kept for the "failed job" log.
MAX_STDERR_BYTES = 64 * 1024
# Seconds between ffprobe/ffmpeg availability re-checks.
GPU_INFO_CACHE_SECONDS = 30.0
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")


def _initial_mount_path() -> Path:
    raw = os.environ.get("HOST_MOUNT_PATH", "/mnt/host")
    # On POSIX an absolute path is used verbatim; on Windows "/mnt/host" would
    # resolve to the current drive (C:\mnt\host), which is the closest analogue.
    return Path(raw).expanduser().resolve()


HOST_MOUNT_PATH: Path = _initial_mount_path()


def host_root() -> Path:
    """Return the (possibly re-resolved) zero-copy mount root."""

    return HOST_MOUNT_PATH


def set_host_mount_path(path: str | os.PathLike[str]) -> Path:
    """Point the application at a different mount root (used by tests)."""

    global HOST_MOUNT_PATH
    HOST_MOUNT_PATH = Path(path).expanduser().resolve()
    return HOST_MOUNT_PATH


class PathSecurityError(ValueError):
    """Raised when a requested path escapes the configured mount root."""


def resolve_host_path(raw: str | None, *, must_exist: bool = False) -> Path:
    """Resolve ``raw`` and guarantee it stays inside :func:`host_root`.

    Raises :class:`PathSecurityError` for traversal attempts, foreign drives or
    symlinks that point outside of the mount root.
    """

    root = host_root()
    raw = (raw or "").strip()
    if not raw:
        candidate = root
    else:
        # ``Path('/abs')`` on Windows anchors to the current drive, which is
        # exactly what we want for the container's POSIX-style paths.
        candidate = Path(raw).expanduser()

    try:
        resolved_root = root.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - platform specific
        raise PathSecurityError(f"Invalid path: {raw}") from exc

    if not _is_within(resolved, resolved_root):
        raise PathSecurityError("Path is outside of the configured host mount")

    if must_exist and not resolved.exists():
        raise PathSecurityError(f"Path does not exist: {raw}")
    return resolved


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(candidate), str(root)]) == str(root)
    except ValueError:
        # Different drives / mixed absolute-relative paths.
        return False


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_subtitle_file(path: Path) -> bool:
    return path.suffix.lower() in SUBTITLE_EXTENSIONS


def is_output_file(path: Path) -> bool:
    return path.suffix.lower() in OUTPUT_EXTENSIONS


def relative_to_root(path: Path) -> str:
    """POSIX-ish display path relative to the mount root."""

    try:
        rel = path.resolve(strict=False).relative_to(host_root())
    except (OSError, ValueError):
        return str(path)
    return "" if str(rel) == "." else str(rel).replace(os.sep, "/")


def normalize_output_extension(filename: str, codec: str) -> str:
    """Force an allowed container extension on a user supplied filename."""

    name = (filename or "").strip().replace("\\", "/").split("/")[-1]
    if not name:
        name = "output"
    suffix = Path(name).suffix.lower()
    if suffix in OUTPUT_EXTENSIONS:
        return name
    stem = Path(name).stem if suffix else name
    return f"{stem}.mp4" if codec != "mkv" else f"{stem}.mkv"
