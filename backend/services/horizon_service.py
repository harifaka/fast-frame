"""Automatic horizon levelling for 360° / equirectangular sources.

An equirectangular frame keeps the horizon horizontal only when the camera was
level at capture time.  Handheld 360 footage is almost never perfectly level,
so the preview and the render look tilted.  This service samples a few frames,
estimates the horizon roll from the image gradient field and returns the
rotation that levels it.

The estimator shears the frame by every candidate angle and measures how well
the horizon collapses into a single row: a level horizon concentrates all the
vertical gradient energy in one row, while a tilted one smears it over many
rows.  The angle with the sharpest row peak is the horizon tilt, so the whole
pass costs a few milliseconds per frame and needs only numpy.
"""

from __future__ import annotations

import logging
from typing import Any

from services.yolo_tracking_service import TrackingError, extract_frame

logger = logging.getLogger(__name__)

#: Angles outside this range are almost never camera roll (and would need the
#: whole equirect sphere rotated rather than the flat frame).
DEFAULT_MAX_ANGLE = 20.0
DEFAULT_STEP = 0.5
#: Vertical band that is searched for the horizon (poles contain no horizon).
BAND_TOP = 0.18
BAND_BOTTOM = 0.82
#: Below this confidence the estimate is treated as noise and not applied.
MIN_CONFIDENCE = 0.12
DEFAULT_SAMPLES = 3


class HorizonError(ValueError):
    """Raised when horizon levelling cannot be computed."""


def _to_gray(pixels: bytes, width: int, height: int) -> Any:
    import numpy as np

    frame = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 3)
    return frame.astype(np.float32).mean(axis=2)


def _shear_peak_ratio(gray: Any, angle: float) -> float:
    """Row-gradient concentration after shearing the frame by ``angle`` degrees.

    Shearing (instead of rotating) keeps the rows pixel-aligned: a horizon that
    tilts by ``theta`` collapses into a single row when sheared by ``-theta``.
    """

    import numpy as np

    height, width = gray.shape
    centre = (width - 1) / 2.0
    shift = np.tan(np.deg2rad(angle)) * (np.arange(width) - centre)
    rows = np.arange(height, dtype=np.float32)[:, None]
    source = np.clip(rows - shift[None, :], 0.0, height - 1.001)
    base = np.clip(np.floor(source).astype(np.int32), 0, height - 2)
    fraction = source - base
    columns = np.arange(width)[None, :]
    sampled = (1.0 - fraction) * gray[base, columns] + fraction * gray[base + 1, columns]

    row_energy = np.abs(np.diff(sampled, axis=0)).sum(axis=1)
    peak = float(row_energy.max())
    if peak <= 0.0:
        return 0.0
    median = float(np.median(row_energy))
    return peak / max(median, 1e-6)


def estimate_roll(gray: Any, *, max_angle: float = DEFAULT_MAX_ANGLE) -> tuple[float, float]:
    """Return ``(roll, confidence)`` for one grayscale equirect frame.

    ``roll`` is the correction to apply to the *image*: a horizon that slopes
    down to the right yields a negative roll (rotate counter-clockwise), which
    matches the CSS/FFmpeg ``rotate`` convention used by the preview and render.
    """

    import numpy as np

    gray = np.asarray(gray, dtype=np.float32)
    height, width = gray.shape
    if height < 8 or width < 16:
        return 0.0, 0.0

    top = int(height * BAND_TOP)
    bottom = max(top + 4, int(height * BAND_BOTTOM))
    # Decimating by two is plenty for a horizon line and keeps the 81-angle
    # scan well under a tenth of a second.
    band = gray[top:bottom:2, ::2]
    if band.size == 0 or float(band.std()) < 1e-3:
        return 0.0, 0.0

    # A tiny box blur removes the staircase aliasing of slanted edges, which
    # would otherwise bias the vote towards 45° corners.
    padded = np.pad(band, 1, mode="edge")
    blurred = (
        padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:]
        + padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:]
        + padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]
    ) / 9.0

    angles = np.arange(-max_angle, max_angle + DEFAULT_STEP / 2.0, DEFAULT_STEP)
    best_angle = 0.0
    best_ratio = 0.0
    for angle in angles:
        ratio = _shear_peak_ratio(blurred, float(angle))
        if ratio > best_ratio:
            best_ratio = ratio
            best_angle = float(angle)

    if best_ratio < 1.4:
        return 0.0, 0.0
    confidence = float(np.clip(np.log(best_ratio / 1.4) / np.log(30.0), 0.0, 1.0))
    if confidence < MIN_CONFIDENCE:
        # Texture-only frames produce a much flatter row profile; reporting an
        # angle there would make the preview jitter for no reason.
        return 0.0, round(confidence, 3)
    return round(best_angle, 2), round(confidence, 3)


def sample_times(duration: float, samples: int) -> list[float]:
    """Evenly spaced probe timestamps inside the clip (never on the very end)."""

    count = max(1, min(int(samples) or DEFAULT_SAMPLES, 12))
    if duration <= 0:
        return [0.0] * count
    usable = max(0.0, duration - 0.1)
    return [round(usable * (index + 1) / (count + 1), 3) for index in range(count)]


def detect_horizon_roll(
    source: str,
    *,
    duration: float = 0.0,
    samples: int = DEFAULT_SAMPLES,
    max_angle: float = DEFAULT_MAX_ANGLE,
) -> dict[str, Any]:
    """Estimate the level correction for a spherical source.

    Several frames are sampled and their estimates combined with a
    confidence-weighted median so a single ambiguous frame (fast motion, sky
    only) cannot dominate the result.
    """

    if not source:
        raise HorizonError("A source path is required")

    probes: list[dict[str, float]] = []
    errors: list[str] = []
    for timestamp in sample_times(duration, samples):
        try:
            pixels, width, height = extract_frame(source, timestamp)
            roll, confidence = estimate_roll(_to_gray(pixels, width, height))
        except TrackingError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:  # pragma: no cover - decode/runtime guard
            errors.append(f"{exc.__class__.__name__}: {exc}")
            continue
        probes.append({"time": timestamp, "roll": roll, "confidence": confidence})

    if not probes:
        detail = "; ".join(dict.fromkeys(errors)) or "no frame could be decoded"
        raise HorizonError(f"Could not analyse the source: {detail[:300]}")

    ordered = sorted(probes, key=lambda item: item["roll"])
    median_roll = float(ordered[len(ordered) // 2]["roll"])
    # Confidence-weighted median of the per-frame estimates.
    weighted = ordered
    total_weight = sum(item["confidence"] for item in weighted)
    if total_weight > 0:
        target = total_weight / 2.0
        running = 0.0
        for item in weighted:
            running += item["confidence"]
            if running >= target:
                median_roll = item["roll"]
                break
    confidence = round(
        sum(item["confidence"] for item in probes) / len(probes), 3
    )
    reliable = confidence >= MIN_CONFIDENCE and abs(median_roll) >= 0.1

    return {
        "roll": round(median_roll, 2),
        "confidence": confidence,
        "reliable": reliable,
        "applied_roll": round(median_roll, 2) if reliable else 0.0,
        "samples": probes,
        "source": source,
    }
