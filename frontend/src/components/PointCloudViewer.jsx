import { Canvas, useLoader, useThree } from '@react-three/fiber'
import { FlyControls } from '@react-three/drei'
import { Component, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { Box, Loader2, TriangleAlert, X } from 'lucide-react'
import { Vector3 } from 'three'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { api } from '../api'
import { basename } from '../utils'

/**
 * Renders the loaded PLY as a vertex-coloured point cloud.
 *
 * The cloud is centred by offsetting the `points` object instead of mutating
 * the cached geometry, so re-opening the same file never double-translates it.
 */
function PointCloudModel({ url, pointSize, onReady }) {
  const geometry = useLoader(PLYLoader, url)

  const shape = useMemo(() => {
    geometry.computeBoundingBox()
    const center = new Vector3()
    const size = new Vector3()
    if (geometry.boundingBox) {
      geometry.boundingBox.getCenter(center)
      geometry.boundingBox.getSize(size)
    }
    return {
      center,
      radius: Math.max(size.length() / 2, 1e-4),
      hasColors: Boolean(geometry.attributes.color),
      count: geometry.attributes.position?.count || 0,
    }
  }, [geometry])

  useEffect(() => {
    onReady?.({ radius: shape.radius, count: shape.count, hasColors: shape.hasColors })
  }, [onReady, shape])

  return (
    <points
      geometry={geometry}
      position={[-shape.center.x, -shape.center.y, -shape.center.z]}
      frustumCulled={false}
    >
      <pointsMaterial
        vertexColors={shape.hasColors}
        color={shape.hasColors ? '#ffffff' : '#7dd3fc'}
        size={pointSize}
        sizeAttenuation
      />
    </points>
  )
}

/** Places the camera at a sane distance once the cloud extents are known. */
function FrameCamera({ radius }) {
  const camera = useThree((state) => state.camera)

  useEffect(() => {
    const distance = Math.max(radius * 2.4, 0.01)
    camera.position.set(0, 0, distance)
    camera.near = Math.max(radius / 100, 1e-4)
    camera.far = radius * 120
    camera.updateProjectionMatrix()
    camera.lookAt(0, 0, 0)
  }, [camera, radius])

  return null
}

/** Turns a failed PLY fetch/parse into an inline message instead of a blank canvas. */
class LoadErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error) {
    this.props.onError?.(error)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid h-full place-items-center p-8 text-center">
          <div className="max-w-md">
            <TriangleAlert className="mx-auto h-8 w-8 text-red-400" />
            <p className="mt-2 text-sm text-red-200">Could not load this point cloud.</p>
            <p className="mt-1 break-words text-[11px] text-slate-500">
              {this.state.error?.message || String(this.state.error)}
            </p>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

function HudSlider({ label, value, onChange, min, max, step }) {
  return (
    <label className="pointer-events-auto flex items-center gap-2 text-[10px] uppercase tracking-wider text-slate-400">
      <span className="w-16 shrink-0">{label}</span>
      <input
        type="range"
        className="h-1 w-24 cursor-pointer accent-cyan-400"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  )
}

/**
 * Full-screen WebGL fly-through of a generated `.ply` cloud.
 *
 * Controls come from FlyControls: WASD to move, mouse to look, Q/E to roll and
 * Shift to slow down. The overlay listens for Escape to close.
 */
export default function PointCloudViewer({ open, path, onClose }) {
  const [stats, setStats] = useState(null)
  const [failed, setFailed] = useState(false)
  const [sizeFactor, setSizeFactor] = useState(1)
  const [speedFactor, setSpeedFactor] = useState(1)

  const url = path ? api.fileUrl(path) : null

  useEffect(() => {
    if (!open) return undefined
    const handleKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose?.()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [open, onClose])

  useEffect(() => {
    setStats(null)
    setFailed(false)
    setSizeFactor(1)
    setSpeedFactor(1)
  }, [path, open])

  const handleReady = useCallback((next) => setStats(next), [])
  const handleFailure = useCallback(() => setFailed(true), [])

  if (!open || !path || !url) return null

  const radius = stats?.radius || 1
  const pointSize = Math.max(radius * 0.004 * sizeFactor, 1e-4)
  const movementSpeed = Math.max(radius * 0.6 * speedFactor, 0.02)

  return (
    <div className="fixed inset-0 z-[60] bg-black/95">
      <LoadErrorBoundary key={url} onError={handleFailure}>
        <Canvas
          camera={{ fov: 60, near: 0.01, far: 1000, position: [0, 0, 5] }}
          dpr={[1, 2]}
          gl={{ antialias: false, powerPreference: 'high-performance' }}
        >
          <color attach="background" args={['#03050a']} />
          <Suspense fallback={null}>
            <PointCloudModel url={url} pointSize={pointSize} onReady={handleReady} />
            <FrameCamera radius={radius} />
            <FlyControls
              movementSpeed={movementSpeed}
              rollSpeed={0.5}
              dragToLook={false}
              makeDefault
            />
          </Suspense>
        </Canvas>
      </LoadErrorBoundary>

      <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="pointer-events-auto flex max-w-[70%] items-center gap-2 rounded-xl border border-white/10 bg-studio-900/85 px-3 py-2 backdrop-blur">
            <Box className="h-4 w-4 shrink-0 text-cyan-300" />
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-white">
                {basename(path) || 'Point cloud'}
              </p>
              <p className="truncate font-mono text-[10px] text-slate-500">{path}</p>
            </div>
            <span className="chip shrink-0 border-cyan-500/25 bg-cyan-500/10 text-cyan-200">
              {stats
                ? `${stats.count.toLocaleString()} pts`
                : failed
                  ? 'Load failed'
                  : 'Loading…'}
              {stats && !stats.hasColors ? ' · depth tint' : ''}
            </span>
          </div>
          <button
            type="button"
            onClick={() => onClose?.()}
            className="pointer-events-auto btn-ghost !p-2"
            title="Close viewer (ESC)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {!stats && !failed && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-studio-900/80 px-3 py-2 text-xs text-slate-300 backdrop-blur">
              <Loader2 className="h-4 w-4 animate-spin text-cyan-300" />
              Loading point cloud…
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="pointer-events-auto rounded-xl border border-white/10 bg-studio-900/85 px-3 py-2 backdrop-blur">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-cyan-300">
              🎮 Fly mode
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">
              <span className="font-mono text-slate-200">WASD</span> to move ·{' '}
              <span className="font-mono text-slate-200">Mouse</span> to look ·{' '}
              <span className="font-mono text-slate-200">Q/E</span> roll ·{' '}
              <span className="font-mono text-slate-200">R/F</span> up/down ·{' '}
              <span className="font-mono text-slate-200">Shift</span> slow ·{' '}
              <span className="font-mono text-slate-200">ESC</span> to close
            </p>
          </div>

          <div className="pointer-events-auto flex flex-col gap-1.5 rounded-xl border border-white/10 bg-studio-900/85 px-3 py-2 backdrop-blur">
            <HudSlider
              label="Point size"
              value={sizeFactor}
              onChange={setSizeFactor}
              min={0.25}
              max={5}
              step={0.25}
            />
            <HudSlider
              label="Speed"
              value={speedFactor}
              onChange={setSpeedFactor}
              min={0.25}
              max={5}
              step={0.25}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
