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
from typing import Any
from urllib.parse import urlparse

from config import app_edition

logger = logging.getLogger(__name__)

MODEL_DOWNLOAD_TIMEOUT = 120.0

#: Requested in this order for every ``InferenceSession``: the Pro image installs
#: ``onnxruntime-gpu`` so the CUDA provider wins, while the default image only
#: ships the CPU provider and therefore binds to CPU without failing.
ONNX_PROVIDERS: tuple[str, ...] = ("CUDAExecutionProvider", "CPUExecutionProvider")
CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"


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


def url_filename(url: str) -> str:
    """Cache filename implied by a model ``url`` (``""`` when unusable).

    Weights hosted in a repository are usually all called ``model.onnx``, which
    would make every variant share one cache entry, so Hugging Face URLs are
    prefixed with the repository name (``depth-anything-v2-base-model.onnx``).
    """

    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return ""
    # Split on "/" directly: Path() uses the platform separator and would return
    # different parts on Windows and Linux for the same URL.
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts or not parts[-1].lower().endswith((".onnx", ".ort", ".pb")):
        return ""
    name = parts[-1]
    host = (parsed.hostname or "").lower()
    if (host == "huggingface.co" or host.endswith(".huggingface.co")) and len(parts) > 1:
        return f"{parts[1]}-{name}"
    return name


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


# ------------------------------------------------------------------- execution


def ort_providers(ort: Any) -> list[str]:
    """Execution providers to request for ``ort``, GPU first.

    Providers the installed wheel does not expose are dropped instead of being
    passed through, so the CPU-only runtime never logs (or raises on) an unknown
    provider name.
    """

    try:
        available = set(ort.get_available_providers())
    except Exception:  # pragma: no cover - broken onnxruntime install
        return [CPU_PROVIDER]
    providers = [name for name in ONNX_PROVIDERS if name in available]
    return providers or [CPU_PROVIDER]


def session_provider(session: Any) -> str:
    """Provider an ``InferenceSession`` actually bound to (first = highest)."""

    try:
        providers = session.get_providers()
    except Exception:  # pragma: no cover - stub or partially built session
        return CPU_PROVIDER
    return providers[0] if providers else CPU_PROVIDER


def weights_label() -> str:
    """Weight tier of the active edition: ``"Pro"``, ``"Standard"`` or ``"Lite"``."""

    return {"pro": "Pro", "standard": "Standard"}.get(app_edition(), "Lite")


def log_session_ready(
    logger_: logging.Logger,
    *,
    label: str,
    model_path: str | os.PathLike[str],
    session: Any,
    detail: str = "",
) -> None:
    """Log which device, provider and weight tier a freshly loaded model uses."""

    provider = session_provider(session)
    tier = weights_label()
    device = "CUDA" if provider == CUDA_PROVIDER else "CPU"
    logger_.info(
        "AI %s ready | edition=%s | weights=%s | device=%s | provider=%s | model=%s%s",
        label,
        app_edition(),
        tier,
        device,
        provider,
        Path(model_path).name,
        detail,
    )
    if tier == "Pro" and provider != CUDA_PROVIDER:
        logger_.warning(
            "AI %s fell back to the CPU provider: build the image with "
            "INSTALL_PRO_AI=1 (onnxruntime-gpu) and run the container with --gpus all",
            label,
        )
