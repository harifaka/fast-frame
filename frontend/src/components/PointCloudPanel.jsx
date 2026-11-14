import { Box, Download, Loader2, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { formatBytes } from '../utils'

const SAMPLE_RATES = [
  { value: 0.25, label: '0.25 fps', hint: 'Fastest draft, very sparse' },
  { value: 0.5, label: '0.5 fps', hint: 'Light cloud for long clips' },
  { value: 1, label: '1 fps', hint: 'Balanced detail and speed' },
  { value: 2, label: '2 fps', hint: 'Denser cloud' },
  { value: 5, label: '5 fps', hint: 'Maximum detail, slowest' },
]

const PHASE_LABELS = {
  starting: 'Preparing…',
  'loading-model': 'Loading depth model…',
  sampling: 'Sampling frames & estimating depth…',
  finalizing: 'Voxel-filtering the cloud…',
  writing: 'Writing .ply…',
  done: 'Export complete',
  failed: 'Export failed',
  idle: 'Idle',
}

export default function PointCloudPanel({
  status = null,
  error = null,
  defaultOutputPath = '',
  duration = 0,
  disabled = false,
  onGenerate,
  onRefresh,
  onView3D,
}) {
  const [sampleRate, setSampleRate] = useState(1)
  const [outputPath, setOutputPath] = useState(defaultOutputPath || '')

  useEffect(() => {
    setOutputPath(defaultOutputPath || '')
  }, [defaultOutputPath])

  const running = Boolean(status?.running)
  const progress = Math.min(1, Math.max(0, Number(status?.progress) || 0))
  const percent = Math.round(progress * 100)
  const model = status?.model || null
  const modelReady = Boolean(model?.available)
  const modelLoading = Boolean(model?.loading)
  const totalSeconds = Number(duration) || 0
  const estimatedFrames = useMemo(() => {
    const cap = Number(status?.defaults?.max_frames) || 600
    if (totalSeconds <= 0) return 1
    return Math.min(cap, Math.max(1, Math.ceil(totalSeconds * sampleRate)))
  }, [sampleRate, status?.defaults?.max_frames, totalSeconds])

  const destination = outputPath || defaultOutputPath || ''
  const canGenerate = Boolean(!disabled && destination && modelReady && !running)

  const generate = () =>
    onGenerate?.({
      output_path: destination,
      sample_rate: Number(sampleRate),
    })

  return (
    <div className="space-y-3 p-4">
      <div className="rounded-lg border border-cyan-500/25 bg-cyan-500/[0.07] px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-cyan-300">
            <Box className="h-3.5 w-3.5" />
            3D Point Cloud Export (.ply)
          </div>
          <span
            className={`chip shrink-0 ${
              modelReady
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            }`}
            title={model?.model || model?.model_url || 'Monocular depth model'}
          >
            {modelLoading
              ? 'Loading depth model…'
              : modelReady
                ? `Depth · ${model?.backend || 'onnxruntime'}`
                : 'Depth model unavailable'}
          </span>
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
          Samples the clip with a small monocular-depth ONNX model and projects RGB pixels into 3D.
          360° sources use equirectangular projection; everything else uses a pinhole camera.
        </p>
      </div>

      <label className="block">
        <span className="field-label">Destination .ply</span>
        <input
          className="field text-xs"
          value={outputPath}
          disabled={disabled || running}
          placeholder={defaultOutputPath || 'Select a source and output folder first'}
          onChange={(event) => setOutputPath(event.target.value)}
          spellCheck={false}
        />
        <span className="mt-0.5 block text-[10px] text-slate-600">
          Written directly inside the host mount; a missing <span className="font-mono">.ply</span>{' '}
          extension is added automatically.
        </span>
      </label>

      <div>
        <span className="field-label">Sample rate</span>
        <div className="segmented">
          {SAMPLE_RATES.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={disabled || running}
              data-active={Number(sampleRate) === option.value}
              title={option.hint}
              onClick={() => setSampleRate(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p className="mt-0.5 text-[10px] text-slate-600">
          {SAMPLE_RATES.find((option) => option.value === sampleRate)?.hint} · about{' '}
          {estimatedFrames} frame{estimatedFrames === 1 ? '' : 's'} will be analysed
          {totalSeconds > 0 ? ` (${Math.round(totalSeconds)}s source)` : ''}.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 !text-[11px]"
          disabled={!canGenerate}
          onClick={generate}
          title="Generate the .ply point cloud in the background"
        >
          {running ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          {running ? 'Generating…' : 'Generate .ply'}
        </button>
        <button
          type="button"
          className="btn-ghost !px-2 !py-1 !text-[11px]"
          disabled={running}
          onClick={() => onRefresh?.()}
          title="Refresh export status"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${running ? 'animate-spin' : ''}`} />
          Status
        </button>
        <span className="font-mono text-[10px] text-slate-500">
          {Number(status?.points || 0).toLocaleString()} pts
        </span>
      </div>

      {(running || progress > 0) && (
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-2 text-[10px] text-slate-500">
            <span className={status?.state === 'failed' ? 'text-red-300' : 'text-slate-400'}>
              {PHASE_LABELS[status?.phase] || PHASE_LABELS[status?.state] || 'Working…'}
            </span>
            <span className="font-mono">{percent}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                status?.state === 'failed'
                  ? 'bg-red-500'
                  : 'bg-gradient-to-r from-cyan-500 to-emerald-400'
              }`}
              style={{ width: `${percent}%` }}
            />
          </div>
          {status?.frames_total ? (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-slate-600">
              <span>
                frames {status.frames_processed || 0}/{status.frames_total}
              </span>
              <span>{Number(status.points || 0).toLocaleString()} pts buffered</span>
              {status.elapsed_seconds ? <span>{status.elapsed_seconds.toFixed(1)}s</span> : null}
            </div>
          ) : null}
        </div>
      )}

      {status?.state === 'completed' && status.output ? (
        <div className="rounded-md border border-emerald-500/25 bg-emerald-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-emerald-200">
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate font-mono">{status.output}</div>
              <div className="text-emerald-300/80">
                {Number(status.points || 0).toLocaleString()} points ·{' '}
                {formatBytes(Number(status.output_size_bytes) || 0)} ·{' '}
                {status.frames_processed}/{status.frames_total} frames
              </div>
            </div>
            <button
              type="button"
              className="btn-primary shrink-0 !px-2 !py-1 !text-[11px]"
              onClick={() => onView3D?.(status.output)}
              title="Open the fly-through viewer"
            >
              🎮 View 3D
            </button>
          </div>
        </div>
      ) : null}

      {!modelReady && !modelLoading && (model?.reason || status?.model?.reason) ? (
        <p className="rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] leading-relaxed text-amber-200">
          {model?.reason || status?.model?.reason}
        </p>
      ) : null}

      {status?.state === 'failed' && status.error ? (
        <p className="rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {status.error}
        </p>
      ) : null}

      {error ? (
        <p className="rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {error}
        </p>
      ) : null}

      <p className="text-[10px] leading-relaxed text-slate-600">
        <span className="font-mono text-slate-500">POST</span> /api/ai/point-cloud/generate ·{' '}
        <span className="font-mono text-slate-500">GET</span> /api/ai/point-cloud/status ·{' '}
        <span className="font-mono text-slate-500">GET</span> /api/fs/file
      </p>
    </div>
  )
}
