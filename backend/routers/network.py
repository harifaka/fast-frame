"""Network share API: mount, list and release SMB/CIFS locations."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from models import NetworkMountRequest
from services import network_service
from services.network_service import NetworkSourceError

router = APIRouter(prefix="/api/fs", tags=["network"])


@router.get("/network-mounts")
async def list_mounts() -> dict:
    """Every share that is currently reachable for source and destination use."""

    return {
        "mounts": network_service.list_locations(),
        "root": str(network_service.network_root_path()),
        "smb_available": network_service.smb_available(),
    }


@router.post("/network-mount", status_code=201)
async def mount_share(payload: NetworkMountRequest) -> dict:
    """Mount/register a share under ``NETWORK_MOUNT_PATH/<share>``.

    Credentials are held in memory only: they are never returned to the browser,
    never logged and never written to disk (``mount.cifs`` reads them from a
    0600 credentials file that is deleted immediately after the mount).
    """

    try:
        location = await asyncio.to_thread(
            network_service.connect_share,
            payload.path,
            username=payload.username or None,
            password=payload.password or None,
            name=payload.name or None,
        )
    except NetworkSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"location": location.public()}


@router.delete("/network-mount/{location_id}")
async def unmount_share(location_id: str) -> dict:
    removed = await asyncio.to_thread(network_service.disconnect, location_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Network location not found")
    return {"removed": location_id}
