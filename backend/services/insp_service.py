"""Insta360 ``.insp`` / ``.insv`` container inspection.

Insta360 writes 360° media as ``.insp`` (still frames or short clips) and
``.insv`` (video). Depending on the firmware the file is either a JPEG
container with XMP spherical metadata or an MP4-like stream carrying one or
two fisheye lenses.

Nothing is extracted or copied: ``ffprobe`` reads the streams in place,
Pillow reads the JPEG/XMP payload of still containers, and the very same path
is handed to the render pipeline. :func:`inspect_spherical` never raises so a
slightly unsupported firmware revision still opens in the neutral preview.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import is_spherical_file

logger = logging.getLogger(__name__)

try:  # Pillow is optional at import time so a missing wheel cannot break the API.
    from PIL import Image  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on the runtime image
    Image = None  # type: ignore[assignment]

EQUIRECTANGULAR = "equirectangular"
DUAL_FISHEYE = "dual_fisheye"
UNKNOWN_PROJECTION = "unknown"

#: Lens index suffix Insta360 appends to every file of a dual-lens recording.
_LENS_SUFFIX = re.compile(r"_\d{2}_\d{3}\.(?:insp|insv)$", re.IGNORECASE)
_DUAL_HINTS = re.compile(r"(dual|fisheye|360|lens)", re.IGNORECASE)
_EQUIRECT_KEYWORDS = ("equirectangular", "spherical", "panorama")
_FISHEYE_KEYWORDS = ("fisheye", "dual-fisheye", "dual fisheye")

#: ffprobe demuxers that expose a single still frame as a one-frame stream.
IMAGE_PIPE_FORMATS: frozenset[str] = frozenset(
    {"jpeg_pipe", "png_pipe", "webp_pipe", "image2", "image2pipe"}
)


@dataclass(frozen=True)
class SphericalInfo:
    """Normalised description of a spherical container."""

    extension: str
    container: str = "unknown"  # "jpeg" | "video" | "unknown"
    projection: str = EQUIRECTANGULAR
    projection_source: str = "default"  # "xmp" | "filename" | "default"
    is_still: bool = True
    stereo: str = "mono"  # "mono" | "stereo"
    lens_count: int = 2
    width: int = 0
    height: int = 0
    duration: float = 0.0
    tags: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "extension": self.extension,
            "container": self.container,
            "projection": self.projection,
            "projection_source": self.projection_source,
            "preview_mode": "equirectangular",
            "is_still": self.is_still,
            "stereo": self.stereo,
            "lens_count": self.lens_count,
            "width": self.width,
            "height": self.height,
            "duration": round(float(self.duration), 3),
            "tags": dict(self.tags),
        }


def projection_from_text(text: str | None) -> str | None:
    """Classify a projection keyword found in XMP/EXIF/filename text."""

    if not text:
        return None
    lowered = str(text).lower()
    if any(keyword in lowered for keyword in _FISHEYE_KEYWORDS):
        return DUAL_FISHEYE
    if any(keyword in lowered for keyword in _EQUIRECT_KEYWORDS):
        return EQUIRECTANGULAR
    return None


def projection_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Scan a nested metadata mapping (XMP/ffprobe tags) for a projection."""

    if not metadata:
        return None
    stack: list[Any] = [metadata]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif isinstance(value, str):
            found = projection_from_text(value)
            if found:
                return found
    return None


def projection_from_filename(name: str) -> str | None:
    """Insta360 names dual-lens frames ``..._00_001.insp`` (one file per lens)."""

    if not name:
        return None
    if _LENS_SUFFIX.search(name) or _DUAL_HINTS.search(name):
        return DUAL_FISHEYE
    return None


def _xmp_text(image: Any) -> str:
    """Flatten Pillow's XMP tree (``info['xmp']`` bytes or ``getxmp()``)."""

    chunks: list[str] = []
    raw = image.info.get("xmp") if hasattr(image, "info") else None
    if isinstance(raw, bytes):
        chunks.append(raw.decode("utf-8", "replace"))
    elif isinstance(raw, str):
        chunks.append(raw)
    try:
        xmp = image.getxmp() if hasattr(image, "getxmp") else None
    except (OSError, ValueError):  # pragma: no cover - malformed APP1 segment
        xmp = None
    if isinstance(xmp, dict):
        chunks.extend(str(value) for value in _flatten(xmp))
    return " ".join(chunks)


def _flatten(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _flatten(nested)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _flatten(nested)]
    return [str(value)]


def _read_image(path: Path) -> dict[str, Any] | None:
    if Image is None or not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            return {
                "width": int(image.width or 0),
                "height": int(image.height or 0),
                "format": str(image.format or "").lower(),
                "xmp": _xmp_text(image),
            }
    except (OSError, ValueError, Image.DecompressionBombError):  # type: ignore[union-attr]
        return None


def build_spherical_info(
    path: Path | str,
    *,
    probe: dict[str, Any] | None = None,
    image: dict[str, Any] | None = None,
) -> SphericalInfo:
    """Combine ffprobe/Pillow evidence into a :class:`SphericalInfo`.

    Kept pure so unit tests can describe a container without real media.
    """

    name = Path(str(path)).name
    extension = Path(name).suffix.lower()

    video_stream = None
    duration = 0.0
    tags: dict[str, Any] = {}
    format_name = ""
    if probe:
        streams = [s for s in probe.get("streams") or [] if isinstance(s, dict)]
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        fmt = probe.get("format") or {}
        format_name = str(fmt.get("format_name") or "").split(",")[0]
        try:
            duration = float(fmt.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        tags.update({str(key): value for key, value in (fmt.get("tags") or {}).items()})
        if video_stream:
            tags.update({str(key): value for key, value in (video_stream.get("tags") or {}).items()})

    if image and image.get("xmp"):
        tags.setdefault("xmp", image["xmp"])

    projection = None
    projection_source = "default"
    from_xmp = projection_from_metadata(tags)
    if from_xmp:
        projection, projection_source = from_xmp, "xmp"
    if projection is None:
        from_name = projection_from_filename(name)
        if from_name:
            projection, projection_source = from_name, "filename"

    is_still = video_stream is None or format_name in IMAGE_PIPE_FORMATS
    width = int((video_stream or {}).get("width") or (image or {}).get("width") or 0)
    height = int((video_stream or {}).get("height") or (image or {}).get("height") or 0)

    if is_still:
        image_format = str((image or {}).get("format") or "").lower()
        container = "jpeg" if image_format in ("jpeg", "jpg") else ("image" if image else "unknown")
        duration = 0.0
    else:
        container = "video"
    if is_still and image:
        tags.setdefault("image_format", image.get("format") or "")

    lens_count = 2 if projection == DUAL_FISHEYE else 1
    if projection == EQUIRECTANGULAR and projection_source != "default":
        lens_count = 1

    return SphericalInfo(
        extension=extension,
        container=container,
        projection=projection or EQUIRECTANGULAR,
        projection_source=projection_source,
        is_still=is_still,
        stereo="stereo" if "stereo" in str(tags).lower() else "mono",
        lens_count=lens_count,
        width=width,
        height=height,
        duration=duration,
        tags=tags,
    )


def build_still_metadata(path: Path | str, info: SphericalInfo) -> dict[str, Any]:
    """Metadata payload for an ``.insp`` still, shaped like ``probe_video``."""

    from services.ffprobe_service import format_duration  # local import avoids a cycle

    name = Path(str(path)).name
    return {
        "path": str(path),
        "name": name,
        "network_source": False,
        "preview_supported": True,
        "container": info.container,
        "duration": round(float(info.duration), 3),
        "duration_formatted": format_duration(info.duration),
        "width": info.width,
        "height": info.height,
        "display_width": info.width,
        "display_height": info.height,
        "fps": 0.0,
        "codec": (info.tags.get("image_format") or "jpeg").upper(),
        "codec_long_name": "Insta360 spherical still",
        "pix_fmt": "",
        "bit_rate": 0,
        "size_bytes": _file_size(path),
        "rotation": 0,
        "suggested_rotation": "none",
        "has_audio": False,
        "audio_codec": "",
        "audio_channels": 0,
        "audio_channel_layout": "",
        "audio_sample_rate": 0,
        "video_stream_index": 0,
        "audio_stream_index": None,
        "is_still": True,
        "spherical": info.as_dict(),
    }


def _file_size(path: Path | str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


async def inspect_spherical(path: Path | str) -> dict[str, Any]:
    """Inspect an ``.insp``/``.insv`` file without extracting it.

    Returns ``{"metadata": ..., "spherical": ..., "probe_error": ...}``; the
    metadata is a still-frame payload when no video stream can be probed.
    """

    from services.ffprobe_service import ProbeError, build_metadata, run_ffprobe

    probe: dict[str, Any] | None = None
    probe_error: str | None = None
    try:
        probe = await run_ffprobe(path)
    except ProbeError as exc:
        probe_error = str(exc)
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        probe_error = str(exc)

    image: dict[str, Any] | None = None
    video_stream = None
    format_name = ""
    if probe:
        format_name = str((probe.get("format") or {}).get("format_name") or "").split(",")[0]
        video_stream = next(
            (
                stream
                for stream in probe.get("streams") or []
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ),
            None,
        )
    if video_stream is None or format_name in IMAGE_PIPE_FORMATS:
        try:
            image = await asyncio.to_thread(_read_image, Path(path))
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            logger.debug("Pillow could not read %s: %s", path, exc)

    info = build_spherical_info(path, probe=probe, image=image)

    metadata: dict[str, Any]
    if info.is_still:
        metadata = build_still_metadata(path, info)
    else:
        try:
            metadata = build_metadata(path, probe or {})
        except ProbeError as exc:  # pragma: no cover - ffprobe raced the read
            probe_error = probe_error or str(exc)
            metadata = build_still_metadata(path, info)
        metadata["spherical"] = info.as_dict()

    if probe_error:
        metadata["probe_error"] = probe_error
    return {"metadata": metadata, "spherical": info.as_dict(), "probe_error": probe_error}


def is_spherical_path(path: Path | str) -> bool:
    return is_spherical_file(Path(path))
