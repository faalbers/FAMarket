/**
 * Subscribe to a server-sent event stream.
 *
 * EventSource is used rather than a WebSocket because it reconnects on its own
 * — a dropped socket would otherwise need hand-written retry and backoff. The
 * server sends a full snapshot on connect, so a reconnect (or a tab opened
 * mid-run) repaints everything without any client-side replay.
 */
import { useEffect, useRef, useState } from "react";

export function useEventStream<T>(url: string, onMessage: (data: T) => void) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    const source = new EventSource(url);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (event) => {
      try {
        handlerRef.current(JSON.parse(event.data) as T);
      } catch {
        /* a malformed frame shouldn't kill the stream */
      }
    };
    return () => source.close();
  }, [url]);

  return connected;
}
