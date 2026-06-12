"""Backfill: rebuild tradernick.exchange_flow_minute.

LEGACY SHIM. The actual rebuild work now lives in `data_processor.backfill`
and is parameterized by a `materializers` list in `args`. This module
remains so existing operator runbooks / dashboard endpoints that target
`JOB_TYPE_BACKFILL_EXCHANGE_FLOW_MINUTE` keep working: a job of this
type simply forwards to the unified backfill with
`materializers=['exchange_flow_minute']`.

The forwarded job_id is NOT the same as this job's id — instead, this
job records the spawned child's id in its `args.forwarded_to_job_id`
and then waits on the child's status. From the user's perspective the
two are stitched together by the admin UI's job table.
"""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from clickhouse import async_client

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
    """Forward to data_processor.backfill main() directly. We're already
    inside an `ingestion_jobs` row of type `backfill_exchange_flow_minute`,
    so rather than spawning a child job we just rebuild this job's args
    in-place to look like a backfill_data_processor job and run it.
    """
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    job = await _load_job(job_id)
    job_type = job["job_type"]
    args = job["args"]
    started_at = job["started_at"]

    # The legacy job_type didn't carry materializers. Default to the
    # single exchange_flow_minute target plus the same since/until.
    args.setdefault("materializers", ["exchange_flow_minute"])
    # since/until are already present on the row (every backfill row has
    # them via create_backfill_args), so data_processor.backfill can run
    # against `args` directly.
    await _write_status(job_id=job_id, job_type=job_type, args=args,
                        status="running", progress=0.0, started_at=started_at)
    log.info("job %s legacy exchange_flow rebuild — forwarding to data_processor.backfill",
             job_id)
    try:
        from data_processor.backfill import main as dp_main
        await dp_main(job_id)
        log.info("job %s completed via data_processor.backfill", job_id)
    except SystemExit:
        # data_processor.backfill calls sys.exit(1) on failure; let that
        # propagate so the supervisor surfaces the bad exit code.
        raise
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
