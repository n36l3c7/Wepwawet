/*
 * Local API client. The session cookie is set by /auth when the window opens,
 * so every request is same-origin and needs no token in JavaScript.
 */

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed with status ${status}`)
    this.status = status
  }
}

export async function getJson(path, { signal } = {}) {
  const response = await fetch(path, { credentials: 'same-origin', signal, headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let detail
    try {
      detail = (await response.json()).detail ?? ''
    } catch {
      detail = response.statusText
    }
    throw new ApiError(response.status, detail)
  }
  return response.json()
}

export const fetchMeta = (options) => getJson('/api/meta', options)

/**
 * Subscribe to /api/events. `handlers` maps event names to callbacks receiving
 * the parsed JSON; `onState` receives 'open' | 'reconnecting'.
 * Returns an unsubscribe function.
 */
export function subscribeEvents(handlers, onState = () => {}) {
  const source = new EventSource('/api/events')
  source.onopen = () => onState('open')
  source.onerror = () => onState('reconnecting')
  const listeners = Object.entries(handlers).map(([name, handler]) => {
    const listener = (event) => handler(JSON.parse(event.data))
    source.addEventListener(name, listener)
    return [name, listener]
  })
  return () => {
    listeners.forEach(([name, listener]) => source.removeEventListener(name, listener))
    source.close()
  }
}

async function sendJson(method, path, body) {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ') : data.detail
    throw new ApiError(response.status, detail)
  }
  return data
}

export const fetchProviders = (options) => getJson('/api/providers', options)
export const fetchBudget = (options) => getJson('/api/budget', options)
export const startRun = (query) => sendJson('POST', '/api/runs', { query })
export const cancelRun = (id) => sendJson('DELETE', `/api/runs/${encodeURIComponent(id)}`)
