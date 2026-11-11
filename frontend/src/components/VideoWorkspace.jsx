import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AudioLines,
  Crop,
  Expand,
  FileVideo,
  FlipHorizontal2,
  FolderOpen,
  Gauge,
  Pause,
  Play,
  RectangleHorizontal,
  RectangleVertical,
  Redo2,
  RotateCcw,
  RotateCw,
  SkipBack,
  SkipForward,
  Square,
  Undo2,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { api } from '../api'
import { clamp, formatBytes, formatClock, formatSpeed } from '../utils'

const ROTATION_DEGREES = { none: 0, '90_cw': 90, '90_ccw': -90, 180: 180 }

const CROP_RATIOS = [
  { id: 'free', label: 'Free', value: null, icon: Crop },
  { id: '16:9', label: '16:9', value: 16 / 9, icon: RectangleHorizontal },
  { id: '9:16', label: '9:16', value: 9 / 16, icon: RectangleVertical },
  { id: '1:1', label: '1:1', value: 1, icon: Square },
]

const MIN_CROP = 0.05

function roundCrop(value) {
  return Number(value.toFixed(4))
}

function fitCrop(ratio, frameWidth, frameHeight) {
  const frameAspect = frameWidth > 0 && frameHeight > 0 ? frameWidth / frameHeight : 16 / 9
  let w = 0.9
  let h = 0.9
  if (ratio) {
    h = (w * frameAspect) / ratio
    if (h > 0.9) {
      h = 0.9
      w = (h * ratio) / frameAspect
    }
  }
  return {
    x: roundCrop((1 - w) / 2),
    y: roundCrop((1 - h) / 2),
    w: roundCrop(w),
    h: roundCrop(h),
  }
}

function evenPixel(value) {
  const rounded = Math.round(value)
  return Math.max(2, rounded - (rounded % 2))
}

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

function ToolButton({ children, label, onClick, disabled, active }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      data-active={active}
      className="grid h-7 w-7 place-items-center rounded-md border border-transparent text-slate-400 transition hover:bg-white/5 hover:text-white disabled:opacity-25 data-[active=true]:border-accent-500/30 data-[active=true]:bg-accent-500/15 data-[active=true]:text-accent-300"
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
  onChange,
  onTimeUpdate,
  currentTime,
  videoRef,
  children,
}) {
  const containerRef = useRef(null)
  const stageRef = useRef(null)
  const overlayRef = useRef(null)
  const dragRef = useRef(null)
  const [box, setBox] = useState({ width: 0, height: 0 })
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [videoError, setVideoError] = useState(null)
  const [cropAspect, setCropAspect] = useState('free')

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

  const crop = settings?.crop || null
  const cropPixels = crop
    ? { w: evenPixel(crop.w * orientedW), h: evenPixel(crop.h * orientedH) }
    : null

  const applyRotation = (value) => onChange?.({ rotation: rotation === value ? 'none' : value })
  const resetTransforms = () => {
    setCropAspect('free')
    onChange?.({ rotation: 'none', flipHorizontal: false, crop: null })
  }
  const toggleCrop = () => {
    if (crop) {
      onChange?.({ crop: null })
      return
    }
    const preset = CROP_RATIOS.find((item) => item.id === cropAspect) || CROP_RATIOS[0]
    onChange?.({ crop: fitCrop(preset.value, orientedW, orientedH) })
  }
  const applyCropAspect = (preset) => {
    setCropAspect(preset.id)
    onChange?.({ crop: fitCrop(preset.value, orientedW, orientedH) })
  }

  const startCropDrag = (event, handle) => {
    if (!crop || !stageRef.current) return
    event.preventDefault()
    event.stopPropagation()
    try {
      overlayRef.current?.setPointerCapture?.(event.pointerId)
    } catch {
      /* synthetic or already released pointer */
    }
    dragRef.current = {
      handle,
      rect: stageRef.current.getBoundingClientRect(),
      start: { ...crop },
      startClientX: event.clientX,
      startClientY: event.clientY,
      ratio: CROP_RATIOS.find((preset) => preset.id === cropAspect)?.value ?? null,
    }
  }

  const moveCrop = (event) => {
    const drag = dragRef.current
    if (!drag) return
    const { rect, start, handle, ratio } = drag
    const frameAspect = orientedW > 0 && orientedH > 0 ? orientedW / orientedH : 16 / 9

    if (handle === 'move') {
      const dx = (event.clientX - drag.startClientX) / rect.width
      const dy = (event.clientY - drag.startClientY) / rect.height
      onChange?.({
        crop: {
          x: roundCrop(clamp(start.x + dx, 0, 1 - start.w)),
          y: roundCrop(clamp(start.y + dy, 0, 1 - start.h)),
          w: start.w,
          h: start.h,
        },
      })
      return
    }

    const px = clamp((event.clientX - rect.left) / rect.width, 0, 1)
    const py = clamp((event.clientY - rect.top) / rect.height, 0, 1)
    const anchorX = handle.includes('w') ? start.x + start.w : start.x
    const anchorY = handle.includes('n') ? start.y + start.h : start.y

    let nextW = Math.max(MIN_CROP, Math.abs(px - anchorX))
    let nextH = Math.max(MIN_CROP, Math.abs(py - anchorY))

    if (ratio) {
      nextH = (nextW * frameAspect) / ratio
      const maxHeight = handle.includes('n') ? anchorY : 1 - anchorY
      if (nextH > maxHeight) {
        nextH = maxHeight
        nextW = (nextH * ratio) / frameAspect
      }
      const maxWidth = handle.includes('w') ? anchorX : 1 - anchorX
      if (nextW > maxWidth) {
        nextW = maxWidth
        nextH = (nextW * frameAspect) / ratio
      }
    }

    nextW = clamp(nextW, MIN_CROP, 1)
    nextH = clamp(nextH, MIN_CROP, 1)
    const nextX = clamp(handle.includes('w') ? anchorX - nextW : anchorX, 0, 1 - nextW)
    const nextY = clamp(handle.includes('n') ? anchorY - nextH : anchorY, 0, 1 - nextH)

    onChange?.({
      crop: {
        x: roundCrop(nextX),
        y: roundCrop(nextY),
        w: roundCrop(nextW),
        h: roundCrop(nextH),
      },
    })
  }

  const endCropDrag = (event) => {
    dragRef.current = null
    try {
      if (overlayRef.current?.hasPointerCapture?.(event.pointerId)) {
        overlayRef.current.releasePointerCapture(event.pointerId)
      }
    } catch {
      /* pointer already released */
    }
  }

  return (
    <section className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="panel-header shrink-0">
        <div className="panel-title shrink-0">
          <FileVideo className="h-3.5 w-3.5 text-accent-400" />
          Preview
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={onBrowseSource}
            className="btn-ghost !px-2 !py-1 !text-[11px]"
            title="Browse host drives for a video"
          >
            <FolderOpen className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Browse files</span>
          </button>
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
            ref={stageRef}
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

            {crop && (
              <div
                ref={overlayRef}
                className="pointer-events-none absolute inset-0"
                onPointerMove={moveCrop}
                onPointerUp={endCropDrag}
                onPointerCancel={endCropDrag}
              >
                <div
                  onPointerDown={(event) => startCropDrag(event, 'move')}
                  className="pointer-events-auto absolute cursor-move border border-accent-400/90"
                  style={{
                    left: `${crop.x * 100}%`,
                    top: `${crop.y * 100}%`,
                    width: `${crop.w * 100}%`,
                    height: `${crop.h * 100}%`,
                    boxShadow: '0 0 0 9999px rgba(2, 6, 15, 0.62)',
                  }}
                >
                  <div className="pointer-events-none absolute inset-0 grid grid-cols-3 grid-rows-3">
                    {Array.from({ length: 9 }).map((_, index) => (
                      <div key={index} className="border border-white/10" />
                    ))}
                  </div>
                  {['nw', 'ne', 'sw', 'se'].map((handle) => (
                    <button
                      key={handle}
                      type="button"
                      aria-label={`Resize crop ${handle}`}
                      onPointerDown={(event) => startCropDrag(event, handle)}
                      className={`absolute h-2.5 w-2.5 rounded-sm border border-slate-950 bg-accent-400 ${
                        handle === 'nw'
                          ? '-left-1 -top-1 cursor-nwse-resize'
                          : handle === 'ne'
                            ? '-right-1 -top-1 cursor-nesw-resize'
                            : handle === 'sw'
                              ? '-bottom-1 -left-1 cursor-nesw-resize'
                              : '-bottom-1 -right-1 cursor-nwse-resize'
                      }`}
                    />
                  ))}
                  <span className="pointer-events-none absolute -bottom-5 left-0 rounded bg-black/75 px-1 font-mono text-[9px] text-accent-200">
                    {cropPixels.w}×{cropPixels.h}
                  </span>
                </div>
              </div>
            )}

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

      <div className="flex shrink-0 flex-wrap items-center gap-1 border-t border-white/5 bg-studio-950/40 px-2.5 py-1.5">
        <span className="px-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-600">
          Rotate
        </span>
        <ToolButton
          label="Rotate 90° left"
          active={rotation === '90_ccw'}
          disabled={!sourcePath}
          onClick={() => applyRotation('90_ccw')}
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </ToolButton>
        <ToolButton
          label="Rotate 90° right"
          active={rotation === '90_cw'}
          disabled={!sourcePath}
          onClick={() => applyRotation('90_cw')}
        >
          <RotateCw className="h-3.5 w-3.5" />
        </ToolButton>
        <ToolButton
          label="Rotate 180°"
          active={rotation === '180'}
          disabled={!sourcePath}
          onClick={() => applyRotation('180')}
        >
          <Redo2 className="h-3.5 w-3.5" />
        </ToolButton>
        <ToolButton
          label="Flip horizontally"
          active={Boolean(settings?.flipHorizontal)}
          disabled={!sourcePath}
          onClick={() => onChange?.({ flipHorizontal: !settings?.flipHorizontal })}
        >
          <FlipHorizontal2 className="h-3.5 w-3.5" />
        </ToolButton>

        <div className="mx-1 h-4 w-px bg-white/5" />

        <span className="px-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-600">
          Crop
        </span>
        <ToolButton
          label={crop ? 'Disable crop' : 'Enable crop'}
          active={Boolean(crop)}
          disabled={!sourcePath}
          onClick={toggleCrop}
        >
          <Crop className="h-3.5 w-3.5" />
        </ToolButton>
        {crop &&
          CROP_RATIOS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => applyCropAspect(preset)}
              data-active={cropAspect === preset.id}
              className="rounded-md border border-transparent px-1.5 py-1 text-[10px] font-semibold text-slate-500 transition hover:bg-white/5 hover:text-slate-200 data-[active=true]:border-accent-500/30 data-[active=true]:bg-accent-500/15 data-[active=true]:text-accent-300"
            >
              {preset.label}
            </button>
          ))}

        <div className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          {cropPixels && <span className="font-mono text-slate-400">{cropPixels.w}×{cropPixels.h}</span>}
          {(isTransformed || crop) && (
            <button
              type="button"
              onClick={resetTransforms}
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 font-semibold text-slate-500 transition hover:bg-white/5 hover:text-slate-200"
              title="Reset rotation, flip and crop"
            >
              <Undo2 className="h-3 w-3" />
              Reset
            </button>
          )}
        </div>
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
