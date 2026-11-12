"""AI view selector API: framing state shared with external MCP agents.

Phase 8 exposed a preview-only framing document.  Phase 9 adds a real YOLO
tracker: ``/api/ai/tracking/analyze`` samples a clip, follows the primary
subject and stores pan/tilt/zoom keyframes in the same document, which the
render worker turns into time-varying FFmpeg crop expressions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

import config
from models import (
    AiAutoAlignRequest,
    AiMcpCommand,
    AiTrackingAnalyzeRequest,
    AiTrackingUpdate,
    AiViewStateUpdate,
)
from services.ai_view_service import AiViewError, ai_view_state
from services.ffprobe_service import ProbeError, probe_video
from services.horizon_service import HorizonError, detect_horizon_roll
from services.insp_service import inspect_spherical
from services.yolo_tracking_service import TrackingError, tracking_service

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/view-state")
async def get_view_state() -> dict[str, Any]:
    """Current framing directives (pan, tilt, zoom, tracking, keyframes)."""

    return ai_view_state.snapshot()


@router.post("/view-state")
async def update_view_state(update: AiViewStateUpdate) -> dict[str, Any]:
    """Merge a partial framing update coming from the studio UI or an agent."""

    try:
        # ``exclude_unset`` keeps explicit nulls (e.g. clearing tracking_analysis)
        # while still ignoring fields the client did not mention.
        return ai_view_state.update(update.model_dump(exclude_unset=True))
    except AiViewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/mcp-command")
async def mcp_command(command: AiMcpCommand) -> dict[str, Any]:
    """Apply a Model Context Protocol command envelope to the framing state."""

    payload = command.model_dump(exclude_none=True)
    try:
        return ai_view_state.apply_command(payload)
    except AiViewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/view-state/reset")
async def reset_view_state() -> dict[str, Any]:
    return ai_view_state.reset()


@router.get("/tracking/status")
async def tracking_status() -> dict[str, Any]:
    """Detector availability plus the current auto-framing configuration."""

    state = ai_view_state.snapshot()
    return {
        "detector": tracking_service.status(),
        "enabled": state["ai_tracking_enabled"],
        "level": state["ai_tracking_level"],
        "keyframe_count": len(state["tracking_keyframes"]),
        "analysis": state["tracking_analysis"],
    }


@router.post("/tracking")
async def update_tracking(update: AiTrackingUpdate) -> dict[str, Any]:
    """Toggle AI auto-framing or switch the strength level."""

    payload: dict[str, Any] = {}
    if update.enabled is not None:
        payload["ai_tracking_enabled"] = update.enabled
    if update.level is not None:
        payload["ai_tracking_level"] = update.level
    if not payload:
        return ai_view_state.snapshot()
    try:
        return ai_view_state.update(payload)
    except AiViewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/tracking")
async def clear_tracking() -> dict[str, Any]:
    """Disable auto-framing and drop the analysed keyframes."""

    try:
        return ai_view_state.update(
            {
                "ai_tracking_enabled": False,
                "tracking_keyframes": [],
                "tracking_analysis": None,
                "controller": "ui",
            }
        )
    except AiViewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tracking/analyze")
async def analyze_tracking(payload: AiTrackingAnalyzeRequest) -> dict[str, Any]:
    """Run the YOLO pass over a source and store the resulting keyframes."""

    source = _resolve_source(payload.source_path)
    try:
        metadata = await _probe_source(source)
    except ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state = ai_view_state.snapshot()
    level = payload.level or state["ai_tracking_level"]
    spherical = config.is_spherical_file(Path(source))
    try:
        summary = await run_in_threadpool(
            tracking_service.analyze,
            source,
            duration=float(metadata.get("duration") or 0.0),
            width=int(metadata.get("width") or 0),
            height=int(metadata.get("height") or 0),
            mode="spherical" if spherical else "rectilinear",
            level=level,
            sample_fps=payload.sample_fps,
            max_samples=payload.max_samples,
            force=payload.force,
        )
    except TrackingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    keyframes = summary.pop("keyframes", [])
    try:
        new_state = ai_view_state.update(
            {
                "ai_tracking_enabled": True,
                "ai_tracking_level": summary.get("level", level),
                "tracking_keyframes": keyframes,
                "tracking_analysis": summary,
                "source_path": source,
                "controller": "ui",
            }
        )
    except AiViewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"analysis": summary, "view_state": new_state}


@router.post("/auto-align")
async def auto_align(payload: AiAutoAlignRequest) -> dict[str, Any]:
    """Estimate the horizon roll of a 360° source and optionally apply it."""

    source = _resolve_source(payload.source_path)
    if not payload.force and not config.is_spherical_file(Path(source)):
        raise HTTPException(
            status_code=422,
            detail="Auto-align is only available for 360° spherical sources (.insp/.insv)",
        )
    try:
        metadata = await _probe_source(source)
    except ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        alignment = await run_in_threadpool(
            detect_horizon_roll,
            source,
            duration=float(metadata.get("duration") or 0.0),
            samples=payload.samples,
        )
    except HorizonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not payload.apply:
        return {"alignment": alignment, "view_state": ai_view_state.snapshot()}
    try:
        state = ai_view_state.update(
            {
                "roll": alignment["applied_roll"],
                "horizon_alignment": alignment,
                "source_path": source,
                "controller": "ui",
            }
        )
    except AiViewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"alignment": alignment, "view_state": state}


@router.get("/tracking/frame")
async def tracking_frame(
    path: str = Query(min_length=1),
    time: float = Query(default=0.0, ge=0),
    level: int | None = Query(default=None, ge=1, le=3),
) -> dict[str, Any]:
    """Detect subjects in one frame (powers the preview debug overlay)."""

    source = _resolve_source(path)
    state = ai_view_state.snapshot()
    try:
        return await run_in_threadpool(
            tracking_service.detect_frame,
            source,
            timestamp=time,
            level=level or state["ai_tracking_level"],
        )
    except TrackingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _resolve_source(raw: str) -> str:
    """Accept a mounted host path or a direct stream URL."""

    if config.is_network_url(raw):
        return raw.strip()
    return str(config.resolve_host_path(raw, must_exist=True))


async def _probe_source(source: str) -> dict[str, Any]:
    """Metadata for analysis; spherical containers use the .insp inspector."""

    if config.is_spherical_file(Path(source)):
        inspected = await inspect_spherical(source)
        metadata = inspected.get("metadata") or {}
        if metadata:
            return metadata
    return await probe_video(source)
