import { useCallback, useEffect, useState } from 'react'
import {
  ArrowUp,
  Captions,
  Check,
  FileVideo,
  Folder,
  FolderOpen,
  HardDrive,
  Loader2,
  TriangleAlert,
  X,
} from 'lucide-react'
import { api } from '../api'
import { formatBytes } from '../utils'

const VIDEO_KINDS = new Set(['video'])

function formatModified(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function FileBrowser({
  open,
  mode,
  initialPath,
  root,
  onClose,
  onSelect,
  videoExtensions,
  subtitleExtensions,
}) {
  const [currentPath, setCurrentPath] = useState(initialPath || root || '')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  const isSource = mode === 'source'

  const load = useCallback(async (path) => {
    setLoading(true)
    setError(null)
    try {
      const response = await api.list(path)
      setData(response)
      setCurrentPath(response.path)
      setSelected(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    const startPath = initialPath || root
    if (startPath) load(startPath)
  }, [open, initialPath, root, load])

  useEffect(() => {
    if (!open) return undefined
    const handleKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [open, onClose])

  if (!open) return null

  const entries = data?.entries || []
  const directories = entries.filter((entry) => entry.is_dir)
  const playable = entries.filter((entry) => VIDEO_KINDS.has(entry.kind))
  const subtitles = entries.filter((entry) => entry.kind === 'subtitle')
  const otherFiles = entries.filter((entry) => !entry.is_dir && !VIDEO_KINDS.has(entry.kind) && entry.kind !== 'subtitle')
  const ordered = [...directories, ...playable, ...subtitles, ...otherFiles]

  const enterDirectory = (entry) => load(entry.path)
  const goUp = () => {
    if (data?.parent) load(data.parent)
  }

  const confirm = () => {
    if (isSource) {
      if (selected && selected.kind === 'video') onSelect(selected.path)
      return
    }
    onSelect(selected?.is_dir ? selected.path : currentPath)
  }

  const activeSelection = isSource
    ? selected?.kind === 'video'
      ? selected
      : null
    : selected?.is_dir
      ? selected
      : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="flex h-[76vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-studio-900 shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-white/5 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent-500/10 text-accent-400">
              {isSource ? <FileVideo className="h-4 w-4" /> : <FolderOpen className="h-4 w-4" />}
            </div>
            <div>
              <h2 className="text-sm font-semibold text-white">
                {isSource ? 'Select source video' : 'Select target directory'}
              </h2>
              <p className="text-[11px] text-slate-500">
                {isSource
                  ? 'Browse host drives mounted at the container root. Nothing is uploaded.'
                  : 'Rendered files are written directly into this folder.'}
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost !p-1.5">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-white/5 bg-studio-950/40 px-4 py-2">
          <button
            type="button"
            onClick={goUp}
            disabled={!data?.parent || loading}
            className="rounded-md p-1.5 text-slate-400 transition hover:bg-white/5 hover:text-white disabled:opacity-30"
            title="Parent folder"
          >
            <ArrowUp className="h-4 w-4" />
          </button>
          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {(data?.breadcrumbs || []).map((crumb, index) => (
              <span key={crumb.path} className="flex shrink-0 items-center gap-1">
                {index > 0 && <span className="text-slate-700">/</span>}
                <button
                  type="button"
                  onClick={() => load(crumb.path)}
                  className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-xs transition hover:bg-white/5 ${
                    index === (data?.breadcrumbs?.length || 0) - 1
                      ? 'font-semibold text-slate-200'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {index === 0 && <HardDrive className="h-3 w-3" />}
                  {crumb.name}
                </button>
              </span>
            ))}
          </div>
          {loading && <Loader2 className="h-4 w-4 animate-spin text-accent-400" />}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {error && (
            <div className="m-2 flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {!error && !loading && ordered.length === 0 && (
            <div className="grid h-full place-items-center p-8 text-center">
              <div>
                <Folder className="mx-auto h-8 w-8 text-slate-700" />
                <p className="mt-2 text-sm text-slate-500">This folder is empty.</p>
              </div>
            </div>
          )}

          {ordered.map((entry) => {
            const isSelected = selected?.path === entry.path
            const disabled = !entry.is_dir && !isSource
            const isVideo = entry.kind === 'video'
            return (
              <button
                key={entry.path}
                type="button"
                disabled={disabled}
                onClick={() => setSelected(entry)}
                onDoubleClick={() => {
                  if (entry.is_dir) enterDirectory(entry)
                  else if (isVideo && isSource) onSelect(entry.path)
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
                    {entry.modified_iso ? ` · ${formatModified(entry.modified_iso)}` : ''}
                  </span>
                </span>

                {isVideo && isSource && (
                  <span className="hidden shrink-0 rounded-md border border-white/5 bg-white/[0.03] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500 sm:inline">
                    {videoExtensions?.join(' ')}
                  </span>
                )}
                {isSelected && <Check className="h-4 w-4 shrink-0 text-accent-400" />}
              </button>
            )
          })}

          {data?.truncated && (
            <p className="px-3 py-2 text-[11px] text-amber-400/80">
              Showing the first {ordered.length} entries of this folder.
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 bg-studio-950/40 px-4 py-3">
          <div className="min-w-0 text-[11px] text-slate-600">
            {isSource
              ? activeSelection
                ? `Selected: ${activeSelection.name}`
                : 'Select a video file to continue'
              : `Destination: ${activeSelection?.path || currentPath || '—'}`}
            {isSource && subtitleExtensions?.length ? (
              <span className="ml-2 text-slate-700">
                ({subtitleExtensions.join(', ')} files are detected automatically)
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onClose} className="btn-ghost">
              Cancel
            </button>
            <button
              type="button"
              onClick={confirm}
              disabled={isSource ? !activeSelection : !currentPath}
              className="btn-primary"
            >
              <Check className="h-4 w-4" />
              {isSource ? 'Use this video' : 'Use this folder'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
