import {
  AudioLines,
  Bookmark,
  Captions,
  ChevronDown,
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
  Settings2,
  Sparkles,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { formatBytes, formatClock, formatSpeed } from '../utils'
import AiViewPanel from './AiViewPanel'

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

const SECTIONS = [
  { id: 'presets', label: 'Presets', icon: Bookmark, tone: 'text-indigo-400' },
  { id: 'source', label: 'Source', icon: Film, tone: 'text-accent-400' },
  { id: 'speed', label: 'Speed', icon: Gauge, tone: 'text-accent-400' },
  { id: 'encoding', label: 'Encoding', icon: Cpu, tone: 'text-emerald-400' },
  { id: 'aiview', label: 'AI View', icon: Sparkles, tone: 'text-accent-400' },
  { id: 'subtitles', label: 'Subtitles', icon: Captions, tone: 'text-indigo-400' },
]

const ALL_SECTIONS_OPEN = Object.fromEntries(SECTIONS.map((section) => [section.id, true]))

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

function ConfigSection({ id, title, icon: Icon, tone, badge, open, onToggle, sectionRef, children }) {
  return (
    <section ref={sectionRef} className="panel scroll-mt-2 overflow-hidden">
      <div className="panel-header">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={`config-section-${id}`}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <Icon className={`h-3.5 w-3.5 shrink-0 ${tone}`} />
          <span className="panel-title">{title}</span>
        </button>
        <div className="flex shrink-0 items-center gap-2">
          {badge}
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls={`config-section-${id}`}
            aria-label={`${open ? 'Collapse' : 'Expand'} ${title}`}
            className="rounded-md p-1 text-slate-500 transition hover:bg-white/5 hover:text-slate-200"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
            />
          </button>
        </div>
      </div>
      <div
        id={`config-section-${id}`}
        className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out ${
          open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
        }`}
      >
        <div className="overflow-hidden">{children}</div>
      </div>
    </section>
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
  onSubmit,
  submitting,
  canRender,
  subtitleFiles = [],
  onRefreshSubtitles,
  subtitlesLoading = false,
  open = false,
  onClose,
  focusSection = null,
  aiView = null,
  aiViewSaving = false,
  aiViewError = null,
  onAiViewChange,
  onAiViewCommand,
  onAiViewReset,
  aiViewEndpoint = '/api/ai/view-state',
  currentTime = 0,
}) {
  const speed = Number(settings.speed) || 1
  const encoderName =
    settings.codec === 'hevc'
      ? gpu?.hevc_encoder || 'hevc_nvenc'
      : gpu?.h264_encoder || 'h264_nvenc'
  const hardware = gpu?.hardware_encoding_available

  const [userPresets, setUserPresets] = useState(readUserPresets)
  const [selectedPreset, setSelectedPreset] = useState('')
  const [saveOpen, setSaveOpen] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [presetError, setPresetError] = useState(null)
  const [openSections, setOpenSections] = useState(ALL_SECTIONS_OPEN)
  const sectionRefs = useRef({})

  const allPresets = [...BUILTIN_PRESETS, ...userPresets]
  const activePreset = allPresets.find((preset) => preset.id === selectedPreset) || null
  const canDeletePreset = Boolean(activePreset && !activePreset.builtin)
  const allOpen = SECTIONS.every((section) => openSections[section.id])

  const toggleSection = (id) =>
    setOpenSections((previous) => ({ ...previous, [id]: !previous[id] }))

  const setAllSections = (value) =>
    setOpenSections(Object.fromEntries(SECTIONS.map((section) => [section.id, value])))

  const jumpTo = useCallback((id) => {
    if (!id) return
    setOpenSections((previous) => ({ ...previous, [id]: true }))
    window.requestAnimationFrame(() => {
      sectionRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [])

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  useEffect(() => {
    if (!open) return undefined
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [open])

  useEffect(() => {
    if (!open || !focusSection?.id) return
    jumpTo(focusSection.id)
  }, [open, focusSection, jumpTo])

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
    <div
      className="fixed inset-0 z-40"
      aria-hidden={!open}
      style={{
        visibility: open ? 'visible' : 'hidden',
        transition: open ? 'none' : 'visibility 0s linear 300ms',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      <div
        onClick={onClose}
        className={`absolute inset-0 bg-slate-950/70 backdrop-blur-sm transition-opacity duration-300 ${
          open ? 'opacity-100' : 'opacity-0'
        }`}
      />

      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Configuration"
        className={`absolute right-0 top-0 flex h-full w-full max-w-[480px] flex-col border-l border-white/10 bg-studio-950/95 shadow-2xl transition-transform duration-300 ease-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/5 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-bold tracking-[0.18em] text-white">
              <Settings2 className="h-4 w-4 text-accent-400" />
              CONFIGURATION
            </div>
            <div className="truncate text-[10px] uppercase tracking-[0.18em] text-slate-500">
              Presets · Source · Speed · Encoding · AI View · Subtitles
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-white/5 hover:text-white"
            aria-label="Close configuration"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-white/5 bg-studio-950/90 px-3 py-2">
          {SECTIONS.map((section) => (
            <button
              key={section.id}
              type="button"
              onClick={() => jumpTo(section.id)}
              className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 transition hover:border-accent-500/40 hover:text-accent-300"
            >
              {section.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setAllSections(!allOpen)}
            className="ml-auto text-[10px] font-semibold uppercase tracking-wider text-accent-400 transition hover:text-accent-300"
          >
            {allOpen ? 'Collapse all' : 'Expand all'}
          </button>
        </div>

        <div className="flex-1 space-y-2.5 overflow-y-auto px-3 py-3">
          <ConfigSection
            id="presets"
            title="Presets"
            icon={Bookmark}
            tone="text-indigo-400"
            open={openSections.presets}
            onToggle={() => toggleSection('presets')}
            sectionRef={(node) => {
              sectionRefs.current.presets = node
            }}
            badge={
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
            }
          >
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
                  <button
                    type="button"
                    onClick={savePreset}
                    className="btn-primary !px-3 !py-1.5 !text-xs"
                  >
                    <Save className="h-3.5 w-3.5" />
                    Save
                  </button>
                </div>
              )}

              {presetError && <p className="text-[10px] text-amber-300">{presetError}</p>}

              <p className="text-[10px] leading-relaxed text-slate-600">
                Presets store speed, codec and subtitle size. Cuts, rotation and crop stay tied to
                the current video.
              </p>
            </div>
          </ConfigSection>

          <ConfigSection
            id="source"
            title="Source"
            icon={Film}
            tone="text-accent-400"
            open={openSections.source}
            onToggle={() => toggleSection('source')}
            sectionRef={(node) => {
              sectionRefs.current.source = node
            }}
            badge={
              <button
                type="button"
                onClick={onBrowseSource}
                className="text-[11px] font-medium text-accent-400 hover:text-accent-300"
              >
                {sourcePath ? 'Change' : 'Choose'}
              </button>
            }
          >
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
                    <div
                      className="truncate text-sm font-semibold text-white"
                      title={metadata.name}
                    >
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
          </ConfigSection>

          <ConfigSection
            id="speed"
            title="Speed"
            icon={Gauge}
            tone="text-accent-400"
            open={openSections.speed}
            onToggle={() => toggleSection('speed')}
            sectionRef={(node) => {
              sectionRefs.current.speed = node
            }}
            badge={
              <span className="font-mono text-xs font-semibold text-accent-300">
                {formatSpeed(speed)}×
              </span>
            }
          >
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
                  onClick={() =>
                    onChange({ speed: Math.min(4, Number((speed + 0.25).toFixed(2))) })
                  }
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
                Preview plays at the selected rate and audio is re-timed with chained atempo
                filters, so voices keep their natural pitch.
              </p>
            </div>
          </ConfigSection>

          <ConfigSection
            id="encoding"
            title="Encoding"
            icon={Cpu}
            tone="text-emerald-400"
            open={openSections.encoding}
            onToggle={() => toggleSection('encoding')}
            sectionRef={(node) => {
              sectionRefs.current.encoding = node
            }}
            badge={
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
            }
          >
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
                Output is written directly to the host folder. Nothing is uploaded and the source
                file is never modified.
              </p>
            </div>
          </ConfigSection>

          <ConfigSection
            id="aiview"
            title="AI View Selector"
            icon={Sparkles}
            tone="text-accent-400"
            open={openSections.aiview}
            onToggle={() => toggleSection('aiview')}
            sectionRef={(node) => {
              sectionRefs.current.aiview = node
            }}
            badge={
              <span
                className={`chip ${
                  metadata?.spherical
                    ? 'border-accent-500/30 bg-accent-500/10 text-accent-300'
                    : 'border-white/10 bg-white/[0.03] text-slate-500'
                }`}
              >
                {metadata?.spherical ? '360° source' : 'MCP ready'}
              </span>
            }
          >
            <AiViewPanel
              state={aiView}
              onChange={onAiViewChange}
              onCommand={onAiViewCommand}
              onReset={onAiViewReset}
              disabled={!sourcePath}
              saving={aiViewSaving}
              error={aiViewError}
              currentTime={currentTime}
              endpointHint={aiViewEndpoint}
            />
          </ConfigSection>

          <ConfigSection
            id="subtitles"
            title="Subtitles"
            icon={Captions}
            tone="text-indigo-400"
            open={openSections.subtitles}
            onToggle={() => toggleSection('subtitles')}
            sectionRef={(node) => {
              sectionRefs.current.subtitles = node
            }}
            badge={
              <button
                type="button"
                onClick={onRefreshSubtitles}
                disabled={!sourcePath || subtitlesLoading}
                className="rounded-md p-1 text-slate-500 transition hover:bg-white/5 hover:text-slate-200 disabled:opacity-30"
                title="Rescan the source folder for .srt files"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${subtitlesLoading ? 'animate-spin' : ''}`} />
              </button>
            }
          >
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
                  No .srt files were found next to this video. Drop one into the same folder and
                  press refresh.
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
                Subtitles are burned onto the frames at their original 1x timestamps before any
                cutting or speed change, so they stay millisecond-accurate in the final render.
              </p>
            </div>
          </ConfigSection>
        </div>

        <div className="shrink-0 border-t border-white/5 bg-studio-950/95 px-3 py-2.5">
          <button
            type="button"
            onClick={onBrowseTarget}
            title={targetDir || hostRoot || 'Select output folder'}
            className="mb-2 flex w-full min-w-0 items-center gap-1.5 text-left transition"
          >
            <HardDrive className="h-3 w-3 shrink-0 text-slate-600" />
            <span className="shrink-0 text-[10px] uppercase tracking-[0.14em] text-slate-600">
              Destination
            </span>
            <span
              className={`min-w-0 flex-1 truncate font-mono text-[10px] ${
                targetDir ? 'text-slate-300' : 'text-amber-300'
              }`}
            >
              {targetDir || hostRoot || 'Select output folder'}
            </span>
          </button>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => onSubmit({ renderNow: true })}
              disabled={!canRender || submitting}
              className="btn-primary !py-2 !text-xs"
            >
              <Rocket className={`h-4 w-4 ${submitting ? 'animate-bounce' : ''}`} />
              Render Now
            </button>
            <button
              type="button"
              onClick={() => onSubmit({ renderNow: false })}
              disabled={!canRender || submitting}
              className="btn-ghost !py-2 !text-xs"
              title="Queue this render behind the active job"
            >
              <Plus className="h-4 w-4" />
              Add to Queue
            </button>
          </div>
        </div>
      </aside>
    </div>
  )
}
