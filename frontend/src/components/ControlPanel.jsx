import { FolderOpen, FileVideo, HardDrive, AudioLines, Film, Clock, Info } from 'lucide-react'
import { formatBytes, formatClock } from '../utils'

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-[0.14em] text-slate-600">{label}</div>
      <div className="mt-0.5 truncate text-xs font-semibold text-slate-200">{value}</div>
    </div>
  )
}

export default function ControlPanel({
  metadata,
  sourcePath,
  targetDir,
  hostRoot,
  onBrowseSource,
  onBrowseTarget,
}) {
  return (
    <div className="flex flex-col gap-4">
      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <FileVideo className="h-3.5 w-3.5 text-accent-400" />
            Source
          </div>
          <button
            type="button"
            onClick={onBrowseSource}
            className="text-[11px] font-medium text-accent-400 hover:text-accent-300"
          >
            {sourcePath ? 'Change' : 'Choose'}
          </button>
        </div>
        <div className="p-4">
          {!metadata ? (
            <button
              type="button"
              onClick={onBrowseSource}
              className="flex w-full flex-col items-center gap-2 rounded-xl border border-dashed border-white/10 bg-white/[0.01] px-4 py-8 text-center transition hover:border-accent-500/40 hover:bg-accent-500/[0.04]"
            >
              <FolderOpen className="h-6 w-6 text-slate-600" />
              <span className="text-xs font-medium text-slate-400">
                Select a video from a host drive
              </span>
              <span className="text-[10px] uppercase tracking-widest text-slate-600">
                .mp4 · .mkv · .avi
              </span>
            </button>
          ) : (
            <>
              <div className="mb-3 min-w-0">
                <div className="truncate text-sm font-semibold text-white" title={metadata.name}>
                  {metadata.name}
                </div>
                <div
                  className="mt-0.5 truncate font-mono text-[10px] text-slate-600"
                  title={metadata.path}
                >
                  {metadata.path}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Duration" value={formatClock(metadata.duration)} />
                <Stat
                  label="Resolution"
                  value={`${metadata.display_width}×${metadata.display_height}`}
                />
                <Stat label="Frame rate" value={metadata.fps ? `${metadata.fps} fps` : '—'} />
                <Stat
                  label="Codec"
                  value={`${metadata.codec.toUpperCase()}${metadata.pix_fmt ? ` · ${metadata.pix_fmt}` : ''}`}
                />
                <Stat label="Size" value={formatBytes(metadata.size_bytes)} />
                <Stat
                  label="Audio"
                  value={
                    metadata.has_audio
                      ? `${metadata.audio_codec.toUpperCase()} · ${metadata.audio_channels}ch`
                      : 'None'
                  }
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {metadata.rotation ? (
                  <span className="chip border-amber-400/20 bg-amber-400/5 text-amber-300">
                    <Film className="h-3 w-3" />
                    Rotation metadata {metadata.rotation}°
                  </span>
                ) : null}
                {metadata.has_audio ? (
                  <span className="chip">
                    <AudioLines className="h-3 w-3" />
                    {metadata.audio_sample_rate || 0} Hz
                  </span>
                ) : (
                  <span className="chip border-amber-400/20 bg-amber-400/5 text-amber-300">
                    Audio will be omitted
                  </span>
                )}
                <span className="chip">
                  <Clock className="h-3 w-3" />
                  {metadata.duration_formatted}
                </span>
              </div>
            </>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <HardDrive className="h-3.5 w-3.5 text-emerald-400" />
            Destination
          </div>
          <button
            type="button"
            onClick={onBrowseTarget}
            className="text-[11px] font-medium text-accent-400 hover:text-accent-300"
          >
            {targetDir ? 'Change' : 'Choose'}
          </button>
        </div>
        <div className="p-4">
          <button
            type="button"
            onClick={onBrowseTarget}
            className={`flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-left transition ${
              targetDir
                ? 'border-white/10 bg-white/[0.02] hover:border-accent-500/40'
                : 'border-dashed border-white/10 bg-white/[0.01] hover:border-accent-500/40 hover:bg-accent-500/[0.04]'
            }`}
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-emerald-400/10 text-emerald-300">
              <FolderOpen className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-semibold text-slate-200">
                {targetDir ? targetDir.split(/[\\/]/).filter(Boolean).pop() : 'Select output folder'}
              </span>
              <span className="block truncate font-mono text-[10px] text-slate-600">
                {targetDir || hostRoot || 'No folder selected'}
              </span>
            </span>
          </button>
          <p className="mt-3 flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
            <Info className="mt-0.5 h-3 w-3 shrink-0" />
            Rendered files are written straight to this folder on the host. The source file is never
            uploaded, moved or overwritten.
          </p>
        </div>
      </section>
    </div>

  )
}
