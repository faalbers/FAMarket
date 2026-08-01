/**
 * Subscribe to a server-sent event stream.
 *
 * EventSource is used rather than a WebSocket because it reconnects on its own
 * — a dropped socket would otherwise need hand-written retry and backoff. The
 * server sends a full snapshot on connect, so a reconnect (or a tab opened
 * mid-run) repaints everything without any client-side replay.
 *
 * `closeWhen`, if given, marks a stream as one-shot rather than persistent:
 * when it returns true for a received frame, the client closes the
 * EventSource itself. Without this, a server that simply ends the response
 * (as a finite stream does once it has sent its last frame) still looks like
 * an unexpected drop to EventSource, which auto-reconnects and re-triggers
 * whatever the connection does server-side.
 *
 * `failed` distinguishes a permanent failure (bad route, wrong content-type —
 * the browser gives up and leaves `readyState` CLOSED) from the normal
 * temporary drop-and-retry EventSource does on its own. Without checking for
 * it, a caller has no way to tell "still connecting" from "never going to
 * connect" — both just look like silence.
 *
 * `url` may be `null` to mean "don't connect yet" — for a stream that starts
 * on a user action (a button click) rather than on mount.
 */
import { useEffect, useRef, useState } from "react";

export function useEventStream<T>(
  url: string | null,
  onMessage: (data: T) => void,
  options?: { closeWhen?: (data: T) => boolean },
): { connected: boolean; failed: boolean } {
  const [connected, setConnected] = useState(false);
  const [failed, setFailed] = useState(false);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;
  const closeWhenRef = useRef(options?.closeWhen);
  closeWhenRef.current = options?.closeWhen;

  useEffect(() => {
    setFailed(false);
    if (!url) {
      setConnected(false);
      return;
    }
    const source = new EventSource(url);
    source.onopen = () => setConnected(true);
    source.onerror = () => {
      setConnected(false);
      if (source.readyState === EventSource.CLOSED) setFailed(true);
    };
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as T;
        handlerRef.current(data);
        if (closeWhenRef.current?.(data)) source.close();
      } catch {
        /* a malformed frame shouldn't kill the stream */
      }
    };
    return () => source.close();
  }, [url]);

  return { connected, failed };
}
