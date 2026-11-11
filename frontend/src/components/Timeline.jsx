import { useMemo, useRef, useState } from 'react'
import {
  Clock,
  Crop,
  Play,
  Plus,
  Scissors,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import { clamp, formatTimecode } from '../utils'

function precision(seconds) {
  return Number.isInteger(seconds) ? formatTimecode(seconds, false) : formatTimecode(seconds, true)
}

export default function Timeline({
  duration = 0,
  currentTime = 0,
  segments = [],
  speed = 1,
  disabled = false,
  onSeek,
  onAddSegment,
  onRemoveSegment,
  onClearSegments,
}) {
  const trackRef = useRef(null)
  const [inPoint, setInPoint] = useState(null)
  const [outPoint, setOutPoint] = useState(null)
  const [scrubbing, setScrubbing] = useState(false)
  const [error, setError] = useState(null)

  const safeDuration = Number(duration) || 0
  const totalKept = useMemo(
    () => segments.reduce((sum, segment) => sum + (segment.end - segment.start), 0),
    [segments],
  )
  const outputLength = totalKept > 0 ? totalKept / (Number(speed) || 1) : safeDuration / (Number(speed) || 1)

  const seekFromEvent = (event) => {
    if (!trackRef.current || safeDuration <= 0) return
    const rect = trackRef.current.getBoundingClientRect()
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1)
    onSeek?.(ratio * safeDuration)
  }

  const handleSetIn = () => {
    setError(null)
    setInPoint(currentTime)
    if (outPoint !== null && outPoint <= currentTime) setOutPoint(null)
  }

  const handleSetOut = () => {
    setError(null)
    setOutPoint(currentTime)
    if (inPoint !== null && inPoint >= currentTime) setInPoint(null)
  }

  const handleAdd = () => {
    if (inPoint === null || outPoint === null) {
      setError('Set both an in point and an out point first.')
      return
    }
    const start = Math.min(inPoint, outPoint)
    const end = Math.max(inPoint, outPoint)
    if (end - start < 0.1) {
      setError('Keep segments must be at least 0.1s long.')
      return
    }
    onAddSegment?.({ start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) })
    setInPoint(null)
    setOutPoint(null)
    setError(null)
  }

  const handleClear = () => {
    onClearSegments?.()
    setInPoint(null)
    setOutPoint(null)
    setError(null)
  }

  const pct = (value) => (safeDuration > 0 ? clamp((value / safeDuration) * 100, 0, 100) : 0)

  return (
    <div className="shrink-0 border-t border-white/5 bg-studio-950/40 px-3 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
          <Scissors className="h-3.5 w-3.5 text-accent-400" />
          Multi-cut timeline
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={handleSetIn}
            disabled={disabled || !safeDuration}
            className="rounded-md border border-amber-400/25 bg-amber-400/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-300 transition hover:bg-amber-400/20 disabled:opacity-30"
            title="Mark the current position as the segment start"
          >
            Set In
          </button>
          <button
            type="button"
            onClick={handleSetOut}
            disabled={disabled || !safeDuration}
            className="rounded-md border border-emerald-400/25 bg-emerald-400/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-300 transition hover:bg-emerald-400/20 disabled:opacity-30"
            title="Mark the current position as the segment end"
          >
            Set Out
          </button>
          <button
            type="button"
            onClick={handleAdd}
            disabled={disabled || !safeDuration}
            className="btn-primary !px-2 !py-1 !text-[10px]"
            title="Add the in/out range as a keep segment"
          >
            <Plus className="h-3 w-3" />
            Add Keep Segment
          </button>
          {segments.length > 0 && (
            <button
              type="button"
              onClick={handleClear}
              className="rounded-md border border-white/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400 transition hover:border-red-500/30 hover:text-red-300"
              title="Keep the whole video again"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Timeline"
        aria-valuemin={0}
        aria-valuemax={safeDuration}
        aria-valuenow={currentTime}
        onPointerDown={(event) => {
          if (disabled || !safeDuration) return
          event.currentTarget.setPointerCapture?.(event.pointerId)
          setScrubbing(true)
          seekFromEvent(event)
        }}
        onPointerMove={(event) => {
          if (scrubbing) seekFromEvent(event)
        }}
        onPointerUp={() => setScrubbing(false)}
        onPointerCancel={() => setScrubbing(false)}
        className={`relative h-12 w-full touch-none select-none overflow-hidden rounded-lg border border-white/5 bg-studio-950 ${
          disabled ? 'opacity-40' : 'cursor-crosshair'
        }`}
      >
        <div className="timeline-grid absolute inset-0 opacity-40" />

        {segments.map((segment, index) => (
          <div
            key={`${segment.start}-${segment.end}-${index}`}
            className="absolute inset-y-0 border-x border-emerald-400/50 bg-emerald-400/20"
            style={{
              left: `${pct(segment.start)}%`,
              width: `${Math.max(0.4, pct(segment.end) - pct(segment.start))}%`,
            }}
            title={`Kept: ${precision(segment.start)} → ${precision(segment.end)}`}
          >
            <span className="absolute left-1 top-1 rounded bg-emerald-950/70 px-1 text-[9px] font-semibold text-emerald-300">
              {index + 1}
            </span>
          </div>
        ))}

        {inPoint !== null && (
          <div
            className="absolute inset-y-0 w-px bg-amber-400"
            style={{ left: `${pct(inPoint)}%` }}
          >
            <span className="absolute -top-0 left-1 text-[9px] font-bold text-amber-300">IN</span>
          </div>
        )}
        {outPoint !== null && (
          <div
            className="absolute inset-y-0 w-px bg-emerald-400"
            style={{ left: `${pct(outPoint)}%` }}
          >
            <span className="absolute -top-0 left-1 text-[9px] font-bold text-emerald-300">OUT</span>
          </div>
        )}

        <div
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-white shadow-[0_0_8px_rgba(255,255,255,0.6)]"
          style={{ left: `${pct(currentTime)}%` }}
        >
          <span className="absolute -left-1 top-0 h-2 w-2.5 -translate-y-0.5 rounded-sm bg-white" />
        </div>

        <div className="pointer-events-none absolute inset-x-2 bottom-1 flex justify-between font-mono text-[9px] text-slate-600">
          <span>0:00</span>
          <span>{formatTimecode(safeDuration, false)}</span>
        </div>
      </div>

      {error && (
        <p className="mt-1.5 flex items-center gap-1 text-[10px] text-amber-300">
          <TriangleAlert className="h-3 w-3" />
          {error}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {segments.length === 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-white/10 px-2 py-1 text-[10px] text-slate-500">
            <Crop className="h-3 w-3" />
            Whole video is kept — add segments to cut out unwanted parts.
          </span>
        ) : (
          segments.map((segment, index) => (
            <div
              key={`${segment.start}-${segment.end}-${index}`}
              className="group flex items-center gap-1 rounded-md border border-emerald-400/20 bg-emerald-400/5 py-0.5 pl-2 pr-1 text-[10px]"
            >
              <button
                type="button"
                onClick={() => onSeek?.(segment.start)}
                className="flex items-center gap-1 text-emerald-200 transition hover:text-white"
                title="Seek to this segment"
              >
                <Play className="h-2.5 w-2.5" />
                <span className="font-semibold">Segment {index + 1}</span>
                <span className="font-mono text-emerald-300/80">
                  {precision(segment.start)} – {precision(segment.end)}
                </span>
                <span className="text-slate-500">({(segment.end - segment.start).toFixed(1)}s)</span>
              </button>
              <button
                type="button"
                onClick={() => onRemoveSegment?.(index)}
                className="rounded p-0.5 text-slate-500 transition hover:bg-red-500/10 hover:text-red-300"
                title="Remove this segment"
                aria-label={`Remove segment ${index + 1}`}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))
        )}

        <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-slate-500">
          <Clock className="h-3 w-3" />
          Output {formatTimecode(outputLength, false)}
          {segments.length > 0 ? ` from ${segments.length} segment${segments.length > 1 ? 's' : ''}` : ''}
        </span>
      </div>
    </div>
  )
}
