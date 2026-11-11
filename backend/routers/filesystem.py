"""Zero-copy file browser, ffprobe metadata and byte-range video streaming."""

from __future__ import annotations

import asyncio
import mimetypes
import re
import urllib.error
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config import (
    PathSecurityError,
    host_root,
    is_network_url,
    is_previewable_url,
    is_subtitle_file,
    is_video_file,
    mask_credentials,
    network_url_scheme,
    relative_to_root,
    resolve_host_path,
)
from services import network_service
from services.ffprobe_service import ProbeError, probe_video
from services.network_service import NetworkSourceError

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

    routed = network_service.match_location((path or "").strip())
    if routed is not None:
        try:
            return network_service.list_directory((path or "").strip())
        except NetworkSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
async def video_metadata(
    path: str | None = Query(default=None, description="Video file path"),
    url: str | None = Query(default=None, description="Direct http/https/rtsp/WebDAV URL"),
) -> dict[str, Any]:
    """Probe a local file or a network stream without copying it first."""

    remote = (url or "").strip()
    if remote:
        return await _probe_url(remote)

    raw = (path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Provide a 'path' or a 'url'")

    if is_network_url(raw):
        return await _probe_url(raw)

    routed = network_service.match_location(raw)
    if routed is not None:
        location, _relative = routed
        if location.mode == "smb":
            info = network_service.stat_path(raw)
            if info is None:
                raise HTTPException(status_code=404, detail=f"Cannot read {raw} on {location.name}")
            if info["is_dir"]:
                raise HTTPException(status_code=400, detail="Path is not a file")
            name = PurePosixPath(raw.replace("\\", "/")).name
            if not is_video_file(Path(name)):
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported video format. Supported: .mp4, .mkv, .avi",
                )
            try:
                metadata = await probe_video(network_service.stream_url_for_path(raw), display_name=name)
            except ProbeError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            metadata["path"] = raw
            metadata["network"] = {
                "kind": "smb",
                "location": location.public(),
                "preview_supported": True,
            }
            return metadata
        # native network paths live on the OS filesystem - fall through.

    file_path = _safe_resolve(raw, must_exist=True)
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


async def _probe_url(target: str) -> dict[str, Any]:
    if not is_network_url(target):
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported URL. Use http://, https://, rtsp:// or a WebDAV link "
                "(webdav://, webdavs://)."
            ),
        )
    try:
        metadata = await probe_video(target)
    except ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metadata["path"] = mask_credentials(target)
    metadata["source_url"] = mask_credentials(target)
    metadata["network"] = {
        "kind": "url",
        "scheme": network_url_scheme(target),
        "preview_supported": is_previewable_url(target),
    }
    return metadata


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


def _stream_target(path: str | None, url: str | None) -> tuple[str, str]:
    """Classify a stream request as ``url``, ``smb`` or ``local``."""

    raw_path = (path or "").strip()
    remote = (url or "").strip()
    if not remote and is_network_url(raw_path):
        remote = raw_path
    if remote:
        if not is_previewable_url(remote):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This stream cannot be previewed in a browser (RTSP/RTMP). "
                    "Rendering still reads it directly from the source."
                ),
            )
        return "url", remote

    if not raw_path:
        raise HTTPException(status_code=400, detail="Provide a 'path' or a 'url'")
    routed = network_service.match_location(raw_path)
    if routed is not None and routed[0].mode == "smb":
        return "smb", raw_path
    return "local", raw_path


def _remote_headers(remote: Any, target: str) -> dict[str, str]:
    headers = {"Cache-Control": "no-store", "X-Stream-Url": mask_credentials(target)}
    for key in ("Content-Range", "Content-Length", "Accept-Ranges"):
        value = remote.headers.get(key)
        if value:
            headers[key] = value
    headers.setdefault("Accept-Ranges", "bytes")
    return headers


def _range_total(content_range: str | None) -> int | None:
    if not content_range or "/" not in content_range:
        return None
    total = content_range.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else None


def _remote_media_type(remote: Any, clean_url: str) -> str:
    content_type = (remote.headers.get("Content-Type") or "").split(";")[0].strip()
    if content_type and content_type != "application/octet-stream":
        return content_type
    return _media_type(Path(clean_url.split("?", 1)[0]))


async def _remote_chunks(remote: Any) -> AsyncIterator[bytes]:
    try:
        while True:
            chunk = await asyncio.to_thread(remote.read, STREAM_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    except (ConnectionResetError, asyncio.CancelledError):
        return
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream stream failed: {exc}") from exc
    finally:
        await asyncio.to_thread(remote.close)


async def _proxy_remote(request: Request, target: str) -> StreamingResponse:
    """Relay an HTTP(S)/WebDAV stream (with Range support) to the browser."""

    try:
        remote, clean = await asyncio.to_thread(
            network_service.open_remote, target, request.headers.get("range")
        )
    except urllib.error.HTTPError as exc:
        status = exc.code if exc.code in {400, 401, 403, 404, 416} else 502
        raise HTTPException(
            status_code=status,
            detail=f"{mask_credentials(target)} returned HTTP {exc.code}",
        ) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach {mask_credentials(target)}: {exc}",
        ) from exc

    status = int(getattr(remote, "status", 200) or 200)
    headers = _remote_headers(remote, target)
    if status not in {200, 206}:
        await asyncio.to_thread(remote.close)
        raise HTTPException(status_code=502, detail=f"Upstream returned HTTP {status}")
    return StreamingResponse(
        _remote_chunks(remote),
        status_code=status,
        headers=headers,
        media_type=_remote_media_type(remote, clean),
    )


async def _network_chunks(raw: str, start: int, length: int) -> AsyncIterator[bytes]:
    handle = await asyncio.to_thread(network_service.open_read, raw)
    try:
        await asyncio.to_thread(handle.seek, start)
        remaining = length
        while remaining > 0:
            chunk = await asyncio.to_thread(handle.read, min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    except (ConnectionResetError, asyncio.CancelledError):
        return
    finally:
        await asyncio.to_thread(handle.close)


async def _stream_network_file(request: Request, raw: str) -> StreamingResponse:
    """Stream a share that is not kernel-mounted, honouring Range requests."""

    try:
        info = await asyncio.to_thread(network_service.stat_path, raw)
    except NetworkSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if info is None:
        raise HTTPException(status_code=404, detail=f"Cannot read {raw}")
    if info["is_dir"]:
        raise HTTPException(status_code=400, detail="Path is not a file")

    file_size = int(info["size_bytes"])
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="File is empty")
    media_type = _media_type(Path(PurePosixPath(raw.replace("\\", "/")).name))
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-File-Path": raw,
        "X-File-Size": str(file_size),
    }

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
            _network_chunks(raw, start, length),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    headers["Content-Length"] = str(file_size)
    return StreamingResponse(
        _network_chunks(raw, 0, file_size),
        status_code=200,
        headers=headers,
        media_type=media_type,
    )


@router.head("/video/stream")
async def stream_video_head(
    path: str | None = Query(default=None), url: str | None = Query(default=None)
) -> StreamingResponse:
    kind, target = _stream_target(path, url)

    if kind == "url":
        try:
            remote, clean = await asyncio.to_thread(
                network_service.open_remote, target, "bytes=0-0"
            )
        except urllib.error.HTTPError as exc:
            raise HTTPException(
                status_code=exc.code if exc.code in {400, 401, 403, 404} else 502,
                detail=f"{mask_credentials(target)} returned HTTP {exc.code}",
            ) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Cannot reach {mask_credentials(target)}: {exc}"
            ) from exc
        try:
            headers = _remote_headers(remote, target)
            media_type = _remote_media_type(remote, clean)
            total = _range_total(remote.headers.get("Content-Range"))
        finally:
            await asyncio.to_thread(remote.close)
        if total is not None:
            headers["Content-Length"] = str(total)
        return StreamingResponse(iter(()), media_type=media_type, headers=headers)

    if kind == "smb":
        info = await asyncio.to_thread(network_service.stat_path, target)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Cannot read {target}")
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "Content-Length": str(int(info["size_bytes"])),
            "X-File-Path": target,
            "X-File-Size": str(int(info["size_bytes"])),
        }
        media_type = _media_type(Path(PurePosixPath(target.replace("\\", "/")).name))
        return StreamingResponse(iter(()), media_type=media_type, headers=headers)

    file_path = _safe_resolve(target, must_exist=True)
    if not file_path.is_file() or not is_video_file(file_path):
        raise HTTPException(status_code=400, detail="Unsupported video file")
    file_size = file_path.stat().st_size
    headers = _stream_headers(file_path, file_size)
    headers["Content-Length"] = str(file_size)
    return StreamingResponse(iter(()), media_type=_media_type(file_path), headers=headers)


@router.get("/video/stream")
async def stream_video(
    request: Request,
    path: str | None = Query(default=None),
    url: str | None = Query(default=None),
) -> StreamingResponse:
    """Serve a local file, a network share or a remote URL with Range support.

    The browser's ``<video>`` element performs range requests while seeking;
    nothing is copied or uploaded, bytes are streamed straight from the source.
    """

    kind, target = _stream_target(path, url)
    if kind == "url":
        return await _proxy_remote(request, target)
    if kind == "smb":
        return await _stream_network_file(request, target)

    file_path = _safe_resolve(target, must_exist=True)
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


@router.head("/fs/network-stream")
async def network_stream_head(path: str = Query(...)) -> StreamingResponse:
    """HEAD probe for the loopback relay used when a share is not mounted."""

    if network_service.match_location(path) is None:
        raise HTTPException(status_code=404, detail="Unknown network location")
    info = await asyncio.to_thread(network_service.stat_path, path)
    if info is None or info["is_dir"]:
        raise HTTPException(status_code=404, detail=f"Cannot read {path}")
    size = int(info["size_bytes"])
    media_type = _media_type(Path(PurePosixPath(path.replace("\\", "/")).name))
    return StreamingResponse(
        iter(()),
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(size),
            "X-File-Path": path,
            "X-File-Size": str(size),
        },
    )


@router.get("/fs/network-stream")
async def network_stream(request: Request, path: str = Query(...)) -> StreamingResponse:
    """Loopback HTTP relay for SMB shares that could not be kernel-mounted.

    FFmpeg reads this URL with ``-i http://127.0.0.1:8000/api/fs/network-stream``
    so a NAS render still avoids downloading a temporary copy of the source.
    """

    if network_service.match_location(path) is None:
        raise HTTPException(status_code=404, detail="Unknown network location")
    return await _stream_network_file(request, path)
