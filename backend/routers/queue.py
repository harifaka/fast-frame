"""Render queue API: enqueue, inspect and cancel jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config import PathSecurityError
from models import QueueAddRequest
from services.ffprobe_service import ProbeError
from services.worker import JobNotFoundError, JobValidationError, job_manager

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.post("/add", status_code=201)
async def add_job(payload: QueueAddRequest) -> dict:
    """Queue a render. ``render_now`` jumps the queue but never pre-empts a render."""

    try:
        return await job_manager.add_job(payload, render_now=payload.render_now)
    except PathSecurityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except JobValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
async def list_jobs() -> dict:
    return job_manager.snapshot()


@router.post("/clear-completed")
async def clear_completed() -> dict:
    removed = await job_manager.clear_completed()
    return {"removed": removed}


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    try:
        return job_manager.get_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.delete("/{job_id}")
async def cancel_job(job_id: str) -> dict:
    try:
        return await job_manager.cancel(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except JobValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
