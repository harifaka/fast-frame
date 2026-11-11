import { Activity, Cpu, ListVideo, RefreshCw, Zap } from 'lucide-react'

function StatusDot({ ready }) {
  return (
    <span
      className={`relative inline-flex h-2 w-2 rounded-full ${
        ready ? 'bg-emerald-400' : 'bg-amber-400'
      }`}
    >
      <span
        className={`absolute inset-0 rounded-full ${
          ready ? 'bg-emerald-400' : 'bg-amber-400'
        } animate-ping opacity-60`}
      />
    </span>
  )
}

export default function Header({
  gpu,
  gpuError,
  refreshing,
  onRefresh,
  onToggleQueue,
  queueOpen,
  queuedCount,
  activeCount,
  wsStatus,
}) {
  const ready = Boolean(gpu?.hardware_encoding_available)
  const gpuName = gpu?.gpu_name || 'No NVIDIA GPU'
  const badgeBase =
    'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide'

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-studio-950/80 backdrop-blur-xl">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent-400 to-indigo-500 text-slate-950 shadow-glow">
            <Zap className="h-5 w-5" strokeWidth={2.5} />
          </div>
          <div className="leading-tight">
            <div className="flex items-center gap-2 text-sm font-bold tracking-[0.22em] text-white">
              FAST-FRAME
            </div>
            <div className="text-[10px] uppercase tracking-[0.22em] text-slate-500">
              GPU Speed &amp; Processing Studio
            </div>
          </div>
        </div>

        <div className="mx-1 hidden h-8 w-px bg-white/5 sm:block" />

        <div
          className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-white/5 bg-white/[0.02] px-3 py-1.5"
          title={gpu?.message || gpuError || 'Checking NVIDIA NVENC availability'}
        >
          <StatusDot ready={ready} />
          <Cpu className="h-3.5 w-3.5 shrink-0 text-slate-500" />
          <span className="truncate text-xs font-semibold text-slate-200">
            {gpu ? gpuName : gpuError ? 'GPU check failed' : 'Detecting GPU…'}
          </span>
          <span
            className={`shrink-0 text-[10px] font-medium uppercase tracking-wider ${
              ready ? 'text-emerald-400' : 'text-amber-400'
            }`}
          >
            {gpu ? (ready ? 'NVENC Ready' : 'CPU Fallback') : ''}
          </span>

          {gpu && (
            <div className="ml-auto hidden items-center gap-1.5 md:flex">
              <span
                className={`${badgeBase} ${
                  gpu.nvenc_h264
                    ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
                    : 'border-white/10 bg-white/5 text-slate-500'
                }`}
              >
                H.264 NVENC
              </span>
              <span
                className={`${badgeBase} ${
                  gpu.nvenc_hevc
                    ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
                    : 'border-white/10 bg-white/5 text-slate-500'
                }`}
              >
                HEVC NVENC
              </span>
              {gpu.driver_version && (
                <span className="hidden text-[10px] text-slate-600 lg:inline">
                  driver {gpu.driver_version}
                </span>
              )}
            </div>
          )}

          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            title="Re-run GPU detection"
            className="ml-1 shrink-0 rounded-md p-1 text-slate-500 transition hover:bg-white/5 hover:text-slate-200 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <div className="flex items-center gap-2">
          {wsStatus && (
            <span
              className="hidden items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-600 sm:flex"
              title={`Progress socket: ${wsStatus}`}
            >
              <Activity
                className={`h-3 w-3 ${
                  wsStatus === 'open'
                    ? 'text-emerald-400'
                    : wsStatus === 'connecting'
                      ? 'text-amber-400'
                      : 'text-slate-600'
                }`}
              />
              {wsStatus === 'open' ? 'Live' : wsStatus}
            </span>
          )}

          {onToggleQueue && (
            <button
              type="button"
              onClick={onToggleQueue}
              className={`btn-ghost relative ${queueOpen ? 'border-accent-500/40 text-white' : ''}`}
            >
              <ListVideo className="h-4 w-4" />
              <span className="hidden sm:inline">Queue</span>
              {(queuedCount > 0 || activeCount > 0) && (
                <span className="absolute -right-1.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent-500 px-1 text-[10px] font-bold text-slate-950">
                  {queuedCount + activeCount}
                </span>
              )}
            </button>
          )}
        </div>
      </div>
    </header>
  )
}
