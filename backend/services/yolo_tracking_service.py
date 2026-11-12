"""Ultra-lightweight YOLO subject tracking for AI auto-framing.

The service has two halves:

* **Pure framing math** - subject selection, equirect/rectilinear mapping,
  three strength profiles and the smoothing/keyframe generation.  It is
  stdlib-only so it can be unit-tested without numpy or a model.
* **Detection backends** - ``ultralytics`` when the package is installed, or
  ``onnxruntime`` with an exported YOLOv8n ``.onnx`` file.  Both are imported
  lazily so a slim image without the AI extras keeps working; the UI simply
  reports the tracker as unavailable with an actionable reason.

Tracking is an *offline* pass: frames are sampled at a low rate once, resolved
into ``pan``/``tilt``/``zoom`` keyframes, and the render itself only evaluates
FFmpeg expressions, so enabling auto-framing never slows the encode down.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from config import FFMPEG_BIN, is_network_url, is_spherical_file

logger = logging.getLogger(__name__)

#: FFmpeg decodes every sample at this width; YOLO resizes internally anyway.
SAMPLE_FRAME_WIDTH = 640
DEFAULT_SAMPLE_FPS = 2.0
DEFAULT_MAX_SAMPLES = 180
MAX_KEYFRAMES = 1200
FRAME_EXTRACT_TIMEOUT = 30.0
DEFAULT_OUTPUT_ASPECT = 16.0 / 9.0

#: COCO classes the framer is willing to follow (people, pets, vehicles).
TRACKABLE_CLASSES = frozenset(
    {
        "person",
        "bicycle",
        "car",
        "motorcycle",
        "bus",
        "train",
        "truck",
        "bird",
        "cat",
        "dog",
        "horse",
        "sheep",
        "cow",
    }
)


class TrackingError(ValueError):
    """Raised when a tracking request cannot be satisfied."""


@dataclass(frozen=True, slots=True)
class LevelProfile:
    """Tuning for one of the three selectable tracking strengths."""

    level: int
    name: str
    description: str
    smoothing_seconds: float
    deadband_degrees: float
    max_speed_deg_per_second: float
    margin: float
    max_zoom: float
    min_confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "name": self.name,
            "description": self.description,
            "margin": self.margin,
            "max_zoom": self.max_zoom,
        }


LEVEL_PROFILES: dict[int, LevelProfile] = {
    1: LevelProfile(
        level=1,
        name="Smooth / Cinematic",
        description="Slow damped follow with wide margins",
        smoothing_seconds=1.1,
        deadband_degrees=2.5,
        max_speed_deg_per_second=35.0,
        margin=0.55,
        max_zoom=1.8,
        min_confidence=0.35,
    ),
    2: LevelProfile(
        level=2,
        name="Dynamic / Action",
        description="Balanced speed with tighter framing",
        smoothing_seconds=0.45,
        deadband_degrees=1.0,
        max_speed_deg_per_second=90.0,
        margin=0.28,
        max_zoom=2.6,
        min_confidence=0.3,
    ),
    3: LevelProfile(
        level=3,
        name="Aggressive / Lock-on",
        description="Fast snapping and strict centering",
        smoothing_seconds=0.15,
        deadband_degrees=0.0,
        max_speed_deg_per_second=240.0,
        margin=0.1,
        max_zoom=3.6,
        min_confidence=0.25,
    ),
}

DEFAULT_LEVEL = 2


def normalize_level(value: Any) -> int:
    """Coerce a level coming from JSON into ``1``, ``2`` or ``3``."""

    try:
        number = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise TrackingError("level must be 1, 2 or 3") from exc
    if number not in LEVEL_PROFILES:
        raise TrackingError("level must be 1, 2 or 3")
    return number


def level_profile(value: Any = DEFAULT_LEVEL) -> LevelProfile:
    return LEVEL_PROFILES[normalize_level(value)]


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, float(value)))


def _round(value: float, digits: int = 3) -> float:
    # ``+ 0.0`` normalises -0.0 so JSON and the UI never show "-0".
    return round(float(value), digits) + 0.0


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class Detection:
    """Normalised bounding box: centre (``x``/``y``) plus ``w``/``h`` in 0..1."""

    label: str
    confidence: float
    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "w": round(self.w, 4),
            "h": round(self.h, 4),
        }


@dataclass(frozen=True, slots=True)
class Framing:
    """Camera offset for one instant: degrees of pan/tilt plus a zoom factor."""

    pan: float
    tilt: float
    zoom: float

    def as_dict(self) -> dict[str, float]:
        return {
            "pan": _round(self.pan),
            "tilt": _round(self.tilt),
            "zoom": _round(self.zoom),
        }


def select_subject(
    detections: Iterable[Detection],
    *,
    profile: LevelProfile,
    previous: Detection | None = None,
) -> Detection | None:
    """Pick the primary subject among the frame's detections.

    Confidence is weighted by on-screen area (large subjects dominate) and by
    proximity to the previously tracked box so the camera does not jump between
    two people on opposite sides of a 360° frame.
    """

    best: Detection | None = None
    best_score = 0.0
    for detection in detections:
        if detection.label not in TRACKABLE_CLASSES:
            continue
        if detection.confidence < profile.min_confidence:
            continue
        score = detection.confidence * (1.0 + min(detection.area, 0.5) * 2.0)
        if previous is not None:
            distance = math.hypot(detection.x - previous.x, detection.y - previous.y)
            score *= max(0.25, 1.0 - distance * 1.2)
        if score > best_score:
            best, best_score = detection, score
    return best


def framing_for_detection(
    detection: Detection,
    *,
    mode: str,
    frame_aspect: float,
    output_aspect: float = DEFAULT_OUTPUT_ASPECT,
    profile: LevelProfile = LEVEL_PROFILES[DEFAULT_LEVEL],
) -> Framing:
    """Translate a bounding box into camera pan/tilt/zoom.

    ``pan``/``tilt`` are angular offsets from the frame centre.  For spherical
    sources ``pan`` is yaw (±180° = frame edges); for rectilinear sources it is
    the horizontal offset normalised to ±180° so the same keyframe document can
    drive either FFmpeg mapping.  ``tilt`` is always ±180° from edge to edge.
    """

    width_fraction = max(detection.w, 1e-4)
    height_fraction = max(detection.h, 1e-4)
    # The subject should fill 1/(1+margin) of the framed window.
    target = 1.0 / (1.0 + profile.margin)

    zoom_from_width = target / width_fraction
    frame_aspect = frame_aspect if frame_aspect > 0 else DEFAULT_OUTPUT_ASPECT
    zoom_from_height = (frame_aspect * target) / (height_fraction * output_aspect)
    zoom = _clamp(min(zoom_from_width, zoom_from_height), 1.0, profile.max_zoom)

    if mode == "spherical":
        pan = (detection.x - 0.5) * 360.0
    else:
        pan = (detection.x - 0.5) * 180.0
    tilt = (0.5 - detection.y) * 180.0
    return Framing(
        pan=round(_clamp(pan, -180.0, 180.0), 3),
        tilt=round(_clamp(tilt, -90.0, 90.0), 3),
        zoom=round(zoom, 3),
    )


def _approach(
    current: float,
    target: float,
    *,
    alpha: float,
    deadband: float,
    max_delta: float,
) -> float:
    delta = target - current
    if abs(delta) <= deadband:
        return current
    if deadband:
        delta -= math.copysign(deadband, delta)
    step = delta * alpha
    if max_delta > 0 and abs(step) > max_delta:
        step = math.copysign(max_delta, step)
    return current + step


class FramingSmoother:
    """Exponential follow with per-level deadband and speed limit."""

    def __init__(self, level: Any = DEFAULT_LEVEL, initial: Framing | None = None) -> None:
        self.profile = level_profile(level)
        self._current = initial

    @property
    def current(self) -> Framing | None:
        return self._current

    def update(self, target: Framing, dt: float) -> Framing:
        dt = _clamp(dt, 1e-3, 5.0)
        if self._current is None:
            self._current = target
            return target

        profile = self.profile
        alpha = 1.0 - math.exp(-dt / max(profile.smoothing_seconds, 1e-3))
        pan = _approach(
            self._current.pan,
            target.pan,
            alpha=alpha,
            deadband=profile.deadband_degrees,
            max_delta=profile.max_speed_deg_per_second * dt,
        )
        tilt = _approach(
            self._current.tilt,
            target.tilt,
            alpha=alpha,
            deadband=profile.deadband_degrees,
            max_delta=profile.max_speed_deg_per_second * dt,
        )
        # Zoom changes are cushioned in "zoom units" so a 3.6× snap does not
        # feel like a cut; levels 1/2/3 allow 0.5/1.2/3.0 × per second.
        zoom = _approach(
            self._current.zoom,
            target.zoom,
            alpha=alpha,
            deadband=0.0,
            max_delta=(0.5, 1.2, 3.0)[profile.level - 1] * dt,
        )
        self._current = Framing(pan=pan, tilt=tilt, zoom=max(1.0, zoom))
        return self._current


def build_tracking_keyframes(
    samples: Sequence[tuple[float, Framing]],
    *,
    duration: float = 0.0,
    level: Any = DEFAULT_LEVEL,
    mode: str = "rectilinear",
) -> list[dict[str, Any]]:
    """Compress per-sample framings into render keyframes.

    Samples that hardly differ from the previous keyframe are dropped (that is
    what makes a static shot cheap to evaluate) while the first and last
    instants are always kept so the expression covers the whole clip.
    """

    level_profile(level)
    if mode not in {"spherical", "rectilinear"}:
        raise TrackingError("mode must be 'spherical' or 'rectilinear'")
    if not samples:
        return []

    ordered = sorted(samples, key=lambda item: float(item[0]))
    keyframes: list[dict[str, Any]] = []
    for time_value, framing in ordered:
        entry = {
            "time": _round(max(0.0, float(time_value))),
            "pan": _round(_clamp(framing.pan, -180.0, 180.0)),
            "tilt": _round(_clamp(framing.tilt, -90.0, 90.0)),
            "zoom": _round(_clamp(framing.zoom, 1.0, 8.0)),
        }
        if keyframes:
            previous = keyframes[-1]
            is_last = duration > 0 and entry["time"] >= duration - 1e-6
            if not is_last and (
                entry["time"] - previous["time"] < 0.25
                or (
                    abs(entry["pan"] - previous["pan"]) < 1.5
                    and abs(entry["tilt"] - previous["tilt"]) < 1.5
                    and abs(entry["zoom"] - previous["zoom"]) < 0.04
                )
            ):
                continue
        keyframes.append(entry)

    last = keyframes[-1]
    if duration > 0 and last["time"] < duration - 1e-6:
        keyframes.append({**last, "time": round(float(duration), 3)})

    if len(keyframes) > MAX_KEYFRAMES:
        step = len(keyframes) / float(MAX_KEYFRAMES)
        keyframes = [keyframes[int(index * step)] for index in range(MAX_KEYFRAMES)]
        keyframes[-1] = last

    return keyframes


# --------------------------------------------------------------------- backends

#: COCO-80 names used to label YOLOv8 detections.
COCO_NAMES: tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


def _normalise_box(
    label: str,
    confidence: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    width: float,
    height: float,
) -> Detection | None:
    if width <= 0 or height <= 0:
        return None
    left, right = sorted((x1 / width, x2 / width))
    top, bottom = sorted((y1 / height, y2 / height))
    left = _clamp(left, 0.0, 1.0)
    right = _clamp(right, 0.0, 1.0)
    top = _clamp(top, 0.0, 1.0)
    bottom = _clamp(bottom, 0.0, 1.0)
    if right - left <= 0 or bottom - top <= 0:
        return None
    return Detection(
        label=label,
        confidence=_clamp(confidence, 0.0, 1.0),
        x=(left + right) / 2.0,
        y=(top + bottom) / 2.0,
        w=right - left,
        h=bottom - top,
    )


class DetectorBackend:
    """Base class for the optional inference runtimes."""

    name = "unavailable"
    model: str | None = None
    reason: str | None = None

    def detect(self, frame: Any, width: int, height: int) -> list[Detection]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name, "model": self.model}


class UltralyticsDetector(DetectorBackend):
    """``ultralytics`` YOLO backend (weights auto-download on first use)."""

    name = "ultralytics"

    def __init__(self, model_path: str | None = None) -> None:
        from ultralytics import YOLO  # imported lazily: ~torch-sized dependency

        self.model = model_path or "yolov8n.pt"
        self._net = YOLO(self.model)

    def detect(self, frame: Any, width: int, height: int) -> list[Detection]:
        results = self._net.predict(frame, imgsz=640, conf=0.2, verbose=False)
        detections: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            names = getattr(result, "names", None) or {}
            for box in boxes:
                class_id = int(box.cls[0])
                label = str(names.get(class_id, COCO_NAMES[class_id] if class_id < len(COCO_NAMES) else class_id))
                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
                detection = _normalise_box(
                    label,
                    float(box.conf[0]),
                    x1,
                    y1,
                    x2,
                    y2,
                    width=width,
                    height=height,
                )
                if detection is not None:
                    detections.append(detection)
        return detections


def letterbox_frame(frame: Any, size: int) -> tuple[Any, float, float, float]:
    """Resize ``frame`` (HxWx3 uint8) to ``size`` with grey padding.

    Returns ``(image, scale, pad_x, pad_y)``; the padding offsets are needed to
    map predictions back onto the original frame.
    """

    import numpy as np

    height, width = int(frame.shape[0]), int(frame.shape[1])
    scale = min(size / float(width), size / float(height))
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    pad_x = (size - new_w) / 2.0
    pad_y = (size - new_h) / 2.0

    # Nearest-neighbour resize keeps the runtime numpy-only (no OpenCV).
    rows = (np.arange(new_h) / scale).astype(np.int32).clip(0, height - 1)
    cols = (np.arange(new_w) / scale).astype(np.int32).clip(0, width - 1)
    resized = frame[rows][:, cols]

    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    top = int(round(pad_y))
    left = int(round(pad_x))
    canvas[top : top + new_h, left : left + new_w] = resized
    return canvas, scale, left, top


def _nms(boxes: Any, scores: Any, iou_threshold: float) -> list[int]:
    import numpy as np

    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        index = int(order[0])
        keep.append(index)
        xx1 = np.maximum(x1[index], x1[order[1:]])
        yy1 = np.maximum(y1[index], y1[order[1:]])
        xx2 = np.minimum(x2[index], x2[order[1:]])
        yy2 = np.minimum(y2[index], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[index] + areas[order[1:]] - inter
        iou = np.where(union > 0, inter / union, 0.0)
        order = order[1:][iou <= iou_threshold]
    return keep


def decode_yolov8_output(
    output: Any,
    *,
    confidence: float = 0.25,
    iou_threshold: float = 0.45,
    src_width: int,
    src_height: int,
    scale: float = 1.0,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> list[Detection]:
    """Convert a raw ``(1, 84, N)`` YOLOv8 ONNX tensor into detections."""

    import numpy as np

    array = np.asarray(output)
    while array.ndim > 2:
        array = array[0]
    if array.ndim != 2:
        raise TrackingError("Unexpected YOLO output shape")

    # YOLOv8 emits either (features, N) or (N, features); the feature axis is
    # the small one (4 box values + 80 class scores).
    rows, cols = array.shape
    rows_are_features = 5 <= rows <= 112
    cols_are_features = 5 <= cols <= 112
    if rows_are_features:
        array = array.T
    elif cols_are_features:
        pass
    elif rows > cols:
        array = array.T

    boxes_cxcywh = array[:, :4]
    class_scores = array[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(class_scores.shape[0]), class_ids]
    mask = scores >= confidence
    if not bool(mask.any()):
        return []

    boxes_cxcywh = boxes_cxcywh[mask]
    scores = scores[mask]
    class_ids = class_ids[mask]

    cx, cy, bw, bh = (
        boxes_cxcywh[:, 0],
        boxes_cxcywh[:, 1],
        boxes_cxcywh[:, 2],
        boxes_cxcywh[:, 3],
    )
    boxes = np.stack(
        [cx - bw / 2.0, cy - bh / 2.0, cx + bw / 2.0, cy + bh / 2.0], axis=1
    )
    # Undo the letterbox transform and normalise against the source frame.
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / max(scale, 1e-6) / src_width
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / max(scale, 1e-6) / src_height

    detections: list[Detection] = []
    for index in _nms(boxes, scores, iou_threshold):
        class_id = int(class_ids[index])
        label = COCO_NAMES[class_id] if class_id < len(COCO_NAMES) else f"class_{class_id}"
        left, top, right, bottom = (float(value) for value in boxes[index])
        detection = _normalise_box(
            label,
            float(scores[index]),
            left * src_width,
            top * src_height,
            right * src_width,
            bottom * src_height,
            width=float(src_width),
            height=float(src_height),
        )
        if detection is not None:
            detections.append(detection)
    return detections


class OnnxRuntimeDetector(DetectorBackend):
    """``onnxruntime`` backend for an exported ``yolov8n.onnx`` (~12 MB)."""

    name = "onnxruntime"

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort  # imported lazily: optional runtime extra

        path = Path(model_path)
        if not path.is_file():
            raise TrackingError(f"ONNX model not found: {model_path}")
        self.model = str(path)
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        shape = self._session.get_inputs()[0].shape
        self._size = int(shape[2]) if isinstance(shape[2], int) else 640

    def detect(self, frame: Any, width: int, height: int) -> list[Detection]:
        import numpy as np

        image, scale, pad_x, pad_y = letterbox_frame(frame, self._size)
        blob = (image.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        outputs = self._session.run(None, {self._input_name: blob})
        return decode_yolov8_output(
            outputs[0],
            src_width=width,
            src_height=height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )


class UnavailableDetector(DetectorBackend):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def detect(self, frame: Any, width: int, height: int) -> list[Detection]:
        raise TrackingError(self.reason or "YOLO backend unavailable")


# ------------------------------------------------------------------ frames & IO


def extract_frame(
    source: str | os.PathLike[str],
    timestamp: float = 0.0,
    *,
    width: int = 0,
    height: int = 0,
    ffmpeg_bin: str | None = None,
) -> tuple[bytes, int, int]:
    """Decode a single RGB24 frame through FFmpeg (no OpenCV dependency)."""

    # Resolved at call time so tests and FFMPEG_BIN overrides take effect.
    ffmpeg_bin = ffmpeg_bin or FFMPEG_BIN
    target_width = SAMPLE_FRAME_WIDTH
    target_height = 0
    if width > 0 and height > 0:
        target_height = max(2, int(round(target_width * height / width / 2)) * 2)
        scale_filter = f"scale={target_width}:{target_height}"
    else:
        scale_filter = f"scale={target_width}:-2"

    is_remote = is_network_url(source)
    args = [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin"]
    # Local files seek fast with -ss before -i; a live URL must be decoded in
    # order, so the (accurate) output-side seek is used instead.
    if timestamp > 0 and not is_remote:
        args += ["-ss", f"{float(timestamp):.3f}"]
    args += ["-i", str(source)]
    if timestamp > 0 and is_remote:
        args += ["-ss", f"{float(timestamp):.3f}"]
    args += [
        "-frames:v",
        "1",
        "-vf",
        scale_filter,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]

    try:
        completed = subprocess.run(
            args, capture_output=True, timeout=FRAME_EXTRACT_TIMEOUT, check=False
        )
    except FileNotFoundError as exc:
        raise TrackingError("ffmpeg executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise TrackingError("Timed out while decoding a tracking frame") from exc

    raw = completed.stdout or b""
    if completed.returncode != 0 or not raw:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise TrackingError(f"Could not decode a frame at {timestamp:.2f}s: {detail[:200]}")

    if target_height <= 0:
        target_height = len(raw) // (target_width * 3)
    expected = target_width * target_height * 3
    if target_height <= 0 or len(raw) < expected:
        raise TrackingError("Decoded frame had an unexpected size")
    return raw[:expected], target_width, target_height


def _frame_to_array(raw: bytes, width: int, height: int) -> Any:
    import numpy as np

    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)


# ---------------------------------------------------------------------- service


class YoloTrackingService:
    """Process-wide tracker: backend resolution, analysis runs and status."""

    def __init__(
        self,
        *,
        backend: str | None = None,
        model_path: str | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._backend_preference = (
            backend
            if backend is not None
            else os.environ.get("AI_TRACKING_BACKEND", "")
        ).strip().lower() or "auto"
        self._model_path = (
            model_path if model_path is not None else os.environ.get("AI_TRACKING_MODEL", "")
        ).strip()
        self._detector: DetectorBackend | None = None
        self._resolved = False
        self._resolving = False
        self._cache: dict[str, dict[str, Any]] = {}
        # YOLO models are not guaranteed to be re-entrant; serialise inference
        # calls so a preview poll cannot interleave with an analysis pass.
        self._detect_lock = threading.Lock()

    # ------------------------------------------------------------- backend
    def _resolve(self) -> DetectorBackend | None:
        with self._lock:
            if self._resolved:
                return self._detector
            # Loading ultralytics pulls in torch (seconds); keep it under the
            # lock so concurrent callers wait instead of loading twice.
            self._detector = self._create_detector()
            self._resolved = True
            self._resolving = False
            return self._detector

    def _begin_resolve(self) -> None:
        """Warm up the detector in the background (never blocks a status poll)."""

        with self._lock:
            if self._resolved or self._resolving:
                return
            self._resolving = True
        threading.Thread(
            target=self._resolve, name="yolo-detector-init", daemon=True
        ).start()

    def _create_detector(self) -> DetectorBackend | None:
        preference = self._backend_preference
        errors: list[str] = []

        if preference in {"auto", "ultralytics"}:
            try:
                import ultralytics  # noqa: F401
            except Exception as exc:  # pragma: no cover - depends on install
                errors.append(f"ultralytics not installed ({exc.__class__.__name__})")
            else:
                try:
                    return UltralyticsDetector(self._model_path or None)
                except Exception as exc:  # pragma: no cover - model/runtime issue
                    errors.append(f"ultralytics failed to load: {exc}")

        if preference in {"auto", "onnx", "onnxruntime"}:
            try:
                import onnxruntime  # noqa: F401
            except Exception as exc:  # pragma: no cover - depends on install
                errors.append(f"onnxruntime not installed ({exc.__class__.__name__})")
            else:
                model = self._model_path or ""
                if not model:
                    errors.append(
                        "AI_TRACKING_MODEL is not set (expected a yolov8n.onnx path)"
                    )
                else:
                    try:
                        return OnnxRuntimeDetector(model)
                    except Exception as exc:
                        errors.append(f"onnxruntime failed to load the model: {exc}")

        if preference not in {"auto", "ultralytics", "onnx", "onnxruntime"}:
            errors.insert(0, f"unknown AI_TRACKING_BACKEND '{preference}'")
        self._unavailable_reason = (
            "; ".join(errors) if errors else "No YOLO backend is installed"
        )
        logger.info("AI tracking unavailable: %s", self._unavailable_reason)
        return None

    @property
    def unavailable_reason(self) -> str:
        if not self._resolved:
            self._resolve()
        return getattr(self, "_unavailable_reason", "No YOLO backend is installed")

    def status(self) -> dict[str, Any]:
        """Detector availability; never blocks on a slow first-time import."""

        if not self._resolved:
            self._begin_resolve()
            if not self._resolved:
                return {
                    "available": False,
                    "loading": True,
                    "backend": None,
                    "model": self._model_path or None,
                    "reason": "Loading the YOLO runtime…",
                    "preference": self._backend_preference,
                    "default_level": DEFAULT_LEVEL,
                    "levels": [
                        LEVEL_PROFILES[level].as_dict() for level in sorted(LEVEL_PROFILES)
                    ],
                    "trackable_classes": sorted(TRACKABLE_CLASSES),
                }
        detector = self._detector
        return {
            "available": detector is not None,
            "loading": False,
            "backend": detector.name if detector else None,
            "model": detector.model if detector else (self._model_path or None),
            "reason": None if detector else self.unavailable_reason,
            "preference": self._backend_preference,
            "default_level": DEFAULT_LEVEL,
            "levels": [LEVEL_PROFILES[level].as_dict() for level in sorted(LEVEL_PROFILES)],
            "trackable_classes": sorted(TRACKABLE_CLASSES),
        }

    # ------------------------------------------------------------- analysis
    def analyze(
        self,
        source: str | os.PathLike[str],
        *,
        duration: float = 0.0,
        width: int = 0,
        height: int = 0,
        mode: str | None = None,
        level: Any = DEFAULT_LEVEL,
        sample_fps: float = DEFAULT_SAMPLE_FPS,
        max_samples: int = DEFAULT_MAX_SAMPLES,
        output_aspect: float = DEFAULT_OUTPUT_ASPECT,
        force: bool = False,
    ) -> dict[str, Any]:
        """Sample the clip, track the primary subject and return keyframes."""

        profile = level_profile(level)
        mode = mode or ("spherical" if is_spherical_file(Path(str(source))) else "rectilinear")
        if mode not in {"spherical", "rectilinear"}:
            raise TrackingError("mode must be 'spherical' or 'rectilinear'")
        if is_network_url(source):
            raise TrackingError(
                "AI tracking analysis needs a seekable local file; "
                "mount the stream as a share or render it to a file first"
            )

        detector = self._resolve()
        if detector is None:
            raise TrackingError(self.unavailable_reason)

        sample_fps = _clamp(float(sample_fps or DEFAULT_SAMPLE_FPS), 0.2, 10.0)
        max_samples = int(_clamp(int(max_samples or DEFAULT_MAX_SAMPLES), 2, MAX_KEYFRAMES))
        duration = max(0.0, float(duration or 0.0))

        cache_key = json.dumps(
            {
                "source": str(source),
                "mtime": self._source_stamp(source),
                "level": profile.level,
                "mode": mode,
                "fps": sample_fps,
                "samples": max_samples,
                "backend": detector.name,
            },
            sort_keys=True,
        )
        if not force and cache_key in self._cache:
            return dict(self._cache[cache_key])

        frame_aspect = (width / height) if width > 0 and height > 0 else DEFAULT_OUTPUT_ASPECT
        times = self._sample_times(duration, sample_fps, max_samples)

        smoother = FramingSmoother(profile.level)
        previous: Detection | None = None
        samples: list[tuple[float, Framing]] = []
        subject_frames = 0
        misses = 0
        last_framing: Framing | None = None
        interval = (duration / max(len(times) - 1, 1)) if duration > 0 else 0.0

        for index, timestamp in enumerate(times):
            raw, frame_w, frame_h = extract_frame(
                source, timestamp, width=width, height=height
            )
            frame_array = _frame_to_array(raw, frame_w, frame_h)
            with self._detect_lock:
                detections = detector.detect(frame_array, frame_w, frame_h)
            subject = select_subject(detections, profile=profile, previous=previous)
            if subject is None:
                misses += 1
                if last_framing is not None:
                    # Hold the last framing through short detection gaps.
                    samples.append((timestamp, smoother.update(last_framing, interval)))
                continue

            subject_frames += 1
            misses = 0
            previous = subject
            target = framing_for_detection(
                subject,
                mode=mode,
                frame_aspect=frame_aspect,
                output_aspect=output_aspect,
                profile=profile,
            )
            last_framing = target
            dt = interval if index else 0.0
            samples.append((timestamp, smoother.update(target, dt)))

        if not samples:
            raise TrackingError(
                "No trackable subject was found in the sampled frames"
            )

        keyframes = build_tracking_keyframes(
            samples, duration=duration, level=profile.level, mode=mode
        )
        summary = {
            "source": str(source),
            "backend": detector.name,
            "model": detector.model,
            "level": profile.level,
            "mode": mode,
            "sample_fps": round(sample_fps, 2),
            "frames_analyzed": len(times),
            "frames_with_subject": subject_frames,
            "duration": round(duration, 3),
            "keyframe_count": len(keyframes),
            "analyzed_at": _utc_now(),
            "keyframes": keyframes,
        }
        self._cache[cache_key] = summary
        if len(self._cache) > 64:
            # Bounded LRU-ish cache: drop the oldest analysis result.
            self._cache.pop(next(iter(self._cache)))
        return dict(summary)

    def detect_frame(
        self,
        source: str | os.PathLike[str],
        *,
        timestamp: float = 0.0,
        level: Any = DEFAULT_LEVEL,
        mode: str | None = None,
        output_aspect: float = DEFAULT_OUTPUT_ASPECT,
    ) -> dict[str, Any]:
        """Detect subjects in a single frame (used by the debug overlay)."""

        profile = level_profile(level)
        detector = self._resolve()
        if detector is None:
            return {
                "available": False,
                "reason": self.unavailable_reason,
                "timestamp": round(float(timestamp), 3),
                "detection": None,
                "framing": None,
                "detections": [],
            }

        mode = mode or ("spherical" if is_spherical_file(Path(str(source))) else "rectilinear")
        raw, frame_w, frame_h = extract_frame(source, float(timestamp))
        frame_array = _frame_to_array(raw, frame_w, frame_h)
        with self._detect_lock:
            detections = detector.detect(frame_array, frame_w, frame_h)
        subject = select_subject(detections, profile=profile)
        framing = (
            framing_for_detection(
                subject,
                mode=mode,
                frame_aspect=frame_w / max(frame_h, 1),
                output_aspect=output_aspect,
                profile=profile,
            )
            if subject
            else None
        )
        return {
            "available": True,
            "backend": detector.name,
            "model": detector.model,
            "timestamp": round(float(timestamp), 3),
            "frame": {"width": frame_w, "height": frame_h},
            "detection": subject.as_dict() if subject else None,
            "framing": framing.as_dict() if framing else None,
            "detections": [detection.as_dict() for detection in detections],
        }

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _sample_times(duration: float, sample_fps: float, max_samples: int) -> list[float]:
        if duration <= 0:
            return [0.0]
        count = int(_clamp(math.ceil(duration * sample_fps), 2, max_samples))
        step = duration / count
        # Stay one step away from EOF: seeking exactly to the end yields no frame.
        return [round(index * step, 3) for index in range(count)]

    @staticmethod
    def _source_stamp(source: str | os.PathLike[str]) -> str:
        try:
            stat = os.stat(str(source))
        except OSError:
            return "unknown"
        return f"{int(stat.st_mtime)}:{stat.st_size}"


#: Process-wide tracker used by the AI router.
tracking_service = YoloTrackingService()
