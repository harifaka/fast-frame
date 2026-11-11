import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArrowUp,
  Check,
  Cloud,
  Globe,
  HardDrive,
  Link2,
  Loader2,
  Plug,
  Server,
  Star,
  TriangleAlert,
  Unplug,
} from 'lucide-react'
import { api } from '../api'
import { formatBytes, formatClock, isPreviewableUrl } from '../utils'
import FileEntryList from './FileEntryList'

const RECENT_URLS_KEY = 'fastframe.network.urls'
const SAVED_SHARES_KEY = 'fastframe.network.shares'
const MAX_BOOKMARKS = 8

function readBookmarks(key) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '[]')
    return Array.isArray(parsed) ? parsed.filter(Boolean) : []
  } catch {
    return []
  }
}

function writeBookmarks(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value.slice(0, MAX_BOOKMARKS)))
  } catch {
    /* private mode / quota - bookmarks are a convenience, not a requirement */
  }
}

function modeLabel(mode) {
  if (mode === 'kernel') return 'mounted'
  if (mode === 'native') return 'host path'
  return 'SMB relay'
}

export default function NetworkPanel({ mode, onSelect, videoExtensions }) {
  const isSource = mode === 'source'

  const [urlInput, setUrlInput] = useState('')
  const [urlLoading, setUrlLoading] = useState(false)
  const [urlError, setUrlError] = useState(null)
  const [urlMetadata, setUrlMetadata] = useState(null)
  const [recentUrls, setRecentUrls] = useState(() => readBookmarks(RECENT_URLS_KEY))

  const [shareInput, setShareInput] = useState('')
  const [shareUser, setShareUser] = useState('')
  const [sharePassword, setSharePassword] = useState('')
  const [shareName, setShareName] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [shareError, setShareError] = useState(null)
  const [mounts, setMounts] = useState([])
  const [smbAvailable, setSmbAvailable] = useState(true)
  const [savedShares, setSavedShares] = useState(() => readBookmarks(SAVED_SHARES_KEY))

  const [currentPath, setCurrentPath] = useState(null)
  const [listing, setListing] = useState(null)
  const [listingLoading, setListingLoading] = useState(false)
  const [listingError, setListingError] = useState(null)
  const [selected, setSelected] = useState(null)

  const refreshMounts = useCallback(async () => {
    try {
      const response = await api.networkMounts()
      setMounts(response.mounts || [])
      setSmbAvailable(response.smb_available !== false)
    } catch (error) {
      setShareError(error.message)
    }
  }, [])

  useEffect(() => {
    refreshMounts()
  }, [refreshMounts])

  const loadUrl = useCallback(async (value) => {
    const target = (value || '').trim()
    if (!target) return
    setUrlLoading(true)
    setUrlError(null)
    setUrlMetadata(null)
    try {
      const metadata = await api.metadata(target)
      setUrlMetadata({ ...metadata, url: target })
      const next = [
        { url: target, name: metadata.name },
        ...readBookmarks(RECENT_URLS_KEY).filter((item) => item.url !== target),
      ]
      writeBookmarks(RECENT_URLS_KEY, next)
      setRecentUrls(next)
    } catch (error) {
      setUrlError(error.message)
    } finally {
      setUrlLoading(false)
    }
  }, [])

  const openFolder = useCallback(async (path) => {
    if (!path) return
    setListingLoading(true)
    setListingError(null)
    setSelected(null)
    try {
      const response = await api.list(path)
      setListing(response)
      setCurrentPath(response.path)
    } catch (error) {
      setListingError(error.message)
    } finally {
      setListingLoading(false)
    }
  }, [])

  const connectShare = useCallback(
    async (override) => {
      const payload = {
        path: (override?.path ?? shareInput).trim(),
        username: (override?.username ?? shareUser).trim() || null,
        password: override?.password ?? (sharePassword || null),
        name: (override?.name ?? shareName).trim() || null,
      }
      if (!payload.path) {
        setShareError('Enter a share such as //192.168.1.100/videos')
        return
      }
      setConnecting(true)
      setShareError(null)
      try {
        const response = await api.mountNetwork(payload)
        const location = response.location
        const next = [
          { path: payload.path, name: location.name, username: payload.username },
          ...readBookmarks(SAVED_SHARES_KEY).filter((item) => item.path !== payload.path),
        ]
        writeBookmarks(SAVED_SHARES_KEY, next)
        setSavedShares(next)
        setSharePassword('')
        await refreshMounts()
        await openFolder(location.root)
      } catch (error) {
        setShareError(error.message)
      } finally {
        setConnecting(false)
      }
    },
    [shareInput, shareUser, sharePassword, shareName, refreshMounts, openFolder],
  )

  const disconnect = useCallback(
    async (locationId) => {
      try {
        await api.unmountNetwork(locationId)
        await refreshMounts()
        if (currentPath && !mounts.some((mount) => mount.id !== locationId && currentPath.startsWith(mount.root))) {
          setListing(null)
          setCurrentPath(null)
        }
      } catch (error) {
        setShareError(error.message)
      }
    },
    [currentPath, mounts, refreshMounts],
  )

  const selectedVideo = isSource && selected?.kind === 'video' ? selected : null
  const destinationPath = listing?.path || currentPath
  const canConfirm = isSource ? Boolean(selectedVideo || urlMetadata) : Boolean(destinationPath)

  const summary = useMemo(() => {
    if (!urlMetadata) return null
    const bits = []
    if (urlMetadata.display_width) {
      bits.push(`${urlMetadata.display_width}×${urlMetadata.display_height}`)
    }
    if (urlMetadata.duration) bits.push(formatClock(urlMetadata.duration))
    if (urlMetadata.codec) bits.push(urlMetadata.codec.toUpperCase())
    if (urlMetadata.size_bytes) bits.push(formatBytes(urlMetadata.size_bytes))
    return bits.join(' · ')
  }, [urlMetadata])

  const confirm = () => {
    if (isSource) {
      if (urlMetadata) onSelect(urlMetadata.url)
      else if (selectedVideo) onSelect(selectedVideo.path)
      return
    }
    if (destinationPath) onSelect(destinationPath)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {isSource && (
          <section className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-200">
              <Globe className="h-3.5 w-3.5 text-accent-400" />
              Direct network video URL
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                className="field min-w-0 flex-1 text-xs"
                value={urlInput}
                onChange={(event) => setUrlInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    loadUrl(urlInput)
                  }
                }}
                placeholder="http://192.168.1.20:8080/video.mp4 · https://nas/clip.mkv · rtsp://cam/live"
                aria-label="Network video URL"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => loadUrl(urlInput)}
                disabled={urlLoading || !urlInput.trim()}
                className="btn-primary shrink-0 whitespace-nowrap"
              >
                {urlLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                Load Network Stream
              </button>
            </div>

            {recentUrls.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {recentUrls.map((item) => (
                  <button
                    key={item.url}
                    type="button"
                    title={item.url}
                    onClick={() => {
                      setUrlInput(item.url)
                      loadUrl(item.url)
                    }}
                    className="flex max-w-[16rem] items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] text-slate-400 transition hover:border-accent-500/40 hover:text-slate-200"
                  >
                    <Star className="h-2.5 w-2.5 shrink-0 text-amber-300/80" />
                    <span className="truncate">{item.name || item.url}</span>
                  </button>
                ))}
              </div>
            )}

            {urlError && (
              <div className="mt-2 flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                <span className="break-all">{urlError}</span>
              </div>
            )}

            {urlMetadata && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.07] px-3 py-2 text-[11px] text-emerald-200">
                <Check className="h-3.5 w-3.5 shrink-0" />
                <span className="font-semibold">{urlMetadata.name}</span>
                {summary ? <span className="text-emerald-300/70">{summary}</span> : null}
                {!isPreviewableUrl(urlMetadata.url) && (
                  <span className="text-amber-300/90">
                    browser preview unavailable - FFmpeg reads this stream directly
                  </span>
                )}
                <button type="button" onClick={confirm} className="btn-ghost ml-auto !px-2 !py-1 text-[11px]">
                  Use this stream
                </button>
              </div>
            )}

            <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
              http, https, rtsp and WebDAV links are passed to FFmpeg with reconnect flags - the
              source is never downloaded first.
            </p>
          </section>
        )}

        <section className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
            <span className="flex items-center gap-2">
              <Server className="h-3.5 w-3.5 text-accent-400" />
              SMB / NAS network share
            </span>
            {!smbAvailable && (
              <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                smbclient unavailable
              </span>
            )}
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <input
              className="field text-xs sm:col-span-2"
              value={shareInput}
              onChange={(event) => setShareInput(event.target.value)}
              placeholder="//192.168.1.100/videos · \\\\SERVER\\Share · smb://nas/media"
              aria-label="SMB share path"
              spellCheck={false}
            />
            <input
              className="field text-xs"
              value={shareUser}
              onChange={(event) => setShareUser(event.target.value)}
              placeholder="Username (optional)"
              aria-label="SMB username"
              autoComplete="off"
            />
            <input
              className="field text-xs"
              type="password"
              value={sharePassword}
              onChange={(event) => setSharePassword(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  connectShare()
                }
              }}
              placeholder="Password (optional)"
              aria-label="SMB password"
              autoComplete="off"
            />
            <div className="flex gap-2 sm:col-span-2">
              <input
                className="field min-w-0 flex-1 text-xs"
                value={shareName}
                onChange={(event) => setShareName(event.target.value)}
                placeholder="Mount name (optional)"
                aria-label="Mount name"
              />
              <button
                type="button"
                onClick={() => connectShare()}
                disabled={connecting || !shareInput.trim()}
                className="btn-primary shrink-0 whitespace-nowrap"
              >
                {connecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
                Connect
              </button>
            </div>
          </div>

          {shareError && (
            <div className="mt-2 flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="break-all">{shareError}</span>
            </div>
          )}

          {savedShares.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {savedShares.map((item) => (
                <button
                  key={item.path}
                  type="button"
                  title={`Reconnect to ${item.path}`}
                  onClick={() => {
                    setShareInput(item.path)
                    setShareUser(item.username || '')
                    setShareName(item.name || '')
                    setSharePassword('')
                    connectShare({ path: item.path, username: item.username || '', name: item.name })
                  }}
                  className="flex max-w-[16rem] items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] text-slate-400 transition hover:border-accent-500/40 hover:text-slate-200"
                >
                  <Star className="h-2.5 w-2.5 shrink-0 text-amber-300/80" />
                  <span className="truncate">{item.name || item.path}</span>
                </button>
              ))}
            </div>
          )}

          {mounts.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {mounts.map((mount) => (
                <div
                  key={mount.id}
                  className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 ${
                    currentPath && currentPath.startsWith(mount.root)
                      ? 'border-accent-500/30 bg-accent-500/[0.06]'
                      : 'border-white/5 bg-white/[0.02]'
                  }`}
                >
                  <Cloud className="h-3.5 w-3.5 shrink-0 text-sky-300/80" />
                  <button
                    type="button"
                    onClick={() => openFolder(mount.root)}
                    className="min-w-0 flex-1 text-left"
                    title={mount.root}
                  >
                    <span className="block truncate text-xs text-slate-200">{mount.name}</span>
                    <span className="block truncate font-mono text-[10px] text-slate-500">
                      {mount.unc || mount.root}
                    </span>
                  </button>
                  <span className="shrink-0 rounded-full border border-white/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-500">
                    {modeLabel(mount.mode)}
                  </span>
                  <button
                    type="button"
                    onClick={() => disconnect(mount.id)}
                    className="shrink-0 rounded p-1 text-slate-500 transition hover:bg-white/5 hover:text-red-300"
                    title="Disconnect share"
                  >
                    <Unplug className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {mounts.length === 0 && (
            <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
              Shares are exposed under the network mount root so the same folder can be used as the
              source video and the destination folder. Windows mapped drives (Z:, Y:, …) that are
              bind-mounted into the container show up in <span className="text-slate-400">Host Drives</span>.
            </p>
          )}
        </section>

        {(listing || listingError || listingLoading) && (
          <section className="rounded-xl border border-white/5 bg-white/[0.02]">
            <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2">
              <button
                type="button"
                onClick={() => listing?.parent && openFolder(listing.parent)}
                disabled={!listing?.parent || listingLoading}
                className="rounded-md p-1 text-slate-400 transition hover:bg-white/5 hover:text-white disabled:opacity-30"
                title="Parent folder"
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </button>
              <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                {(listing?.breadcrumbs || []).map((crumb, index) => (
                  <span key={crumb.path} className="flex shrink-0 items-center gap-1">
                    {index > 0 && <span className="text-slate-700">/</span>}
                    <button
                      type="button"
                      onClick={() => openFolder(crumb.path)}
                      className={`flex items-center gap-1 rounded px-1 py-0.5 text-[11px] transition hover:bg-white/5 ${
                        index === (listing?.breadcrumbs?.length || 0) - 1
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
              {listingLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-accent-400" />}
            </div>

            <div className="max-h-64 min-h-[8rem] overflow-y-auto p-1.5">
              {listingError && (
                <div className="m-1 flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="break-all">{listingError}</span>
                </div>
              )}
              {listing && (
                <FileEntryList
                  entries={listing.entries || []}
                  isSource={isSource}
                  selectedPath={selected?.path}
                  onPick={setSelected}
                  onOpen={(entry) => {
                    if (entry.is_dir) openFolder(entry.path)
                    else if (isSource) onSelect(entry.path)
                  }}
                  videoExtensions={videoExtensions}
                  emptyLabel="This share folder is empty."
                />
              )}
            </div>
          </section>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 bg-studio-950/40 px-4 py-3">
        <div className="min-w-0 text-[11px] text-slate-600">
          {isSource
            ? urlMetadata
              ? `Stream: ${urlMetadata.name}`
              : selectedVideo
                ? `Selected: ${selectedVideo.name}`
                : 'Paste a stream URL or pick a video from a NAS share'
            : `Destination: ${destinationPath || '—'}`}
        </div>
        <button type="button" onClick={confirm} disabled={!canConfirm} className="btn-primary">
          <Check className="h-4 w-4" />
          {isSource ? 'Use this video' : 'Use this folder'}
        </button>
      </div>
    </div>
  )
}
