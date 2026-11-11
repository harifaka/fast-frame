"""Builds the FFmpeg command line and ``filter_complex`` graph.

The module is intentionally dependency-free (standard library only) so the
command construction can be unit-tested without FastAPI or ffmpeg installed.

Pipeline order (later phases extend this):
    subtitles (1x) -> multi-trim & concat -> rotate/flip -> crop -> speed -> NVENC
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from config import (
    AUDIO_BITRATE,
    FFMPEG_BIN,
    NVENC_H264_CQ,
    NVENC_HEVC_CQ,
    NVENC_PRESET,
    SUPPORTED_SPEED_MAX,
    SUPPORTED_SPEED_MIN,
    X264_CRF,
    X265_CRF,
)

ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0
# Guards against floating point noise such as 2.0000000000000004.
_EPSILON = 1e-6


class FFmpegBuildError(ValueError):
    """Raised when the render options cannot produce a valid command."""


@dataclass(slots=True)
class RenderOptions:
    """Everything the builder needs to produce a deterministic render."""

    input_path: str
    output_path: str
    speed: float = 1.0
    codec: str = "h264"
    has_audio: bool = True
    source_width: int = 0
    source_height: int = 0
    source_duration: float = 0.0
    h264_encoder: str = "h264_nvenc"
    hevc_encoder: str = "hevc_nvenc"
    audio_bitrate: str = AUDIO_BITRATE
    # FFmpeg 5+ replaced ``-vsync`` with ``-fps_mode``.
    ffmpeg_major: int = 5

    @property
    def encoder(self) -> str:
        return self.hevc_encoder if self.codec == "hevc" else self.h264_encoder

    @property
    def is_hardware(self) -> bool:
        return self.encoder.endswith("_nvenc")

    @property
    def container(self) -> str:
        return Path(self.output_path).suffix.lower()


@dataclass(slots=True)
class FilterGraph:
    """A rendered ``filter_complex`` plus the pads that must be mapped."""

    filter_complex: str
    video_label: str | None = None
    audio_label: str | None = None
    statements: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.statements)


def _format_number(value: float) -> str:
    """Render floats without trailing zeros, using a dot as decimal separator."""

    rounded = round(float(value), 4)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def validate_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError) as exc:
        raise FFmpegBuildError("Speed must be a number") from exc
    if math.isnan(value) or math.isinf(value):
        raise FFmpegBuildError("Speed must be a finite number")
    if value < SUPPORTED_SPEED_MIN - _EPSILON or value > SUPPORTED_SPEED_MAX + _EPSILON:
        raise FFmpegBuildError(
            f"Speed {value:g}× is outside the supported "
            f"{SUPPORTED_SPEED_MIN:g}×–{SUPPORTED_SPEED_MAX:g}× range"
        )
    return round(value, 4)


def build_atempo_chain(speed: float) -> str | None:
    """Return an ``atempo`` chain that preserves pitch for ``speed``.

    ``atempo`` only accepts 0.5–2.0 per instance, so extreme values are chained
    (3× → ``atempo=2,atempo=1.5``, 0.25× → ``atempo=0.5,atempo=0.5``).
    """

    value = abs(float(speed))
    if value <= 0:
        raise FFmpegBuildError("Speed must be greater than zero")
    if abs(value - 1.0) < 1e-9:
        return None

    factors: list[float] = []
    remaining = value
    while remaining > ATEMPO_MAX + _EPSILON:
        factors.append(ATEMPO_MAX)
        remaining /= ATEMPO_MAX
    while remaining < ATEMPO_MIN - _EPSILON:
        factors.append(ATEMPO_MIN)
        remaining /= ATEMPO_MIN
    factors.append(remaining)

    # Fold floating point drift into the final factor of each pair.
    normalised = [_format_number(factor) for factor in factors]
    return ",".join(f"atempo={factor}" for factor in normalised)


def _video_codec_args(options: RenderOptions) -> list[str]:
    encoder = options.encoder
    is_mp4 = options.container == ".mp4"

    if encoder == "h264_nvenc":
        args = ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-rc", "vbr",
                "-cq", str(NVENC_H264_CQ), "-b:v", "0", "-pix_fmt", "yuv420p"]
    elif encoder == "hevc_nvenc":
        args = ["-c:v", "hevc_nvenc", "-preset", NVENC_PRESET, "-rc", "vbr",
                "-cq", str(NVENC_HEVC_CQ), "-b:v", "0", "-pix_fmt", "yuv420p"]
        if is_mp4:
            args += ["-tag:v", "hvc1"]
    elif encoder == "libx264":
        args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(X264_CRF),
                "-pix_fmt", "yuv420p"]
    elif encoder == "libx265":
        args = ["-c:v", "libx265", "-preset", "veryfast", "-crf", str(X265_CRF),
                "-pix_fmt", "yuv420p"]
        if is_mp4:
            args += ["-tag:v", "hvc1"]
    else:
        raise FFmpegBuildError(f"Unsupported encoder: {encoder}")
    return args


def build_filter_graph(options: RenderOptions) -> FilterGraph:
    """Create the video/audio filter graph for the requested transforms."""

    speed = validate_speed(options.speed)
    statements: list[str] = []
    video_label = "0:v:0"
    audio_label = "0:a:0" if options.has_audio else None
    needs_reencode = abs(speed - 1.0) > _EPSILON

    if needs_reencode:
        statements.append(f"[{video_label}]setpts=PTS/{_format_number(speed)}[v_speed]")
        video_label = "v_speed"
        if audio_label:
            chain = build_atempo_chain(speed)
            statements.append(f"[{audio_label}]{chain}[a_speed]")
            audio_label = "a_speed"

    return FilterGraph(
        filter_complex=";".join(statements),
        video_label=video_label if statements else None,
        audio_label=audio_label if statements else None,
        statements=statements,
    )


def build_ffmpeg_command(options: RenderOptions) -> list[str]:
    """Return the full argv list for an ffmpeg render."""

    speed = validate_speed(options.speed)
    input_path = Path(options.input_path)
    output_path = Path(options.output_path)
    if not options.input_path or not options.output_path:
        raise FFmpegBuildError("Input and output paths are required")
    if output_path.suffix.lower() not in {".mp4", ".mkv"}:
        raise FFmpegBuildError("Output must be an .mp4 or .mkv file")

    graph = build_filter_graph(options)
    command: list[str] = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        # We control orientation explicitly; never let ffmpeg double-rotate.
        "-noautorotate",
        "-i",
        str(input_path),
    ]

    if graph:
        command += ["-filter_complex", graph.filter_complex]
        command += ["-map", f"[{graph.video_label}]"]
        if options.has_audio and graph.audio_label:
            command += ["-map", f"[{graph.audio_label}]"]
    else:
        command += ["-map", "0:v:0"]
        if options.has_audio:
            command += ["-map", "0:a:0"]

    command += _video_codec_args(options)

    if options.has_audio:
        command += ["-c:a", "aac", "-b:a", options.audio_bitrate]
    else:
        command += ["-an"]

    if abs(speed - 1.0) > _EPSILON:
        # Keep the exact timestamps produced by setpts instead of resampling to
        # the nominal frame rate (which would silently drop frames on 2×+).
        if options.ffmpeg_major >= 5:
            command += ["-fps_mode", "passthrough"]
        else:
            command += ["-vsync", "passthrough"]
    if options.container == ".mp4":
        command += ["-movflags", "+faststart"]
    # Never inherit a display matrix from the source (rotation is explicit).
    command += ["-map_metadata", "-1"]

    command += ["-progress", "pipe:1", "-nostats", str(output_path)]
    return command


def target_duration(options: RenderOptions) -> float:
    """Duration of the rendered output in seconds (used for progress/ETA)."""

    source_duration = max(0.0, float(options.source_duration or 0.0))
    speed = validate_speed(options.speed)
    return source_duration / speed if speed else source_duration


def describe_encoder(encoder: str) -> str:
    if encoder == "h264_nvenc":
        return "H.264 · NVENC (GPU)"
    if encoder == "hevc_nvenc":
        return "HEVC · NVENC (GPU)"
    if encoder == "libx264":
        return "H.264 · libx264 (CPU)"
    if encoder == "libx265":
        return "HEVC · libx265 (CPU)"
    return encoder


def iter_progress_lines(raw: bytes) -> Iterable[str]:  # pragma: no cover - helper
    for line in raw.decode("utf-8", "replace").splitlines():
        yield line.strip()


def parse_progress_field(key: str, value: str) -> tuple[str, Any]:
    """Convert a ``-progress`` key/value pair into typed data."""

    value = value.strip()
    if key in {"out_time_us", "out_time_ms", "frame", "total_size"}:
        try:
            return key, int(value)
        except ValueError:
            return key, None
    if key in {"fps", "speed", "bitrate", "dup_frames", "drop_frames"}:
        cleaned = value.rstrip("x")
        try:
            return key, float(cleaned)
        except ValueError:
            return key, None
    return key, value
