"""Backfill: rematerialize transfers' wallet-derived columns + indexes.

Triggered after a wallet-labels parquet upload so the dictGet-sourced
MATERIALIZED columns (sender/receiver categories + entity) on the
`transfers` table reflect the new mapping. Internally:

  1. SYSTEM RELOAD DICTIONARY wallet_labels
  2. MATERIALIZE COLUMN ×4 (sender/receiver categories + entity)
  3. Wait for column mutations to finish
  4. DROP + ADD + MATERIALIZE INDEX ×4 (skip indexes layered over those
     materialized columns)

Then optionally fire the exchange_flow rebuild so the rollup reflects
the new entity mapping. The whole thing is monolithic — there are no
chunks — so progress goes 0 → 1.0 once at the end.

Currently exposed only at the HTTP layer (POST
/jobs/backfill/transfers_rematerialize); the user can kick it manually
from the Data process backfill page, and tradernick_admin will fire it
automatically after a wallets parquet upload (post-Phase B).
"""
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from clickhouse import async_client
from scripts.bootstrap_wallets import _rematerialize_worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [backfill_transfers_remat] %(levelname)s %(message)s")
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
    table = args.get("table", "tradernick.transfers")
    refresh_flow = bool(args.get("refresh_exchange_flow", True))

    await _write_status(job_id=job_id, job_type=job_type, args=args,
                        status="running", progress=0.0, started_at=started_at)
    log.info("job %s starting transfers rematerialize on %s "
             "(refresh_exchange_flow=%s)", job_id, table, refresh_flow)

    # Throttle progress writes so the long MATERIALIZE COLUMN wait doesn't
    # flood ingestion_jobs with sub-percent updates. We poll mutations every
    # 5s inside `_wait_for_mutations`; persist at most one row every ~30s.
    _last_write_at = 0.0
    _last_progress = -1.0

    async def progress_cb(stage: str, p: float):
        nonlocal _last_write_at, _last_progress
        now = _utcnow().timestamp()
        # Always write on first call and on coarse milestones; otherwise
        # rate-limit by elapsed wall-clock + progress delta.
        if (now - _last_write_at < 30.0 and abs(p - _last_progress) < 0.02
                and 0.0 < _last_progress < 1.0):
            return
        _last_write_at = now
        _last_progress = p
        args["stage"] = stage
        try:
            await _write_status(job_id=job_id, job_type=job_type, args=args,
                                status="running", progress=p,
                                started_at=started_at)
        except Exception:  # noqa: BLE001
            log.exception("progress write failed (continuing)")

    try:
        # _rematerialize_worker kicks the exchange_flow rebuild internally
        # (always, regardless of `refresh_flow`). The old job-wrapper kick
        # here was a duplicate that spawned a redundant backfill_data_processor
        # job — REPLACE PARTITION made it harmless but wasteful. Flag is
        # preserved in args for visibility; the worker kick can't be turned
        # off without a code edit to bootstrap_wallets.py.
        args["refresh_exchange_flow"] = refresh_flow
        await _rematerialize_worker(table, progress_cb=progress_cb)
        await _write_status(job_id=job_id, job_type=job_type, args=args,
                            status="completed", progress=1.0,
                            started_at=started_at, finished_at=_utcnow())
        log.info("job %s completed", job_id)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        await _write_status(job_id=job_id, job_type=job_type, args=args,
                            status="failed", progress=0.0, started_at=started_at,
                            finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_transfers_rematerialize <job_id>",
              file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
