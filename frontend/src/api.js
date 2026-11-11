const JSON_HEADERS = { 'Content-Type': 'application/json' }

function buildQuery(params) {
  if (!params) return ''
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(path, options)
  } catch (error) {
    throw new Error(`Cannot reach the Fast-Frame API: ${error.message}`)
  }

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    const detail = payload && payload.detail
    let message
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail.map((item) => item.msg || JSON.stringify(item)).join('; ')
    } else {
      message = `Request failed with status ${response.status}`
    }
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  return payload
}

export const api = {
  get: (path, params) => request(`${path}${buildQuery(params)}`),
  post: (path, body) =>
    request(path, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body ?? {}) }),
  del: (path) => request(path, { method: 'DELETE' }),
  health: () => request('/api/health'),
  gpu: (refresh = false) => request(`/api/system/gpu-check${refresh ? '?refresh=true' : ''}`),
  config: () => request('/api/system/config'),
  list: (path) => request(`/api/fs/list${buildQuery({ path })}`),
  metadata: (path) => request(`/api/video/metadata${buildQuery({ path })}`),
  streamUrl: (path) => `/api/video/stream${buildQuery({ path })}`,
  queue: () => request('/api/queue'),
  addToQueue: (payload) => request('/api/queue/add', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  }),
  cancelJob: (jobId) => request(`/api/queue/${encodeURIComponent(jobId)}`, { method: 'DELETE' }),
  clearCompleted: () => request('/api/queue/clear-completed', { method: 'POST' }),
}

export function openProgressSocket(onEvent, onStatusChange) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}/ws/progress`
  let socket = null
  let closedByUser = false
  let retry = 0
  let retryTimer = null

  const connect = () => {
    onStatusChange?.('connecting')
    socket = new WebSocket(url)

    socket.onopen = () => {
      retry = 0
      onStatusChange?.('open')
    }
    socket.onmessage = (event) => {
      try {
        onEvent(JSON.parse(event.data))
      } catch {
        /* ignore malformed frames */
      }
    }
    socket.onerror = () => onStatusChange?.('error')
    socket.onclose = () => {
      onStatusChange?.('closed')
      if (closedByUser) return
      retry = Math.min(retry + 1, 6)
      const delay = Math.min(1000 * retry, 8000)
      retryTimer = window.setTimeout(connect, delay)
    }
  }

  connect()

  return () => {
    closedByUser = true
    if (retryTimer) window.clearTimeout(retryTimer)
    socket?.close()
  }
}
