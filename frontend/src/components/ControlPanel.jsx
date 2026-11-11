import {
  AudioLines,
  Bookmark,
  Captions,
  Clock,
  Cpu,
  Film,
  FolderOpen,
  Gauge,
  HardDrive,
  Info,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  Scissors,
  Sparkles,
  Trash2,
  Zap,
} from 'lucide-react'
import { useState } from 'react'
import { formatBytes, formatClock, formatSpeed } from '../utils'
import JobProgress from './JobProgress'

const SPEED_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3]
const FONT_SIZES = [
  { id: 'small', label: 'Small', value: 16 },
  { id: 'medium', label: 'Medium', value: 24 },
  { id: 'large', label: 'Large', value: 32 },
]
const PRESET_STORAGE_KEY = 'fastframe.presets'
const BUILTIN_PRESETS = [
  {
    id: 'builtin-fast-review',
    name: 'Fast Review (1.5x HEVC)',
    builtin: true,
    settings: { speed: 1.5, codec: 'hevc', subtitleFontSize: 24, preferGpu: true },
  },
  {
    id: 'builtin-2x-h264',
    name: '2x Speedup (H.264)',
    builtin: true,
    settings: { speed: 2, codec: 'h264', subtitleFontSize: 24, preferGpu: true },
  },
]

function readUserPresets() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PRESET_STORAGE_KEY) || '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (preset) => preset && typeof preset.id === 'string' && typeof preset.name === 'string',
    )
  } catch {
    return []
  }
}

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-[0.14em] text-slate-600">{label}</div>
      <div className="mt-0.5 truncate text-xs font-semibold text-slate-200">{value}</div>
    </div>
  )
}

export default function ControlPanel({
  metadata,
  sourcePath,
  targetDir,
  hostRoot,
  onBrowseSource,
  onBrowseTarget,
  settings,
  onChange,
  gpu,
  activeJob,
  onCancelJob,
  onSubmit,
  submitting,
  canRender,
  subtitleFiles = [],
  onRefreshSubtitles,
  subtitlesLoading = false,
}) {
  const speed = Number(settings.speed) || 1
  const encoderName =
    settings.codec === 'hevc'
      ? gpu?.hevc_encoder || 'hevc_nvenc'
      : gpu?.h264_encoder || 'h264_nvenc'
  const hardware = gpu?.hardware_encoding_available
  const keptDuration = (settings.segments || []).reduce(
    (sum, segment) => sum + (segment.end - segment.start),
    0,
  )
  const outputDuration =
    keptDuration > 0 ? keptDuration / speed : ((metadata?.duration || 0) / speed)

  const [userPresets, setUserPresets] = useState(readUserPresets)
  const [selectedPreset, setSelectedPreset] = useState('')
  const [saveOpen, setSaveOpen] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [presetError, setPresetError] = useState(null)

  const allPresets = [...BUILTIN_PRESETS, ...userPresets]
  const activePreset = allPresets.find((preset) => preset.id === selectedPreset) || null
  const canDeletePreset = Boolean(activePreset && !activePreset.builtin)

  const applyPreset = () => {
    if (!activePreset) return
    onChange({ ...activePreset.settings })
    setPresetError(null)
  }

  const savePreset = () => {
    const name = presetName.trim()
    if (!name) {
      setPresetError('Give the preset a name first.')
      return
    }
    const preset = {
      id: `preset-${Date.now()}`,
      name,
      settings: {
        speed: Number(settings.speed) || 1,
        codec: settings.codec,
        subtitleFontSize: Number(settings.subtitleFontSize) || 24,
        preferGpu: settings.preferGpu !== false,
      },
    }
    const next = [...userPresets.filter((item) => item.name.toLowerCase() !== name.toLowerCase()), preset]
    setUserPresets(next)
    window.localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(next))
    setSelectedPreset(preset.id)
    setPresetName('')
    setSaveOpen(false)
    setPresetError(null)
  }

  const deletePreset = () => {
    if (!canDeletePreset) return
    const next = userPresets.filter((preset) => preset.id !== selectedPreset)
    setUserPresets(next)
    window.localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(next))
    setSelectedPreset('')
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <Bookmark className="h-3.5 w-3.5 text-indigo-400" />
            Presets
          </div>
          <button
            type="button"
            onClick={() => {
              setSaveOpen((value) => !value)
              setPresetError(null)
            }}
            className="text-[11px] font-medium text-accent-400 hover:text-accent-300"
          >
            {saveOpen ? 'Cancel' : 'Save current'}
          </button>
        </div>
        <div className="space-y-2 p-4">
          <div className="flex gap-1.5">
            <select
              className="field text-xs"
              value={selectedPreset}
              onChange={(event) => {
                setSelectedPreset(event.target.value)
                setPresetError(null)
              }}
              aria-label="Saved presets"
            >
              <option value="">Choose a preset…</option>
              <optgroup label="Built-in">
                {BUILTIN_PRESETS.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </optgroup>
              {userPresets.length > 0 && (
                <optgroup label="Saved">
                  {userPresets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.name}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            <button
              type="button"
              onClick={applyPreset}
              disabled={!activePreset}
              className="btn-primary !px-3 !py-1.5 !text-xs"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={deletePreset}
              disabled={!canDeletePreset}
              className="btn-ghost !px-2 !py-1.5"
              title="Delete the selected saved preset"
              aria-label="Delete preset"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>

          {saveOpen && (
            <div className="flex gap-1.5">
              <input
                className="field text-xs"
                placeholder="Preset name"
                value={presetName}
                onChange={(event) => setPresetName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') savePreset()
                }}
                aria-label="Preset name"
              />
              <button type="button" onClick={savePreset} className="btn-primary !px-3 !py-1.5 !text-xs">
                <Save className="h-3.5 w-3.5" />
                Save
              </button>
            </div>
          )}

          {presetError && <p className="text-[10px] text-amber-300">{presetError}</p>}

          <p className="text-[10px] leading-relaxed text-slate-600">
            Presets store speed, codec and subtitle size. Cuts, rotation and crop stay tied to the
            current video.
          </p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <Film className="h-3.5 w-3.5 text-accent-400" />
            Source
          </div>
          <button
            type="button"
            onClick={onBrowseSource}
            className="text-[11px] font-medium text-accent-400 hover:text-accent-300"
          >
            {sourcePath ? 'Change' : 'Choose'}
          </button>
        </div>
        <div className="p-4">
          {!metadata ? (
            <button
              type="button"
              onClick={onBrowseSource}
              className="flex w-full flex-col items-center gap-2 rounded-xl border border-dashed border-white/10 bg-white/[0.01] px-4 py-8 text-center transition hover:border-accent-500/40 hover:bg-accent-500/[0.04]"
            >
              <FolderOpen className="h-6 w-6 text-slate-600" />
              <span className="text-xs font-medium text-slate-400">
                Select a video from a host drive
              </span>
              <span className="text-[10px] uppercase tracking-widest text-slate-600">
                .mp4 · .mkv · .avi
              </span>
            </button>
          ) : (
            <>
              <div className="mb-3 min-w-0">
                <div className="truncate text-sm font-semibold text-white" title={metadata.name}>
                  {metadata.name}
                </div>
                <div
                  className="mt-0.5 truncate font-mono text-[10px] text-slate-600"
                  title={metadata.path}
                >
                  {metadata.path}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Duration" value={formatClock(metadata.duration)} />
                <Stat
                  label="Resolution"
                  value={`${metadata.display_width}×${metadata.display_height}`}
                />
                <Stat label="Frame rate" value={metadata.fps ? `${metadata.fps} fps` : '—'} />
                <Stat label="Codec" value={metadata.codec.toUpperCase()} />
                <Stat label="Size" value={formatBytes(metadata.size_bytes)} />
                <Stat
                  label="Audio"
                  value={
                    metadata.has_audio
                      ? `${metadata.audio_codec.toUpperCase()} · ${metadata.audio_channels}ch`
                      : 'None'
                  }
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {metadata.has_audio ? (
                  <span className="chip">
                    <AudioLines className="h-3 w-3" />
                    Pitch-corrected audio
                  </span>
                ) : (
                  <span className="chip border-amber-400/20 bg-amber-400/5 text-amber-300">
                    Audio will be omitted
                  </span>
                )}
                {metadata.rotation ? (
                  <span className="chip">
                    <Film className="h-3 w-3" />
                    Rotation {metadata.rotation}°
                  </span>
                ) : null}
              </div>
            </>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <Gauge className="h-3.5 w-3.5 text-accent-400" />
            Speed
          </div>
          <span className="font-mono text-xs font-semibold text-accent-300">
            {formatSpeed(speed)}×
          </span>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-4 gap-1.5">
            {SPEED_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => onChange({ speed: preset })}
                data-active={Math.abs(speed - preset) < 0.001}
                className="rounded-lg border border-white/5 bg-white/[0.02] py-2 text-xs font-semibold text-slate-400 transition hover:border-white/10 hover:bg-white/[0.05] hover:text-white data-[active=true]:border-accent-500/40 data-[active=true]:bg-accent-500/15 data-[active=true]:text-accent-300"
              >
                {formatSpeed(preset)}×
              </button>
            ))}
            <button
              type="button"
              onClick={() => onChange({ speed: Math.min(4, Number((speed + 0.25).toFixed(2))) })}
              className="rounded-lg border border-dashed border-white/10 py-2 text-xs font-semibold text-slate-500 transition hover:border-accent-500/40 hover:text-accent-300"
              title="Increase speed by 0.25×"
            >
              +0.25
            </button>
          </div>

          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-600">
              <span>0.25×</span>
              <span>Fine tune</span>
              <span>4×</span>
            </div>
            <input
              type="range"
              min={0.25}
              max={4}
              step={0.05}
              value={speed}
              onChange={(event) => onChange({ speed: Number(event.target.value) })}
              className="w-full"
              aria-label="Playback speed"
            />
          </div>
          <p className="mt-3 flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
            <Sparkles className="mt-0.5 h-3 w-3 shrink-0" />
            Preview plays at the selected rate and audio is re-timed with chained atempo filters, so
            voices keep their natural pitch.
          </p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <Cpu className="h-3.5 w-3.5 text-emerald-400" />
            Encoding
          </div>
          <span
            className={`chip ${
              hardware
                ? 'border-emerald-400/25 bg-emerald-400/5 text-emerald-300'
                : 'border-amber-400/25 bg-amber-400/5 text-amber-300'
            }`}
          >
            <Zap className="h-3 w-3" />
            {hardware ? 'NVENC' : 'CPU'}
          </span>
        </div>
        <div className="space-y-3 p-4">
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => onChange({ codec: 'h264' })}
              data-active={settings.codec === 'h264'}
              className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5 text-left transition hover:border-white/10 data-[active=true]:border-accent-500/40 data-[active=true]:bg-accent-500/[0.08]"
            >
              <div className="text-xs font-semibold text-slate-200">H.264</div>
              <div className="mt-0.5 text-[10px] leading-tight text-slate-500">
                Max compatibility
              </div>
            </button>
            <button
              type="button"
              onClick={() => onChange({ codec: 'hevc' })}
              data-active={settings.codec === 'hevc'}
              className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5 text-left transition hover:border-white/10 data-[active=true]:border-accent-500/40 data-[active=true]:bg-accent-500/[0.08]"
            >
              <div className="text-xs font-semibold text-slate-200">H.265 / HEVC</div>
              <div className="mt-0.5 text-[10px] leading-tight text-slate-500">
                ~50% smaller · faster USB writes
              </div>
            </button>
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-slate-600">
            <Zap className="h-3 w-3" />
            {encoderName}
            {settings.codec === 'hevc' ? ' · hvc1 tag' : ''}
          </div>

          <div>
            <label className="field-label" htmlFor="output-filename">
              Output filename
            </label>
            <input
              id="output-filename"
              className="field font-mono text-xs"
              value={settings.outputFilename}
              onChange={(event) =>
                onChange({ outputFilename: event.target.value, filenameCustom: true })
              }
              onBlur={() => {
                if (!settings.outputFilename.trim()) onChange({ filenameCustom: false })
              }}
              placeholder="video_2x_hevc.mp4"
              spellCheck={false}
            />
            <p className="mt-1 text-[10px] text-slate-600">
              .mp4 or .mkv. Existing files are never overwritten — a _1 suffix is added
              automatically.
            </p>
          </div>

          <button
            type="button"
            onClick={onBrowseTarget}
            className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
              targetDir
                ? 'border-white/10 bg-white/[0.02] hover:border-accent-500/40'
                : 'border-dashed border-white/10 bg-white/[0.01] hover:border-accent-500/40 hover:bg-accent-500/[0.04]'
            }`}
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-emerald-400/10 text-emerald-300">
              <FolderOpen className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[10px] uppercase tracking-[0.14em] text-slate-600">
                Destination
              </span>
              <span className="block truncate font-mono text-[10px] text-slate-300">
                {targetDir || hostRoot || 'Select output folder'}
              </span>
            </span>
            <HardDrive className="h-3.5 w-3.5 shrink-0 text-slate-600" />
          </button>

          <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
            <Info className="mt-0.5 h-3 w-3 shrink-0" />
            Output is written directly to the host folder. Nothing is uploaded and the source file
            is never modified.
          </p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <Captions className="h-3.5 w-3.5 text-indigo-400" />
            Subtitles
          </div>
          <button
            type="button"
            onClick={onRefreshSubtitles}
            disabled={!sourcePath || subtitlesLoading}
            className="rounded-md p-1 text-slate-500 transition hover:bg-white/5 hover:text-slate-200 disabled:opacity-30"
            title="Rescan the source folder for .srt files"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${subtitlesLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        <div className="space-y-3 p-4">
          <select
            className="field text-xs"
            value={settings.subtitlePath || ''}
            onChange={(event) => onChange({ subtitlePath: event.target.value })}
            disabled={!sourcePath}
            aria-label="Subtitle file"
          >
            <option value="">None — do not burn subtitles</option>
            {subtitleFiles.map((file) => (
              <option key={file.path} value={file.path}>
                {file.name}
              </option>
            ))}
          </select>

          {sourcePath && subtitleFiles.length === 0 && (
            <p className="text-[10px] leading-relaxed text-slate-600">
              No .srt files were found next to this video. Drop one into the same folder and press
              refresh.
            </p>
          )}

          <div>
            <span className="field-label">Font size</span>
            <div className="segmented">
              {FONT_SIZES.map((size) => (
                <button
                  key={size.id}
                  type="button"
                  onClick={() => onChange({ subtitleFontSize: size.value })}
                  data-active={Number(settings.subtitleFontSize) === size.value}
                  disabled={!settings.subtitlePath}
                >
                  {size.label}
                </button>
              ))}
            </div>
          </div>

          <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
            <Info className="mt-0.5 h-3 w-3 shrink-0" />
            Subtitles are burned onto the frames at their original 1x timestamps before any cutting
            or speed change, so they stay millisecond-accurate in the final render.
          </p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <Rocket className="h-3.5 w-3.5 text-accent-400" />
            Render
          </div>
          <span className="text-[10px] text-slate-600">
            {formatSpeed(speed)}× · {settings.codec === 'hevc' ? 'HEVC' : 'H.264'}
            {settings.segments?.length ? ` · ${settings.segments.length} cut` : ''}
            {settings.subtitlePath ? ' · subs' : ''}
          </span>
        </div>
        <div className="space-y-3 p-4">
          {activeJob && <JobProgress job={activeJob} onCancel={onCancelJob} />}

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => onSubmit({ renderNow: true })}
              disabled={!canRender || submitting}
              className="btn-primary !py-2.5"
            >
              <Rocket className={`h-4 w-4 ${submitting ? 'animate-bounce' : ''}`} />
              Render Now
            </button>
            <button
              type="button"
              onClick={() => onSubmit({ renderNow: false })}
              disabled={!canRender || submitting}
              className="btn-ghost !py-2.5"
              title="Queue this render behind the active job"
            >
              <Plus className="h-4 w-4" />
              Add to Queue
            </button>
          </div>

          {!canRender && (
            <p className="text-center text-[10px] text-slate-600">
              Choose a source video and a destination folder to enable rendering.
            </p>
          )}

          {metadata && (
            <div className="space-y-1 rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 text-[10px] text-slate-500">
              <div className="flex items-center justify-between">
                <span className="inline-flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Output length
                </span>
                <span className="font-mono text-slate-300">{formatClock(outputDuration)}</span>
              </div>
              {settings.segments?.length ? (
                <div className="flex items-center justify-between">
                  <span className="inline-flex items-center gap-1">
                    <Scissors className="h-3 w-3" />
                    Kept footage
                  </span>
                  <span className="font-mono text-slate-300">{formatClock(keptDuration)}</span>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
