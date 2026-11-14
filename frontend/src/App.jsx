import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CircleAlert, X } from 'lucide-react'
import { api, openProgressSocket } from './api'
import { EDITION_STANDARD, isLiteEdition, normalizeEdition } from './edition'
import { dirname, formatBytes, isNetworkUrl, suggestOutputName } from './utils'
import Header from './components/Header'
import FileBrowser from './components/FileBrowser'
import VideoWorkspace from './components/VideoWorkspace'
import ControlPanel from './components/ControlPanel'
import CommandBar from './components/CommandBar'
import Timeline from './components/Timeline'
import RenderQueue from './components/RenderQueue'

// Three.js is heavy: only pull the WebGL viewer into the bundle on demand.
const PointCloudViewer = lazy(() => import('./components/PointCloudViewer'))

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
  autoAlignHorizon: true,
}

const ACTIVE_STATUSES = new Set(['processing', 'cancelling'])

const POINT_CLOUD_HISTORY_KEY = 'fastframe.pointCloudHistory'
const MAX_POINT_CLOUD_HISTORY = 5

function readPointCloudHistory() {
  try {
    const raw = window.localStorage.getItem(POINT_CLOUD_HISTORY_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? parsed.filter((item) => typeof item === 'string' && item.trim())
      : []
  } catch {
    return []
  }
}

function persistPointCloudHistory(entries) {
  try {
    window.localStorage.setItem(POINT_CLOUD_HISTORY_KEY, JSON.stringify(entries))
  } catch {
    /* storage disabled or full - the in-memory list still works */
  }
}

function defaultPointCloudPath(sourcePath, targetDir) {
  if (!sourcePath || !targetDir) return ''
  const stem = (sourcePath.split(/[\\/]/).pop() || 'pointcloud').replace(/\.[^.]+$/, '')
  return `${targetDir.replace(/[\\/]+$/, '')}/${stem}.ply`
}

export default function App() {
  const videoRef = useRef(null)
  const jobStatusRef = useRef(new Map())

  const [gpuInfo, setGpuInfo] = useState(null)
  const [gpuError, setGpuError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [appConfig, setAppConfig] = useState(null)
  const [edition, setEdition] = useState(EDITION_STANDARD)

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
  const [aiView, setAiView] = useState(null)
  const [aiViewSaving, setAiViewSaving] = useState(false)
  const [aiViewError, setAiViewError] = useState(null)
  const [aiAnalyzing, setAiAnalyzing] = useState(false)
  const [aiAnalyzeError, setAiAnalyzeError] = useState(null)
  const [aiAutoAligning, setAiAutoAligning] = useState(false)
  const [aiAutoAlignError, setAiAutoAlignError] = useState(null)
  const [pointCloud, setPointCloud] = useState(null)
  const [pointCloudError, setPointCloudError] = useState(null)
  const [pointCloudView, setPointCloudView] = useState(null)
  const [pointCloudHistory, setPointCloudHistory] = useState(readPointCloudHistory)
  const settingsRef = useRef(settings)

  // The Lite image is built without numpy/onnxruntime: every AI panel and
  // polling loop is skipped, leaving cutting, speed, 360° preview and crop.
  const aiEnabled = !isLiteEdition(edition)

  useEffect(() => {
    settingsRef.current = settings
  }, [settings])

  const openConfig = useCallback((section = null) => {
    setQueueOpen(false)
    setConfigRequest({ id: section, nonce: Date.now() })
    setConfigOpen(true)
  }, [])

  const closeConfig = useCallback(() => setConfigOpen(false), [])

  const rememberPointCloud = useCallback((path) => {
    const target = (path || '').trim()
    if (!target) return
    setPointCloudHistory((previous) => {
      if (previous[0] === target) return previous
      const next = [target, ...previous.filter((item) => item !== target)].slice(
        0,
        MAX_POINT_CLOUD_HISTORY,
      )
      persistPointCloudHistory(next)
      return next
    })
  }, [])

  const openPointCloudViewer = useCallback(
    (path) => {
      const target = (path || '').trim()
      if (!target) return
      setPointCloudView(target)
      rememberPointCloud(target)
    },
    [rememberPointCloud],
  )

  const closePointCloudViewer = useCallback(() => setPointCloudView(null), [])

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

  const aiViewBusyRef = useRef(false)

  const refreshAiView = useCallback(async (force = false) => {
    if (aiViewBusyRef.current && !force) return null
    try {
      const state = await api.aiViewState()
      setAiView(state)
      setAiViewError(null)
      return state
    } catch (error) {
      setAiViewError(error.message)
      return null
    }
  }, [])

  const updateAiView = useCallback(async (patch) => {
    if (!patch) return null
    aiViewBusyRef.current = true
    setAiViewSaving(true)
    try {
      const state = await api.updateAiViewState({ controller: 'ui', ...patch })
      setAiView(state)
      setAiViewError(null)
      return state
    } catch (error) {
      setAiViewError(error.message)
      return null
    } finally {
      aiViewBusyRef.current = false
      setAiViewSaving(false)
    }
  }, [])

  const sendAiCommand = useCallback(async (command) => {
    aiViewBusyRef.current = true
    setAiViewSaving(true)
    try {
      const state = await api.aiCommand(command)
      setAiView(state)
      setAiViewError(null)
      return state
    } catch (error) {
      setAiViewError(error.message)
      return null
    } finally {
      aiViewBusyRef.current = false
      setAiViewSaving(false)
    }
  }, [])

  const resetAiView = useCallback(async () => {
    aiViewBusyRef.current = true
    setAiViewSaving(true)
    try {
      const state = await api.resetAiViewState()
      setAiView(state)
      setAiViewError(null)
      return state
    } catch (error) {
      setAiViewError(error.message)
      return null
    } finally {
      aiViewBusyRef.current = false
      setAiViewSaving(false)
    }
  }, [])

  const analyzeAiTracking = useCallback(
    async (level = null) => {
      if (!sourcePath) {
        setAiAnalyzeError('Select a source video before running YOLO auto-framing.')
        return null
      }
      setAiAnalyzing(true)
      setAiAnalyzeError(null)
      try {
        const result = await api.analyzeAiTracking({
          source_path: sourcePath,
          level: level ?? aiView?.ai_tracking_level ?? 2,
        })
        if (result?.view_state) setAiView(result.view_state)
        const analysis = result?.analysis || {}
        setNotice({
          type: 'success',
          message: `YOLO auto-framing → ${analysis.keyframe_count ?? 0} keyframes from ${
            analysis.frames_with_subject ?? 0
          }/${analysis.frames_analyzed ?? 0} sampled frames`,
        })
        return result
      } catch (error) {
        setAiAnalyzeError(error.message)
        return null
      } finally {
        setAiAnalyzing(false)
      }
    },
    [aiView?.ai_tracking_level, sourcePath],
  )

  const runAutoAlign = useCallback(
    async (path = sourcePath, apply = true) => {
      if (!aiEnabled) return null
      if (!path) {
        setAiAutoAlignError('Select a 360° source before auto-aligning the horizon.')
        return null
      }
      setAiAutoAligning(true)
      setAiAutoAlignError(null)
      try {
        const result = await api.autoAlignHorizon({ source_path: path, apply, samples: 3 })
        if (result?.view_state) setAiView(result.view_state)
        return result
      } catch (error) {
        setAiAutoAlignError(error.message)
        return null
      } finally {
        setAiAutoAligning(false)
      }
    },
    [aiEnabled, sourcePath],
  )

  const refreshPointCloud = useCallback(async () => {
    try {
      const status = await api.pointCloudStatus()
      setPointCloud(status)
      setPointCloudError(null)
      return status
    } catch (error) {
      setPointCloudError(error.message)
      return null
    }
  }, [])

  const generatePointCloud = useCallback(
    async ({ output_path, sample_rate } = {}) => {
      if (!sourcePath) {
        setPointCloudError('Select a source video before generating a point cloud.')
        return null
      }
      const fallback = defaultPointCloudPath(sourcePath, targetDir)
      const destination = (output_path || fallback || '').trim()
      if (!destination) {
        setPointCloudError('Choose an output folder before generating a point cloud.')
        return null
      }
      setPointCloudError(null)
      try {
        const status = await api.generatePointCloud({
          source_path: sourcePath,
          output_path: destination,
          sample_rate: Number(sample_rate) || 1,
        })
        setPointCloud(status)
        setNotice({
          type: 'success',
          message: `Point cloud export started at ${Number(sample_rate) || 1} fps → ${status?.output || destination}`,
        })
        return status
      } catch (error) {
        setPointCloudError(error.message)
        return null
      }
    },
    [sourcePath, targetDir],
  )

  const loadGpu = useCallback(async (refresh = false) => {    setRefreshing(true)
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
      .then((config) => {
        setAppConfig(config)
        setEdition(normalizeEdition(config?.edition))
      })
      .catch((error) => setNotice({ type: 'error', message: error.message }))
  }, [loadGpu])

  useEffect(() => {
    if (!aiEnabled) return undefined
    refreshAiView(true)
    const timer = window.setInterval(() => refreshAiView(), 4000)
    return () => window.clearInterval(timer)
  }, [aiEnabled, refreshAiView])

  useEffect(() => {
    if (!aiEnabled) return undefined
    refreshPointCloud()
    const timer = window.setInterval(() => refreshPointCloud(), 2500)
    return () => window.clearInterval(timer)
  }, [aiEnabled, refreshPointCloud])

  useEffect(() => {
    if (pointCloud?.state === 'completed' && pointCloud.output) {
      rememberPointCloud(pointCloud.output)
    }
  }, [pointCloud?.state, pointCloud?.output, rememberPointCloud])

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
        const sphericalSource = Boolean(meta.spherical)
        await updateAiView({
          active: sphericalSource,
          source_path: path,
          pan: 0,
          tilt: 0,
          roll: 0,
          zoom: 1,
          fov: 90,
          tracking_mode: sphericalSource ? 'manual' : 'off',
          target: '',
          keyframe_offsets: [],
          ai_tracking_enabled: false,
          tracking_keyframes: [],
          tracking_analysis: null,
          horizon_alignment: null,
        })
        if (sphericalSource && aiEnabled && settingsRef.current.autoAlignHorizon) {
          runAutoAlign(path, true)
        }
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
    [aiEnabled, loadSubtitles, runAutoAlign, updateAiView],
  )

  const previousAutoAlign = useRef(settings.autoAlignHorizon)
  useEffect(() => {
    const was = previousAutoAlign.current
    previousAutoAlign.current = settings.autoAlignHorizon
    if (was === settings.autoAlignHorizon || !metadata?.spherical || !sourcePath) return
    if (settings.autoAlignHorizon) {
      runAutoAlign(sourcePath, true)
    } else {
      updateAiView({ roll: 0 })
    }
  }, [metadata?.spherical, runAutoAlign, settings.autoAlignHorizon, sourcePath, updateAiView])

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

  const defaultPointCloudOutput = useMemo(
    () => defaultPointCloudPath(sourcePath, targetDir),
    [sourcePath, targetDir],
  )

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
            roll: metadata?.spherical ? Number(aiView?.roll) || 0 : 0,
            ai_tracking:
              aiEnabled &&
              aiView?.ai_tracking_enabled &&
              (aiView?.tracking_keyframes?.length || 0) > 1
                ? {
                    enabled: true,
                    level: Number(aiView.ai_tracking_level) || 2,
                    mode: metadata?.spherical ? 'spherical' : 'rectilinear',
                    keyframes: aiView.tracking_keyframes,
                  }
                : null,
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
    [aiEnabled, aiView, metadata, settings, sourcePath, targetDir],
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
      : browser?.mode === 'point-cloud'
        ? dirname(pointCloudHistory[0] || pointCloud?.output || '') ||
          targetDir ||
          appConfig?.host_root
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
            aiView={aiView}
            onAiViewChange={updateAiView}
            aiEnabled={aiEnabled}
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
        onViewPointCloud={aiEnabled ? openPointCloudViewer : undefined}
        allowPointCloud={aiEnabled}
        suppressEscape={Boolean(pointCloudView)}
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
        edition={edition}
        focusSection={configRequest}
        metadata={metadata}
        sourcePath={sourcePath}
        targetDir={targetDir}
        hostRoot={appConfig?.host_root}
        onBrowseSource={() => {
          setConfigOpen(false)
          setBrowser({ mode: 'source' })
        }}
        onBrowsePointCloud={() => {
          setConfigOpen(false)
          setBrowser({ mode: 'point-cloud' })
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
        aiView={aiView}
        aiViewSaving={aiViewSaving}
        aiViewError={aiViewError}
        onAiViewChange={updateAiView}
        onAiViewCommand={sendAiCommand}
        onAiViewReset={resetAiView}
        onAiTrackingAnalyze={analyzeAiTracking}
        aiTrackingAnalyzing={aiAnalyzing}
        aiTrackingError={aiAnalyzeError}
        aiViewEndpoint={appConfig?.ai_view?.view_state_endpoint || '/api/ai/view-state'}
        currentTime={currentTime}
        onAutoAlign={() => runAutoAlign(sourcePath, true)}
        autoAligning={aiAutoAligning}
        autoAlignError={aiAutoAlignError}
        pointCloud={pointCloud}
        pointCloudError={pointCloudError}
        pointCloudOutput={defaultPointCloudOutput}
        onPointCloudGenerate={generatePointCloud}
        onPointCloudRefresh={refreshPointCloud}
        onViewPointCloud={openPointCloudViewer}
        pointCloudHistory={pointCloudHistory}
        suppressEscape={Boolean(pointCloudView)}
      />

      {aiEnabled && pointCloudView ? (
        <Suspense
          fallback={
            <div className="fixed inset-0 z-[60] grid place-items-center bg-black/95 text-xs text-slate-400">
              Loading 3D engine…
            </div>
          }
        >
          <PointCloudViewer open path={pointCloudView} onClose={closePointCloudViewer} />
        </Suspense>
      ) : null}
    </div>
  )
}
