"""NVIDIA / NVENC capability detection with a graceful CPU fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from config import FFMPEG_BIN, GPU_INFO_CACHE_SECONDS

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 15.0
_cache: dict[str, Any] | None = None
_cache_at: float = 0.0
_lock = asyncio.Lock()


async def _run(args: list[str]) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"{args[0]} not found"
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_PROBE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return 124, "", f"{args[0]} timed out"
    return (
        process.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


def _parse_encoders(output: str) -> tuple[bool, bool, list[str]]:
    has_h264 = False
    has_hevc = False
    nvenc_keys: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if "nvenc" not in stripped.lower():
            continue
        # Encoder listing lines look like: " V....D h264_nvenc NVIDIA NVENC H.264 encoder"
        for part in stripped.split():
            if part.endswith("_nvenc"):
                nvenc_keys.append(part)
                if part == "h264_nvenc":
                    has_h264 = True
                elif part == "hevc_nvenc":
                    has_hevc = True
                break
    return has_h264, has_hevc, sorted(set(nvenc_keys))


async def _query_gpu() -> dict[str, Any]:
    code, stdout, _stderr = await _run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader",
        ]
    )
    if code != 0 or not stdout.strip():
        return {"gpu_name": None, "driver_version": None, "vram_mb": None}

    first = stdout.strip().splitlines()[0]
    fields = [field.strip() for field in first.split(",")]
    gpu_name = fields[0] if fields else None
    driver = fields[1] if len(fields) > 1 else None
    vram_mb = None
    if len(fields) > 2:
        digits = "".join(ch for ch in fields[2] if ch.isdigit())
        vram_mb = int(digits) if digits else None
    return {"gpu_name": gpu_name, "driver_version": driver, "vram_mb": vram_mb}


async def _detect() -> dict[str, Any]:
    gpu = await _query_gpu()
    encoders_code, encoders_out, encoders_err = await _run(
        [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-encoders"]
    )
    ffmpeg_available = encoders_code == 0
    h264_nvenc, hevc_nvenc, nvenc_encoders = (
        _parse_encoders(encoders_out) if ffmpeg_available else (False, False, [])
    )

    cpu_encoders: list[str] = []
    if ffmpeg_available:
        for name in ("libx264", "libx265"):
            if name in encoders_out:
                cpu_encoders.append(name)

    hardware_encoding_available = bool(h264_nvenc or hevc_nvenc)

    if hardware_encoding_available:
        message = "NVENC ready"
    elif gpu["gpu_name"]:
        message = "GPU detected, NVENC encoders unavailable - CPU fallback"
    elif not ffmpeg_available:
        message = "FFmpeg unavailable on this host"
    else:
        message = "No NVIDIA GPU detected - CPU fallback"

    return {
        "gpu_name": gpu["gpu_name"],
        "driver_version": gpu["driver_version"],
        "vram_mb": gpu["vram_mb"],
        "ffmpeg_available": ffmpeg_available,
        "nvenc_h264": h264_nvenc,
        "nvenc_hevc": hevc_nvenc,
        "nvenc_encoders": nvenc_encoders,
        "hardware_encoding_available": hardware_encoding_available,
        "cpu_fallback_encoders": cpu_encoders,
        "h264_encoder": "h264_nvenc" if h264_nvenc else "libx264",
        "hevc_encoder": "hevc_nvenc" if hevc_nvenc else "libx265",
        "status": "ready" if hardware_encoding_available else "fallback",
        "message": message,
        "ffmpeg_error": encoders_err.strip().splitlines()[-1] if encoders_err.strip() else None,
        "checked_at": time.time(),
    }


async def get_gpu_info(force: bool = False) -> dict[str, Any]:
    """Return cached GPU/NVENC information (refreshed every 30s by default)."""

    global _cache, _cache_at
    async with _lock:
        now = time.monotonic()
        if not force and _cache is not None and (now - _cache_at) < GPU_INFO_CACHE_SECONDS:
            return _cache
        try:
            _cache = await _detect()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("GPU detection failed")
            _cache = {
                "gpu_name": None,
                "driver_version": None,
                "vram_mb": None,
                "ffmpeg_available": False,
                "nvenc_h264": False,
                "nvenc_hevc": False,
                "nvenc_encoders": [],
                "hardware_encoding_available": False,
                "cpu_fallback_encoders": [],
                "h264_encoder": "libx264",
                "hevc_encoder": "libx265",
                "status": "fallback",
                "message": f"GPU detection error: {exc}",
                "ffmpeg_error": str(exc),
                "checked_at": time.time(),
            }
        _cache_at = now
        return _cache


async def resolve_encoders(prefer_gpu: bool = True) -> dict[str, Any]:
    """Encoder names to use for a render, honouring the CPU fallback."""

    info = await get_gpu_info()
    if prefer_gpu and info["hardware_encoding_available"]:
        return {
            "h264": "h264_nvenc" if info["nvenc_h264"] else "libx264",
            "hevc": "hevc_nvenc" if info["nvenc_hevc"] else "libx265",
            "hardware": bool(info["nvenc_h264"] or info["nvenc_hevc"]),
        }
    return {"h264": "libx264", "hevc": "libx265", "hardware": False}
