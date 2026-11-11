import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CircleAlert, X } from 'lucide-react'
import { api, openProgressSocket } from './api'
import { dirname, formatBytes, isNetworkUrl, suggestOutputName } from './utils'
import Header from './components/Header'
import FileBrowser from './components/FileBrowser'
import VideoWorkspace from './components/VideoWorkspace'
import ControlPanel from './components/ControlPanel'
import CommandBar from './components/CommandBar'
import Timeline from './components/Timeline'
import RenderQueue from './components/RenderQueue'

const DEFAULT_SETTINGS = {
  speed: 1,
  codec: 'h264',
  outputFilename: '',
  filenameCustom: false,
  segments: [],
  subtitlePath: '',
  subtitleFontSize: 24,
  rotation: 'none',
  flipHorizontal: false,
  crop: null,
  preferGpu: true,
}

const ACTIVE_STATUSES = new Set(['processing', 'cancelling'])

export default function App() {
  const videoRef = useRef(null)
  const jobStatusRef = useRef(new Map())

  const [gpuInfo, setGpuInfo] = useState(null)
  const [gpuError, setGpuError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [appConfig, setAppConfig] = useState(null)

  const [browser, setBrowser] = useState(null)
  const [sourcePath, setSourcePath] = useState(null)
  const [metadata, setMetadata] = useState(null)
  const [metadataLoading, setMetadataLoading] = useState(false)
  const [targetDir, setTargetDir] = useState(
    () => window.localStorage.getItem('fastframe.targetDir') || '',
  )
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)
  const [currentTime, setCurrentTime] = useState(0)
  const [notice, setNotice] = useState(null)

  const [jobs, setJobs] = useState([])
  const [wsStatus, setWsStatus] = useState('connecting')
  const [submitting, setSubmitting] = useState(false)
  const [subtitleFiles, setSubtitleFiles] = useState([])
  const [subtitlesLoading, setSubtitlesLoading] = useState(false)
  const [queueOpen, setQueueOpen] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [configRequest, setConfigRequest] = useState(null)

  const openConfig = useCallback((section = null) => {
    setQueueOpen(false)
    setConfigRequest({ id: section, nonce: Date.now() })
    setConfigOpen(true)
  }, [])

  const closeConfig = useCallback(() => setConfigOpen(false), [])

  const updateSettings = useCallback(
    (patch) => {
      setSettings((previous) => {
        const next = { ...previous, ...patch }
        const speedOrCodecChanged =
          patch.speed !== undefined || patch.codec !== undefined
        if (speedOrCodecChanged && !next.filenameCustom && metadata) {
          next.outputFilename = suggestOutputName(metadata.name, next.speed, next.codec)
        }
        return next
      })
    },
    [metadata],
  )

  const loadGpu = useCallback(async (refresh = false) => {
    setRefreshing(true)
    setGpuError(null)
    try {
      const info = await api.gpu(refresh)
      setGpuInfo(info)
    } catch (error) {
      setGpuError(error.message)
      setGpuInfo(null)
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    loadGpu()
    api
      .config()
      .then(setAppConfig)
      .catch((error) => setNotice({ type: 'error', message: error.message }))
  }, [loadGpu])

  useEffect(() => {
    if (targetDir) {
      window.localStorage.setItem('fastframe.targetDir', targetDir)
    }
  }, [targetDir])

  const handleProgressEvent = useCallback((event) => {
    if (event.type === 'snapshot') {
      const snapshotJobs = event.jobs || []
      setJobs(snapshotJobs)
      snapshotJobs.forEach((job) => jobStatusRef.current.set(job.id, job.status))
      return
    }
    if (event.type !== 'job' || !event.job) return

    const incoming = event.job
    const previousStatus = jobStatusRef.current.get(incoming.id)
    jobStatusRef.current.set(incoming.id, incoming.status)

    setJobs((previous) => {
      const index = previous.findIndex((job) => job.id === incoming.id)
      if (index === -1) return [...previous, incoming]
      const next = [...previous]
      next[index] = incoming
      return next
    })

    if (incoming.status === 'completed' && previousStatus !== 'completed') {
      setNotice({
        type: 'success',
        message: `Render complete → ${incoming.output_path} (${formatBytes(incoming.output_size_bytes)})`,
      })
    } else if (incoming.status === 'failed' && previousStatus !== 'failed') {
      setNotice({ type: 'error', message: `Render failed: ${incoming.error}` })
    } else if (incoming.status === 'cancelled' && previousStatus !== 'cancelled') {
      setNotice({ type: 'error', message: 'Render cancelled' })
    }
  }, [])

  useEffect(() => {
    api
      .queue()
      .then((snapshot) => {
        const snapshotJobs = snapshot.jobs || []
        setJobs(snapshotJobs)
        snapshotJobs.forEach((job) => jobStatusRef.current.set(job.id, job.status))
      })
      .catch(() => {
        /* the WebSocket snapshot will populate the list */
      })
    return openProgressSocket(handleProgressEvent, setWsStatus)
  }, [handleProgressEvent])

  const loadSubtitles = useCallback(async (videoPath) => {
    if (!videoPath || isNetworkUrl(videoPath)) {
      setSubtitleFiles([])
      return []
    }
    setSubtitlesLoading(true)
    try {
      const listing = await api.list(dirname(videoPath))
      const files = (listing.entries || []).filter((entry) => entry.kind === 'subtitle')
      setSubtitleFiles(files)
      return files
    } catch {
      setSubtitleFiles([])
      return []
    } finally {
      setSubtitlesLoading(false)
    }
  }, [])

  const selectSource = useCallback(
    async (path) => {
      setBrowser(null)
      setMetadataLoading(true)
      setNotice(null)
      try {
        const meta = await api.metadata(path)
        const files = await loadSubtitles(path)
        const stem = meta.name.replace(/\.[^.]+$/, '').toLowerCase()
        const match =
          files.find((file) => file.name.toLowerCase() === `${stem}.srt`) ||
          files.find((file) => file.name.toLowerCase().startsWith(stem))

        setSourcePath(path)
        setMetadata(meta)
        setCurrentTime(0)
        setSettings((previous) => ({
          ...previous,
          rotation: meta.suggested_rotation || 'none',
          flipHorizontal: false,
          segments: [],
          subtitlePath: match ? match.path : '',
          crop: null,
          filenameCustom: false,
          outputFilename: suggestOutputName(meta.name, previous.speed, previous.codec),
        }))
        setTargetDir((previous) => previous || (isNetworkUrl(path) ? previous : dirname(path)))
      } catch (error) {
        setNotice({ type: 'error', message: error.message })
      } finally {
        setMetadataLoading(false)
      }
    },
    [loadSubtitles],
  )

  const addSegment = useCallback((segment) => {
    setSettings((previous) => ({
      ...previous,
      segments: [...previous.segments, segment].sort((a, b) => a.start - b.start),
    }))
  }, [])

  const removeSegment = useCallback((index) => {
    setSettings((previous) => ({
      ...previous,
      segments: previous.segments.filter((_, position) => position !== index),
    }))
  }, [])

  const clearSegments = useCallback(() => {
    setSettings((previous) => ({ ...previous, segments: [] }))
  }, [])

  const seekTo = useCallback((time) => {
    if (!videoRef.current) {
      setCurrentTime(time)
      return
    }
    videoRef.current.currentTime = time
    setCurrentTime(time)
  }, [])

  const submitRender = useCallback(
    async ({ renderNow = true } = {}) => {
      if (!sourcePath || !targetDir) {
        setNotice({
          type: 'error',
          message: 'Select a source video and a destination folder first.',
        })
        return
      }
      setSubmitting(true)
      try {
        const job = await api.addToQueue({
          input_path: sourcePath,
          output_dir: targetDir,
          output_filename: settings.outputFilename || null,
          render_now: renderNow,
          options: {
            speed: Number(settings.speed) || 1,
            codec: settings.codec,
            segments: settings.segments,
            subtitle_path: settings.subtitlePath || null,
            subtitle_font_size: Number(settings.subtitleFontSize) || 24,
            rotation: settings.rotation,
            flip_horizontal: settings.flipHorizontal,
            crop: settings.crop,
            prefer_gpu: settings.preferGpu,
          },
        })
        jobStatusRef.current.set(job.id, job.status)
        setJobs((previous) =>
          previous.some((existing) => existing.id === job.id) ? previous : [...previous, job],
        )
        setNotice({
          type: 'success',
          message: `${renderNow ? 'Rendering' : 'Queued'} ${job.output_filename}`,
        })
        if (!renderNow) setQueueOpen(true)
      } catch (error) {
        setNotice({ type: 'error', message: error.message })
      } finally {
        setSubmitting(false)
      }
    },
    [settings, sourcePath, targetDir],
  )

  const cancelJob = useCallback(async (jobId) => {
    try {
      const job = await api.cancelJob(jobId)
      jobStatusRef.current.set(job.id, job.status)
      setJobs((previous) =>
        previous.map((existing) => (existing.id === job.id ? job : existing)),
      )
    } catch (error) {
      setNotice({ type: 'error', message: error.message })
    }
  }, [])

  const dismissJob = useCallback(async (jobId) => {
    try {
      await api.cancelJob(jobId)
      jobStatusRef.current.delete(jobId)
      setJobs((previous) => previous.filter((job) => job.id !== jobId))
    } catch (error) {
      setNotice({ type: 'error', message: error.message })
    }
  }, [])

  const clearCompleted = useCallback(async () => {
    try {
      await api.clearCompleted()
      setJobs((previous) =>
        previous.filter((job) => !['completed', 'failed', 'cancelled'].includes(job.status)),
      )
    } catch (error) {
      setNotice({ type: 'error', message: error.message })
    }
  }, [])

  const activeJob = useMemo(
    () => jobs.find((job) => ACTIVE_STATUSES.has(job.status)) || null,
    [jobs],
  )
  const latestJob = useMemo(() => {
    if (jobs.length === 0) return null
    return [...jobs].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0]
  }, [jobs])
  const progressJob = activeJob || latestJob

  const queuedCount = jobs.filter((job) => job.status === 'queued').length
  const activeCount = activeJob ? 1 : 0

  const browserInitialPath =
    browser?.mode === 'source'
      ? sourcePath && !isNetworkUrl(sourcePath)
        ? dirname(sourcePath)
        : appConfig?.host_root
      : targetDir || appConfig?.host_root

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        gpu={gpuInfo}
        gpuError={gpuError}
        refreshing={refreshing}
        onRefresh={() => loadGpu(true)}
        wsStatus={wsStatus}
        queuedCount={queuedCount}
        activeCount={activeCount}
        queueOpen={queueOpen}
        onToggleQueue={() => {
          setConfigOpen(false)
          setQueueOpen((value) => !value)
        }}
      />

      {notice && (
        <div className="mx-auto w-full max-w-[1800px] px-4 pt-3">
          <div
            className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-xs ${
              notice.type === 'error'
                ? 'border-red-500/25 bg-red-500/10 text-red-200'
                : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
            }`}
          >
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1 break-all">{notice.message}</span>
            <button
              type="button"
              onClick={() => setNotice(null)}
              className="rounded p-0.5 opacity-70 transition hover:opacity-100"
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      <main className="mx-auto flex w-full max-w-[1800px] flex-1 flex-col gap-3 p-4">
        <CommandBar
          metadata={metadata}
          settings={settings}
          targetDir={targetDir}
          hostRoot={appConfig?.host_root}
          activeJob={progressJob}
          onCancelJob={cancelJob}
          onBrowseTarget={() => {
            setConfigOpen(false)
            setBrowser({ mode: 'target' })
          }}
          onOpenConfig={openConfig}
          configOpen={configOpen}
          onSubmit={submitRender}
          submitting={submitting}
          metadataLoading={metadataLoading}
          canRender={Boolean(sourcePath && targetDir && metadata)}
        />

        <div className="flex min-h-[60vh] flex-1 flex-col">
          <VideoWorkspace
            sourcePath={sourcePath}
            metadata={metadata}
            settings={settings}
            onBrowseSource={() => setBrowser({ mode: 'source' })}
            onChange={updateSettings}
            onTimeUpdate={setCurrentTime}
            currentTime={currentTime}
            videoRef={videoRef}
          >
            <Timeline
              duration={metadata?.duration || 0}
              currentTime={currentTime}
              segments={settings.segments}
              speed={settings.speed}
              disabled={!sourcePath}
              onSeek={seekTo}
              onAddSegment={addSegment}
              onRemoveSegment={removeSegment}
              onClearSegments={clearSegments}
            />
          </VideoWorkspace>
        </div>
      </main>

      <FileBrowser
        open={Boolean(browser)}
        mode={browser?.mode || 'source'}
        initialPath={browserInitialPath}
        root={appConfig?.host_root}
        onClose={() => setBrowser(null)}
        onSelect={(path) => {
          if (browser?.mode === 'source') selectSource(path)
          else {
            setTargetDir(path)
            setBrowser(null)
          }
        }}
        videoExtensions={appConfig?.video_extensions || ['.mp4', '.mkv', '.avi']}
        subtitleExtensions={appConfig?.subtitle_extensions || ['.srt']}
      />

      <RenderQueue
        open={queueOpen}
        jobs={jobs}
        wsStatus={wsStatus}
        onClose={() => setQueueOpen(false)}
        onCancel={cancelJob}
        onDismiss={dismissJob}
        onClearCompleted={clearCompleted}
      />

      <ControlPanel
        open={configOpen}
        onClose={closeConfig}
        focusSection={configRequest}
        metadata={metadata}
        sourcePath={sourcePath}
        targetDir={targetDir}
        hostRoot={appConfig?.host_root}
        onBrowseSource={() => {
          setConfigOpen(false)
          setBrowser({ mode: 'source' })
        }}
        onBrowseTarget={() => setBrowser({ mode: 'target' })}
        settings={settings}
        onChange={updateSettings}
        gpu={gpuInfo}
        onSubmit={submitRender}
        submitting={submitting}
        canRender={Boolean(sourcePath && targetDir && metadata)}
        subtitleFiles={subtitleFiles}
        onRefreshSubtitles={() => loadSubtitles(sourcePath)}
        subtitlesLoading={subtitlesLoading}
      />
    </div>
  )
}
