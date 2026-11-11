"""Pydantic request/response models for render jobs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0, description="Keep-segment start in seconds")
    end: float = Field(gt=0, description="Keep-segment end in seconds")

    @model_validator(mode="after")
    def _check_order(self) -> "Segment":
        if self.end <= self.start:
            raise ValueError("Segment end must be greater than start")
        return self

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 6)


class CropRegion(BaseModel):
    """Normalised crop rectangle (0..1) relative to the displayed frame."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _check_bounds(self) -> "CropRegion":
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("Crop region extends beyond the video frame")
        return self


class RenderOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    codec: Literal["h264", "hevc"] = "h264"
    segments: list[Segment] = Field(default_factory=list)
    subtitle_path: str | None = None
    subtitle_font_size: int = Field(default=24, ge=8, le=96)
    rotation: Literal["none", "90_cw", "90_ccw", "180"] = "none"
    flip_horizontal: bool = False
    crop: CropRegion | None = None
    prefer_gpu: bool = True


class QueueAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_path: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    output_filename: str | None = None
    render_now: bool = False
    options: RenderOptionsPayload = Field(default_factory=RenderOptionsPayload)


class NetworkMountRequest(BaseModel):
    """Connect an SMB/CIFS share or a native network path."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        description="//server/share, \\\\server\\share, smb://server/share or Z:\\folder",
    )
    username: str | None = None
    password: str | None = None
    name: str | None = Field(default=None, description="Display name for the mount")


TrackingMode = Literal["off", "manual", "subject", "face", "object"]


class AiKeyframe(BaseModel):
    """A framing offset applied at a timestamp of the spherical source."""

    model_config = ConfigDict(extra="forbid")

    time: float = Field(ge=0, description="Offset in seconds from the clip start")
    pan: float = Field(default=0.0, ge=-180, le=180)
    tilt: float = Field(default=0.0, ge=-90, le=90)
    zoom: float = Field(default=1.0, ge=0.5, le=8.0)


class AiViewStateUpdate(BaseModel):
    """Partial framing update; omitted fields keep their current value."""

    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    pan: float | None = Field(default=None, ge=-180, le=180)
    tilt: float | None = Field(default=None, ge=-90, le=90)
    roll: float | None = Field(default=None, ge=-45, le=45)
    zoom: float | None = Field(default=None, ge=0.5, le=8.0)
    fov: float | None = Field(default=None, ge=20, le=140)
    tracking_mode: TrackingMode | None = None
    target: str | None = Field(default=None, max_length=120)
    keyframe_offsets: list[AiKeyframe] | None = None
    source_path: str | None = None
    controller: Literal["ui", "mcp"] | None = None


class AiMcpCommand(BaseModel):
    """Envelope an external MCP agent uses to steer the mock view selector."""

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "set_view",
        "set_tracking",
        "add_keyframe",
        "clear_keyframes",
        "activate",
        "deactivate",
        "reset",
    ]
    view: AiViewStateUpdate | None = None
    keyframe: AiKeyframe | None = None
    tracking_mode: TrackingMode | None = None
    target: str | None = Field(default=None, max_length=120)
    source_path: str | None = None
