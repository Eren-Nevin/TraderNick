"""Hyperliquid backfill. 8 endpoints, per-endpoint chunk size that mirrors
the live group's gap-fill cadence (6h for high-volume, 24h otherwise).

Each chunk fires one multi-token call (or unfiltered, for endpoints that
don't take a token). The completed_chunks state is a list of
"<event>|<chunk_start_iso>" strings — one entry per (event, chunk) pair —
so resume works correctly even though events have different chunk sizes."""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import HL_EVENTS, async_client, safe_ident

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_hyperliquid_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

INTER_CHUNK_SLEEP_S = 1.2
RETRY_DELAYS_S = (1.0, 3.0, 8.0, 20.0, 45.0)
_stop = False


def _on_sigterm(*_):
    global _stop; _stop = True


def _utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None)
def _parse_iso(s): return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)
def _iso_z(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def _sql_dt(dt): return dt.strftime("%Y-%m-%d %H:%M:%S")


# Per-endpoint backfill chunk size in hours. All events use multi-token
# chunks (one call covers all 26 tokens) so each chunk contains 26x the
# payload of a single-token call — chunk sizes here are tuned per-event
# so the response stays small enough to stream cleanly.
#
# trade_history is pre-aggregated server-side (rows are per-(wallet,
# token, bucket) summaries, not per-event), so 6h × 26 tokens is small
# (~200K rows) and well within HTTP-stream limits.
#
# position_history is currently DEFERRED (see HL_EVENTS in clickhouse.py)
# — its per-(wallet × token × snapshot-tick) fan-out is too large for any
# reasonable multi-token chunk to complete. If/when re-enabled it'll need
# a different approach (server-side aggregate / as_link streaming).
_CHUNK_HOURS = {
    "ohlcv":            6,
    "trades":           6,
    "fills":            6,
    "trade_history":    6,
    "transfers":        24,
    "funding":          24,
    "vaults":           24,
}

# Events that get one chunk PER TOKEN (instead of one multi-token chunk).
# Empty for now — position_history was the only candidate and it's
# deferred. If a future event needs per-token chunking, add it here.
_PER_TOKEN_CHUNKED: set[str] = set()

_TOKEN_REQUIRED = {"ohlcv", "trade_history"}
_PER_TOKEN_TABLE = {"ohlcv", "trades", "fills", "funding", "trade_history"}


async def _load_job(job_id):
    ch = await async_client()
    rows = await ch.query(
        "SELECT job_type, args, status, started_at FROM tradernick.ingestion_jobs FINAL WHERE job_id = {j:String}",
        parameters={"j": job_id})
    if not rows.result_rows: raise RuntimeError(f"job {job_id} not found")
    r = rows.result_rows[0]
    return {"job_type": r[0], "args": json.loads(r[1]), "status": r[2], "started_at": r[3]}


async def _write_status(*, job_id, job_type, args, status, progress, started_at, finished_at=None, error=None):
    ch = await async_client()
    await ch.insert("tradernick.ingestion_jobs",
        [[job_id, job_type, json.dumps(args), status, float(progress), started_at, finished_at, error, _utcnow()]],
        column_names=["job_id","job_type","args","status","progress","started_at","finished_at","error","updated_at"])


def _is_rate_limit(exc):
    m = str(exc).lower()
    return "too many requests" in m or "429" in m or "rate limit" in m


def _planned_chunks(events, tokens, since, until):
    """Returns list of (event, token_or_None, chunk_start, chunk_end).
    Events in _PER_TOKEN_CHUNKED get one chunk per (token, time-window)
    so each fetch carries only one token's data — keeps the response
    payload bounded for high-cardinality endpoints like position_history
    where one 24h × 26-token call hangs the HTTP client."""
    out = []
    for ev in events:
        step = timedelta(hours=_CHUNK_HOURS[ev])
        if ev in _PER_TOKEN_CHUNKED:
            for tok in tokens:
                t = since
                while t < until:
                    t_end = min(t + step, until)
                    out.append((ev, tok, t, t_end))
                    t = t_end
        else:
            t = since
            while t < until:
                t_end = min(t + step, until)
                out.append((ev, None, t, t_end))
                t = t_end
    return out


async def _fetch_chunk(ds, *, event, tokens, since, until):
    """tokens is either the full roster (multi-token chunks) or a
    single-element list (per-token chunks for high-cardinality events)."""
    method, table, columns, transform = HL_EVENTS[event]
    last_exc = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            log.info("rate-limited; backoff %.1fs (attempt %d)", delay, attempt)
            await asyncio.sleep(delay)
        try:
            b = getattr(ds.exchange.hyperliquid, method)()
            if event in _TOKEN_REQUIRED or event in _PER_TOKEN_TABLE:
                b = b.token(*tokens)
            b = b.date_range(_iso_z(since), _iso_z(until))
            if event == "ohlcv":
                b = b.window("1m")
            df = await b.as_df("polars")
            if df.is_empty(): return 0
            rows = transform(df)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit(exc): raise
    raise last_exc


async def main(job_id):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    if not config.DEFISTREAM_API_KEY: sys.exit(2)
    job = await _load_job(job_id)
    job_type, args, started_at = job["job_type"], job["args"], job["started_at"]
    tokens = args.get("tokens") or list(config.INGEST_TOKENS)
    events = args.get("events") or list(HL_EVENTS.keys())
    unknown = [e for e in events if e not in HL_EVENTS]
    if unknown: log.error("unknown events: %s", unknown); sys.exit(2)
    since = _parse_iso(args["since"]); until = _parse_iso(args["until"])
    completed_set = {tuple(k) if isinstance(k, list) else k for k in args.get("completed_chunks", [])}
    chunks = _planned_chunks(events, tokens, since, until)
    total = len(chunks)
    def _chunk_key(ev: str, tok, cs):
        return f"{ev}|{tok}|{cs.isoformat()}" if tok else f"{ev}|{cs.isoformat()}"
    done = sum(1 for (ev, tok, cs, _) in chunks if _chunk_key(ev, tok, cs) in completed_set)
    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                        progress=(done/total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: tokens=%d events=%s chunks=%d resumed_at=%d",
             job_id, len(tokens), events, total, done)

    if args.get("force") and done == 0:
        ch = await async_client()
        for ev in events:
            _, table, _, _ = HL_EVENTS[ev]
            where = f"time >= '{_sql_dt(since)}' AND time <  '{_sql_dt(until)}'"
            log.info("force purge: %s WHERE %s", table, where)
            await ch.command(f"ALTER TABLE {table} DELETE WHERE {where}")

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    try:
        for (ev, tok, cs, ce) in chunks:
            if _stop:
                args["completed_chunks"] = sorted(completed_set)
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                    progress=(done/total) if total else 1.0,
                                    started_at=started_at, finished_at=_utcnow())
                return
            key = _chunk_key(ev, tok, cs)
            if key in completed_set: continue
            # For per-token-chunked events we pass [tok] so the fetch is
            # restricted to a single token; multi-token events pass the
            # full roster.
            call_tokens = [tok] if tok else tokens
            log.info("chunk %s%s %s..%s", ev, f"/{tok}" if tok else "", cs, ce)
            n = await _fetch_chunk(ds, event=ev, tokens=call_tokens, since=cs, until=ce)
            log.info("chunk %s%s rows=%d", ev, f"/{tok}" if tok else "", n)
            completed_set.add(key); done += 1
            args["completed_chunks"] = sorted(completed_set)
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                                progress=done/total, started_at=started_at)
            await asyncio.sleep(INTER_CHUNK_SLEEP_S)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="completed",
                            progress=1.0, started_at=started_at, finished_at=_utcnow())
    except Exception as exc:
        log.exception("job %s failed", job_id)
        args["completed_chunks"] = sorted(completed_set)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="failed",
                            progress=(done/total) if total else 0.0,
                            started_at=started_at, finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_hyperliquid_events <job_id>", file=sys.stderr); sys.exit(2)
    asyncio.run(main(sys.argv[1]))
