import { useEffect, useMemo } from 'react'
import {
  Activity,
  CheckCircle2,
  Inbox,
  Layers,
  ListVideo,
  Trash2,
  X,
} from 'lucide-react'
import JobProgress from './JobProgress'
import { formatBytes } from '../utils'

const ACTIVE = new Set(['processing', 'cancelling'])
const FINISHED = new Set(['completed', 'failed', 'cancelled'])

function Section({ title, count, children, tone = 'text-slate-400' }) {
  if (count === 0) return null
  return (
    <div className="space-y-2">
      <div className={`flex items-center gap-2 px-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${tone}`}>
        {title}
        <span className="rounded-full bg-white/5 px-1.5 py-0.5 text-[9px] text-slate-400">{count}</span>
      </div>
      {children}
    </div>
  )
}

export default function RenderQueue({
  open,
  jobs = [],
  wsStatus,
  onClose,
  onCancel,
  onDismiss,
  onClearCompleted,
}) {
  const { active, queued, finished, completedCount, totalBytes } = useMemo(() => {
    const activeJobs = jobs.filter((job) => ACTIVE.has(job.status))
    const queuedJobs = jobs
      .filter((job) => job.status === 'queued')
      .sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
    const finishedJobs = jobs
      .filter((job) => FINISHED.has(job.status))
      .sort((a, b) => (b.finished_at || b.created_at || 0) - (a.finished_at || a.created_at || 0))
    const completed = jobs.filter((job) => job.status === 'completed')
    return {
      active: activeJobs,
      queued: queuedJobs,
      finished: finishedJobs,
      completedCount: completed.length,
      totalBytes: completed.reduce((sum, job) => sum + (job.output_size_bytes || 0), 0),
    }
  }, [jobs])

  useEffect(() => {
    if (!open) return undefined
    const handleKey = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [open, onClose])

  if (!open) return null

  const pendingCount = active.length + queued.length

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50 backdrop-blur-sm" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose?.()
    }}>
      <aside className="flex h-full w-full max-w-md flex-col border-l border-white/10 bg-studio-900 shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-white/5 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent-500/10 text-accent-400">
              <ListVideo className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-white">Render queue</h2>
              <p className="flex items-center gap-1.5 text-[10px] text-slate-500">
                <Activity
                  className={`h-2.5 w-2.5 ${
                    wsStatus === 'open' ? 'text-emerald-400' : 'text-amber-400'
                  }`}
                />
                {wsStatus === 'open' ? 'Live updates' : `Socket ${wsStatus || 'offline'}`}
                {pendingCount > 0 ? ` · ${pendingCount} pending` : ''}
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost !p-1.5" aria-label="Close queue">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
          {jobs.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
              <div className="grid h-14 w-14 place-items-center rounded-2xl border border-white/5 bg-white/[0.02] text-slate-600">
                <Inbox className="h-6 w-6" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-slate-300">Queue is empty</h3>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                  Use <span className="text-slate-300">Add to Queue</span> to line up several renders.
                  They run one at a time on the GPU and are written straight to the target folder.
                </p>
              </div>
            </div>
          ) : (
            <>
              <Section title="Processing" count={active.length} tone="text-accent-300">
                {active.map((job) => (
                  <JobProgress key={job.id} job={job} onCancel={onCancel} onDismiss={onDismiss} />
                ))}
              </Section>

              <Section title="Queued" count={queued.length}>
                {queued.map((job, index) => (
                  <JobProgress
                    key={job.id}
                    job={job}
                    position={index + 1}
                    onCancel={onCancel}
                    onDismiss={onDismiss}
                  />
                ))}
              </Section>

              <Section title="History" count={finished.length} tone="text-slate-500">
                {finished.map((job) => (
                  <JobProgress
                    key={job.id}
                    job={job}
                    onCancel={onCancel}
                    onDismiss={onDismiss}
                  />
                ))}
              </Section>
            </>
          )}
        </div>

        <div className="border-t border-white/5 bg-studio-950/50 px-4 py-3">
          <div className="flex items-center justify-between gap-2 text-[10px] text-slate-500">
            <span className="inline-flex items-center gap-1.5">
              <CheckCircle2 className="h-3 w-3 text-emerald-400" />
              {completedCount} completed · {formatBytes(totalBytes)} written
            </span>
            <button
              type="button"
              onClick={onClearCompleted}
              disabled={finished.length === 0}
              className="inline-flex items-center gap-1 rounded-md border border-white/10 px-2 py-1 font-semibold uppercase tracking-wide text-slate-400 transition hover:border-red-500/30 hover:text-red-300 disabled:opacity-30"
            >
              <Trash2 className="h-3 w-3" />
              Clear finished
            </button>
          </div>
          <p className="mt-2 flex items-center gap-1.5 text-[9px] leading-relaxed text-slate-600">
            <Layers className="h-3 w-3 shrink-0" />
            Jobs render sequentially to protect NVENC sessions; queued items keep the settings that
            were active when they were added.
          </p>
        </div>
      </aside>
    </div>
  )
}
