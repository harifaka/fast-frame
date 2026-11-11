import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AudioLines,
  Expand,
  FileVideo,
  FolderOpen,
  Gauge,
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { api } from '../api'
import { formatBytes, formatClock, formatSpeed } from '../utils'

const ROTATION_DEGREES = { none: 0, '90_cw': 90, '90_ccw': -90, 180: 180 }

function IconButton({ children, label, onClick, disabled, active }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={`grid h-8 w-8 place-items-center rounded-lg transition disabled:opacity-30 ${
        active
          ? 'bg-accent-500/15 text-accent-400'
          : 'text-slate-400 hover:bg-white/5 hover:text-white'
      }`}
    >
      {children}
    </button>
  )
}

export default function VideoWorkspace({
  sourcePath,
  metadata,
  settings,
  onBrowseSource,
  onTimeUpdate,
  currentTime,
  videoRef,
  children,
}) {
  const containerRef = useRef(null)
  const [box, setBox] = useState({ width: 0, height: 0 })
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [videoError, setVideoError] = useState(null)

  const srcW = Number(metadata?.width) || 0
  const srcH = Number(metadata?.height) || 0
  const rotation = settings?.rotation || 'none'
  const swapped = rotation === '90_cw' || rotation === '90_ccw'
  const orientedW = swapped ? srcH : srcW
  const orientedH = swapped ? srcW : srcH
  const aspect = orientedW > 0 && orientedH > 0 ? orientedW / orientedH : 16 / 9
  const rotationDegrees = ROTATION_DEGREES[rotation] ?? 0

  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined
    const measure = () => {
      const rect = element.getBoundingClientRect()
      if (!rect.width || !rect.height) return
      let width = rect.width
      let height = width / aspect
      if (height > rect.height) {
        height = rect.height
        width = height * aspect
      }
      setBox({ width: Math.floor(width), height: Math.floor(height) })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [aspect])

  useEffect(() => {
    setPlaying(false)
    setDuration(0)
    setVideoError(null)
  }, [sourcePath])

  useEffect(() => {
    const video = videoRef?.current
    if (!video) return
    const rate = Math.min(4, Math.max(0.0625, Number(settings?.speed) || 1))
    if (video.playbackRate !== rate) {
      video.playbackRate = rate
    }
  }, [settings?.speed, sourcePath, videoRef])

  useEffect(() => {
    const video = videoRef?.current
    if (!video) return
    video.volume = volume
    video.muted = muted
  }, [volume, muted, videoRef])

  const togglePlay = useCallback(() => {
    const video = videoRef?.current
    if (!video) return
    if (video.paused) {
      video.play().catch(() => setVideoError('Playback was blocked by the browser'))
    } else {
      video.pause()
    }
  }, [videoRef])

  const seek = useCallback(
    (time) => {
      const video = videoRef?.current
      if (!video) return
      const clamped = Math.max(0, Math.min(duration || video.duration || 0, time))
      video.currentTime = clamped
      onTimeUpdate?.(clamped)
    },
    [duration, onTimeUpdate, videoRef],
  )

  const seekBy = useCallback((delta) => seek((videoRef?.current?.currentTime || 0) + delta), [seek, videoRef])

  useEffect(() => {
    if (!sourcePath) return undefined
    const handleKey = (event) => {
      const tag = event.target?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || event.target?.isContentEditable) {
        return
      }
      if (event.code === 'Space') {
        event.preventDefault()
        togglePlay()
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        seekBy(-5)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        seekBy(5)
      } else if (event.key.toLowerCase() === 'm') {
        setMuted((value) => !value)
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [seekBy, sourcePath, togglePlay])

  const videoSizeStyle = useMemo(() => {
    if (swapped && srcW && srcH) {
      return { width: `${(srcW / srcH) * 100}%`, height: `${(srcH / srcW) * 100}%` }
    }
    return { width: '100%', height: '100%' }
  }, [srcW, srcH, swapped])

  const videoTransform = `translate(-50%, -50%) ${
    settings?.flipHorizontal ? 'scaleX(-1) ' : ''
  }rotate(${rotationDegrees}deg)`

  const isTransformed = rotation !== 'none' || settings?.flipHorizontal

  return (
    <section className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="panel-header shrink-0">
        <div className="panel-title">
          <FileVideo className="h-3.5 w-3.5 text-accent-400" />
          Preview
        </div>
        <div className="flex items-center gap-2">
          {metadata && (
            <>
              <span className="chip">
                {orientedW}×{orientedH}
              </span>
              <span className="chip">{metadata.fps ? `${metadata.fps} fps` : '— fps'}</span>
              <span className="chip">{metadata.codec?.toUpperCase()}</span>
              <span className="chip">
                <AudioLines className="h-3 w-3" />
                {metadata.has_audio ? metadata.audio_codec.toUpperCase() : 'No audio'}
              </span>
            </>
          )}
          {settings?.speed && Number(settings.speed) !== 1 && (
            <span className="chip border-accent-500/30 bg-accent-500/10 text-accent-300">
              <Gauge className="h-3 w-3" />
              Preview {formatSpeed(settings.speed)}×
            </span>
          )}
        </div>
      </div>

      <div
        ref={containerRef}
        className="relative flex min-h-[240px] flex-1 items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_center,#111a29_0%,#04070d_100%)] p-3"
      >
        {!sourcePath ? (
          <div className="flex max-w-sm flex-col items-center gap-3 px-6 py-10 text-center">
            <div className="grid h-14 w-14 place-items-center rounded-2xl border border-white/5 bg-white/[0.03] text-slate-500">
              <FileVideo className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-200">No source selected</h2>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">
                Pick a video straight from a mounted host drive. Fast-Frame streams it in place
                and renders the result into the folder of your choice.
              </p>
            </div>
            <button type="button" onClick={onBrowseSource} className="btn-primary">
              <FolderOpen className="h-4 w-4" />
              Browse host files
            </button>
          </div>
        ) : (
          <div
            className="relative overflow-hidden rounded-lg bg-black shadow-2xl ring-1 ring-white/10"
            style={{ width: box.width || '100%', height: box.height || '60%' }}
          >
            <video
              ref={videoRef}
              key={sourcePath}
              src={api.streamUrl(sourcePath)}
              className="absolute left-1/2 top-1/2 bg-black"
              style={{ ...videoSizeStyle, transform: videoTransform, objectFit: 'fill' }}
              playsInline
              preload="metadata"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onLoadedMetadata={(event) => {
                setDuration(event.currentTarget.duration || 0)
                event.currentTarget.playbackRate = Math.min(
                  4,
                  Math.max(0.0625, Number(settings?.speed) || 1),
                )
              }}
              onTimeUpdate={(event) => onTimeUpdate?.(event.currentTarget.currentTime)}
              onSeeked={(event) => onTimeUpdate?.(event.currentTarget.currentTime)}
              onError={() =>
                setVideoError('This file could not be streamed (unsupported codec or path).')
              }
              onClick={togglePlay}
            />

            {videoError && (
              <div className="absolute inset-x-3 bottom-3 rounded-md bg-red-950/80 px-3 py-2 text-xs text-red-200 ring-1 ring-red-500/30">
                {videoError}
              </div>
            )}

            {isTransformed && (
              <div className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[10px] uppercase tracking-wider text-accent-300 ring-1 ring-white/10">
                {rotation !== 'none' ? rotation.replace('_', ' ').toUpperCase() : 'Flip'}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-white/5 bg-studio-950/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="w-12 shrink-0 text-right font-mono text-[11px] text-slate-400">
            {formatClock(currentTime || 0)}
          </span>
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.01}
            value={Math.min(currentTime || 0, duration || 0)}
            onChange={(event) => seek(Number(event.target.value))}
            disabled={!sourcePath || !duration}
            className="h-1.5 flex-1"
            aria-label="Seek"
          />
          <span className="w-12 shrink-0 font-mono text-[11px] text-slate-500">
            {formatClock(duration)}
          </span>
        </div>

        <div className="mt-1.5 flex items-center gap-1">
          <IconButton label="Back 5 seconds" onClick={() => seekBy(-5)} disabled={!sourcePath}>
            <SkipBack className="h-4 w-4" />
          </IconButton>
          <button
            type="button"
            onClick={togglePlay}
            disabled={!sourcePath}
            className="grid h-9 w-9 place-items-center rounded-full bg-white text-slate-950 transition hover:bg-slate-200 disabled:opacity-30"
            aria-label={playing ? 'Pause' : 'Play'}
          >
            {playing ? (
              <Pause className="h-4 w-4" fill="currentColor" />
            ) : (
              <Play className="ml-0.5 h-4 w-4" fill="currentColor" />
            )}
          </button>
          <IconButton label="Forward 5 seconds" onClick={() => seekBy(5)} disabled={!sourcePath}>
            <SkipForward className="h-4 w-4" />
          </IconButton>

          <div className="mx-1 h-5 w-px bg-white/5" />

          <IconButton label={muted ? 'Unmute' : 'Mute'} onClick={() => setMuted((value) => !value)}>
            {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
          </IconButton>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={(event) => {
              setVolume(Number(event.target.value))
              setMuted(false)
            }}
            className="h-1 w-20"
            aria-label="Volume"
          />

          <div className="ml-auto flex items-center gap-2">
            {metadata && (
              <span className="hidden text-[11px] text-slate-600 md:inline">
                {formatBytes(metadata.size_bytes)} ·{' '}
                {metadata.name.split('.').pop()?.toUpperCase() || metadata.container}
              </span>
            )}
            <IconButton label="Reset preview" onClick={() => seek(0)} disabled={!sourcePath}>
              <RotateCcw className="h-4 w-4" />
            </IconButton>
            <IconButton
              label="Fullscreen"
              onClick={() => containerRef.current?.requestFullscreen?.()}
              disabled={!sourcePath}
            >
              <Expand className="h-4 w-4" />
            </IconButton>
          </div>
        </div>
      </div>

      {children}
    </section>
  )
}
