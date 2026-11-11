import { useCallback, useEffect, useRef, useState } from 'react'
import { CircleAlert, X } from 'lucide-react'
import { api } from './api'
import { dirname, suggestOutputName } from './utils'
import Header from './components/Header'
import FileBrowser from './components/FileBrowser'
import VideoWorkspace from './components/VideoWorkspace'
import ControlPanel from './components/ControlPanel'

const DEFAULT_SETTINGS = {
  speed: 1,
  codec: 'h264',
  outputFilename: '',
  segments: [],
  subtitlePath: '',
  subtitleFontSize: 24,
  rotation: 'none',
  flipHorizontal: false,
  crop: null,
  preferGpu: true,
}

export default function App() {
  const videoRef = useRef(null)

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

  const updateSettings = useCallback((patch) => {
    setSettings((previous) => ({ ...previous, ...patch }))
  }, [])

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

  const selectSource = useCallback(async (path) => {
    setBrowser(null)
    setMetadataLoading(true)
    setNotice(null)
    try {
      const meta = await api.metadata(path)
      setSourcePath(path)
      setMetadata(meta)
      setCurrentTime(0)
      setSettings((previous) => ({
        ...previous,
        rotation: meta.suggested_rotation || 'none',
        flipHorizontal: false,
        segments: [],
        subtitlePath: '',
        crop: null,
        outputFilename: suggestOutputName(meta.name, previous.speed, previous.codec),
      }))
      setTargetDir((previous) => previous || dirname(path))
    } catch (error) {
      setNotice({ type: 'error', message: error.message })
    } finally {
      setMetadataLoading(false)
    }
  }, [])

  const browserInitialPath =
    browser?.mode === 'source'
      ? sourcePath
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
        wsStatus={undefined}
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
            <span className="flex-1">{notice.message}</span>
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

      <main className="mx-auto flex w-full max-w-[1800px] flex-1 flex-col gap-4 p-4 xl:flex-row xl:items-start">
        <div className="flex min-h-[60vh] flex-1 flex-col gap-4">
          <VideoWorkspace
            sourcePath={sourcePath}
            metadata={metadata}
            settings={settings}
            onBrowseSource={() => setBrowser({ mode: 'source' })}
            onTimeUpdate={setCurrentTime}
            currentTime={currentTime}
            videoRef={videoRef}
          />
        </div>

        <aside className="flex w-full shrink-0 flex-col gap-4 xl:w-[400px]">
          <ControlPanel
            metadata={metadata}
            sourcePath={sourcePath}
            targetDir={targetDir}
            hostRoot={appConfig?.host_root}
            onBrowseSource={() => setBrowser({ mode: 'source' })}
            onBrowseTarget={() => setBrowser({ mode: 'target' })}
            settings={settings}
            onChange={updateSettings}
            metadataLoading={metadataLoading}
          />
        </aside>
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
    </div>
  )
}
