import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import OPEN_INTEREST_COLUMNS, async_client, open_interest_df_to_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_open_interest] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_DAYS = 31
TABLE = "tradernick.binance_open_interest"

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


def _planned_chunks(tokens: list[str], since: datetime, until: datetime) -> list[tuple[str, datetime, datetime]]:
    chunks: list[tuple[str, datetime, datetime]] = []
    step = timedelta(days=CHUNK_DAYS)
    for token in tokens:
        t = since
        while t < until:
            t_end = min(t + step, until)
            chunks.append((token, t, t_end))
            t = t_end
    return chunks


async def _fetch_chunk(ds: AsyncDeFiStream, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.open_interest()
        .token(token)
        .time_range(_iso_z(since), _iso_z(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = open_interest_df_to_rows(df, token)
    ch = await async_client()
    await ch.insert(TABLE, rows, column_names=OPEN_INTEREST_COLUMNS)
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
    completed_set = {(t, s) for t, s in args.get("completed_chunks", [])}

    chunks = _planned_chunks(tokens, since, until)
    total = len(chunks)
    done = sum(1 for token, cs, _ in chunks if (token, cs.isoformat()) in completed_set)

    await _write_status(
        job_id=job_id, job_type=job_type, args=args, status="running",
        progress=(done / total) if total else 1.0, started_at=started_at,
    )
    log.info("job %s starting: tokens=%s chunks=%d resumed_at=%d", job_id, tokens, total, done)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)

    try:
        for token, cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(completed_set)
                await _write_status(
                    job_id=job_id, job_type=job_type, args=args, status="cancelled",
                    progress=(done / total) if total else 1.0, started_at=started_at,
                    finished_at=_utcnow(),
                )
                log.info("job %s cancelled at %d/%d chunks", job_id, done, total)
                return
            key = (token, cs.isoformat())
            if key in completed_set:
                continue
            log.info("job %s chunk %s %s..%s", job_id, token, cs, ce)
            n = await _fetch_chunk(ds, token, cs, ce)
            log.info("job %s chunk %s rows=%d", job_id, token, n)
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
        print("usage: python -m jobs.backfill_binance_open_interest <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
