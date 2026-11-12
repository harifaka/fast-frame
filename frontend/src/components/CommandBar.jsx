import {
  Captions,
  ChevronRight,
  HardDrive,
  Plus,
  Rocket,
  Scissors,
  Settings2,
} from 'lucide-react'
import { formatClock, formatSpeed } from '../utils'
import JobProgress from './JobProgress'

function SummaryPill({ onClick, title, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="chip transition hover:border-accent-500/40 hover:text-accent-200"
    >
      {children}
    </button>
  )
}

export default function CommandBar({
  metadata,
  settings,
  targetDir,
  hostRoot,
  activeJob,
  onCancelJob,
  onBrowseTarget,
  onOpenConfig,
  configOpen,
  onSubmit,
  submitting,
  metadataLoading = false,
  canRender,
}) {
  const speed = Number(settings.speed) || 1
  const segments = settings.segments || []
  const keptDuration = segments.reduce((sum, segment) => sum + (segment.end - segment.start), 0)
  const outputDuration =
    keptDuration > 0 ? keptDuration / speed : (metadata?.duration || 0) / speed
  const destination = targetDir || hostRoot || ''

  return (
    <div className="panel px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onOpenConfig?.()}
          aria-expanded={Boolean(configOpen)}
          title="Open the configuration drawer (Input, Output, Automation)"
          className={`inline-flex select-none items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/70 ${
            configOpen
              ? 'border-accent-500/40 bg-accent-500/15 text-accent-200'
              : 'border-white/5 bg-white/[0.03] text-slate-300 hover:border-white/10 hover:bg-white/[0.07] hover:text-white'
          }`}
        >
          <Settings2 className="h-4 w-4" />
          Configuration
          <ChevronRight
            className={`h-3.5 w-3.5 transition-transform duration-300 ${
              configOpen ? 'rotate-180' : ''
            }`}
          />
        </button>

        <div className="flex flex-wrap items-center gap-1.5">
          <SummaryPill onClick={() => onOpenConfig?.('speed')} title="Change the render speed">
            <span className="font-mono font-semibold text-accent-300">{formatSpeed(speed)}×</span>
          </SummaryPill>
          <SummaryPill
            onClick={() => onOpenConfig?.('encoding')}
            title="Change the output codec"
          >
            {settings.codec === 'hevc' ? 'H.265 / HEVC' : 'H.264'}
          </SummaryPill>
          {settings.subtitlePath ? (
            <SummaryPill
              onClick={() => onOpenConfig?.('subtitles')}
              title={`Burning ${settings.subtitlePath.split(/[\\/]/).pop()}`}
            >
              <Captions className="h-3 w-3" />
              Subtitles
            </SummaryPill>
          ) : null}
          {segments.length > 0 ? (
            <span className="chip" title="Multi-cut segments are edited on the timeline">
              <Scissors className="h-3 w-3" />
              {segments.length} cut{segments.length === 1 ? '' : 's'}
            </span>
          ) : null}
          {metadata ? (
            <span className="chip text-slate-400" title="Estimated output length">
              Output ~{formatClock(outputDuration)}
            </span>
          ) : null}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onBrowseTarget}
            title={destination || 'Select the destination folder'}
            className={`inline-flex max-w-[260px] items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
              targetDir
                ? 'border-emerald-400/25 bg-emerald-400/5 text-emerald-200 hover:border-emerald-400/50'
                : 'border-dashed border-amber-400/30 bg-amber-400/5 text-amber-200 hover:border-amber-400/60'
            }`}
          >
            <HardDrive className="h-3 w-3 shrink-0" />
            <span className="truncate font-mono">
              {destination ? `Destination: ${destination}` : 'Choose destination'}
            </span>
          </button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onSubmit?.({ renderNow: true })}
              disabled={!canRender || submitting || metadataLoading}
              className="btn-primary !py-1.5 !text-xs"
            >
              <Rocket className={`h-4 w-4 ${submitting ? 'animate-bounce' : ''}`} />
              Render Now
            </button>
            <button
              type="button"
              onClick={() => onSubmit?.({ renderNow: false })}
              disabled={!canRender || submitting || metadataLoading}
              className="btn-ghost !py-1.5 !text-xs"
              title="Queue this render behind the active job"
            >
              <Plus className="h-4 w-4" />
              Add to Queue
            </button>
          </div>
        </div>
      </div>

      {activeJob && (
        <div className="mt-2">
          <JobProgress job={activeJob} onCancel={onCancelJob} />
        </div>
      )}
    </div>
  )
}
