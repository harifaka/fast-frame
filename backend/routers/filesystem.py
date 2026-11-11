"""Zero-copy file browser, ffprobe metadata and byte-range video streaming."""

from __future__ import annotations

import asyncio
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config import (
    PathSecurityError,
    host_root,
    is_subtitle_file,
    is_video_file,
    relative_to_root,
    resolve_host_path,
)
from services.ffprobe_service import ProbeError, probe_video

router = APIRouter(prefix="/api", tags=["filesystem"])

STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MiB
MAX_DIRECTORY_ENTRIES = 5000
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$", re.IGNORECASE)


def _safe_resolve(raw: str | None, *, must_exist: bool) -> Path:
    try:
        return resolve_host_path(raw, must_exist=must_exist)
    except PathSecurityError as exc:
        if must_exist and "does not exist" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _entry_for(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        size_bytes = 0 if path.is_dir() else stat.st_size
        modified = stat.st_mtime
    except OSError:
        size_bytes = 0
        modified = 0.0

    is_dir = path.is_dir()
    if is_dir:
        kind = "directory"
    elif is_video_file(path):
        kind = "video"
    elif is_subtitle_file(path):
        kind = "subtitle"
    else:
        kind = "other"

    return {
        "name": path.name,
        "path": str(path),
        "relative_path": relative_to_root(path),
        "is_dir": is_dir,
        "kind": kind,
        "extension": "" if is_dir else path.suffix.lower(),
        "size_bytes": size_bytes,
        "modified": modified,
        "modified_iso": (
            datetime.fromtimestamp(modified, tz=timezone.utc).isoformat()
            if modified
            else None
        ),
    }


@router.get("/fs/list")
async def list_directory(
    path: str | None = Query(default=None, description="Absolute path inside HOST_MOUNT_PATH"),
) -> dict[str, Any]:
    """List a directory on the host. Only media and subtitle files are flagged."""

    directory = _safe_resolve(path, must_exist=False)
    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {directory}")
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="Requested path is not a directory")

    try:
        children = sorted(
            directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entries = [_entry_for(child) for child in children[:MAX_DIRECTORY_ENTRIES]]
    root = host_root()
    parent = str(directory.parent) if directory != root and directory.parent != directory else None
    return {
        "path": str(directory),
        "relative_path": relative_to_root(directory),
        "root": str(root),
        "is_root": directory == root,
        "parent": parent,
        "breadcrumbs": _breadcrumbs(directory, root),
        "entries": entries,
        "truncated": len(children) > MAX_DIRECTORY_ENTRIES,
    }


def _breadcrumbs(directory: Path, root: Path) -> list[dict[str, str]]:
    crumbs: list[dict[str, str]] = [{"name": root.name or str(root), "path": str(root)}]
    try:
        parts = directory.relative_to(root).parts
    except ValueError:
        return crumbs
    current = root
    for part in parts:
        current = current / part
        crumbs.append({"name": part, "path": str(current)})
    return crumbs


@router.get("/video/metadata")
async def video_metadata(path: str = Query(..., description="Video file path")) -> dict[str, Any]:
    file_path = _safe_resolve(path, must_exist=True)
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    if not is_video_file(file_path):
        raise HTTPException(
            status_code=400,
            detail="Unsupported video format. Supported: .mp4, .mkv, .avi",
        )
    try:
        return await probe_video(file_path)
    except ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _parse_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    match = _RANGE_RE.match(range_header.strip())
    if not match:
        return None
    raw_start, raw_end = match.groups()
    if raw_start == "" and raw_end == "":
        return None
    if raw_start == "":
        suffix_length = int(raw_end)
        if suffix_length <= 0:
            return None
        suffix_length = min(suffix_length, file_size)
        return file_size - suffix_length, file_size - 1
    start = int(raw_start)
    end = int(raw_end) if raw_end else file_size - 1
    if start >= file_size:
        return None
    end = min(end, file_size - 1)
    if end < start:
        return None
    return start, end


def _media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        if path.suffix.lower() == ".mkv":
            return "video/x-matroska"
        if path.suffix.lower() == ".avi":
            return "video/x-msvideo"
        return guessed
    return "application/octet-stream"


def _stream_headers(file_path: Path, file_size: int) -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-File-Path": str(file_path),
        "X-File-Size": str(file_size),
    }


async def _file_chunks(file_path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    remaining = length
    try:
        with file_path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = await asyncio.to_thread(handle.read, min(STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    except (ConnectionResetError, asyncio.CancelledError):  # client seeked away / closed
        return
    except OSError:
        if remaining:
            raise


@router.head("/video/stream")
async def stream_video_head(path: str = Query(...)) -> StreamingResponse:
    file_path = _safe_resolve(path, must_exist=True)
    if not file_path.is_file() or not is_video_file(file_path):
        raise HTTPException(status_code=400, detail="Unsupported video file")
    file_size = file_path.stat().st_size
    headers = _stream_headers(file_path, file_size)
    headers["Content-Length"] = str(file_size)
    return StreamingResponse(iter(()), media_type=_media_type(file_path), headers=headers)


@router.get("/video/stream")
async def stream_video(request: Request, path: str = Query(...)) -> StreamingResponse:
    """Serve a local video file with HTTP 206 partial-content support.

    The browser's ``<video>`` element performs range requests while seeking;
    nothing is copied or uploaded, bytes are streamed straight off the host disk.
    """

    file_path = _safe_resolve(path, must_exist=True)
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    if not is_video_file(file_path):
        raise HTTPException(status_code=400, detail="Unsupported video file")

    file_size = file_path.stat().st_size
    if file_size == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    headers = _stream_headers(file_path, file_size)
    media_type = _media_type(file_path)
    range_header = request.headers.get("range")

    if range_header:
        parsed = _parse_range(range_header, file_size)
        if parsed is None:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{file_size}"},
                media_type=media_type,
            )
        start, end = parsed
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(length)
        return StreamingResponse(
            _file_chunks(file_path, start, length),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    headers["Content-Length"] = str(file_size)
    return StreamingResponse(
        _file_chunks(file_path, 0, file_size),
        status_code=200,
        headers=headers,
        media_type=media_type,
    )
