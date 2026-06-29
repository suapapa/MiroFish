/**
 * SSE (Server-Sent Events) utility for real-time updates
 * Replaces polling with server-pushed events
 */

/**
 * Get the SSE base URL from environment.
 * In dev mode with Vite proxy, use relative URLs (empty string).
 * In production, use VITE_API_BASE_URL.
 */
function getSSEBaseUrl() {
  const url = import.meta.env.VITE_API_BASE_URL
  if (!url) return ''
  return url.endsWith('/') ? url.slice(0, -1) : url
}

/**
 * Create an SSE connection with auto-reconnect
 * 
 * @param {string} path - API path (e.g., '/api/graph/task/xxx/stream')
 * @param {Object} options
 * @param {function} options.onUpdate - Called on 'update' event with parsed JSON data
 * @param {function} options.onComplete - Called on 'complete' event with parsed JSON data
 * @param {function} options.onError - Called on error
 * @param {number} options.reconnectDelay - Base delay before reconnect (default 3000ms)
 * @param {number} options.maxRetries - Max reconnection attempts (default 5)
 * @returns {{ close: function }}
 */
export function createSSE(path, options = {}) {
  const {
    onUpdate = () => {},
    onComplete = () => {},
    onError = () => {},
    reconnectDelay = 3000,
    maxRetries = 5,
  } = options

  let eventSource = null
  let retryCount = 0
  let closed = false
  let reconnectTimeout = null

  function connect() {
    if (closed) return

    const url = `${getSSEBaseUrl()}${path}`
    console.log(`[SSE] Connecting: ${path}`)

    eventSource = new EventSource(url)

    eventSource.addEventListener('update', (e) => {
      retryCount = 0  // Reset retry count on successful message
      try {
        const data = JSON.parse(e.data)
        onUpdate(data)
      } catch (err) {
        console.warn('[SSE] Failed to parse update:', err)
      }
    })

    eventSource.addEventListener('complete', (e) => {
      try {
        const data = JSON.parse(e.data)
        onComplete(data)
      } catch (err) {
        console.warn('[SSE] Failed to parse complete:', err)
      }
      // Close after complete — task is done
      close()
    })

    eventSource.addEventListener('error', (e) => {
      try {
        const data = JSON.parse(e.data)
        onError(data)
      } catch {
        // Not a structured data error, handled by eventSource.onerror
      }
    })

    eventSource.addEventListener('heartbeat', () => {
      // Heartbeat received, connection is alive
    })

    eventSource.onerror = () => {
      if (closed) return

      eventSource.close()

      if (retryCount < maxRetries) {
        retryCount++
        const delay = reconnectDelay * Math.pow(1.5, retryCount - 1)
        console.warn(`[SSE] Connection lost, reconnecting (${retryCount}/${maxRetries}) in ${Math.round(delay)}ms...`)
        reconnectTimeout = setTimeout(connect, delay)
      } else {
        console.error(`[SSE] Max retries reached for ${path}, giving up`)
        onError({ error: 'Max retries reached' })
      }
    }

    eventSource.onopen = () => {
      console.log(`[SSE] Connected: ${path}`)
      retryCount = 0
    }
  }

  function close() {
    closed = true
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout)
      reconnectTimeout = null
    }
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    console.log(`[SSE] Closed: ${path}`)
  }

  connect()

  return { close }
}
