"""Central configuration and path-safety helpers for Fast-Frame.

The application never uploads media: it reads and writes files directly on
host drives that are bind-mounted into the container at ``HOST_MOUNT_PATH``.
Every path coming from the browser therefore has to be validated against that
single root before it is handed to ``ffmpeg``/``ffprobe``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

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

#: Drive-root bind mounts created by ``docker-compose.yml``. Paths inside them
#: may legitimately resolve outside ``HOST_MOUNT_PATH`` (bind mounts, symlinked
#: USB drives, Docker Desktop's ``/run/desktop/mnt/host``), so symlink
#: resolution is allowed to cross that boundary while lexical ``..`` traversal
#: is still rejected.
DRIVE_MOUNT_NAMES: tuple[str, ...] = ("drives", "c")

#: Internal WSL system folders that Docker Desktop exposes next to the real
#: drives. They are never useful for media and are hidden from the browser.
HIDDEN_DRIVE_ENTRIES: frozenset[str] = frozenset({"wslg", "shm", "wsl"})


def host_root() -> Path:
    """Return the (possibly re-resolved) zero-copy mount root."""

    return HOST_MOUNT_PATH


def set_host_mount_path(path: str | os.PathLike[str]) -> Path:
    """Point the application at a different mount root (used by tests)."""

    global HOST_MOUNT_PATH
    HOST_MOUNT_PATH = Path(path).expanduser().resolve()
    return HOST_MOUNT_PATH


def network_root() -> Path:
    """Directory that hosts SMB/CIFS mount points (``/mnt/host/network``).

    It lives inside :func:`host_root` so that every mounted share is listed,
    probed and rendered with the exact same zero-copy path rules as a drive.
    """

    raw = os.environ.get("NETWORK_MOUNT_PATH", "").strip()
    root = Path(raw).expanduser() if raw else host_root() / "network"
    return root


class PathSecurityError(ValueError):
    """Raised when a requested path escapes the configured mount root."""


def resolve_host_path(raw: str | None, *, must_exist: bool = False) -> Path:
    """Validate ``raw`` and return its lexically normalised form inside the root.

    Two checks are performed:

    1. **Lexical containment** - ``..`` segments are collapsed without touching
       the filesystem, so traversal such as ``/mnt/host/../../etc`` is rejected
       before anything is read.
    2. **Symlink / mount containment** - the path is resolved and must stay
       inside :func:`host_root`. Resolution is allowed to leave the root when
       the requested path lives in one of the drive bind mounts
       (:data:`DRIVE_MOUNT_NAMES`) or under an actual mount point, because
       Docker Desktop and Linux USB drives expose them that way.

    The lexically normalised path is returned (not the resolved one) so entry
    paths handed back to the browser stay inside ``HOST_MOUNT_PATH`` and remain
    valid inputs for the next request.
    """

    root = host_root()
    raw = (raw or "").strip()
    if not raw:
        candidate = root
    else:
        # ``Path('/abs')`` on Windows anchors to the current drive, which is
        # exactly what we want for the container's POSIX-style paths.
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate

    lexical = Path(os.path.normpath(str(candidate)))
    if not _is_within(lexical, root):
        raise PathSecurityError("Path is outside of the configured host mount")

    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - platform specific
        raise PathSecurityError(f"Invalid path: {raw}") from exc

    if not _is_within(resolved, root) and not _crosses_drive_mount(lexical, root):
        raise PathSecurityError("Path is outside of the configured host mount")

    if must_exist and not lexical.exists():
        raise PathSecurityError(f"Path does not exist: {raw}")
    return lexical


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate_text = os.path.normcase(str(candidate))
        root_text = os.path.normcase(str(root))
        return os.path.commonpath([candidate_text, root_text]) == root_text
    except ValueError:
        # Different drives / mixed absolute-relative paths.
        return False


def _crosses_drive_mount(lexical: Path, root: Path) -> bool:
    """True when ``lexical`` is anchored on a drive bind mount inside ``root``."""

    for name in DRIVE_MOUNT_NAMES:
        if _is_within(lexical, root / name):
            return True

    # A Linux media mount (``/media``, ``/run/media``) is itself a mount point;
    # anything below it may resolve to a device path outside the root.
    current = lexical
    while current != root:
        if not _is_within(current, root):
            return False
        try:
            if os.path.ismount(current):
                return True
        except OSError:  # pragma: no cover - platform specific
            return False
        current = current.parent
    return False


def is_dir_safe(path: Path) -> bool:
    """``Path.is_dir`` that returns False instead of raising on EACCES."""

    try:
        return path.is_dir()
    except OSError:
        return False


def is_file_safe(path: Path) -> bool:
    """``Path.is_file`` that returns False instead of raising on EACCES."""

    try:
        return path.is_file()
    except OSError:
        return False


def is_hidden_drive_entry(path: Path) -> bool:
    """True for Docker Desktop's internal WSL folders at the drive root."""

    if path.name.casefold() not in HIDDEN_DRIVE_ENTRIES:
        return False
    return path.parent == host_root() / DRIVE_MOUNT_NAMES[0]


def discover_drive_shortcuts() -> list[dict[str, str]]:
    """Quick-access drive shortcuts for the file browser's drive bar.

    Exposes ``C:``/``D:``/``E:`` (all Windows drives, including USB) from the
    ``drives`` bind mount, the direct ``C:`` fallback mount, a ``Users`` shortcut
    per drive and the always-present ``Media`` folder.
    """

    root = host_root()
    shortcuts: list[dict[str, str]] = []
    labels: set[str] = set()

    def add(name: str, label: str, path: Path, kind: str) -> None:
        key = label.casefold()
        if key in labels or not is_dir_safe(path):
            return
        labels.add(key)
        shortcuts.append({"name": name, "label": label, "path": str(path), "kind": kind})

    drives_mount = root / DRIVE_MOUNT_NAMES[0]
    drive_roots: list[Path] = []
    if is_dir_safe(drives_mount):
        try:
            children = sorted(drives_mount.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            children = []
        for child in children:
            if is_hidden_drive_entry(child) or not is_dir_safe(child):
                continue
            label = f"{child.name.upper()}:" if len(child.name) <= 3 else child.name
            add(child.name, label, child, "drive")
            drive_roots.append(child)

    # Direct fallback mount (``C:/`` bound to ``/mnt/host/c``) when the whole
    # host root is not available. Skipped when the drive already exists above.
    for name in DRIVE_MOUNT_NAMES[1:]:
        direct = root / name
        add(name, f"{name.upper()}:", direct, "drive")
        if is_dir_safe(direct):
            drive_roots.append(direct)

    for drive in drive_roots:
        add("users", "Users", drive / "Users", "folder")
        break

    add("media", "Media", root / "media", "media")
    return shortcuts


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


# --------------------------------------------------------------------- network sources
#: URL schemes that may be used as a render source without downloading first.
NETWORK_URL_SCHEMES: tuple[str, ...] = (
    "http",
    "https",
    "rtsp",
    "rtsps",
    "rtmp",
    "rtmps",
    "webdav",
    "webdavs",
    "dav",
    "davs",
)

#: WebDAV is plain HTTP(S) on the wire; ffmpeg has no ``webdav://`` protocol.
_WEBDAV_TRANSPORT = {"webdav": "http", "webdavs": "https", "dav": "http", "davs": "https"}

#: Schemes the browser preview proxy can relay (HTTP range requests).
PREVIEWABLE_SCHEMES = frozenset({"http", "https", "webdav", "webdavs", "dav", "davs"})

HTTP_RECONNECT_ARGS: tuple[str, ...] = (
    "-reconnect",
    "1",
    "-reconnect_streamed",
    "1",
    "-reconnect_delay_max",
    "5",
)
RTSP_RECONNECT_ARGS: tuple[str, ...] = (
    "-rtsp_transport",
    "tcp",
    "-reconnect",
    "1",
    "-reconnect_streamed",
    "1",
    "-reconnect_delay_max",
    "5",
)

_URL_BASENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
#: Container extensions we can safely normalise to ``.mp4`` in output names.
_NORMALISED_SUFFIXES = frozenset(
    {".m3u8", ".m3u", ".ts", ".mpd", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".mpeg", ".mpg", ".m2ts"}
)


def network_url_scheme(raw: str | os.PathLike[str] | None) -> str | None:
    """Return the lowercase scheme when ``raw`` is an allowed stream URL."""

    if raw is None or isinstance(raw, os.PathLike):
        return None
    text = str(raw).strip()
    if "://" not in text:
        return None
    scheme = text.split("://", 1)[0].strip().lower()
    if scheme in NETWORK_URL_SCHEMES:
        return scheme
    return None


def is_network_url(raw: str | os.PathLike[str] | None) -> bool:
    return network_url_scheme(raw) is not None


def is_previewable_url(raw: str | None) -> bool:
    return network_url_scheme(raw) in PREVIEWABLE_SCHEMES


def ffmpeg_network_url(raw: str) -> str:
    """Translate a user URL into something ``ffmpeg``/``ffprobe`` understands."""

    text = str(raw).strip()
    scheme = network_url_scheme(text)
    if scheme is None:
        return text
    replacement = _WEBDAV_TRANSPORT.get(scheme)
    if not replacement:
        return text
    return f"{replacement}://{text.split('://', 1)[1]}"


def mask_credentials(raw: str) -> str:
    """Hide userinfo passwords before a URL is echoed back to the browser."""

    text = str(raw or "")
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    if not parsed.password:
        return text
    netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@", 1)
    return urlunparse(parsed._replace(netloc=netloc))


def network_source_name(raw: str, fallback: str = "network_stream") -> str:
    """Clean, extension-safe display/output name derived from a URL basename."""

    text = str(raw or "").strip()
    name = ""
    try:
        name = unquote(Path(urlparse(text).path).name)
    except ValueError:
        name = ""
    name = _URL_BASENAME_UNSAFE.sub("_", name).strip("._")
    if not name:
        host = ""
        with_scheme = text.split("://", 1)
        if len(with_scheme) == 2:
            host = with_scheme[1].split("/", 1)[0].split("@")[-1].split(":")[0]
        host = _URL_BASENAME_UNSAFE.sub("_", host).strip("._")
        name = f"{host}_{fallback}" if host else fallback
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        if suffix in _NORMALISED_SUFFIXES:
            name = f"{Path(name).stem or name}{VIDEO_EXTENSIONS[0]}"
        else:
            name = f"{name}{VIDEO_EXTENSIONS[0]}"
    return name


def network_input_args(source: str | os.PathLike[str]) -> list[str]:
    """Input arguments (flags + ``-i`` target) for ffmpeg/ffprobe.

    Local paths are passed through untouched. Network URLs get reconnect flags
    so a dropped connection (Wi-Fi hiccup, NAS sleep) never kills a render.
    """

    if not is_network_url(source):
        return ["-i", str(source)]
    url = ffmpeg_network_url(str(source))
    scheme = network_url_scheme(source)
    flags = RTSP_RECONNECT_ARGS if scheme and scheme.startswith("rtsp") else HTTP_RECONNECT_ARGS
    return [*flags, "-i", url]

