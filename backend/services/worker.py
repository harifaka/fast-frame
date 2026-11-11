"""Async render queue, FFmpeg subprocess runner and progress parser."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from config import (
    MAX_STDERR_BYTES,
    PathSecurityError,
    SUPPORTED_INPUT_LABEL,
    is_network_url,
    is_subtitle_file,
    is_video_file,
    mask_credentials,
    network_source_name,
    normalize_output_extension,
    resolve_host_path,
)
from models import QueueAddRequest
from services import ffmpeg_builder, network_service
from services.ffmpeg_builder import FFmpegBuildError, RenderOptions, validate_speed
from services.ffprobe_service import probe_video
from services.gpu_service import resolve_encoders
from services.network_service import NetworkSourceError

logger = logging.getLogger(__name__)

PROGRESS_EMIT_INTERVAL = 0.25
TERMINATE_GRACE_SECONDS = 8.0
MAX_ERROR_LOG_CHARS = 12000

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_CREDENTIAL_URL = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)

NVENC_FAILURE_MARKERS = (
    "nvenc",
    "libcuda",
    "cuda",
    "openencodesession",
    "no capable devices",
    "driver does not support",
    "cannot load",
    "function not implemented",
)


class JobValidationError(RuntimeError):
    """Raised when a submitted job cannot be accepted."""


class JobNotFoundError(KeyError):
    """Raised when a job id is unknown."""


class EventBus:
    """Fan-out of job updates to every connected WebSocket client."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow client must never stall a render; drop the oldest event.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)


def sanitize_filename(name: str, fallback: str = "output") -> str:
    cleaned = _CONTROL_CHARS.sub("", str(name or ""))
    cleaned = _INVALID_FILENAME.sub("_", cleaned).strip().strip(".")
    if cleaned in {"", ".", ".."}:
        return fallback
    return cleaned


def default_output_name(source_name: str, speed: float, codec: str) -> str:
    stem = Path(source_name).stem or "output"
    return f"{stem}_{speed:g}x_{codec}.mp4"


def unique_output_path(directory: Path, filename: str, reserved: set[str]) -> Path:
    """Never overwrite the source or an existing render (``_1``, ``_2``, …)."""

    filename = sanitize_filename(filename)
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while _is_taken(candidate, reserved):
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def _is_taken(candidate: Path, reserved: set[str]) -> bool:
    key = str(candidate).casefold()
    if key in reserved:
        return True
    try:
        if candidate.exists():
            return True
        # Case-insensitive filesystems (Windows, default macOS) need this check.
        return candidate.parent.is_dir() and any(
            child.name.casefold() == candidate.name.casefold()
            for child in candidate.parent.iterdir()
        )
    except OSError:
        return False


def redact_credentials(text: str) -> str:
    """Mask ``user:password@`` in URLs before they reach logs or the browser."""

    return _CREDENTIAL_URL.sub(r"\1***@", str(text or ""))


def plan_source(raw: str) -> dict[str, Any]:
    """Validate a submitted source and decide how FFmpeg will read it.

    Returns ``display`` (shown in the UI), ``render`` (what ffmpeg gets), the
    ``name`` used for the default output filename and the ``kind`` of source.
    """

    value = (raw or "").strip()
    if not value:
        raise JobValidationError("A source video is required")

    if is_network_url(value):
        return {
            "kind": "url",
            "display": mask_credentials(value),
            "render": value,
            "name": network_source_name(value),
            "location": None,
        }

    routed = network_service.match_location(value)
    if routed is not None:
        location, _relative = routed
        name = PurePosixPath(value.replace("\\", "/")).name
        if location.mode == "smb":
            info = network_service.stat_path(value)
            if info is None or info["is_dir"]:
                raise JobValidationError(
                    f"The source file could not be found on {location.name}"
                )
            if not is_video_file(Path(name)):
                raise JobValidationError(f"Unsupported input format (use {SUPPORTED_INPUT_LABEL})")
            return {
                "kind": "smb",
                "display": value,
                "render": network_service.stream_url_for_path(value),
                "name": name,
                "location": location.public(),
            }
        candidate = Path(value)
        if not candidate.is_file():
            raise JobValidationError("The source path is not a file")
        if not is_video_file(candidate):
            raise JobValidationError(f"Unsupported input format (use {SUPPORTED_INPUT_LABEL})")
        return {
            "kind": "native",
            "display": str(candidate),
            "render": str(candidate),
            "name": candidate.name,
            "location": location.public(),
        }

    try:
        candidate = resolve_host_path(value, must_exist=True)
    except PathSecurityError as exc:
        raise JobValidationError(str(exc)) from exc
    if not candidate.is_file():
        raise JobValidationError("The source path is not a file")
    if not is_video_file(candidate):
        raise JobValidationError(f"Unsupported input format (use {SUPPORTED_INPUT_LABEL})")
    return {
        "kind": "local",
        "display": str(candidate),
        "render": str(candidate),
        "name": candidate.name,
        "location": None,
    }


def plan_destination(raw: str) -> dict[str, Any]:
    """Validate the destination folder (host drive, NAS share or native path)."""

    value = (raw or "").strip()
    if not value:
        raise JobValidationError("A destination folder is required")

    routed = network_service.match_location(value)
    if routed is not None:
        location, _relative = routed
        if location.mode == "smb":
            if not network_service.is_dir(value):
                raise JobValidationError(f"The destination folder was not found on {location.name}")
            return {
                "mode": "smb",
                "display": value,
                "location": location.public(),
            }
        candidate = Path(value)
        if not candidate.is_dir():
            raise JobValidationError("The target path is not a directory")
        if not os.access(candidate, os.W_OK):
            raise JobValidationError("The target directory is not writable")
        return {"mode": "local", "display": str(candidate), "path": candidate, "location": location.public()}

    try:
        candidate = resolve_host_path(value, must_exist=True)
    except PathSecurityError as exc:
        raise JobValidationError(str(exc)) from exc
    if not candidate.is_dir():
        raise JobValidationError("The target path is not a directory")
    if not os.access(candidate, os.W_OK):
        raise JobValidationError("The target directory is not writable")
    return {"mode": "local", "display": str(candidate), "path": candidate, "location": None}


class JobManager:
    """Runs one FFmpeg process at a time and streams progress to subscribers."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or EventBus()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._pending: list[str] = []
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._wake = asyncio.Event()
        # Render inputs may embed stream credentials; keep them out of the
        # snapshots that are broadcast to every connected browser.
        self._render_inputs: dict[str, str] = {}
        self._write_paths: dict[str, Path] = {}
        self._task: asyncio.Task[None] | None = None
        self._running_id: str | None = None
        self._stopping = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._worker_loop(), name="fastframe-render-worker")

    async def shutdown(self) -> None:
        self._stopping = True
        self._wake.set()
        for process in list(self._processes.values()):
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._running_id = None

    # ------------------------------------------------------------------ public API
    def snapshot(self) -> dict[str, Any]:
        return {
            "jobs": [copy.deepcopy(job) for job in self._jobs.values()],
            "processing_id": self._running_id,
            "queue_depth": len(self._pending),
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return copy.deepcopy(job)

    async def add_job(
        self, payload: QueueAddRequest, render_now: bool | None = None
    ) -> dict[str, Any]:
        source = plan_source(payload.input_path)
        destination = plan_destination(payload.output_dir)

        speed = validate_speed(payload.options.speed)
        codec = payload.options.codec
        encoders = await resolve_encoders(payload.options.prefer_gpu)
        if not encoders.get("ffmpeg_available"):
            raise JobValidationError("FFmpeg is not available inside the container")

        metadata = await probe_video(source["render"], display_name=source["name"])

        job_id = uuid.uuid4().hex[:12]
        filename = payload.output_filename or default_output_name(metadata["name"], speed, codec)
        filename = normalize_output_extension(sanitize_filename(filename), codec)

        reserved = {
            str(job["output_path"]).casefold()
            for job in self._jobs.values()
            if job["status"] in {"queued", "processing"}
        }

        transfer: dict[str, Any] | None = None
        write_path: Path | None = None
        if destination["mode"] == "smb":
            output_path = network_service.unique_output_path(
                destination["display"], filename, reserved
            )
            output_name = PurePosixPath(output_path.replace("\\", "/")).name
            container = PurePosixPath(output_name).suffix.lower()
            # FFmpeg cannot write straight to an SMB share without a kernel
            # mount, so it renders into a local staging file that is uploaded
            # to the share as soon as the encode finishes.
            write_path = network_service.staging_dir() / f"{job_id}_{output_name}"
            transfer = {
                "kind": "smb",
                "remote_path": output_path,
                "location_id": destination["location"]["id"],
                "location_name": destination["location"]["name"],
            }
        else:
            output_path = unique_output_path(destination["path"], filename, reserved)
            output_name = output_path.name
            container = output_path.suffix.lower()

        options = payload.options.model_dump()
        options["speed"] = speed
        options["output_container"] = container

        subtitle_path = options.get("subtitle_path")
        if subtitle_path:
            try:
                subtitle = resolve_host_path(subtitle_path, must_exist=True)
            except PathSecurityError as exc:
                raise JobValidationError(str(exc)) from exc
            if not subtitle.is_file() or not is_subtitle_file(subtitle):
                raise JobValidationError("Subtitle must be an existing .srt file inside the mount")
            options["subtitle_path"] = str(subtitle)
        else:
            options["subtitle_path"] = None

        network_source = None
        if source["kind"] != "local":
            network_source = {
                "kind": source["kind"],
                "location": source["location"],
                "display": source["display"],
            }

        render_path = str(write_path) if write_path else str(output_path)
        job: dict[str, Any] = {
            "id": job_id,
            "status": "queued",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "input_path": source["display"],
            "input_name": metadata["name"],
            "input_kind": source["kind"],
            "network_source": network_source,
            "output_path": str(output_path),
            "output_dir": destination["display"],
            "output_filename": output_name,
            "transfer": transfer,
            "options": options,
            "source": {
                "name": metadata["name"],
                "duration": metadata["duration"],
                "width": metadata["width"],
                "height": metadata["height"],
                "fps": metadata["fps"],
                "has_audio": metadata["has_audio"],
                "codec": metadata["codec"],
                "size_bytes": metadata["size_bytes"],
            },
            "encoder": encoders[codec],
            "encoder_hardware": bool(encoders.get("hardware")),
            "encoders": {"h264": encoders["h264"], "hevc": encoders["hevc"]},
            "ffmpeg_major": encoders.get("ffmpeg_major") or 0,
            "duration": ffmpeg_builder.target_duration(
                RenderOptions(
                    input_path=source["render"],
                    output_path=render_path,
                    speed=speed,
                    source_duration=metadata["duration"],
                    segments=options.get("segments") or [],
                    source_width=metadata["width"],
                    source_height=metadata["height"],
                    rotation=options.get("rotation") or "none",
                    crop=options.get("crop"),
                )
            ),
            "progress": {
                "percent": 0.0,
                "out_time_seconds": 0.0,
                "fps": 0.0,
                "encode_speed": 0.0,
                "eta_seconds": None,
                "frame": 0,
            },
            "output_size_bytes": None,
            "elapsed_seconds": 0.0,
            "error": None,
            "log": None,
        }

        self._render_inputs[job_id] = source["render"]
        if write_path:
            self._write_paths[job_id] = write_path

        self._jobs[job_id] = job
        priority = payload.render_now if render_now is None else render_now
        if priority:
            self._pending.insert(0, job_id)
        else:
            self._pending.append(job_id)
        self._wake.set()

        await self.bus.publish({"type": "job", "job": copy.deepcopy(job)})
        logger.info(
            "Queued job %s: %s -> %s (%s×, %s, %s, %s source)",
            job_id,
            metadata["name"],
            output_name,
            speed,
            codec,
            encoders[codec],
            source["kind"],
        )
        return copy.deepcopy(job)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)

        if job["status"] in {"completed", "failed", "cancelled"}:
            # Dismiss a finished job from the queue history.
            self._jobs.pop(job_id, None)
            self._render_inputs.pop(job_id, None)
            self._write_paths.pop(job_id, None)
            await self.bus.publish({"type": "snapshot", **self.snapshot()})
            return copy.deepcopy(job)

        if job["status"] == "queued":
            self._pending = [pending for pending in self._pending if pending != job_id]
            self._render_inputs.pop(job_id, None)
            self._write_paths.pop(job_id, None)
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            job["error"] = "Cancelled before start"
            await self._emit(job)
            return copy.deepcopy(job)

        if job["status"] != "processing":
            raise JobValidationError(f"Job is already {job['status']}")

        self._cancelled.add(job_id)
        job["status"] = "cancelling"
        await self._emit(job)

        process = self._processes.get(job_id)
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        job["error"] = "Cancelled by user"
        await self._emit(job)
        return copy.deepcopy(job)

    async def clear_completed(self) -> list[dict[str, Any]]:
        removable = [
            job_id
            for job_id, job in self._jobs.items()
            if job["status"] in {"completed", "failed", "cancelled"}
        ]
        for job_id in removable:
            self._jobs.pop(job_id, None)
            self._render_inputs.pop(job_id, None)
            self._write_paths.pop(job_id, None)
        if removable:
            await self.bus.publish({"type": "snapshot", **self.snapshot()})
        return [{"id": job_id} for job_id in removable]

    # ------------------------------------------------------------------ worker
    async def _worker_loop(self) -> None:
        while not self._stopping:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            job_id = self._pending.pop(0)
            job = self._jobs.get(job_id)
            if job is None or job["status"] != "queued":
                continue
            self._running_id = job_id
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Unexpected render failure for job %s", job_id)
                job["status"] = "failed"
                job["error"] = f"Internal error: {exc}"
                job["finished_at"] = time.time()
                await self._emit(job)
            finally:
                self._running_id = None
                self._cancelled.discard(job_id)

    async def _execute(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        job["status"] = "processing"
        job["started_at"] = time.time()
        job["error"] = None
        await self._emit(job)

        options = job["options"]
        hardware_encoder = job["encoders"]["h264" if options.get("codec") != "hevc" else "hevc"]
        render_input = self._render_inputs.pop(job_id, None) or job["input_path"]
        write_path = self._write_paths.pop(job_id, None)
        output_path = Path(write_path) if write_path else Path(job["output_path"])

        def build(h264_encoder: str, hevc_encoder: str) -> list[str]:
            render_options = RenderOptions(
                input_path=render_input,
                output_path=str(output_path),
                speed=options.get("speed", 1.0),
                codec=options.get("codec", "h264"),
                has_audio=bool(job["source"]["has_audio"]),
                source_width=job["source"]["width"],
                source_height=job["source"]["height"],
                source_duration=job["source"]["duration"],
                h264_encoder=h264_encoder,
                hevc_encoder=hevc_encoder,
                ffmpeg_major=job["ffmpeg_major"] or 5,
                segments=options.get("segments") or [],
                subtitle_path=options.get("subtitle_path"),
                subtitle_font_size=int(options.get("subtitle_font_size") or 24),
                rotation=options.get("rotation") or "none",
                flip_horizontal=bool(options.get("flip_horizontal")),
                crop=options.get("crop"),
            )
            return ffmpeg_builder.build_ffmpeg_command(render_options)

        try:
            command = build(job["encoders"]["h264"], job["encoders"]["hevc"])
        except FFmpegBuildError as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
            await self._emit(job)
            return

        try:
            returncode, log = await self._run_ffmpeg(job, command)
        except FileNotFoundError:
            job["status"] = "failed"
            job["error"] = "ffmpeg executable not found"
            job["finished_at"] = time.time()
            await self._emit(job)
            return

        # A listed NVENC encoder can still fail at runtime (driver/API mismatch,
        # exhausted encode sessions, container without /dev/nvidia*). Retry once
        # on the CPU encoder so a render never dies for that reason alone.
        if (
            returncode != 0
            and job_id not in self._cancelled
            and hardware_encoder.endswith("_nvenc")
            and _looks_like_nvenc_failure(log)
        ):
            logger.warning("Job %s: NVENC failed, retrying with CPU encoder", job_id)
            job["fallback_reason"] = "NVENC was unavailable at runtime — rendered with CPU encoder"
            job["encoder"] = "libx265" if options.get("codec") == "hevc" else "libx264"
            job["encoder_hardware"] = False
            job["progress"] = {
                "percent": 0.0,
                "out_time_seconds": 0.0,
                "fps": 0.0,
                "encode_speed": 0.0,
                "eta_seconds": None,
                "frame": 0,
            }
            await self._emit(job)
            try:
                command = build("libx264", "libx265")
                returncode, log = await self._run_ffmpeg(job, command)
            except FFmpegBuildError as exc:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["finished_at"] = time.time()
                await self._emit(job)
                return

        job["finished_at"] = time.time()
        job["elapsed_seconds"] = round(
            job["finished_at"] - (job["started_at"] or job["finished_at"]), 2
        )

        if job_id in self._cancelled:
            _remove_partial(output_path)
            job["status"] = "cancelled"
            job["progress"]["percent"] = 0.0
            await self._emit(job)
            return

        if returncode != 0:
            _remove_partial(output_path)
            job["status"] = "failed"
            job["error"] = f"FFmpeg exited with code {returncode}"
            job["log"] = log or "FFmpeg exited without writing a diagnostic message"
            await self._emit(job)
            logger.warning("Job %s failed: %s", job_id, job["error"])
            return

        try:
            size = output_path.stat().st_size
        except OSError:
            size = 0
        if size == 0:
            _remove_partial(output_path)
            job["status"] = "failed"
            job["error"] = "FFmpeg finished without producing an output file"
            job["log"] = log or "No output was produced"
            await self._emit(job)
            return

        transfer = job.get("transfer")
        if transfer:
            try:
                size = await asyncio.to_thread(
                    network_service.upload, output_path, job["output_path"]
                )
            except (OSError, NetworkSourceError) as exc:
                _remove_partial(output_path)
                job["status"] = "failed"
                job["error"] = f"Could not copy the render to {job['output_path']}: {exc}"
                job["log"] = log or job["error"]
                await self._emit(job)
                logger.warning("Job %s: upload to %s failed: %s", job_id, job["output_path"], exc)
                return
            _remove_partial(output_path)

        job["status"] = "completed"
        job["output_size_bytes"] = size
        job["log"] = None
        job["progress"].update(
            {"percent": 100.0, "out_time_seconds": round(job["duration"], 3), "eta_seconds": 0}
        )
        await self._emit(job)
        logger.info("Job %s completed: %s (%d bytes)", job_id, output_path, size)

    async def _run_ffmpeg(self, job: dict[str, Any], command: list[str]) -> tuple[int, str]:
        """Run one ffmpeg invocation, streaming progress and capturing stderr."""

        buffer = bytearray()
        job["command"] = redact_credentials(" ".join(command))
        logger.info("Job %s: %s", job["id"], job["command"])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._processes[job["id"]] = process
        stderr_task = asyncio.create_task(self._drain_stderr(process.stderr, buffer))
        try:
            if process.stdout is not None:
                await self._consume_progress(job, process.stdout)
            returncode = await process.wait()
        finally:
            with contextlib.suppress(Exception):
                await stderr_task
            self._processes.pop(job["id"], None)
        return returncode, redact_credentials(_decode_tail(buffer))

    async def _consume_progress(
        self, job: dict[str, Any], stream: asyncio.StreamReader
    ) -> None:
        out_time = 0.0
        fps = 0.0
        encode_speed = 0.0
        frame = 0
        last_emit = 0.0

        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text or "=" not in text:
                continue
            key, _, raw_value = text.partition("=")
            key = key.strip()
            raw_value = raw_value.strip()

            if key in {"out_time_us", "out_time_ms"}:
                try:
                    microseconds = int(raw_value)
                except ValueError:
                    continue
                if microseconds < 0:
                    continue
                out_time = microseconds / 1_000_000
            elif key == "fps":
                try:
                    fps = float(raw_value)
                except ValueError:
                    pass
            elif key == "speed":
                try:
                    encode_speed = float(raw_value.rstrip("x"))
                except ValueError:
                    pass
            elif key == "frame":
                try:
                    frame = int(raw_value)
                except ValueError:
                    pass
            elif key == "progress":
                if raw_value == "end":
                    out_time = max(out_time, job["duration"])
                self._update_progress(job, out_time, fps, encode_speed, frame)
                await self._emit(job)
                last_emit = time.monotonic()
                continue

            now = time.monotonic()
            if now - last_emit >= PROGRESS_EMIT_INTERVAL:
                self._update_progress(job, out_time, fps, encode_speed, frame)
                await self._emit(job)
                last_emit = now

    @staticmethod
    def _update_progress(
        job: dict[str, Any], out_time: float, fps: float, encode_speed: float, frame: int
    ) -> None:
        target = float(job.get("duration") or 0)
        percent = 0.0 if target <= 0 else max(0.0, min(100.0, out_time / target * 100))
        eta = None
        if target > 0 and encode_speed > 0:
            eta = max(0.0, (target - out_time) / encode_speed)
        job["progress"] = {
            "percent": round(percent, 2),
            "out_time_seconds": round(out_time, 3),
            "fps": round(fps, 2),
            "encode_speed": round(encode_speed, 2),
            "eta_seconds": round(eta, 1) if eta is not None else None,
            "frame": frame,
        }
        if job.get("started_at"):
            job["elapsed_seconds"] = round(time.time() - job["started_at"], 1)

    @staticmethod
    async def _drain_stderr(stream: asyncio.StreamReader | None, buffer: bytearray) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > MAX_STDERR_BYTES:
                del buffer[: len(buffer) - MAX_STDERR_BYTES]

    async def _emit(self, job: dict[str, Any]) -> None:
        await self.bus.publish({"type": "job", "job": copy.deepcopy(job)})


def _decode_tail(buffer: bytearray) -> str:
    text = buffer.decode("utf-8", "replace").strip()
    return text[-MAX_ERROR_LOG_CHARS:]


def _looks_like_nvenc_failure(log: str) -> bool:
    lowered = (log or "").lower()
    return any(marker in lowered for marker in NVENC_FAILURE_MARKERS)


def _remove_partial(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.is_file():
            path.unlink()


event_bus = EventBus()
job_manager = JobManager(event_bus)
