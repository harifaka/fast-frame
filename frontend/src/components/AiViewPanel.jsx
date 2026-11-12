import {
  Activity,
  Crosshair,
  Loader2,
  Plus,
  RotateCcw,
  ScanEye,
  Sparkles,
  Target,
  Trash2,
  Wand2,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { clamp } from '../utils'

const TRACKING_MODES = [
  { id: 'off', label: 'Off' },
  { id: 'manual', label: 'Manual' },
  { id: 'subject', label: 'Subject' },
  { id: 'face', label: 'Face' },
  { id: 'object', label: 'Object' },
]

const TRACKING_LEVELS = [
  { level: 1, name: 'Smooth', description: 'Slow, damped follow with wide margins' },
  { level: 2, name: 'Dynamic', description: 'Balanced speed with tighter framing' },
  { level: 3, name: 'Lock-on', description: 'Fast snapping and strict centring' },
]

function toDraft(state) {
  return {
    active: Boolean(state?.active),
    pan: Number(state?.pan ?? 0),
    tilt: Number(state?.tilt ?? 0),
    zoom: Number(state?.zoom ?? 1),
    fov: Number(state?.fov ?? 90),
    tracking_mode: state?.tracking_mode || 'off',
    target: state?.target || '',
  }
}

function Slider({ label, hint, value, min, max, step, suffix, onChange, onCommit, disabled }) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between">
        <span className="field-label !mb-0">{label}</span>
        <span className="font-mono text-[11px] text-accent-300">
          {Number(value).toFixed(step < 1 ? 1 : 0)}
          {suffix}
        </span>
      </span>
      <input
        type="range"
        className="mt-1 h-1.5 w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => onChange(Number(event.target.value))}
        onPointerUp={onCommit}
        onKeyUp={onCommit}
      />
      {hint && <span className="mt-0.5 block text-[10px] text-slate-600">{hint}</span>}
    </label>
  )
}

export default function AiViewPanel({
  state,
  onChange,
  onCommand,
  onReset,
  onAnalyze,
  analyzing = false,
  analyzeError = null,
  canAnalyze = false,
  disabled = false,
  saving = false,
  error = null,
  currentTime = 0,
  endpointHint = '/api/ai/view-state',
}) {
  const [draft, setDraft] = useState(() => toDraft(state))

  useEffect(() => {
    setDraft(toDraft(state))
  }, [state?.revision, state?.updated_at]) // eslint-disable-line react-hooks/exhaustive-deps

  const keyframes = useMemo(() => state?.keyframe_offsets || [], [state?.keyframe_offsets])
  const active = Boolean(state?.active)
  const mcpReady = Boolean(state?.mcp_ready)
  const busy = disabled || saving

  const detector = state?.tracking_detector || null
  const trackingEnabled = Boolean(state?.ai_tracking_enabled)
  const trackingLevel = Number(state?.ai_tracking_level) || 2
  const trackingKeyframes = useMemo(
    () => state?.tracking_keyframes || [],
    [state?.tracking_keyframes],
  )
  const analysis = state?.tracking_analysis || null
  const levels = state?.tracking_levels || [1, 2, 3]
  const levelInfo =
    detector?.levels?.find((item) => item.level === trackingLevel) ||
    TRACKING_LEVELS[trackingLevel - 1]
  const detectorReady = Boolean(detector?.available)

  const commit = (patch) => onChange?.(patch)
  const updateDraft = (key, value) => setDraft((previous) => ({ ...previous, [key]: value }))

  const addKeyframe = () =>
    onCommand?.({
      action: 'add_keyframe',
      keyframe: {
        time: Math.max(0, Number(currentTime) || 0),
        pan: Number(draft.pan),
        tilt: Number(draft.tilt),
        zoom: Number(draft.zoom),
      },
    })

  const simulate = () => {
    const pan = clamp(Number(draft.pan) + (Math.random() * 40 - 20), -180, 180)
    const tilt = clamp(Number(draft.tilt) + (Math.random() * 20 - 10), -90, 90)
    const zoom = clamp(Number((Number(draft.zoom) * (0.85 + Math.random() * 0.4)).toFixed(1)), 0.5, 8)
    setDraft((previous) => ({ ...previous, active: true, pan, tilt, zoom, tracking_mode: 'subject' }))
    commit({ active: true, pan, tilt, zoom, tracking_mode: 'subject' })
  }

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-2 rounded-lg border border-accent-500/25 bg-accent-500/[0.07] px-3 py-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-accent-300">
            <Sparkles className="h-3.5 w-3.5" />
            AI View Selector
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
            YOLO subject tracking drives pan/tilt/zoom for preview and renders; external MCP
            agents steer the same framing document.
          </p>
        </div>
        <span
          className={`chip shrink-0 ${
            mcpReady
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
              : 'border-slate-500/30 bg-slate-500/10 text-slate-400'
          }`}
          title={endpointHint}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${mcpReady ? 'animate-pulse bg-emerald-400' : 'bg-slate-500'}`}
          />
          MCP AI Control Ready
        </span>
      </div>

      <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.02] p-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
            <ScanEye className="h-3.5 w-3.5 text-accent-300" />
            AI YOLO Auto-Framing
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={trackingEnabled}
            aria-label="Toggle AI YOLO auto-framing"
            disabled={busy}
            onClick={() => commit({ ai_tracking_enabled: !trackingEnabled })}
            data-active={trackingEnabled}
            className="relative h-5 w-9 rounded-full border border-white/10 bg-white/10 transition disabled:opacity-40 data-[active=true]:border-accent-500/50 data-[active=true]:bg-accent-500/40"
          >
            <span
              className={`absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all ${
                trackingEnabled ? 'left-[18px] bg-white' : 'left-0.5 bg-slate-300'
              }`}
            />
          </button>
        </div>

        <div className="segmented">
          {levels.map((level) => {
            const info = TRACKING_LEVELS[level - 1]
            return (
              <button
                key={level}
                type="button"
                disabled={busy}
                data-active={trackingLevel === level}
                title={info?.description}
                onClick={() => {
                  commit({ ai_tracking_level: level })
                  if (trackingKeyframes.length > 0) onAnalyze?.(level)
                }}
              >
                {info?.name || `Level ${level}`}
              </button>
            )
          })}
        </div>
        <p className="text-[10px] leading-relaxed text-slate-500">
          {levelInfo?.description || 'Balanced tracking speed and framing.'}
        </p>

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            className="btn-ghost !px-2 !py-1 !text-[11px]"
            disabled={busy || analyzing || !canAnalyze}
            onClick={() => onAnalyze?.(trackingLevel)}
            title="Sample the clip and track the primary subject"
          >
            {analyzing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ScanEye className="h-3.5 w-3.5" />
            )}
            {analyzing ? 'Analysing…' : 'Analyse clip'}
          </button>
          <button
            type="button"
            className="btn-ghost !px-2 !py-1 !text-[11px]"
            disabled={busy || trackingKeyframes.length === 0}
            onClick={() =>
              commit({
                ai_tracking_enabled: false,
                tracking_keyframes: [],
                tracking_analysis: null,
              })
            }
            title="Discard the tracked keyframes"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear track
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-slate-500">
          <span
            className={`chip ${
              detectorReady
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            }`}
          >
            {detector?.loading
              ? 'Loading tracker…'
              : detectorReady
                ? `${detector.backend}${detector.model ? ` · ${detector.model}` : ''}`
                : 'Tracker unavailable'}
          </span>
          <span>{trackingKeyframes.length} keyframes</span>
          {analysis?.frames_analyzed ? (
            <span>
              {analysis.frames_with_subject}/{analysis.frames_analyzed} frames
            </span>
          ) : null}
        </div>

        {!detectorReady && detector?.reason && (
          <p className="rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] leading-relaxed text-amber-200">
            {detector.reason}
          </p>
        )}
        {analyzeError && (
          <p className="rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
            {analyzeError}
          </p>
        )}
      </div>

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => commit({ active: !active })}
          disabled={busy}
          data-active={active}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11px] font-semibold text-slate-300 transition hover:border-accent-500/40 hover:text-accent-300 disabled:opacity-40 data-[active=true]:border-accent-500/40 data-[active=true]:bg-accent-500/15 data-[active=true]:text-accent-300"
        >
          <Activity className="h-3.5 w-3.5" />
          {active ? 'Framing active' : 'Framing idle'}
        </button>
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-600">
          {state?.controller === 'mcp' ? 'MCP' : 'UI'} · rev {state?.revision ?? 0}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Slider
          label="Pan"
          value={draft.pan}
          min={-180}
          max={180}
          step={1}
          suffix="°"
          disabled={busy}
          onChange={(value) => updateDraft('pan', value)}
          onCommit={() => commit({ pan: Number(draft.pan) })}
        />
        <Slider
          label="Tilt"
          value={draft.tilt}
          min={-90}
          max={90}
          step={1}
          suffix="°"
          disabled={busy}
          onChange={(value) => updateDraft('tilt', value)}
          onCommit={() => commit({ tilt: Number(draft.tilt) })}
        />
        <Slider
          label="Zoom"
          value={draft.zoom}
          min={0.5}
          max={8}
          step={0.1}
          suffix="×"
          disabled={busy}
          onChange={(value) => updateDraft('zoom', value)}
          onCommit={() => commit({ zoom: Number(draft.zoom) })}
        />
        <Slider
          label="Field of view"
          value={draft.fov}
          min={20}
          max={140}
          step={1}
          suffix="°"
          disabled={busy}
          onChange={(value) => updateDraft('fov', value)}
          onCommit={() => commit({ fov: Number(draft.fov) })}
        />
      </div>

      <div>
        <span className="field-label">Subject tracking</span>
        <div className="segmented">
          {TRACKING_MODES.map((mode) => (
            <button
              key={mode.id}
              type="button"
              disabled={busy}
              data-active={draft.tracking_mode === mode.id}
              onClick={() => {
                updateDraft('tracking_mode', mode.id)
                commit({ tracking_mode: mode.id })
              }}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      <label className="block">
        <span className="field-label">Tracking target</span>
        <div className="flex gap-1.5">
          <input
            className="field text-xs"
            value={draft.target}
            disabled={busy}
            placeholder="e.g. skier in the red jacket"
            onChange={(event) => updateDraft('target', event.target.value)}
            onBlur={() => commit({ target: draft.target || '' })}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commit({ target: draft.target || '' })
              }
            }}
          />
          <button
            type="button"
            className="btn-ghost shrink-0 !px-2"
            disabled={busy}
            onClick={() => commit({ target: draft.target || '' })}
            title="Apply tracking target"
          >
            <Target className="h-3.5 w-3.5" />
          </button>
        </div>
      </label>

      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 !text-[11px]"
          disabled={busy}
          onClick={addKeyframe}
          title={`Add a keyframe at ${currentTime.toFixed(2)}s`}
        >
          <Plus className="h-3.5 w-3.5" />
          Keyframe @ {Number(currentTime || 0).toFixed(2)}s
        </button>
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 !text-[11px]"
          disabled={busy || keyframes.length === 0}
          onClick={() => onCommand?.({ action: 'clear_keyframes' })}
        >
          <Trash2 className="h-3.5 w-3.5" />
          Clear {keyframes.length ? `(${keyframes.length})` : ''}
        </button>
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 !text-[11px]"
          disabled={busy}
          onClick={simulate}
          title="Simulate an external agent nudging the framing"
        >
          <Wand2 className="h-3.5 w-3.5" />
          Simulate AI
        </button>
        <button
          type="button"
          className="btn-ghost ml-auto !px-2 !py-1 !text-[11px]"
          disabled={busy}
          onClick={onReset}
          title="Reset all framing directives"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset
        </button>
      </div>

      {keyframes.length > 0 && (
        <ul className="max-h-24 space-y-1 overflow-y-auto pr-1">
          {keyframes.map((frame, index) => (
            <li
              key={`${frame.time}-${index}`}
              className="flex items-center gap-2 rounded-md border border-white/5 bg-white/[0.03] px-2 py-1 font-mono text-[10px] text-slate-400"
            >
              <Crosshair className="h-3 w-3 text-accent-400" />
              t={Number(frame.time).toFixed(2)}s · pan {Number(frame.pan).toFixed(0)}° · tilt{' '}
              {Number(frame.tilt).toFixed(0)}° · {Number(frame.zoom).toFixed(2)}×
            </li>
          ))}
        </ul>
      )}

      <p className="text-[10px] leading-relaxed text-slate-600">
        <span className="font-mono text-slate-500">GET</span> {endpointHint} ·{' '}
        <span className="font-mono text-slate-500">POST</span> {endpointHint} ·{' '}
        <span className="font-mono text-slate-500">POST</span> /api/ai/mcp-command
      </p>

      {error && (
        <p className="rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {error}
        </p>
      )}
    </div>
  )
}
