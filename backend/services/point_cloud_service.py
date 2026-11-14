"""Lightweight monocular-depth 3D point cloud exporter (Phase 11).

The pipeline is deliberately small: frames are sampled from the source with
FFmpeg, a small ONNX depth model (MiDaS v2.1 Small by default) predicts an
inverse-depth map for each frame, the RGB pixels are back-projected into 3D and
the accumulated cloud is voxel-filtered before being written as a binary
``.ply`` file. Everything runs through ``onnxruntime``; no PyTorch, OpenCV or
CUDA runtime is required, so the Docker image stays slim.

Rectilinear clips use a pinhole model with an assumed horizontal field of view
and 360° sources (``.insp``/``.insv``) use equirectangular spherical projection.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from services import model_cache
from services.model_cache import ModelCacheError
from services.yolo_tracking_service import extract_frame

logger = logging.getLogger(__name__)

#: FFmpeg decodes depth samples at this width; the model resizes internally.
FRAME_WIDTH = 640

DEFAULT_SAMPLE_RATE = 1.0
MIN_SAMPLE_RATE = 0.1
MAX_SAMPLE_RATE = 5.0
SAMPLE_RATE_OPTIONS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 5.0)

DEFAULT_MAX_FRAMES = 600
MAX_FRAMES_LIMIT = 5000

DEFAULT_VOXEL_SIZE = 0.05
MIN_VOXEL_SIZE = 0.005
MAX_VOXEL_SIZE = 2.0

DEFAULT_MAX_POINTS = 1_500_000
MIN_POINTS = 1000
MAX_POINTS_LIMIT = 8_000_000
#: Pending points are filtered once this many are buffered, bounding RAM.
BUFFER_POINTS = 4_000_000

DEFAULT_HORIZONTAL_FOV = 60.0
DEPTH_PERCENTILE_LOW = 2.0
DEPTH_PERCENTILE_HIGH = 98.0
MIN_DEPTH_METERS = 0.5
MAX_DEPTH_METERS = 50.0

DEFAULT_DEPTH_INPUT_SIZE = 256
#: MiDaS v2.1 Small is ~64 MB and converts the whole video without torch.
DEFAULT_DEPTH_MODEL_NAME = "midas-v21-small.onnx"
DEFAULT_DEPTH_MODEL_URL = os.environ.get(
    "AI_DEPTH_MODEL_URL",
    "https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx",
)
DEFAULT_DEPTH_MODEL_DIR = os.environ.get(
    "AI_DEPTH_MODEL_DIR",
    os.environ.get("AI_TRACKING_MODEL_DIR", "/models"),
)
DEPTH_MODEL_DOWNLOAD_TIMEOUT = 300.0
#: ``midas`` (default) predicts inverse depth; ``depth_anything`` predicts depth.
DEFAULT_DEPTH_MODEL_KIND = os.environ.get("AI_DEPTH_MODEL_KIND", "midas")


def depth_model_filename() -> str:
    """Cache filename for the configured depth URL.

    A custom ``AI_DEPTH_MODEL_URL`` gets its own filename, so switching between
    MiDaS and Depth Anything (Lite vs Pro) never reuses stale weights; the
    default keeps the historical ``midas-v21-small.onnx`` name so existing
    ``/models`` folders are reused instead of re-downloaded.
    """

    return (
        model_cache.url_filename(os.environ.get("AI_DEPTH_MODEL_URL", ""))
        or DEFAULT_DEPTH_MODEL_NAME
    )


class PointCloudError(RuntimeError):
    """Raised when a point cloud export cannot be satisfied."""


class PointCloudBusyError(PointCloudError):
    """Raised when an export is already running."""


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, float(value)))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- projection


def metric_depth(
    depth: Any,
    *,
    near: float = MIN_DEPTH_METERS,
    far: float = MAX_DEPTH_METERS,
) -> Any:
    """Stretch a relative closeness map into pseudo-metric metres.

    The model output is treated as *closeness* (higher = nearer), which matches
    MiDaS inverse depth. A percentile stretch keeps a few saturated pixels from
    flattening the whole cloud; the result is ``near`` at the closest pixel and
    ``far`` at the most distant one.
    """

    import numpy as np

    values = np.asarray(depth, dtype=np.float32)
    if values.size == 0:
        return values
    low = float(np.percentile(values, DEPTH_PERCENTILE_LOW))
    high = float(np.percentile(values, DEPTH_PERCENTILE_HIGH))
    if high - low < 1e-6:
        closeness = np.full_like(values, 0.5)
    else:
        closeness = np.clip((values - low) / (high - low), 0.0, 1.0)
    return (far - closeness * (far - near)).astype(np.float32)


def project_rectilinear(depth: Any, *, horizontal_fov: float = DEFAULT_HORIZONTAL_FOV) -> Any:
    """Back-project a metric depth map with a pinhole camera (Y up, Z forward)."""

    import numpy as np

    values = np.asarray(depth, dtype=np.float32)
    height, width = values.shape
    fov = _clamp(horizontal_fov, 20.0, 140.0)
    focal = 0.5 * width / math.tan(math.radians(fov) / 2.0)
    columns = np.arange(width, dtype=np.float32) - (width - 1) / 2.0
    rows = np.arange(height, dtype=np.float32) - (height - 1) / 2.0
    x = values * (columns[None, :] / focal)
    y = values * (-rows[:, None] / focal)
    z = values
    return np.stack((x, y, z), axis=-1).reshape(-1, 3)


def project_equirectangular(depth: Any) -> Any:
    """Back-project an equirectangular depth map onto a sphere."""

    import numpy as np

    values = np.asarray(depth, dtype=np.float32)
    height, width = values.shape
    phi = (
        (np.arange(width, dtype=np.float32) + 0.5) / width - 0.5
    ) * (2.0 * math.pi)
    theta = (
        0.5 - (np.arange(height, dtype=np.float32) + 0.5) / height
    ) * math.pi
    sin_phi = np.sin(phi)[None, :]
    cos_phi = np.cos(phi)[None, :]
    sin_theta = np.sin(theta)[:, None]
    cos_theta = np.cos(theta)[:, None]
    x = values * cos_theta * sin_phi
    y = values * sin_theta
    z = values * cos_theta * cos_phi
    return np.stack((x, y, z), axis=-1).reshape(-1, 3)


def frame_to_points(
    frame: Any,
    depth: Any,
    *,
    spherical: bool = False,
    horizontal_fov: float = DEFAULT_HORIZONTAL_FOV,
) -> tuple[Any, Any]:
    """Turn one RGB frame plus its depth map into ``(points, colours)``."""

    import numpy as np

    image = np.asarray(frame, dtype=np.uint8)
    height, width = int(image.shape[0]), int(image.shape[1])
    metres = metric_depth(depth)
    if metres.shape != (height, width):
        metres = _resize_depth(metres, width, height)
    colors = image.reshape(-1, 3)
    if spherical:
        points = project_equirectangular(metres)
    else:
        points = project_rectilinear(metres, horizontal_fov=horizontal_fov)
    return points, colors


# ---------------------------------------------------------------- downsampling


def voxel_downsample(points: Any, colors: Any, voxel_size: float) -> tuple[Any, Any]:
    """Keep one representative point per ``voxel_size`` cube (numpy only)."""

    import numpy as np

    pts = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    cols = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)
    if len(pts) != len(cols):
        raise PointCloudError("points and colors must have the same length")
    if len(pts) == 0:
        return pts, cols
    size = float(voxel_size)
    if size <= 0:
        return pts, cols

    keys = np.floor(pts / size).astype(np.int64)
    keys -= keys.min(axis=0)
    dims = keys.max(axis=0) + 1
    # Pack the three voxel axes into one integer for a fast 1-D unique().
    span = int(dims[0]) * int(dims[1]) * int(dims[2])
    if 0 < span <= np.iinfo(np.int64).max:
        flat = keys[:, 0] + dims[0] * (keys[:, 1] + dims[1] * keys[:, 2])
        _, index = np.unique(flat, return_index=True)
    else:  # pragma: no cover - pathological coordinates
        _, index = np.unique(keys, axis=0, return_index=True)
    index = np.sort(index)
    return pts[index], cols[index]


def limit_points(points: Any, colors: Any, max_points: int) -> tuple[Any, Any]:
    """Stride-subsample a cloud that is still above ``max_points``."""

    import numpy as np

    pts = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    cols = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)
    limit = int(max_points or DEFAULT_MAX_POINTS)
    if limit <= 0 or len(pts) <= limit:
        return pts, cols
    stride = int(math.ceil(len(pts) / float(limit)))
    return pts[::stride][:limit], cols[::stride][:limit]


class PointAccumulator:
    """Bounded point buffer that voxel-filters itself while frames arrive."""

    def __init__(
        self,
        *,
        max_points: int = DEFAULT_MAX_POINTS,
        voxel_size: float = DEFAULT_VOXEL_SIZE,
        buffer_limit: int = BUFFER_POINTS,
    ) -> None:
        self.max_points = int(max_points)
        self.voxel_size = float(voxel_size)
        self.buffer_limit = max(int(buffer_limit), self.max_points)
        self._points: list[Any] = []
        self._colors: list[Any] = []
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def add(self, points: Any, colors: Any) -> None:
        import numpy as np

        pts = np.asarray(points, dtype=np.float32).reshape(-1, 3)
        cols = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)
        if len(pts) == 0:
            return
        if len(pts) != len(cols):
            raise PointCloudError("points and colors must have the same length")
        self._points.append(pts)
        self._colors.append(cols)
        self._count += len(pts)
        if self._count >= self.buffer_limit:
            self._compact()

    def _compact(self) -> None:
        import numpy as np

        points = np.concatenate(self._points, axis=0)
        colors = np.concatenate(self._colors, axis=0)
        points, colors = voxel_downsample(points, colors, self.voxel_size)
        points, colors = limit_points(points, colors, self.max_points)
        self._points = [points]
        self._colors = [colors]
        self._count = len(points)

    def finalize(self) -> tuple[Any, Any]:
        import numpy as np

        if not self._points:
            return (
                np.empty((0, 3), dtype=np.float32),
                np.empty((0, 3), dtype=np.uint8),
            )
        points = np.concatenate(self._points, axis=0)
        colors = np.concatenate(self._colors, axis=0)
        points, colors = voxel_downsample(points, colors, self.voxel_size)
        return limit_points(points, colors, self.max_points)


# --------------------------------------------------------------------- PLY I/O


def write_ply(
    path: str | os.PathLike[str],
    points: Any,
    colors: Any,
    *,
    comments: tuple[str, ...] | list[str] = (),
) -> Path:
    """Write a binary little-endian PLY with XYZ + RGB (atomic rename)."""

    import numpy as np

    pts = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    cols = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)
    if len(pts) != len(cols):
        raise PointCloudError("points and colors must have the same length")

    target = Path(path)
    if not target.parent.is_dir():
        raise PointCloudError(f"Output folder does not exist: {target.parent}")

    vertices = np.empty(
        len(pts),
        dtype=[
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
        ],
    )
    vertices["x"] = pts[:, 0]
    vertices["y"] = pts[:, 1]
    vertices["z"] = pts[:, 2]
    vertices["red"] = cols[:, 0]
    vertices["green"] = cols[:, 1]
    vertices["blue"] = cols[:, 2]

    header = [
        "ply",
        "format binary_little_endian 1.0",
        *[f"comment {' '.join(str(item).split())}" for item in comments],
        f"element vertex {len(pts)}",
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
        "end_header",
        "",
    ]

    partial = target.with_suffix(target.suffix + ".part")
    try:
        with open(partial, "wb") as handle:
            handle.write("\n".join(header).encode("ascii"))
            vertices.tofile(handle)
        partial.replace(target)
    except OSError as exc:
        try:
            partial.unlink()
        except OSError:
            pass
        raise PointCloudError(f"Could not write {target}: {exc}") from exc
    return target


# ------------------------------------------------------------------ depth model


def _resize_rgb(frame: Any, width: int, height: int) -> Any:
    import numpy as np
    from PIL import Image

    image = Image.fromarray(np.asarray(frame, dtype=np.uint8))
    if image.size != (width, height):
        image = image.resize((width, height), Image.BILINEAR)
    return np.asarray(image, dtype=np.uint8)


def _resize_depth(depth: Any, width: int, height: int) -> Any:
    import numpy as np
    from PIL import Image

    image = Image.fromarray(np.asarray(depth, dtype=np.float32))
    if image.size != (width, height):
        image = image.resize((width, height), Image.BILINEAR)
    return np.asarray(image, dtype=np.float32)


class DepthEstimator:
    """``onnxruntime`` wrapper around a MiDaS / Depth Anything style model."""

    name = "onnxruntime"

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        *,
        invert: bool = False,
        input_size: int | None = None,
    ) -> None:
        import onnxruntime as ort  # imported lazily: optional runtime extra

        path = Path(model_path)
        if not path.is_file():
            raise PointCloudError(f"depth model not found: {path}")
        self.model = str(path)
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(path), sess_options=options, providers=model_cache.ort_providers(ort)
        )
        spec = self._session.get_inputs()[0]
        self._input_name = spec.name
        shape = list(spec.shape or [])
        fixed_width = shape[3] if len(shape) > 3 and isinstance(shape[3], int) else 0
        fixed_height = shape[2] if len(shape) > 2 and isinstance(shape[2], int) else 0
        fallback = int(
            input_size or _env_int("AI_DEPTH_INPUT_SIZE", DEFAULT_DEPTH_INPUT_SIZE)
        )
        self.input_height = fixed_height or fallback
        self.input_width = fixed_width or fallback
        # Depth Anything predicts depth (near = small); negate so every model
        # downstream sees a "higher = closer" closeness map.
        self._invert = bool(invert)
        self.provider = model_cache.session_provider(self._session)
        self.weights = model_cache.weights_label()
        model_cache.log_session_ready(
            logger,
            label="monocular depth",
            model_path=path,
            session=self._session,
            detail=f" | input={self.input_width}x{self.input_height}",
        )

    def predict(self, frame: Any) -> Any:
        """Return a ``frame`` sized closeness map (higher = closer)."""

        import numpy as np

        image = np.asarray(frame, dtype=np.uint8)
        height, width = int(image.shape[0]), int(image.shape[1])
        resized = _resize_rgb(image, self.input_width, self.input_height)
        blob = resized.astype(np.float32) / 255.0
        mean = np.array((0.485, 0.456, 0.406), dtype=np.float32)
        std = np.array((0.229, 0.224, 0.225), dtype=np.float32)
        blob = ((blob - mean) / std).transpose(2, 0, 1)[None]

        output = self._session.run(None, {self._input_name: blob})[0]
        depth = np.asarray(output, dtype=np.float32)
        while depth.ndim > 2:
            depth = depth[0]
        if depth.ndim != 2:
            raise PointCloudError("Unexpected depth model output shape")
        if depth.shape != (height, width):
            depth = _resize_depth(depth, width, height)
        if self._invert:
            depth = -depth
        return depth


# --------------------------------------------------------------------- service


def sample_timestamps(
    duration: float,
    *,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> list[float]:
    """Evenly spaced seek points for ``duration`` at ``sample_rate`` fps."""

    rate = _clamp(float(sample_rate or DEFAULT_SAMPLE_RATE), MIN_SAMPLE_RATE, MAX_SAMPLE_RATE)
    limit = int(_clamp(int(max_frames or DEFAULT_MAX_FRAMES), 1, MAX_FRAMES_LIMIT))
    total = max(0.0, float(duration or 0.0))
    if total <= 0:
        return [0.0]
    count = max(1, min(limit, int(math.ceil(total * rate))))
    step = 1.0 / rate
    last = max(0.0, total - 0.001)
    return [round(min(last, index * step), 3) for index in range(count)]


def _idle_status() -> dict[str, Any]:
    return {
        "state": "idle",
        "phase": "idle",
        "running": False,
        "progress": 0.0,
        "source": None,
        "output": None,
        "spherical": False,
        "sample_rate": DEFAULT_SAMPLE_RATE,
        "voxel_size": DEFAULT_VOXEL_SIZE,
        "max_points": DEFAULT_MAX_POINTS,
        "frames_total": 0,
        "frames_processed": 0,
        "points": 0,
        "output_size_bytes": 0,
        "started_at": None,
        "finished_at": None,
        "elapsed_seconds": 0.0,
        "error": None,
        "updated_at": None,
    }


class PointCloudService:
    """Process-wide depth model + single background export job."""

    def __init__(
        self,
        *,
        model_path: str | None = None,
        estimator: DepthEstimator | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._model_path = (
            model_path
            if model_path is not None
            else os.environ.get("AI_DEPTH_MODEL", "")
        ).strip()
        self._estimator: DepthEstimator | None = estimator
        self._resolved = estimator is not None
        self._resolving = False
        self._model_error: str | None = None
        self._thread: threading.Thread | None = None
        self._started_monotonic = 0.0
        self._status = _idle_status()

    # ------------------------------------------------------------- model
    def _ensure_model(self) -> Path:
        try:
            return model_cache.ensure_model(
                filename=depth_model_filename(),
                url=DEFAULT_DEPTH_MODEL_URL,
                configured=self._model_path,
                directory=DEFAULT_DEPTH_MODEL_DIR,
                timeout=DEPTH_MODEL_DOWNLOAD_TIMEOUT,
                label="monocular depth model",
            )
        except ModelCacheError as exc:
            raise PointCloudError(str(exc)) from exc

    def _create_estimator(self) -> DepthEstimator:
        try:
            import onnxruntime  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on install
            raise PointCloudError(
                f"onnxruntime is not installed ({exc.__class__.__name__})"
            ) from exc
        kind = os.environ.get("AI_DEPTH_MODEL_KIND", DEFAULT_DEPTH_MODEL_KIND)
        return DepthEstimator(
            self._ensure_model(),
            invert=str(kind).strip().lower().startswith("depth"),
        )

    def _resolve_estimator(self) -> DepthEstimator:
        with self._lock:
            if self._resolved and self._estimator is not None:
                return self._estimator
            if self._resolved and self._estimator is None:
                raise PointCloudError(
                    self._model_error or "the monocular depth model is unavailable"
                )
            try:
                self._estimator = self._create_estimator()
                self._model_error = None
            except Exception as exc:
                self._estimator = None
                self._model_error = str(exc)
                raise PointCloudError(str(exc)) from exc
            finally:
                self._resolved = True
                self._resolving = False
            return self._estimator

    def _warm_target(self) -> None:
        try:
            self._resolve_estimator()
        except Exception as exc:  # pragma: no cover - network/model issues
            logger.info("Point-cloud depth model unavailable: %s", exc)

    def warm_up(self) -> bool:
        """Start downloading/loading the depth model in the background."""

        with self._lock:
            if self._resolved or self._resolving:
                return False
            self._resolving = True
        threading.Thread(
            target=self._warm_target, name="depth-model-init", daemon=True
        ).start()
        return True

    def model_status(self) -> dict[str, Any]:
        """Non-blocking detector-style status for the depth model."""

        with self._lock:
            estimator = self._estimator
            return {
                "available": estimator is not None,
                "loading": bool(self._resolving or (not self._resolved and not self._model_error)),
                "backend": estimator.name if estimator else None,
                "model": estimator.model if estimator else (self._model_path or None),
                "provider": getattr(estimator, "provider", None),
                "weights": getattr(estimator, "weights", None),
                "edition": config.app_edition(),
                "model_name": depth_model_filename(),
                "model_url": DEFAULT_DEPTH_MODEL_URL or None,
                "reason": self._model_error,
            }

    def defaults(self) -> dict[str, Any]:
        return {
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "sample_rate_options": list(SAMPLE_RATE_OPTIONS),
            "sample_rate_min": MIN_SAMPLE_RATE,
            "sample_rate_max": MAX_SAMPLE_RATE,
            "max_frames": DEFAULT_MAX_FRAMES,
            "voxel_size": DEFAULT_VOXEL_SIZE,
            "max_points": DEFAULT_MAX_POINTS,
            "frame_width": FRAME_WIDTH,
            "horizontal_fov": DEFAULT_HORIZONTAL_FOV,
        }

    # ------------------------------------------------------------- status
    def _update(self, **fields: Any) -> None:
        with self._lock:
            self._status.update(fields)
            if "state" in fields:
                self._status["running"] = fields["state"] == "running"
            self._status["updated_at"] = _utc_now()

    def status(self) -> dict[str, Any]:
        if not self._resolved and not self._resolving:
            self.warm_up()
        with self._lock:
            snapshot = copy.deepcopy(self._status)
            started = self._started_monotonic
        snapshot["model"] = self.model_status()
        snapshot["defaults"] = self.defaults()
        if snapshot.get("running") and started:
            snapshot["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return snapshot

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        """Join the running export (used by tests and graceful shutdown)."""

        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()

    # ------------------------------------------------------------ pipeline
    def start(
        self,
        source: str | os.PathLike[str],
        output: str | os.PathLike[str],
        *,
        duration: float = 0.0,
        width: int = 0,
        height: int = 0,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
        max_frames: int = DEFAULT_MAX_FRAMES,
        voxel_size: float = DEFAULT_VOXEL_SIZE,
        max_points: int = DEFAULT_MAX_POINTS,
        spherical: bool = False,
        horizontal_fov: float = DEFAULT_HORIZONTAL_FOV,
    ) -> dict[str, Any]:
        """Validate the request and run the export on a daemon thread."""

        raw_source = str(source)
        if config.is_network_url(raw_source):
            raise PointCloudError(
                "Point cloud generation needs a seekable local file; "
                "mount the stream as a share or render it to a file first"
            )
        if not Path(raw_source).is_file():
            raise PointCloudError(f"Source file not found: {raw_source}")

        target = Path(output)
        if not target.suffix:
            target = target.with_suffix(".ply")
        elif target.suffix.lower() != ".ply":
            target = target.with_suffix(".ply")
        if not target.parent.is_dir():
            raise PointCloudError(f"Output folder does not exist: {target.parent}")

        rate = _clamp(float(sample_rate or DEFAULT_SAMPLE_RATE), MIN_SAMPLE_RATE, MAX_SAMPLE_RATE)
        frames_cap = int(_clamp(int(max_frames or DEFAULT_MAX_FRAMES), 1, MAX_FRAMES_LIMIT))
        voxel = _clamp(float(voxel_size or DEFAULT_VOXEL_SIZE), MIN_VOXEL_SIZE, MAX_VOXEL_SIZE)
        point_cap = int(
            _clamp(int(max_points or DEFAULT_MAX_POINTS), MIN_POINTS, MAX_POINTS_LIMIT)
        )

        with self._lock:
            if self._status.get("running"):
                raise PointCloudBusyError("A point cloud export is already running")
            if self._thread is not None and self._thread.is_alive():
                raise PointCloudBusyError("A point cloud export is already running")
            self._status = {
                **_idle_status(),
                "state": "running",
                "phase": "starting",
                "running": True,
                "source": raw_source,
                "output": str(target),
                "spherical": bool(spherical),
                "sample_rate": round(rate, 3),
                "voxel_size": voxel,
                "max_points": point_cap,
                "started_at": _utc_now(),
                "updated_at": _utc_now(),
            }
            self._started_monotonic = time.monotonic()
            thread = threading.Thread(
                target=self._run,
                kwargs={
                    "source": raw_source,
                    "output": target,
                    "duration": float(duration or 0.0),
                    "width": int(width or 0),
                    "height": int(height or 0),
                    "sample_rate": rate,
                    "max_frames": frames_cap,
                    "voxel_size": voxel,
                    "max_points": point_cap,
                    "spherical": bool(spherical),
                    "horizontal_fov": float(horizontal_fov or DEFAULT_HORIZONTAL_FOV),
                },
                name="point-cloud-export",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return self.status()

    def _run(
        self,
        *,
        source: str,
        output: Path,
        duration: float,
        width: int,
        height: int,
        sample_rate: float,
        max_frames: int,
        voxel_size: float,
        max_points: int,
        spherical: bool,
        horizontal_fov: float,
    ) -> None:
        try:
            import numpy as np

            self._update(phase="loading-model", progress=0.01)
            estimator = self._resolve_estimator()

            times = sample_timestamps(
                duration, sample_rate=sample_rate, max_frames=max_frames
            )
            self._update(
                phase="sampling",
                frames_total=len(times),
                frames_processed=0,
                progress=0.02,
            )

            accumulator = PointAccumulator(
                max_points=max_points, voxel_size=voxel_size
            )
            for index, timestamp in enumerate(times):
                raw, frame_w, frame_h = extract_frame(
                    source,
                    timestamp,
                    width=width,
                    height=height,
                    target_width=FRAME_WIDTH,
                )
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(frame_h, frame_w, 3)
                depth = estimator.predict(frame)
                points, colors = frame_to_points(
                    frame,
                    depth,
                    spherical=spherical,
                    horizontal_fov=horizontal_fov,
                )
                accumulator.add(points, colors)
                self._update(
                    frames_processed=index + 1,
                    points=accumulator.count,
                    progress=round(0.02 + 0.78 * (index + 1) / len(times), 4),
                )

            if accumulator.count == 0:
                raise PointCloudError("No depth samples were produced")

            self._update(phase="finalizing", progress=0.85)
            points, colors = accumulator.finalize()
            self._update(phase="writing", points=len(points), progress=0.92)

            write_ply(
                output,
                points,
                colors,
                comments=(
                    "Fast-Frame monocular depth point cloud",
                    f"source {Path(source).name}",
                    f"frames {len(times)} at {sample_rate:g} fps",
                    f"voxel {voxel_size:g} m",
                    "projection " + ("equirectangular" if spherical else "rectilinear"),
                ),
            )
            self._update(
                state="completed",
                phase="done",
                running=False,
                progress=1.0,
                points=len(points),
                output=str(output),
                output_size_bytes=output.stat().st_size,
                finished_at=_utc_now(),
                error=None,
            )
        except Exception as exc:
            logger.warning("Point cloud export failed: %s", exc)
            self._update(
                state="failed",
                phase="failed",
                running=False,
                error=str(exc),
                finished_at=_utc_now(),
            )


point_cloud_service = PointCloudService()
