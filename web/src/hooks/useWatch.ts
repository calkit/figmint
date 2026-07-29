import { useEffect, useRef, useState } from 'react'
import { useEditor } from '../state/store'

/**
 * Live connection to the backend's filesystem watcher.
 *
 * When a script rewrites a component, the server pushes the changed paths and
 * we re-scan. Only paths come over the wire — hashes and credentials are read
 * back through the normal endpoints, so there is exactly one code path deciding
 * whether something is fresh.
 *
 * Reconnects with backoff, because the common reason the socket drops is the
 * dev server restarting, and having to reload the editor for that is annoying.
 */

export type WatchState = 'connecting' | 'live' | 'offline'

const RECONNECT_MIN_MS = 500
const RECONNECT_MAX_MS = 15_000

interface WatchMessage {
  type: 'watching' | 'changed'
  assets?: string[]
  documents?: string[]
}

export function useWatch(): WatchState {
  const [state, setState] = useState<WatchState>('connecting')
  const attemptRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      if (disposed) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/api/watch`

      let socket: WebSocket
      try {
        socket = new WebSocket(url)
      } catch {
        scheduleReconnect()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        if (disposed) return
        attemptRef.current = 0
        setState('live')
      }

      socket.onmessage = (event) => {
        let message: WatchMessage
        try {
          message = JSON.parse(event.data as string)
        } catch {
          return
        }
        if (message.type !== 'changed') return

        const store = useEditor.getState()
        if (message.assets?.length) {
          void store.loadAssets()
          store.setStatus(
            message.assets.length === 1
              ? `${message.assets[0]} changed on disk`
              : `${message.assets.length} components changed on disk`,
          )
        }
        if (message.documents?.length) {
          store.noteDocumentsChanged(message.documents)
        }
      }

      socket.onclose = () => {
        if (disposed) return
        setState('offline')
        scheduleReconnect()
      }

      socket.onerror = () => {
        // `onclose` always follows, which is where reconnection is handled.
        socket.close()
      }
    }

    const scheduleReconnect = () => {
      if (disposed) return
      setState('offline')
      const delay = Math.min(
        RECONNECT_MAX_MS,
        RECONNECT_MIN_MS * 2 ** attemptRef.current,
      )
      attemptRef.current += 1
      timer = setTimeout(connect, delay)
    }

    connect()

    return () => {
      disposed = true
      if (timer) clearTimeout(timer)
      socketRef.current?.close()
    }
  }, [])

  return state
}
