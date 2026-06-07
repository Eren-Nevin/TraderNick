"""Backfill: rebuild tradernick.exchange_flow_minute from transfers FINAL.

One-shot job. Wraps scripts.bootstrap_wallets._refresh_exchange_flow_worker
so the operation appears in the standard ingestion_jobs table with the
same lifecycle (running → completed / failed) as a DeFiStream backfill.

Used by:
- the admin UI's "Rebuild exchange_flow" button on the Data process page
- automated triggers (e.g. tradernick_admin firing this after a wallet
  parquet upload, so the rollup picks up the new entity mapping)
"""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from clickhouse import async_client
from scripts.bootstrap_wallets import _refresh_exchange_flow_worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [backfill_exchange_flow] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_stop = False


def _on_sigterm(_signum, _frame):
    global _stop
    log.info("SIGTERM received; will exit after current step")
    _stop = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _load_job(job_id: str) -> dict:
    ch = await async_client()
    rows = await ch.query(
        "SELECT job_type, args, status, started_at FROM tradernick.ingestion_jobs FINAL "
        "WHERE job_id = {job_id:String}",
        parameters={"job_id": job_id},
    )
    if not rows.result_rows:
        raise RuntimeError(f"job {job_id} not found")
    r = rows.result_rows[0]
    return {"job_type": r[0], "args": json.loads(r[1]),
            "status": r[2], "started_at": r[3]}


async def _write_status(*, job_id, job_type, args, status, progress,
                        started_at, finished_at=None, error=None):
    ch = await async_client()
    await ch.insert(
        "tradernick.ingestion_jobs",
        [[job_id, job_type, json.dumps(args), status, float(progress),
          started_at, finished_at, error, _utcnow()]],
        column_names=["job_id", "job_type", "args", "status", "progress",
                      "started_at", "finished_at", "error", "updated_at"],
    )


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    job = await _load_job(job_id)
    job_type = job["job_type"]
    args = job["args"]
    started_at = job["started_at"]
    await _write_status(job_id=job_id, job_type=job_type, args=args,
                        status="running", progress=0.0, started_at=started_at)
    log.info("job %s starting exchange_flow rebuild", job_id)
    try:
        result = await _refresh_exchange_flow_worker()
        if not result.get("ok") and result.get("reason") == "already_running":
            # The monolith's self-heal loop happens to be mid-flight. Don't
            # treat as failure — the rebuild is happening, just not by us.
            args["rebuild_result"] = result
            await _write_status(job_id=job_id, job_type=job_type, args=args,
                                status="completed", progress=1.0,
                                started_at=started_at, finished_at=_utcnow())
            log.info("job %s no-op: another rebuild was already running", job_id)
            return
        args["rebuild_result"] = {
            "rows_after": int(result.get("rows_after") or 0),
            "duration_s": float(result.get("duration_s") or 0.0),
        }
        await _write_status(job_id=job_id, job_type=job_type, args=args,
                            status="completed", progress=1.0,
                            started_at=started_at, finished_at=_utcnow())
        log.info("job %s completed: %s", job_id, args["rebuild_result"])
    except Exception as exc:
        log.exception("job %s failed", job_id)
        await _write_status(job_id=job_id, job_type=job_type, args=args,
                            status="failed", progress=0.0, started_at=started_at,
                            finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_exchange_flow_minute <job_id>",
              file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
