import { Captions, Check, FileVideo, Folder } from 'lucide-react'
import { formatBytes, formatModifiedDate } from '../utils'

const VIDEO_KINDS = new Set(['video'])

/**
 * Sorted, clickable media list shared by the host-drive tab and the
 * network/NAS tab so both browsers behave identically.
 */
export default function FileEntryList({
  entries,
  isSource,
  selectedPath,
  onPick,
  onOpen,
  videoExtensions,
  emptyLabel = 'This folder is empty.',
  emptyHint,
}) {
  const directories = entries.filter((entry) => entry.is_dir)
  const playable = entries.filter((entry) => VIDEO_KINDS.has(entry.kind))
  const subtitles = entries.filter((entry) => entry.kind === 'subtitle')
  const otherFiles = entries.filter(
    (entry) => !entry.is_dir && !VIDEO_KINDS.has(entry.kind) && entry.kind !== 'subtitle',
  )
  const ordered = [...directories, ...playable, ...subtitles, ...otherFiles]

  if (ordered.length === 0) {
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <div>
          <Folder className="mx-auto h-8 w-8 text-slate-700" />
          <p className="mt-2 text-sm text-slate-500">{emptyLabel}</p>
          {emptyHint ? <p className="mt-1 text-[11px] text-slate-600">{emptyHint}</p> : null}
        </div>
      </div>
    )
  }

  return (
    <>
      {ordered.map((entry) => {
        const isSelected = selectedPath === entry.path
        const disabled = !entry.is_dir && !isSource
        const isVideo = entry.kind === 'video'
        return (
          <button
            key={entry.path}
            type="button"
            disabled={disabled}
            onClick={() => onPick?.(entry)}
            onDoubleClick={() => {
              if (entry.is_dir) onOpen?.(entry)
              else if (isVideo && isSource) onOpen?.(entry)
            }}
            className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition ${
              isSelected
                ? 'bg-accent-500/10 ring-1 ring-inset ring-accent-500/30'
                : 'hover:bg-white/[0.04]'
            } ${disabled ? 'cursor-default opacity-35' : ''}`}
          >
            <span
              className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${
                entry.is_dir
                  ? 'bg-amber-400/10 text-amber-300'
                  : isVideo
                    ? 'bg-accent-500/10 text-accent-400'
                    : 'bg-white/5 text-slate-400'
              }`}
            >
              {entry.is_dir ? (
                <Folder className="h-4 w-4" />
              ) : isVideo ? (
                <FileVideo className="h-4 w-4" />
              ) : (
                <Captions className="h-4 w-4" />
              )}
            </span>

            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm text-slate-200">{entry.name}</span>
              <span className="block text-[11px] text-slate-600">
                {entry.is_dir
                  ? 'Folder'
                  : `${entry.extension.replace('.', '').toUpperCase() || 'FILE'} · ${formatBytes(entry.size_bytes)}`}
                {entry.modified_iso ? ` · ${formatModifiedDate(entry.modified_iso)}` : ''}
              </span>
            </span>

            {isVideo && isSource && videoExtensions?.length ? (
              <span className="hidden shrink-0 rounded-md border border-white/5 bg-white/[0.03] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500 sm:inline">
                {videoExtensions.join(' ')}
              </span>
            ) : null}
            {isSelected && <Check className="h-4 w-4 shrink-0 text-accent-400" />}
          </button>
        )
      })}
    </>
  )
}
