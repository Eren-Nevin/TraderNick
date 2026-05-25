"""AAVE v3 events backfill.

Iterates chunks of (chain, eth_market, event, start, end) through DeFiStream
and inserts into the matching per-event table in `tradernick`. Reuses the
ingestion_jobs status row pattern + resumable completed_chunks list so a
crashed job picks up where it left off.

Chunk size is 24h — well under DeFiStream's 7-day parquet limit, but tight
enough that no single chunk is bigger than a few thousand events. With the
default 30-day window × 5 chains × 6 events (× 3 ETH markets) that's:

    non-ETH: 4 chains × 6 events × 30 days = 720
    ETH:     1 chain  × 3 markets × 6 events × 30 days = 540
    total:   1260 chunks per 30-day backfill.

Job body:
  {
    "days": 30,                                  # required
    "chains": ["ETH","ARB","BASE","BSC","POLYGON"],
    "events": ["deposit","withdraw","borrow","repay","flashloan","liquidation"],
    "eth_markets": ["Core","Prime","EtherFi"],   # only applied on ETH
    "force": false                               # purge existing range first
  }
"""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import AAVE_EVENTS, async_client, safe_ident

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_aave_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_HOURS = 24
# DeFiStream throttles bursts: walking 1260 chunks at zero-delay back-to-back
# trips a 429 well before the backfill finishes. Sleep a tick between chunks
# (keeps us well under the limit) and retry on 429 with exponential backoff.
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


def _planned_chunks(
    chains: list[str],
    events: list[str],
    eth_markets: list[str],
    since: datetime,
    until: datetime,
):
    """Yield (chain, eth_market, event, cs, ce). eth_market='' on non-ETH."""
    chunks = []
    step = timedelta(hours=CHUNK_HOURS)
    for chain in chains:
        markets: list[str] = eth_markets if chain.upper() == "ETH" else [""]
        for market in markets:
            for event in events:
                t = since
                while t < until:
                    t_end = min(t + step, until)
                    chunks.append((chain, market, event, t, t_end))
                    t = t_end
    return chunks


def _is_rate_limit(exc: Exception) -> bool:
    """DeFiStream's SDK raises a generic exception with 'Too Many Requests'
    in the message — sniff that to decide whether to back off + retry."""
    msg = str(exc).lower()
    return "too many requests" in msg or "429" in msg or "rate limit" in msg


async def _fetch_chunk(
    ds: AsyncDeFiStream,
    *,
    chain: str,
    eth_market: str,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = AAVE_EVENTS[event]

    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            log.info("rate-limited; backing off %.1fs (attempt %d)", delay, attempt)
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.aave_v3, method_name)()
            builder = builder.network(chain).time_range(_iso_z(since), _iso_z(until))
            builder = builder.verbose().with_value()
            if eth_market:
                builder = builder.eth_market_type(eth_market)
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain, eth_market=eth_market)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit(exc):
                raise
    # All retries exhausted on a rate-limit — let the job's catch handle it.
    assert last_exc is not None
    raise last_exc


async def _force_purge(chains, events, eth_markets, since, until):
    """Delete existing rows in the time range for every (chain, eth_market,
    event) combination this job will write to. Runs once at start when
    args.force=true."""
    ch = await async_client()
    for event in events:
        _, table, _, _ = AAVE_EVENTS[event]
        for chain in chains:
            markets = eth_markets if chain.upper() == "ETH" else [""]
            for market in markets:
                where = (
                    f"chain = '{safe_ident(chain)}'"
                    f" AND eth_market = '{safe_ident(market)}'"
                    f" AND time >= '{_iso_z(since)}'"
                    f" AND time <  '{_iso_z(until)}'"
                )
                log.info("force purge: DELETE FROM %s WHERE %s", table, where)
                await ch.command(f"ALTER TABLE {table} DELETE WHERE {where}")


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set"); sys.exit(2)

    job = await _load_job(job_id)
    job_type, args, started_at = job["job_type"], job["args"], job["started_at"]
    chains: list[str] = args["chains"]
    events: list[str] = args.get("events", list(AAVE_EVENTS.keys()))
    eth_markets: list[str] = args.get("eth_markets", ["Core", "Prime", "EtherFi"])
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])

    # Validate events list against the registry.
    unknown = [e for e in events if e not in AAVE_EVENTS]
    if unknown:
        log.error("unknown events in job args: %s", unknown); sys.exit(2)

    completed_set = {tuple(k) for k in args.get("completed_chunks", [])}
    chunks = _planned_chunks(chains, events, eth_markets, since, until)
    total = len(chunks)
    done = sum(
        1 for chain, market, event, cs, _ in chunks
        if (chain, market, event, cs.isoformat()) in completed_set
    )

    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                        progress=(done / total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: chains=%s events=%s eth_markets=%s chunks=%d resumed_at=%d force=%s",
             job_id, chains, events, eth_markets, total, done, bool(args.get("force")))

    if args.get("force") and done == 0:
        await _force_purge(chains, events, eth_markets, since, until)
        log.info("job %s force purge done", job_id)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    try:
        for chain, market, event, cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(map(list, completed_set))
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                    progress=(done / total) if total else 1.0,
                                    started_at=started_at, finished_at=_utcnow())
                return
            key = (chain, market, event, cs.isoformat())
            if key in completed_set:
                continue
            label = f"{chain}" + (f"/{market}" if market else "") + f"/{event}"
            log.info("job %s chunk %s %s..%s", job_id, label, cs, ce)
            n = await _fetch_chunk(
                ds, chain=chain, eth_market=market, event=event, since=cs, until=ce,
            )
            log.info("job %s chunk %s rows=%d", job_id, label, n)
            completed_set.add(key)
            done += 1
            args["completed_chunks"] = sorted(map(list, completed_set))
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                                progress=done / total, started_at=started_at)
            # Spacing between chunks — keeps the steady-state request rate
            # below DeFiStream's per-second cap so we don't have to lean on
            # the 429 retry path.
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
        print("usage: python -m jobs.backfill_aave_events <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
