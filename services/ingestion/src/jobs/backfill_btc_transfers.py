import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import async_client, delete_transfers_range, transfers_df_for_bulk_insert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_btc_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_HOURS = 6
TABLE = "tradernick.transfers"

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


def _planned_chunks(since: datetime, until: datetime):
    chunks = []
    step = timedelta(hours=CHUNK_HOURS)
    t = since
    while t < until:
        t_end = min(t + step, until)
        chunks.append((t, t_end))
        t = t_end
    return chunks


async def _fetch_chunk(ds: AsyncDeFiStream, since: datetime, until: datetime) -> int:
    df = await (
        ds.bitcoin.native.transfers()
        .time_range(_iso_z(since), _iso_z(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    pd_df = transfers_df_for_bulk_insert(df, kind="btc", chain="BTC", token_override="BTC")
    ch = await async_client()
    await ch.insert_df(TABLE, pd_df)
    return len(pd_df)


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set"); sys.exit(2)

    job = await _load_job(job_id)
    job_type, args, started_at = job["job_type"], job["args"], job["started_at"]
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])
    completed_set = set(args.get("completed_chunks", []))

    chunks = _planned_chunks(since, until)
    total = len(chunks)
    done = sum(1 for cs, _ in chunks if cs.isoformat() in completed_set)

    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                       progress=(done / total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: chunks=%d resumed_at=%d force=%s",
             job_id, total, done, bool(args.get("force")))

    if args.get("force") and done == 0:
        log.info("job %s force=true: purging existing btc rows in [%s, %s)", job_id, since, until)
        await delete_transfers_range(
            where_extra="kind = 'btc' AND chain = 'BTC'",
            since=since,
            until=until,
        )
        log.info("job %s force purge done", job_id)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    try:
        for cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(completed_set)
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                   progress=(done / total) if total else 1.0, started_at=started_at,
                                   finished_at=_utcnow())
                return
            key = cs.isoformat()
            if key in completed_set:
                continue
            log.info("job %s chunk BTC %s..%s", job_id, cs, ce)
            n = await _fetch_chunk(ds, cs, ce)
            log.info("job %s chunk BTC rows=%d", job_id, n)
            completed_set.add(key)
            done += 1
            args["completed_chunks"] = sorted(completed_set)
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                               progress=done / total, started_at=started_at)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="completed",
                           progress=1.0, started_at=started_at, finished_at=_utcnow())
    except Exception as exc:
        log.exception("job %s failed: %s", job_id, exc)
        args["completed_chunks"] = sorted(completed_set)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="failed",
                           progress=(done / total) if total else 0.0, started_at=started_at,
                           finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_btc_transfers <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
