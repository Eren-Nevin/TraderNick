"""Binance OHLCV backfill. defistream 2.22 multi-token form — one call
per chunk covers every configured token instead of one call per
(token, chunk) pair."""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import OHLCV_COLUMNS, async_client, ohlcv_df_to_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_DAYS = 7

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
    return {
        "job_type": r[0],
        "args": json.loads(r[1]),
        "status": r[2],
        "started_at": r[3],
    }


async def _write_status(
    *, job_id: str, job_type: str, args: dict, status: str, progress: float,
    started_at: datetime, finished_at: datetime | None = None, error: str | None = None,
):
    ch = await async_client()
    await ch.insert(
        "tradernick.ingestion_jobs",
        [[
            job_id, job_type, json.dumps(args), status, float(progress),
            started_at, finished_at, error, _utcnow(),
        ]],
        column_names=[
            "job_id", "job_type", "args", "status", "progress",
            "started_at", "finished_at", "error", "updated_at",
        ],
    )


def _planned_chunks(since: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
    """No per-token axis any more — each chunk is one multi-token call."""
    chunks: list[tuple[datetime, datetime]] = []
    step = timedelta(days=CHUNK_DAYS)
    t = since
    while t < until:
        t_end = min(t + step, until)
        chunks.append((t, t_end))
        t = t_end
    return chunks


async def _fetch_chunk(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.ohlcv()
        .token(*tokens)
        .window("1m")
        .time_range(_iso_z(since), _iso_z(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = ohlcv_df_to_rows(df)
    ch = await async_client()
    await ch.insert("tradernick.binance_ohlcv_1m", rows, column_names=OHLCV_COLUMNS)
    return len(rows)


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)

    job = await _load_job(job_id)
    job_type = job["job_type"]
    args = job["args"]
    started_at = job["started_at"]

    tokens = args["tokens"]
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])
    # 2.22 backfill state is chunk-only (multi-token per chunk). Legacy
    # (token, chunk_start) entries from older jobs are dropped on resume.
    completed_set = {s for s in args.get("completed_chunks", []) if isinstance(s, str)}

    chunks = _planned_chunks(since, until)
    total = len(chunks)
    done = sum(1 for cs, _ in chunks if cs.isoformat() in completed_set)

    await _write_status(
        job_id=job_id, job_type=job_type, args=args, status="running",
        progress=(done / total) if total else 1.0, started_at=started_at,
    )
    log.info("job %s starting: tokens=%d chunks=%d resumed_at=%d", job_id, len(tokens), total, done)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)

    try:
        for cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(completed_set)
                await _write_status(
                    job_id=job_id, job_type=job_type, args=args, status="cancelled",
                    progress=(done / total) if total else 1.0, started_at=started_at,
                    finished_at=_utcnow(),
                )
                log.info("job %s cancelled at %d/%d chunks", job_id, done, total)
                return
            key = cs.isoformat()
            if key in completed_set:
                continue
            log.info("job %s chunk %s..%s (tokens=%d)", job_id, cs, ce, len(tokens))
            n = await _fetch_chunk(ds, tokens, cs, ce)
            log.info("job %s chunk rows=%d", job_id, n)
            completed_set.add(key)
            done += 1
            args["completed_chunks"] = sorted(completed_set)
            await _write_status(
                job_id=job_id, job_type=job_type, args=args, status="running",
                progress=done / total, started_at=started_at,
            )
        await _write_status(
            job_id=job_id, job_type=job_type, args=args, status="completed",
            progress=1.0, started_at=started_at, finished_at=_utcnow(),
        )
        log.info("job %s completed", job_id)
    except Exception as exc:
        log.exception("job %s failed: %s", job_id, exc)
        args["completed_chunks"] = sorted(completed_set)
        await _write_status(
            job_id=job_id, job_type=job_type, args=args, status="failed",
            progress=(done / total) if total else 0.0, started_at=started_at,
            finished_at=_utcnow(), error=str(exc),
        )
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_binance_ohlcv <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
