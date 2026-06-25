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
import token_batches
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
# position_history is heavy (~147K rows per 5m snapshot across 26 tokens)
# — 1h chunks give ~1.76M rows per fetch (~100–200 MB JSON), safely
# within DS HTTP timeouts and Polars memory. If DS chokes on these in
# practice, drop to 30-min chunks (1,440 total for 30d, ~1h wallclock).
_CHUNK_HOURS = {
    "ohlcv":            6,
    "trades":           6,
    "fills":            6,
    # position_history: 2h chunks are fine for historical windows
    # (~5s/chunk, ~300k rows). The recurring "last chunk hangs" failures
    # are not a chunk-size issue — they're a live-overlap contention
    # issue handled by `_LIVE_OVERLAP_BUFFER` below.
    "position_history": 2,
    # trade_history is now DAILY absolute snapshots (window deprecated). Every
    # wallet that ever traded a token gets a daily row, so payloads scale with
    # (#wallets x #tokens x #days). At a 97-token roster a 7-day chunk pulled
    # ~18M rows across ~398k wallets into a ~9.4 GB process — OOM risk. 1-day
    # chunks keep each request to a single daily snapshot, bounding memory.
    "trade_history":    24,
    "transfers":        6,
    "funding":          6,
    "vaults":           6,
}

# Per-event "freshness buffer" — the backfill won't fetch chunks whose
# end-time is within this window of `now`. Lets the live worker own that
# recent zone and prevents the backfill from queueing the identical
# request behind a still-streaming live response on the same API key.
# Three jobs in a row (de1d2fa19, a4cd69dd, 91278d03) hung on the
# position_history chunk that included the most recent few hours
# while the live position_history sweep was simultaneously hitting DS
# for the same window. Without a buffer, every backfill `until ≈ now`
# request keeps reproducing the same hang.
from datetime import timedelta as _td
_LIVE_OVERLAP_BUFFER = {
    "position_history": _td(hours=2),
}

# Events that get one chunk PER TOKEN (instead of one multi-token chunk).
# Empty — every HL event including position_history uses multi-token
# chunks; chunk *time-window* sizes (above) keep response payloads bounded.
_PER_TOKEN_CHUNKED: set[str] = set()

_TOKEN_REQUIRED = {"ohlcv", "position_history", "trade_history"}
_PER_TOKEN_TABLE = {"ohlcv", "trades", "fills", "funding", "position_history", "trade_history"}


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
            # Pin to perp explicitly so a future DeFiStream default change
            # (e.g. expanding to include spot rows) can't silently start
            # polluting our tables. Mirrors the same lock in
            # groups/hyperliquid_events.py — every HL table we ingest is
            # perp-scoped by construction.
            b = b.market_type("perp")
            if event in _TOKEN_REQUIRED or event in _PER_TOKEN_TABLE:
                b = b.token(*tokens)
            b = b.date_range(_iso_z(since), _iso_z(until))
            if event == "ohlcv":
                b = b.window("1m")
            elif event == "position_history":
                # Mirrors the live group: 15m grid + $100 min position size.
                b = b.window("15m").min_size(100)
            elif event == "trade_history":
                # `window` deprecated — DeFiStream returns one DAILY absolute
                # (cumulative-from-inception) snapshot per wallet/token.
                pass
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
    tokens = args.get("tokens") or token_batches.get_ingest_tokens()
    events = args.get("events") or list(HL_EVENTS.keys())
    unknown = [e for e in events if e not in HL_EVENTS]
    if unknown: log.error("unknown events: %s", unknown); sys.exit(2)
    since = _parse_iso(args["since"]); until = _parse_iso(args["until"])
    completed_set = {tuple(k) if isinstance(k, list) else k for k in args.get("completed_chunks", [])}
    chunks = _planned_chunks(events, tokens, since, until)
    total = len(chunks)
    # Surface the exact planned total so the dashboard can render "X / Y
    # chunks done" instead of just X. The figure is stable for the life of
    # the job — chunk count only depends on (events, tokens, since, until)
    # and the per-event _CHUNK_HOURS map, none of which mutate post-launch.
    args["total_chunks"] = total
    def _chunk_key(ev: str, tok, cs):
        return f"{ev}|{tok}|{cs.isoformat()}" if tok else f"{ev}|{cs.isoformat()}"
    done = sum(1 for (ev, tok, cs, _) in chunks if _chunk_key(ev, tok, cs) in completed_set)
    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                        progress=(done/total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: tokens=%d events=%s chunks=%d resumed_at=%d",
             job_id, len(tokens), events, total, done)

    if args.get("force") and done == 0:
        ch = await async_client()
        # Scope the purge to the tokens being backfilled. Token-keyed tables
        # (everything except transfers/vaults) get `AND token IN (...)` so a
        # subset/batch force-backfill can NEVER wipe other tokens' rows in the
        # window. Regression guard for the 2026-06-13 incident where a Batch-2
        # force backfill purged Batch-1 ohlcv/trades/fills/funding because the
        # DELETE was window-only. transfers/vaults have no `token` column
        # (all-market), so they keep the window-only purge.
        tok_in = ",".join("'" + str(t).replace("'", "''") + "'" for t in tokens)
        for ev in events:
            _, table, _, _ = HL_EVENTS[ev]
            where = f"time >= '{_sql_dt(since)}' AND time <  '{_sql_dt(until)}'"
            if ev in _PER_TOKEN_TABLE and tok_in:
                where += f" AND token IN ({tok_in})"
            log.info("force purge: %s WHERE %s", table, where)
            await ch.command(f"ALTER TABLE {table} DELETE WHERE {where}")

    # Generous per-request timeout — matches the live worker
    # (streams/_hl_common.py:51). The SDK default is 600s, which DS
    # consistently fails to meet for heavy position_history chunks when
    # the API is under load. 1800s lets a slow-but-progressing response
    # finish streaming instead of dying at the 10-minute mark.
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY, timeout=1800.0)
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
