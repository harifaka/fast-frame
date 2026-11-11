"""AI view selector API: framing state shared with external MCP agents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from models import AiMcpCommand, AiViewStateUpdate
from services.ai_view_service import AiViewError, ai_view_state

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/view-state")
async def get_view_state() -> dict[str, Any]:
    """Current framing directives (pan, tilt, zoom, tracking, keyframes)."""

    return ai_view_state.snapshot()


@router.post("/view-state")
async def update_view_state(update: AiViewStateUpdate) -> dict[str, Any]:
    """Merge a partial framing update coming from the studio UI or an agent."""

    try:
        return ai_view_state.update(update.model_dump(exclude_none=True))
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
