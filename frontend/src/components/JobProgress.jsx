import { useState } from 'react'
import {
  Ban,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Gauge,
  HardDriveDownload,
  Loader2,
  XCircle,
} from 'lucide-react'
import { formatBytes, formatClock, formatEta } from '../utils'

const STATUS_META = {
  queued: { label: 'Queued', tone: 'text-slate-300', ring: 'border-white/10', icon: Clock3 },
  processing: { label: 'Encoding', tone: 'text-accent-300', ring: 'border-accent-500/30', icon: Loader2 },
  cancelling: { label: 'Cancelling', tone: 'text-amber-300', ring: 'border-amber-500/30', icon: Loader2 },
  completed: { label: 'Completed', tone: 'text-emerald-300', ring: 'border-emerald-500/30', icon: CheckCircle2 },
  failed: { label: 'Failed', tone: 'text-red-300', ring: 'border-red-500/30', icon: XCircle },
  cancelled: { label: 'Cancelled', tone: 'text-slate-400', ring: 'border-white/10', icon: Ban },
}

export default function JobProgress({ job, onCancel, showLog = true }) {
  const [logOpen, setLogOpen] = useState(false)
  if (!job) return null

  const meta = STATUS_META[job.status] || STATUS_META.queued
  const Icon = meta.icon
  const progress = job.progress || {}
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0))
  const isActive = job.status === 'processing' || job.status === 'cancelling'
  const canCancel = job.status === 'queued' || job.status === 'processing'

  return (
    <div className={`rounded-xl border ${meta.ring} bg-studio-950/60 p-3`}>
      <div className="flex items-start gap-2">
        <Icon
          className={`mt-0.5 h-4 w-4 shrink-0 ${meta.tone} ${
            isActive ? 'animate-spin' : ''
          }`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className={`text-[11px] font-semibold uppercase tracking-wider ${meta.tone}`}>
              {meta.label}
            </span>
            <span className="font-mono text-[11px] text-slate-400">
              {percent.toFixed(1)}%
            </span>
          </div>
          <div
            className="mt-0.5 truncate text-xs font-medium text-slate-200"
            title={job.output_filename}
          >
            {job.output_filename}
          </div>
          {job.fallback_reason && (
            <div className="mt-0.5 text-[10px] text-amber-300">{job.fallback_reason}</div>
          )}
        </div>
        {canCancel && onCancel && (
          <button
            type="button"
            onClick={() => onCancel(job.id)}
            className="rounded-md p-1 text-slate-500 transition hover:bg-red-500/10 hover:text-red-300"
            title="Cancel render"
            aria-label="Cancel render"
          >
            <Ban className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-white/5">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ${
            job.status === 'failed'
              ? 'bg-red-500/70'
              : job.status === 'completed'
                ? 'bg-emerald-400'
                : job.status === 'cancelled'
                  ? 'bg-slate-600'
                  : 'progress-shimmer'
          }`}
          style={{ width: `${job.status === 'completed' ? 100 : percent}%` }}
        />
      </div>

      <div className="mt-2 grid grid-cols-3 gap-1.5 text-[10px]">
        <div className="rounded-md bg-white/[0.03] px-1.5 py-1">
          <div className="text-slate-600">FPS</div>
          <div className="font-mono text-slate-300">
            {progress.fps ? progress.fps.toFixed(0) : '—'}
          </div>
        </div>
        <div className="rounded-md bg-white/[0.03] px-1.5 py-1">
          <div className="text-slate-600">Speed</div>
          <div className="font-mono text-slate-300">
            {progress.encode_speed ? `${progress.encode_speed.toFixed(2)}×` : '—'}
          </div>
        </div>
        <div className="rounded-md bg-white/[0.03] px-1.5 py-1">
          <div className="text-slate-600">{isActive ? 'ETA' : 'Took'}</div>
          <div className="font-mono text-slate-300">
            {isActive ? formatEta(progress.eta_seconds) : formatEta(job.elapsed_seconds)}
          </div>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
        <span className="inline-flex items-center gap-1">
          <Gauge className="h-3 w-3" />
          {job.encoder}
        </span>
        <span>
          {formatClock(progress.out_time_seconds || 0)} / {formatClock(job.duration || 0)}
        </span>
        {job.elapsed_seconds ? <span>{job.elapsed_seconds.toFixed(1)}s elapsed</span> : null}
      </div>

      {job.status === 'completed' && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-2 py-1.5 text-[10px] text-emerald-200/90">
          <HardDriveDownload className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="min-w-0 break-all font-mono">{job.output_path}</span>
          <span className="ml-auto shrink-0 text-emerald-300">
            {formatBytes(job.output_size_bytes)}
          </span>
        </div>
      )}

      {job.status === 'failed' && (
        <div className="mt-2 rounded-md border border-red-500/20 bg-red-500/5 px-2 py-1.5">
          <div className="flex items-start gap-1.5 text-[10px] text-red-200">
            <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="min-w-0 break-words">{job.error}</span>
          </div>
          {showLog && job.log ? (
            <>
              <button
                type="button"
                onClick={() => setLogOpen((value) => !value)}
                className="mt-1.5 inline-flex items-center gap-1 text-[10px] font-medium text-red-300/80 hover:text-red-200"
              >
                <ChevronDown
                  className={`h-3 w-3 transition ${logOpen ? 'rotate-180' : ''}`}
                />
                {logOpen ? 'Hide' : 'Show'} FFmpeg log
              </button>
              {logOpen && (
                <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/50 p-2 font-mono text-[9px] leading-relaxed text-red-200/80">
                  {job.log}
                </pre>
              )}
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}
