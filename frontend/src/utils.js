export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

export function formatBytes(bytes, decimals = 1) {
  const value = Number(bytes)
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const size = value / 1024 ** index
  return `${size.toFixed(index === 0 ? 0 : decimals)} ${units[index]}`
}

export function formatClock(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value < 0) return '0:00'
  const total = Math.floor(value)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

export function formatTimecode(seconds, withMillis = true) {
  const value = Math.max(0, Number(seconds) || 0)
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const secs = Math.floor(value % 60)
  const millis = Math.round((value - Math.floor(value)) * 1000)
  const base = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  return withMillis ? `${base}.${String(millis).padStart(3, '0')}` : base
}

export function parseTimecode(text) {
  if (!text) return null
  const parts = String(text).trim().split(':')
  if (parts.length === 0 || parts.some((part) => part !== '' && Number.isNaN(Number(part)))) {
    return null
  }
  const numbers = parts.map(Number)
  if (numbers.length === 1) return numbers[0]
  if (numbers.length === 2) return numbers[0] * 60 + numbers[1]
  if (numbers.length === 3) return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
  return null
}

export function basename(path) {
  if (!path) return ''
  return String(path).split(/[\\/]/).filter(Boolean).pop() ?? ''
}

export function dirname(path) {
  if (!path) return ''
  const normalised = String(path).replace(/[\\/]+$/, '')
  const index = Math.max(normalised.lastIndexOf('/'), normalised.lastIndexOf('\\'))
  return index > 0 ? normalised.slice(0, index) : ''
}

export function formatSpeed(speed) {
  return String(Number(Number(speed).toFixed(2)))
}

export function suggestOutputName(sourceName, speed, codec) {
  const stem = String(sourceName || 'output').replace(/\.[^.]+$/, '')
  return `${stem}_${formatSpeed(speed)}x_${codec}.mp4`
}

export function formatEta(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 60) return `${Math.ceil(value)}s`
  const minutes = Math.floor(value / 60)
  const secs = Math.round(value % 60)
  if (minutes < 60) return `${minutes}m ${String(secs).padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
}

export function uid(prefix = 'id') {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const NETWORK_URL_PATTERN = /^(?:https?|rtsp|rtsps|rtmp|rtmps|webdavs?|davs?):\/\//i

/** True for direct stream URLs that the backend reads without downloading. */
export function isNetworkUrl(value) {
  return typeof value === 'string' && NETWORK_URL_PATTERN.test(value.trim())
}

/** Schemes the browser <video> element can preview through the API proxy. */
export function isPreviewableUrl(value) {
  return typeof value === 'string' && /^(?:https?|webdavs?|davs?):\/\//i.test(value.trim())
}

export function formatModifiedDate(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
