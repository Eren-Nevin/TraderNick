/**
 * Shared concurrency-limited `fetch` wrapper.
 *
 * The data_server's ClickHouse client has a finite connection pool, and a
 * 60s Sanic response timeout. When ~10+ charts hydrate at once (page
 * reload, restored layout with many panels), the parallel ASOF-JOIN
 * aggregates pile up and either 503 the request or hang it until the
 * timeout. Capping browser-side concurrency keeps the backlog manageable
 * and turns indefinite waits into ordered waits.
 *
 * The cap (`MAX_CONCURRENT`) is intentionally a little below the
 * data_server's effective pool so we never head-of-line block the data
 * server with bursts.
 *
 * Per-request `timeoutMs` aborts the fetch if it stalls — the slot is
 * freed even when something hangs upstream.
 */

const MAX_CONCURRENT = 4;
// Heavy ASOF-JOIN aggregates over compound queries (EVM × Stables, 30d)
// genuinely take ~30-60s under load. Set this comfortably above that so the
// timeout is a "something is wedged" signal, not a "your honest slow query
// got cut off" annoyance. The user always has the refresh button.
// 180s: above the worst-case cold smart-wallet selection (~130s) so an honest
// slow query completes and caches instead of being aborted mid-flight (which
// left the set uncached and triggered an endless retry storm). Must stay BELOW
// the data_server Sanic RESPONSE_TIMEOUT (240s) and CH client timeout (300s).
const DEFAULT_TIMEOUT_MS = 180_000;

let inflight = 0;
const queue: Array<() => void> = [];

function next() {
  inflight--;
  const w = queue.shift();
  if (w) w();
}

export async function queuedFetch(
  url: string,
  init?: RequestInit,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<Response> {
  const userSignal = init?.signal;
  // Bail out before claiming a slot if the caller already gave up.
  if (userSignal?.aborted) {
    throw userSignal.reason ?? new DOMException('aborted', 'AbortError');
  }
  // Wait for a slot — observing the caller's abort signal so a cancelled
  // request gives back its queue spot immediately instead of holding it
  // until its natural turn.
  if (inflight >= MAX_CONCURRENT) {
    await new Promise<void>((resolve, reject) => {
      const wake = () => resolve();
      queue.push(wake);
      if (userSignal) {
        const onAbort = () => {
          const idx = queue.indexOf(wake);
          if (idx >= 0) queue.splice(idx, 1);
          reject(userSignal.reason ?? new DOMException('aborted', 'AbortError'));
        };
        userSignal.addEventListener('abort', onAbort, { once: true });
      }
    });
  }
  // Race: caller may have aborted in the gap between dequeue and here.
  if (userSignal?.aborted) {
    throw userSignal.reason ?? new DOMException('aborted', 'AbortError');
  }
  inflight++;
  const controller = new AbortController();
  if (userSignal) {
    // Forward caller's signal to the fetch — already-aborted is impossible
    // here (we checked), so only the future-abort case needs wiring.
    userSignal.addEventListener(
      'abort',
      () => controller.abort(userSignal.reason),
      { once: true }
    );
  }
  // Pass an explicit reason so the caught error has a meaningful message
  // ("Request timed out after Xs") rather than the browser default
  // ("Signal aborted for no reason" / "The operation was aborted").
  const timer = setTimeout(() => {
    controller.abort(
      new DOMException(
        `Request timed out after ${Math.round(timeoutMs / 1000)}s — click ↻ to retry`,
        'TimeoutError'
      )
    );
  }, timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
    next();
  }
}
