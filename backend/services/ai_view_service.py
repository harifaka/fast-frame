"""AI framing directives shared by the studio UI and external MCP agents.

The service owns a single framing document - pan, tilt, zoom, field of view,
tracking mode, keyframe offsets and the YOLO auto-framing configuration - and
exposes it through ``/api/ai/view-state`` (GET/POST) plus
``/api/ai/mcp-command``.  It is the authoritative state for the UI preview and
for anything an MCP agent decides to send; the vision pass itself lives in
:mod:`services.yolo_tracking_service` and writes its keyframes here.

State is kept in memory and, when ``AI_VIEW_STATE_FILE`` is configured, mirrored
to a JSON file so a restarted API keeps the last framing an agent requested.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.yolo_tracking_service import (
    DEFAULT_LEVEL,
    LEVEL_PROFILES,
    MAX_KEYFRAMES as MAX_TRACKING_KEYFRAMES,
    tracking_service,
)

logger = logging.getLogger(__name__)

TRACKING_MODES: tuple[str, ...] = ("off", "manual", "subject", "face", "object")
CONTROLLERS: tuple[str, ...] = ("ui", "mcp")
TRACKING_LEVELS: tuple[int, ...] = tuple(sorted(LEVEL_PROFILES))

PAN_LIMITS = (-180.0, 180.0)
TILT_LIMITS = (-90.0, 90.0)
ROLL_LIMITS = (-45.0, 45.0)
ZOOM_LIMITS = (0.5, 8.0)
FOV_LIMITS = (20.0, 140.0)
MAX_KEYFRAMES = 500

#: Fields where ``None`` is a meaningful value ("clear this"), so a partial
#: update must not silently drop it the way other ``None`` values are dropped.
_NULLABLE_FIELDS = frozenset({"tracking_analysis", "horizon_alignment"})

MCP_ACTIONS: tuple[str, ...] = (
    "set_view",
    "set_tracking",
    "add_keyframe",
    "clear_keyframes",
    "activate",
    "deactivate",
    "reset",
)


class AiViewError(ValueError):
    """Raised for an invalid framing directive."""


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def _clamp(value: float, limits: tuple[float, float]) -> float:
    low, high = limits
    return round(min(high, max(low, float(value))), 3)


def _coerce_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AiViewError(f"{name} must be a number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise AiViewError(f"{name} must be a finite number")
    return number


def default_state() -> dict[str, Any]:
    return {
        "active": False,
        "pan": 0.0,
        "tilt": 0.0,
        "roll": 0.0,
        "zoom": 1.0,
        "fov": 90.0,
        "tracking_mode": "off",
        "target": None,
        "keyframe_offsets": [],
        "source_path": None,
        "controller": "ui",
        "ai_tracking_enabled": False,
        "ai_tracking_level": DEFAULT_LEVEL,
        "tracking_keyframes": [],
        "tracking_analysis": None,
        "horizon_alignment": None,
        "revision": 0,
        "updated_at": None,
    }


def _normalise_keyframe(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AiViewError("keyframe entries must be objects")
    time_value = _coerce_float(raw.get("time", 0.0), "keyframe.time")
    if time_value < 0:
        raise AiViewError("keyframe.time must be >= 0")
    return {
        "time": round(time_value, 3),
        "pan": _clamp(_coerce_float(raw.get("pan", 0.0), "keyframe.pan"), PAN_LIMITS),
        "tilt": _clamp(_coerce_float(raw.get("tilt", 0.0), "keyframe.tilt"), TILT_LIMITS),
        "zoom": _clamp(_coerce_float(raw.get("zoom", 1.0), "keyframe.zoom"), ZOOM_LIMITS),
    }


def _normalise_tracking_keyframe(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one YOLO framing keyframe (pan/tilt degrees + zoom factor)."""

    if not isinstance(raw, Mapping):
        raise AiViewError("tracking keyframe entries must be objects")
    time_value = _coerce_float(raw.get("time", 0.0), "tracking_keyframe.time")
    if time_value < 0:
        raise AiViewError("tracking_keyframe.time must be >= 0")
    return {
        "time": round(time_value, 3),
        "pan": _clamp(
            _coerce_float(raw.get("pan", 0.0), "tracking_keyframe.pan"), PAN_LIMITS
        ),
        "tilt": _clamp(
            _coerce_float(raw.get("tilt", 0.0), "tracking_keyframe.tilt"), TILT_LIMITS
        ),
        # The rendered window never gets larger than the source frame.
        "zoom": _clamp(
            _coerce_float(raw.get("zoom", 1.0), "tracking_keyframe.zoom"), (1.0, 8.0)
        ),
    }


def _normalise_level(value: Any) -> int:
    try:
        level = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise AiViewError("ai_tracking_level must be 1, 2 or 3") from exc
    if level not in TRACKING_LEVELS:
        raise AiViewError("ai_tracking_level must be 1, 2 or 3")
    return level


def apply_patch(state: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new state with ``patch`` validated, clamped and applied."""

    next_state = dict(state)

    if "active" in patch and patch["active"] is not None:
        next_state["active"] = bool(patch["active"])
    if "pan" in patch and patch["pan"] is not None:
        next_state["pan"] = _clamp(_coerce_float(patch["pan"], "pan"), PAN_LIMITS)
    if "tilt" in patch and patch["tilt"] is not None:
        next_state["tilt"] = _clamp(_coerce_float(patch["tilt"], "tilt"), TILT_LIMITS)
    if "roll" in patch and patch["roll"] is not None:
        next_state["roll"] = _clamp(_coerce_float(patch["roll"], "roll"), ROLL_LIMITS)
    if "zoom" in patch and patch["zoom"] is not None:
        next_state["zoom"] = _clamp(_coerce_float(patch["zoom"], "zoom"), ZOOM_LIMITS)
    if "fov" in patch and patch["fov"] is not None:
        next_state["fov"] = _clamp(_coerce_float(patch["fov"], "fov"), FOV_LIMITS)

    if "tracking_mode" in patch and patch["tracking_mode"] is not None:
        mode = str(patch["tracking_mode"]).strip().lower()
        if mode not in TRACKING_MODES:
            raise AiViewError(
                f"tracking_mode must be one of: {', '.join(TRACKING_MODES)}"
            )
        next_state["tracking_mode"] = mode
        if mode != "off" and not next_state["active"]:
            next_state["active"] = True

    if "target" in patch:
        target = patch["target"]
        next_state["target"] = str(target).strip()[:120] if target else None

    if "source_path" in patch:
        source = patch["source_path"]
        next_state["source_path"] = str(source) if source else None

    if "controller" in patch and patch["controller"] is not None:
        controller = str(patch["controller"]).strip().lower()
        if controller not in CONTROLLERS:
            raise AiViewError(f"controller must be one of: {', '.join(CONTROLLERS)}")
        next_state["controller"] = controller

    if "ai_tracking_level" in patch and patch["ai_tracking_level"] is not None:
        next_state["ai_tracking_level"] = _normalise_level(patch["ai_tracking_level"])

    if "ai_tracking_enabled" in patch and patch["ai_tracking_enabled"] is not None:
        enabled = bool(patch["ai_tracking_enabled"])
        next_state["ai_tracking_enabled"] = enabled
        if enabled:
            next_state["active"] = True
            if next_state["tracking_mode"] == "off":
                next_state["tracking_mode"] = "subject"

    if "tracking_keyframes" in patch and patch["tracking_keyframes"] is not None:
        frames = patch["tracking_keyframes"]
        if not isinstance(frames, (list, tuple)):
            raise AiViewError("tracking_keyframes must be a list")
        if len(frames) > MAX_TRACKING_KEYFRAMES:
            raise AiViewError(
                f"at most {MAX_TRACKING_KEYFRAMES} tracking keyframes are supported"
            )
        next_state["tracking_keyframes"] = sorted(
            (_normalise_tracking_keyframe(frame) for frame in frames),
            key=lambda frame: frame["time"],
        )

    if "tracking_analysis" in patch:
        analysis = patch["tracking_analysis"]
        if analysis is not None and not isinstance(analysis, Mapping):
            raise AiViewError("tracking_analysis must be an object or null")
        next_state["tracking_analysis"] = (
            json.loads(json.dumps(dict(analysis))) if analysis is not None else None
        )

    if "horizon_alignment" in patch:
        alignment = patch["horizon_alignment"]
        if alignment is not None and not isinstance(alignment, Mapping):
            raise AiViewError("horizon_alignment must be an object or null")
        next_state["horizon_alignment"] = (
            json.loads(json.dumps(dict(alignment))) if alignment is not None else None
        )

    if "keyframe_offsets" in patch and patch["keyframe_offsets"] is not None:
        frames = patch["keyframe_offsets"]
        if not isinstance(frames, (list, tuple)):
            raise AiViewError("keyframe_offsets must be a list")
        if len(frames) > MAX_KEYFRAMES:
            raise AiViewError(f"at most {MAX_KEYFRAMES} keyframes are supported")
        next_state["keyframe_offsets"] = sorted(
            (_normalise_keyframe(frame) for frame in frames), key=lambda frame: frame["time"]
        )

    return next_state


class AiViewStateManager:
    """Thread-safe framing state, optionally mirrored to a JSON file."""

    def __init__(self, persist_path: str | os.PathLike[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._persist_path = Path(persist_path) if persist_path else None
        self._state = default_state()
        self._load()

    @property
    def persist_path(self) -> Path | None:
        return self._persist_path

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = json.loads(json.dumps(self._state))
        state["mcp_ready"] = True
        state["tracking_modes"] = list(TRACKING_MODES)
        state["tracking_levels"] = list(TRACKING_LEVELS)
        state["tracking_detector"] = tracking_service.status()
        state["endpoints"] = {
            "view_state": "/api/ai/view-state",
            "mcp_command": "/api/ai/mcp-command",
            "tracking": "/api/ai/tracking",
            "tracking_analyze": "/api/ai/tracking/analyze",
            "tracking_frame": "/api/ai/tracking/frame",
            "auto_align": "/api/ai/auto-align",
        }
        return state

    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        candidate = {
            key: value
            for key, value in (patch or {}).items()
            if value is not None or key in _NULLABLE_FIELDS
        }
        if not candidate:
            return self.snapshot()
        with self._lock:
            state = apply_patch(self._state, candidate)
            state["revision"] = int(self._state.get("revision", 0)) + 1
            state["updated_at"] = _utc_now()
            self._state = state
            self._save_locked()
        return self.snapshot()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            revision = int(self._state.get("revision", 0)) + 1
            self._state = default_state()
            self._state["revision"] = revision
            self._state["updated_at"] = _utc_now()
            self._save_locked()
        return self.snapshot()

    def apply_command(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Handle an MCP-shaped command envelope and return the new state."""

        if not isinstance(command, Mapping):
            raise AiViewError("command must be an object")
        action = str(command.get("action") or "").strip().lower()
        if action not in MCP_ACTIONS:
            raise AiViewError(
                f"action must be one of: {', '.join(MCP_ACTIONS)}"
            )

        if action == "reset":
            return self.reset()
        if action == "activate":
            return self.update({"active": True, "controller": "mcp"})
        if action == "deactivate":
            return self.update({"active": False, "controller": "mcp"})
        if action == "clear_keyframes":
            return self.update({"keyframe_offsets": [], "controller": "mcp"})
        if action == "add_keyframe":
            keyframe = command.get("keyframe")
            if not isinstance(keyframe, Mapping):
                raise AiViewError("add_keyframe requires a 'keyframe' object")
            normalised = _normalise_keyframe(keyframe)
            current = self.snapshot()["keyframe_offsets"]
            return self.update(
                {
                    "keyframe_offsets": [*current, normalised],
                    "controller": "mcp",
                }
            )
        if action == "set_tracking":
            mode = command.get("tracking_mode")
            if mode is None:
                raise AiViewError("set_tracking requires 'tracking_mode'")
            return self.update(
                {
                    "tracking_mode": mode,
                    "target": command.get("target"),
                    "controller": "mcp",
                }
            )

        view = command.get("view")
        if not isinstance(view, Mapping):
            raise AiViewError("set_view requires a 'view' object")
        payload = dict(view)
        payload["controller"] = "mcp"
        if command.get("source_path") is not None:
            payload["source_path"] = command["source_path"]
        return self.update(payload)

    def _load(self) -> None:
        if not self._persist_path:
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable AI view state %s: %s", self._persist_path, exc)
            return
        if not isinstance(raw, dict):
            return
        try:
            state = default_state()
            state.update({key: value for key, value in raw.items() if key in state})
            self._state = apply_patch(state, state)
        except (AiViewError, TypeError, ValueError) as exc:
            logger.warning("Ignoring invalid AI view state %s: %s", self._persist_path, exc)

    def _save_locked(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            tmp.replace(self._persist_path)
        except OSError as exc:  # read-only volume: keep working in memory
            logger.warning("Could not persist AI view state: %s", exc)


def _persist_path_from_env() -> Path | None:
    raw = os.environ.get("AI_VIEW_STATE_FILE", "").strip()
    return Path(raw) if raw else None


#: Process-wide framing state used by the REST layer and the mock UI.
ai_view_state = AiViewStateManager(_persist_path_from_env())
