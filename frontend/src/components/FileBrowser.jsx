import { useCallback, useEffect, useState } from 'react'
import {
  ArrowUp,
  Check,
  FileVideo,
  FolderOpen,
  Globe,
  HardDrive,
  Loader2,
  TriangleAlert,
  X,
} from 'lucide-react'
import { api } from '../api'
import FileEntryList from './FileEntryList'
import NetworkPanel from './NetworkPanel'

function TabButton({ active, onClick, icon: Icon, label }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1.5 rounded-t-lg border-b-2 px-3 py-2 text-xs font-medium transition ${
        active
          ? 'border-accent-500 bg-white/[0.04] text-slate-100'
          : 'border-transparent text-slate-500 hover:bg-white/[0.02] hover:text-slate-300'
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  )
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
  const [tab, setTab] = useState('host')
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
    setTab('host')
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
                  ? 'Browse host drives or load a network stream. Nothing is uploaded.'
                  : 'Rendered files are written directly into this folder.'}
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost !p-1.5">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-end gap-1 border-b border-white/5 bg-studio-950/60 px-4 pt-2">
          <TabButton
            active={tab === 'host'}
            onClick={() => setTab('host')}
            icon={HardDrive}
            label="📁 Host Drives"
          />
          <TabButton
            active={tab === 'network'}
            onClick={() => setTab('network')}
            icon={Globe}
            label="🌐 Network / NAS / URL"
          />
        </div>

        {tab === 'network' ? (
          <NetworkPanel mode={mode} onSelect={onSelect} videoExtensions={videoExtensions} />
        ) : (
          <>
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

              {!error && (
                <FileEntryList
                  entries={entries}
                  isSource={isSource}
                  selectedPath={selected?.path}
                  onPick={setSelected}
                  onOpen={(entry) => {
                    if (entry.is_dir) enterDirectory(entry)
                    else if (isSource && entry.kind === 'video') onSelect(entry.path)
                  }}
                  videoExtensions={videoExtensions}
                  emptyLabel={loading ? 'Loading…' : 'This folder is empty.'}
                />
              )}

              {data?.truncated && (
                <p className="px-3 py-2 text-[11px] text-amber-400/80">
                  Showing the first {entries.length} entries of this folder.
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
          </>
        )}
      </div>
    </div>
  )
}
