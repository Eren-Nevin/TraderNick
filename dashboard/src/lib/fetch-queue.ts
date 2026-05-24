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
const DEFAULT_TIMEOUT_MS = 45_000;

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
  if (inflight >= MAX_CONCURRENT) {
    await new Promise<void>((resolve) => queue.push(resolve));
  }
  inflight++;
  const controller = new AbortController();
  const userSignal = init?.signal;
  if (userSignal) {
    // Honour caller's abort signal too — forward it to our controller.
    if (userSignal.aborted) controller.abort();
    else userSignal.addEventListener('abort', () => controller.abort(), { once: true });
  }
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
    next();
  }
}
