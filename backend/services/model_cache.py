"""Shared download/cache helper for optional ONNX model weights.

Both the YOLO tracker and the monocular-depth point-cloud exporter load ONNX
files that are too large to bake into the image. The first request downloads
them once into ``AI_TRACKING_MODEL_DIR`` (``/models`` in the container,
bind-mounted from ``./models``) and reuses the cached file on every later start.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_DOWNLOAD_TIMEOUT = 120.0


class ModelCacheError(RuntimeError):
    """Raised when model weights cannot be located or downloaded."""


def default_model_dir() -> Path:
    return Path(os.environ.get("AI_TRACKING_MODEL_DIR", "/models"))


def model_cache_path(
    filename: str, *, directory: str | os.PathLike[str] | None = None
) -> Path:
    """First writable cache location for ``filename`` (``/models`` preferred)."""

    candidates: list[Path] = []
    if directory:
        candidates.append(Path(directory))
    candidates.append(default_model_dir())
    candidates.append(Path(tempfile.gettempdir()) / "fast-frame-models")

    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            continue
        return folder / filename
    return Path(tempfile.gettempdir()) / filename


def ensure_model(
    *,
    filename: str,
    url: str,
    configured: str = "",
    directory: str | os.PathLike[str] | None = None,
    timeout: float = MODEL_DOWNLOAD_TIMEOUT,
    label: str = "ONNX model",
) -> Path:
    """Return a local model path, downloading it atomically when missing."""

    if configured.strip():
        return Path(configured.strip())

    target = model_cache_path(filename, directory=directory)
    if target.is_file() and target.stat().st_size > 0:
        return target

    if not url:
        raise ModelCacheError(f"{label} is not configured and no download URL is set")

    logger.info("Downloading %s %s -> %s", label, url, target)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, open(
            partial, "wb"
        ) as handle:
            shutil.copyfileobj(response, handle, length=1 << 20)
        if partial.stat().st_size == 0:
            raise ModelCacheError(f"downloaded {label} was empty")
        partial.replace(target)
    except Exception as exc:
        try:
            partial.unlink()
        except OSError:
            pass
        if isinstance(exc, ModelCacheError):
            raise
        raise ModelCacheError(
            f"could not download the {label} from {url}: {exc}"
        ) from exc

    return target
