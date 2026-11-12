"""Builds the FFmpeg command line and ``filter_complex`` graph.

The module is intentionally dependency-free (standard library only) so the
command construction can be unit-tested without FastAPI or ffmpeg installed.

Pipeline order (later phases extend this):
    subtitles (1x) -> multi-trim & concat -> rotate/flip -> crop -> AI tracking -> speed -> NVENC
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
    is_network_url,
    network_input_args,
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
    # Keep-segments in seconds; empty means "the whole video".
    segments: list[dict[str, float]] = field(default_factory=list)
    # Optional 1x subtitle burn-in applied before trimming.
    subtitle_path: str | None = None
    subtitle_font_size: int = 24
    # Visual transforms applied after trim/concat and before the speed change.
    rotation: str = "none"
    flip_horizontal: bool = False
    crop: dict[str, float] | None = None
    # Level correction (degrees) for spherical sources, positive = clockwise.
    roll: float = 0.0
    # YOLO auto-framing: {"enabled", "level", "mode", "keyframes"}.
    ai_tracking: dict[str, Any] | None = None

    @property
    def encoder(self) -> str:
        return self.hevc_encoder if self.codec == "hevc" else self.h264_encoder

    @property
    def is_hardware(self) -> bool:
        return self.encoder.endswith("_nvenc")

    @property
    def container(self) -> str:
        return Path(self.output_path).suffix.lower()

    @property
    def network_source(self) -> bool:
        """True when the input is read straight from an HTTP/RTSP/WebDAV URL."""

        return is_network_url(self.input_path)


@dataclass(slots=True)
class FilterGraph:
    """A rendered ``filter_complex`` plus the pads that must be mapped."""

    filter_complex: str
    video_label: str | None = None
    audio_label: str | None = None
    statements: list[str] = field(default_factory=list)
    timing_changed: bool = False

    def __bool__(self) -> bool:
        return bool(self.statements)


def _format_number(value: float, precision: int = 4) -> str:
    """Render floats without trailing zeros, using a dot as decimal separator."""

    rounded = round(float(value), precision)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.{precision}f}".rstrip("0").rstrip(".")


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


def normalize_segments(
    segments: list[dict[str, float]] | None, source_duration: float = 0.0
) -> list[dict[str, float]]:
    """Validate, clamp and order keep-segments by start time."""

    cleaned: list[dict[str, float]] = []
    for raw in segments or []:
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FFmpegBuildError("Each segment needs numeric start and end values") from exc
        if start < 0 or end < 0:
            raise FFmpegBuildError("Segment times cannot be negative")
        if source_duration > 0:
            start = min(start, source_duration)
            end = min(end, source_duration)
        if end - start <= 1e-6:
            raise FFmpegBuildError("Segment end must be greater than start")
        cleaned.append({"start": round(start, 6), "end": round(end, 6)})
    cleaned.sort(key=lambda segment: segment["start"])
    return cleaned


def escape_subtitle_path(path: str) -> str:
    """Escape a filesystem path for the ``subtitles`` filter.

    FFmpeg parses filter options twice (filtergraph + filter), so Windows drive
    letters, commas, brackets and apostrophes all need backslash escapes. Paths
    are normalised to forward slashes first.
    """

    text = str(path).replace("\\", "/")
    for char in ("'", ":", ",", ";", "[", "]", "="):
        text = text.replace(char, f"\\{char}")
    return text


def build_subtitle_filter(options: RenderOptions) -> str | None:
    if not options.subtitle_path:
        return None
    font_size = int(options.subtitle_font_size or 24)
    escaped = escape_subtitle_path(options.subtitle_path)
    return (
        f"subtitles=filename='{escaped}'"
        f":force_style='FontSize={font_size},Outline=1,Shadow=0'"
    )


ROTATION_FILTERS: dict[str, list[str]] = {
    "none": [],
    "90_cw": ["transpose=1"],
    "90_ccw": ["transpose=2"],
    "180": ["transpose=1", "transpose=1"],
}


def oriented_dimensions(width: int, height: int, rotation: str) -> tuple[int, int]:
    """Dimensions after rotation (90°/270° swap width and height)."""

    if rotation in {"90_cw", "90_ccw"}:
        return height, width
    return width, height


def build_transform_filters(options: RenderOptions) -> list[str]:
    """Rotation/flip/roll filters; the roll keeps spherical horizons level."""

    if options.rotation not in ROTATION_FILTERS:
        raise FFmpegBuildError(f"Unsupported rotation: {options.rotation}")
    filters = list(ROTATION_FILTERS[options.rotation])
    if options.flip_horizontal:
        filters.append("hflip")
    roll = float(options.roll or 0.0)
    if abs(roll) > 1e-3:
        # ``ow=iw:oh=ih`` keeps the frame size (and every later crop
        # coordinate) unchanged; the corners are filled black.
        radians = f"({_format_number(roll, 3)}*PI/180)"
        filters.append(f"rotate={radians}:ow=iw:oh=ih:fillcolor=black")
    return filters


def _even(value: int) -> int:
    return int(value) - (int(value) % 2)


def compute_crop_pixels(options: RenderOptions) -> tuple[int, int, int, int] | None:
    """Convert the normalised crop rectangle into even pixel coordinates.

    Rotation happens before cropping, so the frame dimensions used here are the
    *oriented* ones. yuv420p needs even width, height and offsets for clean
    chroma alignment.
    """

    crop = options.crop
    if not crop:
        return None

    frame_width, frame_height = oriented_dimensions(
        int(options.source_width or 0), int(options.source_height or 0), options.rotation
    )
    if frame_width <= 0 or frame_height <= 0:
        raise FFmpegBuildError("Source dimensions are required to compute a crop")

    try:
        x = float(crop["x"])
        y = float(crop["y"])
        w = float(crop["w"])
        h = float(crop["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FFmpegBuildError("Crop needs numeric x, y, w and h values") from exc

    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.0), 1.0 - x)
    h = min(max(h, 0.0), 1.0 - y)

    crop_w = min(_even(round(w * frame_width)), _even(frame_width))
    crop_h = min(_even(round(h * frame_height)), _even(frame_height))
    if crop_w < 2 or crop_h < 2:
        raise FFmpegBuildError("Crop region is too small")

    crop_x = _even(round(x * frame_width))
    crop_y = _even(round(y * frame_height))
    crop_x = max(0, min(crop_x, frame_width - crop_w))
    crop_y = max(0, min(crop_y, frame_height - crop_h))
    return crop_w, crop_h, crop_x, crop_y


def build_crop_filter(options: RenderOptions) -> str | None:
    pixels = compute_crop_pixels(options)
    if pixels is None:
        return None
    crop_w, crop_h, crop_x, crop_y = pixels
    return f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"


AI_TRACKING_MODES: tuple[str, ...] = ("rectilinear", "spherical")
#: Deeper expression trees get expensive to evaluate per frame, so extremely
#: long tracking passes are decimated before they reach the filter graph.
AI_TRACKING_MAX_EXPRESSION_KEYFRAMES = 240


def normalize_tracking_keyframes(
    keyframes: Iterable[dict[str, Any]] | None, source_duration: float = 0.0
) -> list[dict[str, float]]:
    """Validate, clamp and order YOLO framing keyframes by time."""

    cleaned: list[dict[str, float]] = []
    for raw in keyframes or []:
        try:
            time_value = float(raw["time"])
            pan = float(raw.get("pan", 0.0))
            tilt = float(raw.get("tilt", 0.0))
            zoom = float(raw.get("zoom", 1.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise FFmpegBuildError(
                "AI tracking keyframes need numeric time/pan/tilt/zoom values"
            ) from exc
        if not all(map(math.isfinite, (time_value, pan, tilt, zoom))):
            raise FFmpegBuildError("AI tracking keyframes must be finite numbers")
        if time_value < 0:
            raise FFmpegBuildError("AI tracking keyframes cannot be negative")
        if source_duration > 0:
            time_value = min(time_value, source_duration)
        cleaned.append(
            {
                "time": round(time_value, 3),
                "pan": round(min(180.0, max(-180.0, pan)), 3),
                "tilt": round(min(90.0, max(-90.0, tilt)), 3),
                "zoom": round(min(8.0, max(1.0, zoom)), 3),
            }
        )

    cleaned.sort(key=lambda frame: frame["time"])
    deduped: list[dict[str, float]] = []
    for frame in cleaned:
        if deduped and abs(deduped[-1]["time"] - frame["time"]) < 1e-3:
            deduped[-1] = frame
        else:
            deduped.append(frame)
    return deduped


def ai_tracking_window(
    frame_width: int,
    frame_height: int,
    keyframe: dict[str, float],
    *,
    mode: str = "rectilinear",
    output_aspect: float = 16 / 9,
) -> tuple[float, float, float, float]:
    """Pixel window ``(w, h, x, y)`` for one tracking keyframe.

    ``pan``/``tilt`` are angular offsets from the frame centre.  Spherical
    sources map yaw across the full 360° equirect width; rectilinear sources map
    the horizontal offset across the full frame (±180° = ±half width) so the
    tracker and renderer agree on the same keyframe document.
    """

    zoom = min(8.0, max(1.0, float(keyframe.get("zoom", 1.0))))
    pan = float(keyframe.get("pan", 0.0))
    tilt = float(keyframe.get("tilt", 0.0))

    if mode == "spherical":
        width = frame_width / zoom
        height = width / max(output_aspect, 0.1)
        if height > frame_height:
            height = frame_height
            width = min(frame_width, height * output_aspect)
        centre_x = (0.5 + pan / 360.0) * frame_width
    else:
        width = frame_width / zoom
        height = frame_height / zoom
        centre_x = (0.5 + pan / 180.0) * frame_width

    centre_y = (0.5 - tilt / 180.0) * frame_height
    x = min(max(centre_x - width / 2.0, 0.0), frame_width - width)
    y = min(max(centre_y - height / 2.0, 0.0), frame_height - height)
    return width, height, x, y


def interpolate_expression(
    points: list[tuple[float, float]], variable: str = "t"
) -> str:
    """Piecewise-linear FFmpeg expression through ``(time, value)`` points."""

    if not points:
        raise FFmpegBuildError("Cannot interpolate an empty point list")
    if len(points) == 1:
        return _format_number(points[0][1])

    expression = _format_number(points[-1][1])
    for index in range(len(points) - 2, -1, -1):
        start_time, start_value = points[index]
        end_time, end_value = points[index + 1]
        span = max(end_time - start_time, 1e-6)
        slope = (end_value - start_value) / span
        segment = (
            f"({_format_number(start_value)}+"
            f"({variable}-{_format_number(start_time)})*{_format_number(slope, 6)})"
        )
        expression = (
            f"if(lt({variable},{_format_number(end_time)}),{segment},{expression})"
        )
    return expression


def add_even_rounding(expression: str) -> str:
    """Keep a pixel expression even for clean yuv420p chroma alignment."""

    return f"trunc(({expression})/2)*2"


def sample_tracking_frame(
    frames: list[dict[str, float]], time_value: float
) -> dict[str, float]:
    """Linear interpolation of the tracking keyframe track at ``time_value``."""

    if time_value <= frames[0]["time"]:
        return dict(frames[0])
    if time_value >= frames[-1]["time"]:
        return dict(frames[-1])
    for previous, following in zip(frames, frames[1:]):
        if previous["time"] <= time_value <= following["time"]:
            span = max(following["time"] - previous["time"], 1e-6)
            ratio = (time_value - previous["time"]) / span
            return {
                "time": time_value,
                "pan": previous["pan"] + (following["pan"] - previous["pan"]) * ratio,
                "tilt": previous["tilt"] + (following["tilt"] - previous["tilt"]) * ratio,
                "zoom": previous["zoom"] + (following["zoom"] - previous["zoom"]) * ratio,
            }
    return dict(frames[-1])


def remap_tracking_keyframes(
    frames: list[dict[str, float]],
    segments: list[dict[str, float]] | None,
    source_duration: float = 0.0,
) -> list[dict[str, float]]:
    """Re-time source keyframes onto the concatenated keep-segment timeline.

    ``trim`` + ``setpts=PTS-STARTPTS`` restarts the clip clock, so a keyframe
    captured at 42 s of the source has to move to its offset inside the cut.
    """

    cleaned = normalize_segments(segments, source_duration)
    if not cleaned or not frames:
        return frames

    remapped: list[dict[str, float]] = []
    offset = 0.0
    for segment in cleaned:
        boundaries = [segment["start"]]
        boundaries += [
            frame["time"]
            for frame in frames
            if segment["start"] < frame["time"] < segment["end"]
        ]
        boundaries.append(segment["end"])
        for boundary in boundaries:
            sample = sample_tracking_frame(frames, boundary)
            sample["time"] = round(offset + (boundary - segment["start"]), 3)
            remapped.append(sample)
        offset += segment["end"] - segment["start"]
    return remapped


def build_ai_tracking_filter(options: RenderOptions) -> str | None:
    """Time-varying ``crop``/``scale`` that follows the tracked subject.

    YOLO runs offline and stores pan/tilt/zoom keyframes; the render itself
    only evaluates these interpolated expressions, so auto-framing costs no
    extra inference during encoding.
    """

    tracking = options.ai_tracking or {}
    if not tracking.get("enabled"):
        return None

    mode = str(tracking.get("mode") or "rectilinear").strip().lower()
    if mode not in AI_TRACKING_MODES:
        raise FFmpegBuildError(f"Unsupported AI tracking mode: {mode}")

    frames = normalize_tracking_keyframes(
        tracking.get("keyframes"), options.source_duration
    )
    if len(frames) < 2:
        return None
    frames = remap_tracking_keyframes(frames, options.segments, options.source_duration)
    if frames[0]["time"] > 0:
        # Hold the first framing before its timestamp instead of extrapolating.
        frames = [{**frames[0], "time": 0.0}, *frames]

    frame_width, frame_height = oriented_dimensions(
        int(options.source_width or 0), int(options.source_height or 0), options.rotation
    )
    if frame_width <= 0 or frame_height <= 0:
        raise FFmpegBuildError("Source dimensions are required for AI tracking")

    if len(frames) > AI_TRACKING_MAX_EXPRESSION_KEYFRAMES:
        last = frames[-1]
        step = len(frames) / float(AI_TRACKING_MAX_EXPRESSION_KEYFRAMES)
        frames = [
            frames[min(len(frames) - 1, int(index * step))]
            for index in range(AI_TRACKING_MAX_EXPRESSION_KEYFRAMES)
        ]
        frames[-1] = last

    output_aspect = frame_width / frame_height
    width_points: list[tuple[float, float]] = []
    height_points: list[tuple[float, float]] = []
    x_points: list[tuple[float, float]] = []
    y_points: list[tuple[float, float]] = []
    for frame in frames:
        width, height, x, y = ai_tracking_window(
            frame_width,
            frame_height,
            frame,
            mode=mode,
            output_aspect=output_aspect,
        )
        width_points.append((frame["time"], width))
        height_points.append((frame["time"], height))
        x_points.append((frame["time"], x))
        y_points.append((frame["time"], y))

    width_expr = add_even_rounding(interpolate_expression(width_points))
    height_expr = add_even_rounding(interpolate_expression(height_points))
    x_expr = add_even_rounding(
        f"min(max({interpolate_expression(x_points)},0),iw-out_w)"
    )
    y_expr = add_even_rounding(
        f"min(max({interpolate_expression(y_points)},0),ih-out_h)"
    )

    return (
        f"crop=w='{width_expr}':h='{height_expr}'"
        f":x='{x_expr}':y='{y_expr}'"
        # Even pixel rounding nudges the window aspect by <1 px; setsar=1 keeps
        # the output square-pixel instead of inheriting a stretched SAR.
        f",scale={_even(frame_width)}:{_even(frame_height)},setsar=1"
    )


def build_filter_graph(options: RenderOptions) -> FilterGraph:
    """Create the video/audio filter graph for the requested transforms.

    Order: subtitles (1x) -> trim/concat (keep segments) -> rotate/flip ->
    crop -> AI tracking -> speed. Subtitles are burned before trimming so they
    stay synced to the original timeline no matter which parts are kept or how
    fast they play.
    """

    speed = validate_speed(options.speed)
    statements: list[str] = []
    video_label = "0:v:0"
    audio_pad: str | None = None
    timing_changed = False

    subtitle_filter = build_subtitle_filter(options)
    if subtitle_filter:
        statements.append(f"[{video_label}]{subtitle_filter}[v_sub]")
        video_label = "v_sub"

    segments = normalize_segments(options.segments, options.source_duration)
    if segments:
        count = len(segments)
        for index, segment in enumerate(segments):
            start = _format_number(segment["start"])
            end = _format_number(segment["end"])
            statements.append(
                f"[{video_label}]trim=start={start}:end={end},"
                f"setpts=PTS-STARTPTS[v{index}]"
            )
            if options.has_audio:
                statements.append(
                    f"[0:a:0]atrim=start={start}:end={end},"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )
        if count == 1:
            video_label = "v0"
            audio_pad = "a0" if options.has_audio else None
        elif options.has_audio:
            inputs = "".join(f"[v{index}][a{index}]" for index in range(count))
            statements.append(f"{inputs}concat=n={count}:v=1:a=1[v_cat][a_cat]")
            video_label = "v_cat"
            audio_pad = "a_cat"
        else:
            inputs = "".join(f"[v{index}]" for index in range(count))
            statements.append(f"{inputs}concat=n={count}:v=1:a=0[v_cat]")
            video_label = "v_cat"
        timing_changed = True

    transforms = build_transform_filters(options)
    if transforms:
        statements.append(f"[{video_label}]{','.join(transforms)}[v_transform]")
        video_label = "v_transform"

    crop_filter = build_crop_filter(options)
    if crop_filter:
        statements.append(f"[{video_label}]{crop_filter}[v_crop]")
        video_label = "v_crop"

    tracking_filter = build_ai_tracking_filter(options)
    if tracking_filter:
        statements.append(f"[{video_label}]{tracking_filter}[v_ai]")
        video_label = "v_ai"

    if abs(speed - 1.0) > _EPSILON:
        statements.append(f"[{video_label}]setpts=PTS/{_format_number(speed)}[v_speed]")
        video_label = "v_speed"
        timing_changed = True
        if options.has_audio:
            chain = build_atempo_chain(speed)
            audio_source = f"[{audio_pad}]" if audio_pad else "[0:a:0]"
            statements.append(f"{audio_source}{chain}[a_speed]")
            audio_pad = "a_speed"

    return FilterGraph(
        filter_complex=";".join(statements),
        video_label=video_label if statements else None,
        audio_label=audio_pad,
        statements=statements,
        timing_changed=timing_changed,
    )


def build_ffmpeg_command(options: RenderOptions) -> list[str]:
    """Return the full argv list for an ffmpeg render."""

    speed = validate_speed(options.speed)
    if not options.input_path or not options.output_path:
        raise FFmpegBuildError("Input and output paths are required")
    output_path = Path(options.output_path)
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
        # Local paths are passed through; URLs get reconnect flags so a dropped
        # connection does not abort the render.
        *network_input_args(options.input_path),
    ]

    if graph:
        command += ["-filter_complex", graph.filter_complex]
        command += ["-map", f"[{graph.video_label}]"]
        if options.has_audio:
            if graph.audio_label:
                command += ["-map", f"[{graph.audio_label}]"]
            else:
                command += ["-map", "0:a:0"]
    else:
        command += ["-map", "0:v:0"]
        if options.has_audio:
            command += ["-map", "0:a:0"]

    command += _video_codec_args(options)

    if options.has_audio:
        command += ["-c:a", "aac", "-b:a", options.audio_bitrate]
    else:
        command += ["-an"]

    if graph.timing_changed:
        # Keep the exact timestamps produced by setpts/trim instead of resampling
        # to the nominal frame rate (which would silently drop frames on 2×+).
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

    speed = validate_speed(options.speed)
    segments = normalize_segments(options.segments, options.source_duration)
    if segments:
        total = sum(segment["end"] - segment["start"] for segment in segments)
    else:
        total = max(0.0, float(options.source_duration or 0.0))
    return total / speed if speed else total


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
