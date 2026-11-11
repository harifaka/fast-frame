"""Thin async wrappers around ``ffprobe`` plus metadata normalisation."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from config import FFPROBE_BIN

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 30.0


class ProbeError(RuntimeError):
    """Raised when ffprobe cannot read a media file."""


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_frame_rate(value: str | None) -> float:
    """Convert an ffprobe rational (``30000/1001``) into frames per second."""

    if not value:
        return 0.0
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        try:
            den = float(denominator)
            return round(float(numerator) / den, 3) if den else 0.0
        except ValueError:
            return 0.0
    return round(_to_float(value), 3)


def _rotation_degrees(stream: dict[str, Any]) -> int:
    rotation = 0
    for side_data in stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            rotation = int(round(_to_float(side_data.get("rotation"))))
            break
    if not rotation:
        tags = stream.get("tags") or {}
        if "rotate" in tags:
            rotation = int(round(_to_float(tags.get("rotate"))))
    normalised = rotation % 360
    if normalised > 180:
        normalised -= 360
    return normalised


def suggested_rotation(rotation: int) -> str:
    """Map an ffmpeg display-matrix angle to our transform enum.

    ffprobe reports the counter-clockwise angle needed to display the frame, so
    a phone recording with ``rotation: -90`` has to be turned 90° clockwise.
    """

    if rotation == 90:
        return "90_ccw"
    if rotation in (-90, 270):
        return "90_cw"
    if abs(rotation) == 180:
        return "180"
    return "none"


async def run_ffprobe(path: Path, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    args = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:  # ffprobe not installed
        raise ProbeError("ffprobe is not available on this host") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ProbeError("ffprobe timed out while reading the file") from exc

    if process.returncode != 0:
        message = stderr.decode("utf-8", "replace").strip().splitlines()
        detail = message[-1] if message else "unknown ffprobe error"
        raise ProbeError(f"ffprobe failed: {detail}")

    try:
        return json.loads(stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned malformed JSON") from exc


def build_metadata(path: Path, probe: dict[str, Any]) -> dict[str, Any]:
    streams = [s for s in probe.get("streams") or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError("No video stream found in the selected file")

    fmt = probe.get("format") or {}
    duration = _to_float(fmt.get("duration")) or _to_float(video.get("duration"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = _rotation_degrees(video)
    swap = rotation in (90, -90, 270)
    display_width, display_height = (height, width) if swap else (width, height)
    fps = parse_frame_rate(video.get("avg_frame_rate")) or parse_frame_rate(
        video.get("r_frame_rate")
    )

    try:
        size_bytes = int(fmt.get("size") or path.stat().st_size)
    except OSError:
        size_bytes = 0

    audio_channels = int(audio.get("channels") or 0) if audio else 0
    audio_layout = (audio.get("channel_layout") or "") if audio else ""

    return {
        "path": str(path),
        "name": path.name,
        "container": (fmt.get("format_name") or path.suffix.lstrip(".")),
        "duration": round(duration, 3),
        "duration_formatted": format_duration(duration),
        "width": width,
        "height": height,
        "display_width": display_width,
        "display_height": display_height,
        "fps": fps,
        "codec": (video.get("codec_name") or "unknown"),
        "codec_long_name": (video.get("codec_long_name") or ""),
        "pix_fmt": video.get("pix_fmt") or "",
        "bit_rate": int(_to_float(fmt.get("bit_rate"))),
        "size_bytes": size_bytes,
        "rotation": rotation,
        "suggested_rotation": suggested_rotation(rotation),
        "has_audio": audio is not None,
        "audio_codec": (audio.get("codec_name") or "") if audio else "",
        "audio_channels": audio_channels,
        "audio_channel_layout": audio_layout,
        "audio_sample_rate": int(_to_float(audio.get("sample_rate"))) if audio else 0,
        "video_stream_index": int(video.get("index") or 0),
        "audio_stream_index": int(audio.get("index")) if audio and audio.get("index") is not None else None,
    }


async def probe_video(path: Path) -> dict[str, Any]:
    """Return normalised metadata for the video at ``path``."""

    probe = await run_ffprobe(path)
    return build_metadata(path, probe)


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}.{millis:03d}" if millis else f"{minutes:02d}:{secs:02d}"
