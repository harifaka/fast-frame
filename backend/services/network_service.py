"""Network sources: direct stream URLs plus SMB/CIFS and native network shares.

Three access modes are supported so a NAS can be used for both the source video
and the destination folder without ever copying a whole file first:

``kernel``
    ``mount.cifs`` succeeded (Linux container running with ``CAP_SYS_ADMIN``);
    the share is a real directory under ``/mnt/host/network/<share>`` and every
    other part of Fast-Frame treats it like a local drive.
``smb``
    Pure-Python SMB through ``smbclient``; files are read on demand as byte
    ranges (preview) or relayed to ffmpeg over a loopback HTTP stream, so the
    render still never materialises a temporary copy of the source.
``native``
    The host OS can already see the share (Windows ``\\\\server\\share`` or a
    mapped drive such as ``Z:``); paths are passed straight through.
"""

from __future__ import annotations

import base64
import logging
import ntpath
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlparse, urlunparse

from config import (
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ffmpeg_network_url,
    network_root,
)

logger = logging.getLogger(__name__)

MAX_NETWORK_ENTRIES = 5000
STAGING_DIR_NAME = ".staging"
_DEFAULT_INTERNAL_URL = "http://127.0.0.1:8000"

_UNC_SCHEME = re.compile(r"^(?:smb|cifs)://", re.IGNORECASE)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class NetworkSourceError(RuntimeError):
    """Raised when a network location cannot be reached or used."""


@dataclass
class NetworkLocation:
    """A registered SMB share or native network path."""

    id: str
    name: str
    mode: str
    root: str
    unc: str | None = None
    server: str | None = None
    share: str | None = None
    username: str | None = None
    password: str | None = None
    connected_at: float = 0.0

    def public(self) -> dict[str, Any]:
        """Serialisable view - the password never leaves the process."""

        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "root": self.root,
            "unc": self.unc,
            "server": self.server,
            "share": self.share,
            "username": self.username,
            "has_credentials": bool(self.username or self.password),
            "connected_at": self.connected_at,
        }


_locations: dict[str, NetworkLocation] = {}
_lock = threading.RLock()


# ------------------------------------------------------------------ dependencies
def _smbclient():
    """Return the ``smbclient`` module, or ``None`` when it is not installed."""

    try:
        import smbclient  # noqa: PLC0415 - optional dependency, imported lazily
    except ImportError:  # pragma: no cover - depends on the installed image
        return None
    return smbclient


def _smb_path_module():
    try:
        import smbclient.path as smbpath  # noqa: PLC0415 - optional dependency
    except ImportError:  # pragma: no cover
        return None
    return smbpath


def smb_available() -> bool:
    return _smbclient() is not None


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


# ------------------------------------------------------------------ location registry
def list_locations() -> list[dict[str, Any]]:
    with _lock:
        return [location.public() for location in _locations.values()]


def get_location(location_id: str) -> NetworkLocation | None:
    with _lock:
        return _locations.get(location_id)


def _unique_name(candidate: str) -> str:
    base = _NAME_UNSAFE.sub("_", candidate or "").strip("._") or "share"
    taken = {location.name.casefold() for location in _locations.values()}
    name = base
    counter = 1
    while name.casefold() in taken:
        name = f"{base}_{counter}"
        counter += 1
    return name


def _register(
    *,
    name: str,
    mode: str,
    root: str,
    unc: str | None = None,
    server: str | None = None,
    share: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> NetworkLocation:
    location = NetworkLocation(
        id=uuid.uuid4().hex[:12],
        name=name,
        mode=mode,
        root=root,
        unc=unc,
        server=server,
        share=share,
        username=username or None,
        password=password or None,
        connected_at=time.time(),
    )
    with _lock:
        _locations[location.id] = location
    logger.info("Network location %s (%s) ready at %s", name, mode, root)
    return location


def _drop_location(location_id: str) -> NetworkLocation | None:
    with _lock:
        return _locations.pop(location_id, None)


# ------------------------------------------------------------------ share parsing
def parse_share_path(raw: str) -> tuple[str, str, str | None, str | None]:
    """Split ``\\\\server\\share`` / ``//server/share`` / ``smb://...`` input.

    Returns ``(server, share, username, password)`` where the credentials come
    from an ``smb://user:pass@server/share`` URL when present.
    """

    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise NetworkSourceError("Enter an SMB path such as //192.168.1.10/videos")

    username: str | None = None
    password: str | None = None
    if _UNC_SCHEME.match(text):
        parsed = urlparse(text)
        server = parsed.hostname or ""
        segments = [segment for segment in parsed.path.split("/") if segment]
        share = segments[0] if segments else ""
        username = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else None
    else:
        segments = [segment for segment in text.replace("/", "\\").split("\\") if segment]
        server = segments[0] if segments else ""
        share = segments[1] if len(segments) > 1 else ""

    if not server or not share:
        raise NetworkSourceError(
            "Use the form //server/share, \\\\server\\share or smb://server/share"
        )
    if _WINDOWS_DRIVE.match(server + "\\"):
        raise NetworkSourceError(
            f"'{raw}' is a local drive, not an SMB share - pick it in Host Drives"
        )
    return server, share, username, password


def _native_candidate(raw: str) -> str | None:
    """Raw path usable as-is when the host OS already resolves the network path."""

    text = (raw or "").strip().strip('"').strip("'")
    if platform.system() != "Windows":
        return None
    if _WINDOWS_DRIVE.match(text) or text.startswith("\\\\"):
        return text
    return None


def _credentials_file(username: str | None, password: str | None) -> str | None:
    """Write ``mount.cifs`` credentials to a 0600 temp file (never argv)."""

    if not username:
        return None
    handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".cred")
    try:
        handle.write(f"username={username}\npassword={password or ''}\n")
    finally:
        handle.close()
    try:
        os.chmod(handle.name, 0o600)
    except OSError:  # pragma: no cover - non-POSIX
        pass
    return handle.name


def _try_kernel_mount(
    unc: str, mountpoint: Path, username: str | None, password: str | None
) -> bool:
    """Best-effort ``mount -t cifs``; returns True when the share is mounted."""

    if platform.system() != "Linux" or not _is_root() or not shutil.which("mount"):
        return False
    options = ["vers=3.0", "iocharset=utf8", "noserverino", "uid=0", "gid=0"]
    credentials = _credentials_file(username, password)
    if credentials:
        options.append(f"credentials={credentials}")
    else:
        options.append("guest")
    command = ["mount", "-t", "cifs", unc, str(mountpoint), "-o", ",".join(options)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        if result.returncode != 0:
            logger.info(
                "CIFS mount of %s failed: %s",
                unc,
                result.stderr.decode("utf-8", "replace").strip()[:400],
            )
            return False
        return os.path.ismount(str(mountpoint))
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - platform
        logger.info("CIFS mount of %s could not run: %s", unc, exc)
        return False
    finally:
        if credentials:
            try:
                os.unlink(credentials)
            except OSError:
                pass


def register_native_path(path: str, name: str | None = None) -> NetworkLocation:
    """Expose a network path the host OS already resolves (UNC or mapped drive)."""

    if not os.path.isdir(path):
        raise NetworkSourceError(f"Path is not reachable from this host: {path}")
    default_name = ntpath.basename(str(path).rstrip("\\/")) or str(path)[:2] or "network"
    return _register(name=_unique_name(name or default_name), mode="native", root=str(path))


def _connect_smb_session(server: str, username: str | None, password: str | None):
    smb = _smbclient()
    if smb is None:
        raise NetworkSourceError(
            "SMB support is unavailable: install the smbclient package or mount "
            "the share on the host and bind-mount it under the host root"
        )
    try:
        smb.register_session(
            server,
            username=username or None,
            password=password or None,
            connection_timeout=20,
        )
    except Exception as exc:  # smbprotocol raises a variety of errors
        raise NetworkSourceError(f"Cannot connect to {server}: {exc}") from exc
    return smb


def connect_share(
    raw_path: str,
    *,
    username: str | None = None,
    password: str | None = None,
    name: str | None = None,
) -> NetworkLocation:
    """Connect an SMB share and expose it under ``NETWORK_MOUNT_PATH``."""

    native = _native_candidate(raw_path)
    if native:
        return register_native_path(native, name=name)

    server, share, url_user, url_pass = parse_share_path(raw_path)
    username = username if username is not None else url_user
    password = password if password is not None else url_pass
    unc = f"\\\\{server}\\{share}"

    location_name = _unique_name(name or share)
    root = network_root() / location_name
    root.mkdir(parents=True, exist_ok=True)

    if _try_kernel_mount(unc, root, username, password):
        return _register(
            name=location_name,
            mode="kernel",
            root=str(root),
            unc=unc,
            server=server,
            share=share,
            username=username,
            password=password,
        )

    try:
        root.rmdir()  # only succeeds while the placeholder is still empty
    except OSError:
        pass

    smb = _connect_smb_session(server, username, password)
    try:
        smb.listdir(unc)
    except Exception as exc:
        raise NetworkSourceError(f"Cannot list {unc}: {exc}") from exc

    root.mkdir(parents=True, exist_ok=True)
    return _register(
        name=location_name,
        mode="smb",
        root=str(root),
        unc=unc,
        server=server,
        share=share,
        username=username,
        password=password,
    )


def disconnect(location_id: str) -> bool:
    location = _drop_location(location_id)
    if location is None:
        return False

    if location.mode == "kernel" and platform.system() == "Linux":
        try:
            subprocess.run(
                ["umount", location.root], capture_output=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - platform
            logger.warning("Could not unmount %s", location.root)
    elif location.mode == "smb" and location.server:
        still_used = any(
            other.server == location.server for other in _locations.values()
        )
        smb = _smbclient()
        if smb is not None and not still_used:
            try:
                smb.delete_session(location.server)
            except Exception:  # pragma: no cover - session already gone
                pass

    if location.mode in {"kernel", "smb"}:
        try:
            Path(location.root).rmdir()
        except OSError:
            pass  # still holds data or is the mount itself
    logger.info("Disconnected network location %s", location.name)
    return True


# ------------------------------------------------------------------ path matching
def _posix_parts(raw: str) -> tuple[str, ...]:
    normalised = str(raw or "").replace("\\", "/").strip("/")
    return tuple(part for part in normalised.split("/") if part)


def _relative_posix(root: str, raw: str) -> str | None:
    root_parts = _posix_parts(root)
    candidate = _posix_parts(raw)
    if candidate[: len(root_parts)] != root_parts:
        return None
    return "/".join(candidate[len(root_parts) :])


def _relative_native(root: str, raw: str) -> str | None:
    normalised_root = ntpath.normcase(ntpath.normpath(root)).rstrip("\\/")
    normalised_raw = ntpath.normcase(ntpath.normpath(str(raw))).rstrip("\\/")
    if normalised_raw == normalised_root:
        return ""
    if normalised_raw.startswith(normalised_root + os.sep) or normalised_raw.startswith(
        normalised_root + "\\"
    ):
        return normalised_raw[len(normalised_root) + 1 :].replace("\\", "/")
    return None


def match_location(raw: str | None) -> tuple[NetworkLocation, str] | None:
    """Return ``(location, relative path)`` for paths routed through this module."""

    if not raw:
        return None
    with _lock:
        locations = list(_locations.values())
    for location in locations:
        if location.mode == "smb":
            relative = _relative_posix(location.root, raw)
        elif location.mode == "native":
            relative = _relative_native(location.root, raw)
        else:
            continue
        if relative is not None:
            return location, relative
    return None


def _virtual_path(location: NetworkLocation, relative: str) -> str:
    if location.mode == "native":
        if not relative:
            return location.root
        return os.path.join(location.root, relative.replace("/", os.sep))
    root = PurePosixPath(location.root)
    return str(root / relative) if relative else str(root)


def _parent_of(location: NetworkLocation, relative: str) -> str | None:
    if not relative:
        return None
    parts = [part for part in relative.split("/") if part][:-1]
    return _virtual_path(location, "/".join(parts))


# ------------------------------------------------------------------ SMB / native listing
def _join_unc(root: str, relative: str) -> str:
    parts = [part for part in str(relative or "").replace("\\", "/").split("/") if part]
    return root.rstrip("\\") + ("\\" + "\\".join(parts) if parts else "")


def _smb_root(location: NetworkLocation) -> str:
    return location.unc or ""


def _kind_for(name: str, is_dir: bool) -> str:
    if is_dir:
        return "directory"
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in SUBTITLE_EXTENSIONS:
        return "subtitle"
    return "other"


def _entry(
    location: NetworkLocation,
    relative: str,
    name: str,
    *,
    is_dir: bool,
    size_bytes: int,
    modified: float,
) -> dict[str, Any]:
    child_relative = f"{relative}/{name}" if relative else name
    return {
        "name": name,
        "path": _virtual_path(location, child_relative),
        "relative_path": child_relative,
        "is_dir": is_dir,
        "kind": _kind_for(name, is_dir),
        "extension": "" if is_dir else PurePosixPath(name).suffix.lower(),
        "size_bytes": 0 if is_dir else int(size_bytes or 0),
        "modified": float(modified or 0.0),
        "modified_iso": (
            datetime.fromtimestamp(modified, tz=timezone.utc).isoformat() if modified else None
        ),
    }


def _smb_entries(location: NetworkLocation, relative: str) -> tuple[list[dict[str, Any]], bool]:
    smb = _smbclient()
    if smb is None:  # pragma: no cover - guarded by connect_share
        raise NetworkSourceError("SMB support is unavailable")
    unc = _join_unc(_smb_root(location), relative)
    try:
        scan = smb.scandir(unc)
    except Exception as exc:
        raise NetworkSourceError(f"Cannot list {unc}: {exc}") from exc

    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        for item in scan:
            if len(entries) >= MAX_NETWORK_ENTRIES:
                truncated = True
                break
            try:
                is_dir = bool(item.is_dir())
                stats = item.stat() if hasattr(item, "stat") else None
                size = int(getattr(stats, "st_size", 0) or 0) if stats else 0
                modified = float(getattr(stats, "st_mtime", 0.0) or 0.0) if stats else 0.0
            except Exception:  # unreadable entry - still show its name
                is_dir, size, modified = False, 0, 0.0
            entries.append(
                _entry(
                    location,
                    relative,
                    item.name,
                    is_dir=is_dir,
                    size_bytes=size,
                    modified=modified,
                )
            )
    finally:
        close = getattr(scan, "close", None)
        if callable(close):
            close()
    return entries, truncated


def _native_entries(
    location: NetworkLocation, relative: str
) -> tuple[list[dict[str, Any]], bool]:
    directory = Path(_virtual_path(location, relative))
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(directory) as scan:
            for item in scan:
                if len(entries) >= MAX_NETWORK_ENTRIES:
                    truncated = True
                    break
                try:
                    is_dir = item.is_dir()
                    stats = item.stat()
                    size, modified = stats.st_size, stats.st_mtime
                except OSError:
                    is_dir, size, modified = False, 0, 0.0
                entries.append(
                    _entry(
                        location,
                        relative,
                        item.name,
                        is_dir=is_dir,
                        size_bytes=size,
                        modified=modified,
                    )
                )
    except OSError as exc:
        raise NetworkSourceError(f"Cannot list {directory}: {exc}") from exc
    return entries, truncated


def _breadcrumbs(location: NetworkLocation, relative: str) -> list[dict[str, str]]:
    crumbs = [{"name": location.name, "path": location.root}]
    parts = [part for part in relative.split("/") if part]
    for index, part in enumerate(parts):
        crumbs.append(
            {"name": part, "path": _virtual_path(location, "/".join(parts[: index + 1]))}
        )
    return crumbs


def list_directory(raw: str) -> dict[str, Any]:
    """Directory listing shaped exactly like ``GET /api/fs/list``."""

    match = match_location(raw)
    if match is None:
        raise NetworkSourceError(f"Not a registered network location: {raw}")
    location, relative = match

    if not relative:
        virtual = location.root
    else:
        virtual = _virtual_path(location, relative)

    if location.mode == "smb":
        entries, truncated = _smb_entries(location, relative)
    else:
        entries, truncated = _native_entries(location, relative)

    entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"].casefold()))
    return {
        "path": virtual,
        "relative_path": relative,
        "root": location.root,
        "is_root": relative == "",
        "parent": _parent_of(location, relative),
        "breadcrumbs": _breadcrumbs(location, relative),
        "entries": entries,
        "truncated": truncated,
        "network": location.public(),
    }


# ------------------------------------------------------------------ file access
def stat_path(raw: str) -> dict[str, Any] | None:
    match = match_location(raw)
    if match is None:
        return None
    location, relative = match
    if location.mode == "smb":
        smb = _smbclient()
        if smb is None:  # pragma: no cover
            return None
        unc = _join_unc(_smb_root(location), relative)
        try:
            stats = smb.stat(unc)
        except Exception:
            return None
        smbpath = _smb_path_module()
        try:
            is_dir = bool(smbpath.isdir(unc)) if smbpath else bool(
                (getattr(stats, "st_file_attributes", 0) or 0) & 0x10
            )
        except Exception:
            is_dir = False
        return {
            "is_dir": is_dir,
            "size_bytes": 0 if is_dir else int(getattr(stats, "st_size", 0) or 0),
            "modified": float(getattr(stats, "st_mtime", 0.0) or 0.0),
        }
    native = _virtual_path(location, relative)
    try:
        stats = os.stat(native)
    except OSError:
        return None
    return {
        "is_dir": os.path.isdir(native),
        "size_bytes": stats.st_size,
        "modified": stats.st_mtime,
    }


def exists(raw: str) -> bool:
    return stat_path(raw) is not None


def is_file(raw: str) -> bool:
    info = stat_path(raw)
    return bool(info and not info["is_dir"])


def is_dir(raw: str) -> bool:
    info = stat_path(raw)
    return bool(info and info["is_dir"])


def open_read(raw: str):
    """Open a binary reader for streaming (supports ``seek`` for range reads)."""

    match = match_location(raw)
    if match is None:
        raise NetworkSourceError(f"Not a registered network location: {raw}")
    location, relative = match
    if location.mode == "smb":
        smb = _smbclient()
        if smb is None:  # pragma: no cover
            raise NetworkSourceError("SMB support is unavailable")
        return smb.open_file(_join_unc(_smb_root(location), relative), mode="rb")
    return open(_virtual_path(location, relative), "rb")


def unique_output_path(remote_dir: str, filename: str, reserved: Iterable[str] = ()) -> str:
    """Collision-safe output name for a network destination folder."""

    match = match_location(remote_dir)
    if match is None:
        raise NetworkSourceError(f"Not a registered network location: {remote_dir}")
    location, relative = match
    taken = {str(value).casefold() for value in reserved}
    stem, suffix = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while True:
        child = f"{relative}/{candidate}" if relative else candidate
        virtual = _virtual_path(location, child)
        if virtual.casefold() not in taken and not exists(virtual):
            return virtual
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1


def staging_dir() -> Path:
    """Local scratch area used only when a share cannot be mounted natively."""

    directory = network_root() / STAGING_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def upload(local_path: Path, target: str) -> int:
    """Move a rendered file into a network destination; returns the byte size."""

    match = match_location(target)
    if match is None:
        raise NetworkSourceError(f"Not a registered network location: {target}")
    location, relative = match

    if location.mode == "smb":
        smb = _smbclient()
        if smb is None:  # pragma: no cover - guarded by connect_share
            raise NetworkSourceError("SMB support is unavailable")
        destination = _join_unc(_smb_root(location), relative)
        parent = ntpath.dirname(destination)
        try:
            if parent:
                smb.makedirs(parent, exist_ok=True)
            with smb.open_file(destination, mode="wb") as sink, open(local_path, "rb") as source:
                shutil.copyfileobj(source, sink, 1024 * 1024)
        except Exception as exc:
            raise NetworkSourceError(f"Cannot write {destination}: {exc}") from exc
        return int(local_path.stat().st_size)

    destination = Path(_virtual_path(location, relative))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_path, destination)
    return destination.stat().st_size


def internal_base_url() -> str:
    return os.environ.get("FASTFRAME_INTERNAL_URL", _DEFAULT_INTERNAL_URL).rstrip("/")


def network_root_path() -> Path:
    """Directory that hosts SMB mount points; created on demand."""

    root = network_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:  # read-only mount - mounting will fail with a clear message
        pass
    return root


def stream_url_for_path(raw: str) -> str:
    """Loopback URL that relays a non-mounted share to ffmpeg/ffprobe."""

    from urllib.parse import quote

    return f"{internal_base_url()}/api/fs/network-stream?path={quote(str(raw))}"


# ------------------------------------------------------------------ remote HTTP streams
def open_remote(url: str, range_header: str | None = None):
    """Open an HTTP(S)/WebDAV stream, forwarding Range and URL credentials."""

    from urllib.request import Request, urlopen

    transport = ffmpeg_network_url(url)
    parsed = urlparse(transport)
    headers = {
        "User-Agent": "Fast-Frame/1.0 (+zero-copy network source)",
        "Accept": "*/*",
    }
    if range_header:
        headers["Range"] = range_header
    if parsed.username:
        token = base64.b64encode(
            f"{unquote(parsed.username)}:{unquote(parsed.password or '')}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    clean = urlunparse(parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]))
    request = Request(clean, headers=headers)
    return urlopen(request, timeout=30), clean


def network_default_output(source_name: str) -> str:
    """Stable default name for URL sources (``stream_video`` -> ``stream_video.mp4``)."""

    stem = PurePosixPath(source_name or "stream").stem or "stream"
    return f"{stem}{VIDEO_EXTENSIONS[0]}"


__all__ = [
    "MAX_NETWORK_ENTRIES",
    "NetworkLocation",
    "NetworkSourceError",
    "connect_share",
    "disconnect",
    "exists",
    "get_location",
    "internal_base_url",
    "is_dir",
    "is_file",
    "list_directory",
    "list_locations",
    "match_location",
    "network_default_output",
    "network_root_path",
    "open_read",
    "open_remote",
    "parse_share_path",
    "register_native_path",
    "smb_available",
    "staging_dir",
    "stat_path",
    "stream_url_for_path",
    "unique_output_path",
    "upload",
]
