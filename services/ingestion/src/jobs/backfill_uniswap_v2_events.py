"""Uniswap V2 events backfill — mirrors backfill_uniswap_events.py without
the fee_tier axis. Iterates chunks of (chain, symbol0, symbol1, event)."""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import UNISWAP_V2_EVENTS, async_client, safe_ident

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_uniswap_v2_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_HOURS = 24
INTER_CHUNK_SLEEP_S = 1.2
RETRY_DELAYS_S = (1.0, 3.0, 8.0, 20.0, 45.0)

_stop = False


def _on_sigterm(_signum, _frame):
    global _stop
    _stop = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)


def _iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _load_job(job_id: str) -> dict:
    ch = await async_client()
    rows = await ch.query(
        "SELECT job_type, args, status, started_at FROM tradernick.ingestion_jobs FINAL WHERE job_id = {job_id:String}",
        parameters={"job_id": job_id},
    )
    if not rows.result_rows:
        raise RuntimeError(f"job {job_id} not found")
    r = rows.result_rows[0]
    return {"job_type": r[0], "args": json.loads(r[1]), "status": r[2], "started_at": r[3]}


async def _write_status(*, job_id, job_type, args, status, progress, started_at,
                        finished_at=None, error=None):
    ch = await async_client()
    await ch.insert(
        "tradernick.ingestion_jobs",
        [[job_id, job_type, json.dumps(args), status, float(progress),
          started_at, finished_at, error, _utcnow()]],
        column_names=["job_id", "job_type", "args", "status", "progress",
                      "started_at", "finished_at", "error", "updated_at"],
    )


def _is_rate_limit(exc: Exception) -> bool:
    m = str(exc).lower()
    return "too many requests" in m or "429" in m or "rate limit" in m


def _is_not_supported(exc: Exception) -> bool:
    m = str(exc).lower()
    return (
        "pool not found" in m or "not available" in m or "not supported" in m
        or "not configured" in m
    )


def _planned_chunks(pools, events, since, until):
    out = []
    step = timedelta(hours=CHUNK_HOURS)
    for chain, sym0, sym1 in pools:
        for event in events:
            t = since
            while t < until:
                t_end = min(t + step, until)
                out.append((chain, sym0, sym1, event, t, t_end))
                t = t_end
    return out


async def _fetch_chunk(ds, *, chain, symbol0, symbol1, event, since, until) -> int:
    method_name, table, columns, transform = UNISWAP_V2_EVENTS[event]
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            log.info("rate-limited; backoff %.1fs (attempt %d)", delay, attempt)
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.uniswap_v2, method_name)(symbol0, symbol1)
            builder = builder.network(chain).time_range(_iso_z(since), _iso_z(until))
            builder = builder.verbose().with_value()
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain, symbol0=symbol0, symbol1=symbol1)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit(exc):
                raise
    assert last_exc is not None
    raise last_exc


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    if not config.DEFISTREAM_API_KEY:
        sys.exit(2)

    job = await _load_job(job_id)
    job_type, args, started_at = job["job_type"], job["args"], job["started_at"]
    pools_arg = args.get("pools") or [
        [c, s0, s1] for (c, s0, s1) in config.UNI_V2_POOLS
    ]
    pools = [(p[0], p[1], p[2]) for p in pools_arg]
    events = args.get("events", list(UNISWAP_V2_EVENTS.keys()))
    unknown = [e for e in events if e not in UNISWAP_V2_EVENTS]
    if unknown:
        log.error("unknown events: %s", unknown); sys.exit(2)
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])

    completed_set = {tuple(k) for k in args.get("completed_chunks", [])}
    chunks = _planned_chunks(pools, events, since, until)
    total = len(chunks)
    done = sum(
        1 for chain, sym0, sym1, event, cs, _ in chunks
        if (chain, sym0, sym1, event, cs.isoformat()) in completed_set
    )

    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                        progress=(done / total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: pools=%d events=%s chunks=%d resumed_at=%d",
             job_id, len(pools), events, total, done)

    if args.get("force") and done == 0:
        ch = await async_client()
        for event in events:
            _, table, _, _ = UNISWAP_V2_EVENTS[event]
            for chain, sym0, sym1 in pools:
                where = (
                    f"chain = '{safe_ident(chain)}'"
                    f" AND symbol0 = '{safe_ident(sym0)}'"
                    f" AND symbol1 = '{safe_ident(sym1)}'"
                    f" AND time >= '{_iso_z(since)}'"
                    f" AND time <  '{_iso_z(until)}'"
                )
                log.info("force purge: ALTER %s DELETE %s", table, where)
                await ch.command(f"ALTER TABLE {table} DELETE WHERE {where}")

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    dead_pools: set[tuple[str, str, str]] = set()
    try:
        for chain, sym0, sym1, event, cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(map(list, completed_set))
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                    progress=(done / total) if total else 1.0,
                                    started_at=started_at, finished_at=_utcnow())
                return
            key = (chain, sym0, sym1, event, cs.isoformat())
            if key in completed_set:
                continue
            pool_key = (chain, sym0, sym1)
            label = f"{chain}/{sym0}-{sym1}/{event}"
            if pool_key in dead_pools:
                completed_set.add(key); done += 1; continue
            log.info("chunk %s %s..%s", label, cs, ce)
            try:
                n = await _fetch_chunk(ds, chain=chain, symbol0=sym0, symbol1=sym1,
                                       event=event, since=cs, until=ce)
            except Exception as exc:
                if _is_not_supported(exc):
                    log.warning("pool %s not on DeFiStream — skipping: %s", label, exc)
                    dead_pools.add(pool_key)
                    completed_set.add(key); done += 1; continue
                raise
            log.info("chunk %s rows=%d", label, n)
            completed_set.add(key); done += 1
            args["completed_chunks"] = sorted(map(list, completed_set))
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                                progress=done / total, started_at=started_at)
            await asyncio.sleep(INTER_CHUNK_SLEEP_S)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="completed",
                            progress=1.0, started_at=started_at, finished_at=_utcnow())
    except Exception as exc:
        log.exception("job %s failed", job_id)
        args["completed_chunks"] = sorted(map(list, completed_set))
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="failed",
                            progress=(done / total) if total else 0.0,
                            started_at=started_at, finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_uniswap_v2_events <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
