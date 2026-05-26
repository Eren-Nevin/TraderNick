"""Lido events backfill — mirrors backfill_uniswap_events.py.

Iterates chunks of (chain, event, start, end) through DeFiStream and inserts
into the matching per-event Lido table. Resumable via completed_chunks list
in the job args, 429-aware retry with backoff, 1.2s sleep between chunks to
stay under the shared per-minute rate budget.

Job body:
  {
    "days": 30,
    "chains": ["ETH","ARB","BASE",...],     # optional, default ETH + all L2s
    "events": ["deposit","l2_deposit",...], # optional, default all 5
    "force": false                          # purge existing range first
  }

A (chain, event) pair that doesn't exist on DeFiStream's side (e.g. an L2
that hasn't shipped a Lido bridge yet) is fast-forwarded after the first
'not found' error rather than failing the whole job — same dead-skip
pattern as the Uniswap backfill, applied per (chain, event).
"""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import LIDO_EVENTS, async_client, safe_ident

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_lido_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_HOURS = 24
INTER_CHUNK_SLEEP_S = 1.2
RETRY_DELAYS_S = (1.0, 3.0, 8.0, 20.0, 45.0)

_stop = False


def _on_sigterm(_signum, _frame):
    global _stop
    log.info("SIGTERM received; will exit after current chunk")
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
        """
        SELECT job_type, args, status, started_at
        FROM tradernick.ingestion_jobs FINAL
        WHERE job_id = {job_id:String}
        """,
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
    msg = str(exc).lower()
    return "too many requests" in msg or "429" in msg or "rate limit" in msg


def _is_not_supported(exc: Exception) -> bool:
    """DeFiStream returns a 400/404 with these messages when an (event, chain)
    isn't supported (e.g. l2_deposit on a chain without a Lido bridge, or a
    mainnet event queried on an L2)."""
    msg = str(exc).lower()
    return (
        "not found" in msg or "not available" in msg or "not supported" in msg
    )


# Mainnet-only event keys — running these against an L2 just wastes a call.
_ETH_ONLY_EVENTS = {"deposit", "withdrawal_request", "withdrawal_claimed"}
# L2-only event keys — same deal in reverse.
_L2_ONLY_EVENTS = {"l2_deposit", "l2_withdrawal_request"}


def _plan_pairs(chains: list[str], events: list[str]) -> list[tuple[str, str]]:
    """Cross-product (chain, event) but skip combinations that aren't
    physically meaningful — mainnet events on L2s, L2 events on ETH."""
    out: list[tuple[str, str]] = []
    for chain in chains:
        c = chain.upper()
        for ev in events:
            if c == "ETH" and ev in _L2_ONLY_EVENTS:
                continue
            if c != "ETH" and ev in _ETH_ONLY_EVENTS:
                continue
            out.append((c, ev))
    return out


def _planned_chunks(pairs, since, until):
    chunks = []
    step = timedelta(hours=CHUNK_HOURS)
    for chain, event in pairs:
        t = since
        while t < until:
            t_end = min(t + step, until)
            chunks.append((chain, event, t, t_end))
            t = t_end
    return chunks


async def _fetch_chunk(
    ds: AsyncDeFiStream,
    *,
    chain: str,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = LIDO_EVENTS[event]
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            log.info("rate-limited; backing off %.1fs (attempt %d)", delay, attempt)
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.lido, method_name)()
            builder = builder.network(chain).time_range(_iso_z(since), _iso_z(until))
            builder = builder.verbose().with_value()
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain)
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
        log.error("DEFISTREAM_API_KEY is not set"); sys.exit(2)

    job = await _load_job(job_id)
    job_type, args, started_at = job["job_type"], job["args"], job["started_at"]
    chains = args.get("chains") or (["ETH"] + list(config.LIDO_L2_CHAINS))
    events = args.get("events") or list(LIDO_EVENTS.keys())
    unknown = [e for e in events if e not in LIDO_EVENTS]
    if unknown:
        log.error("unknown events in job args: %s", unknown); sys.exit(2)
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])

    completed_set = {tuple(k) for k in args.get("completed_chunks", [])}
    pairs = _plan_pairs(chains, events)
    chunks = _planned_chunks(pairs, since, until)
    total = len(chunks)
    done = sum(
        1 for chain, event, cs, _ in chunks
        if (chain, event, cs.isoformat()) in completed_set
    )

    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                        progress=(done / total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: pairs=%d chunks=%d resumed_at=%d",
             job_id, len(pairs), total, done)

    if args.get("force") and done == 0:
        ch = await async_client()
        for chain, event in pairs:
            _, table, _, _ = LIDO_EVENTS[event]
            where = (
                f"chain = '{safe_ident(chain)}'"
                f" AND time >= '{_iso_z(since)}'"
                f" AND time <  '{_iso_z(until)}'"
            )
            log.info("force purge: ALTER %s DELETE %s", table, where)
            await ch.command(f"ALTER TABLE {table} DELETE WHERE {where}")
        log.info("job %s force purge done", job_id)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    # Skip the remaining chunks for any (chain, event) pair that DeFiStream
    # reports as unsupported — keeps the job moving when an L2 hasn't
    # shipped the Lido bridge yet, or when a chain was added to the config
    # ahead of DeFiStream coverage.
    dead_pairs: set[tuple[str, str]] = set()
    try:
        for chain, event, cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(map(list, completed_set))
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                    progress=(done / total) if total else 1.0,
                                    started_at=started_at, finished_at=_utcnow())
                return
            key = (chain, event, cs.isoformat())
            if key in completed_set:
                continue
            pair_key = (chain, event)
            label = f"{chain}/{event}"
            if pair_key in dead_pairs:
                log.info("job %s chunk %s SKIP (unsupported)", job_id, label)
                completed_set.add(key)
                done += 1
                continue
            log.info("job %s chunk %s %s..%s", job_id, label, cs, ce)
            try:
                n = await _fetch_chunk(
                    ds, chain=chain, event=event, since=cs, until=ce,
                )
            except Exception as exc:
                if _is_not_supported(exc):
                    log.warning(
                        "job %s pair %s/%s not on DeFiStream — skipping remaining: %s",
                        job_id, chain, event, exc,
                    )
                    dead_pairs.add(pair_key)
                    completed_set.add(key)
                    done += 1
                    continue
                raise
            log.info("job %s chunk %s rows=%d", job_id, label, n)
            completed_set.add(key)
            done += 1
            args["completed_chunks"] = sorted(map(list, completed_set))
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                                progress=done / total, started_at=started_at)
            await asyncio.sleep(INTER_CHUNK_SLEEP_S)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="completed",
                            progress=1.0, started_at=started_at, finished_at=_utcnow())
    except Exception as exc:
        log.exception("job %s failed: %s", job_id, exc)
        args["completed_chunks"] = sorted(map(list, completed_set))
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="failed",
                            progress=(done / total) if total else 0.0,
                            started_at=started_at, finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_lido_events <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
