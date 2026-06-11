"""Backfill mode entry point: `python -m data_processor.backfill <job_id>`.

One subprocess per job. Reads its spec from `tradernick.ingestion_jobs`,
iterates every (materializer, partition_id) pair in the requested window,
and rebuilds via `build_partition`. Progress is tracked in `args`'s
`completed_chunks` list so a SIGTERM-then-respawn picks up where it left
off without redoing work.

Job args shape (set by the caller — either an HTTP handler or
JobManager._await on a source-backfill completion):

    {
      "materializers": ["exchange_flow_minute", "hl_fills_pnl_daily", ...],
      "since": "2026-06-07T18:00:00Z",
      "until": "2026-06-10T06:30:00Z",
      "completed_chunks": []
    }

`materializers` is a list of names from `registry.ALL_NAMES`. An unknown
name aborts the job (loud failure rather than silent skip).
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from clickhouse import async_client

from .registry import REGISTRY, by_name, ALL_NAMES
from .rebuild import build_partition, partition_ids_in_window

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [data_processor.backfill] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


_stop = False


def _on_sigterm(*_):
    global _stop
    log.info("SIGTERM received; will exit after current partition")
    _stop = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)


async def _load_job(job_id: str) -> dict:
    ch = await async_client()
    rows = await ch.query(
        """
        SELECT job_type, args, status, started_at
        FROM tradernick.ingestion_jobs FINAL
        WHERE job_id = {j:String}
        """,
        parameters={"j": job_id},
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
    started_at: datetime, finished_at: datetime | None = None,
    error: str | None = None,
):
    ch = await async_client()
    await ch.insert(
        "tradernick.ingestion_jobs",
        [[job_id, job_type, json.dumps(args), status, float(progress),
          started_at, finished_at, error, _utcnow()]],
        column_names=["job_id", "job_type", "args", "status", "progress",
                      "started_at", "finished_at", "error", "updated_at"],
    )


def _planned_chunks(materializer_names: list[str], since: datetime, until: datetime):
    """Yield (spec, partition_id) pairs covering the window for each
    materializer. Same shape as the per-source backfills' chunk lists."""
    out: list[tuple[str, str]] = []
    for name in materializer_names:
        spec = by_name(name)
        if spec is None:
            raise ValueError(f"unknown materializer {name!r} — known: {ALL_NAMES}")
        for pid in partition_ids_in_window(spec, since, until):
            out.append((spec.name, pid))
    return out


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    job = await _load_job(job_id)
    job_type = job["job_type"]
    args = job["args"]
    started_at = job["started_at"]

    materializers = list(args.get("materializers") or [])
    if not materializers:
        raise ValueError("args.materializers is empty — nothing to rebuild")
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])

    chunks = _planned_chunks(materializers, since, until)
    total = len(chunks)
    completed_set = {tuple(c) if isinstance(c, list) else c
                     for c in args.get("completed_chunks", [])}
    done = sum(1 for c in chunks if list(c) in args.get("completed_chunks", []))
    args["total_chunks"] = total
    args["materializers"] = materializers  # echo back so the UI knows

    await _write_status(
        job_id=job_id, job_type=job_type, args=args, status="running",
        progress=(done / total) if total else 1.0, started_at=started_at,
    )
    log.info(
        "job %s starting: materializers=%s window=[%s, %s) chunks=%d resumed=%d",
        job_id, materializers, since, until, total, done,
    )

    try:
        for mat_name, pid in chunks:
            if _stop:
                args["completed_chunks"] = [list(c) for c in sorted(completed_set)]
                await _write_status(
                    job_id=job_id, job_type=job_type, args=args, status="cancelled",
                    progress=(done / total) if total else 1.0,
                    started_at=started_at, finished_at=_utcnow(),
                )
                return
            if [mat_name, pid] in args.get("completed_chunks", []):
                continue
            spec = by_name(mat_name)
            assert spec is not None  # already validated in _planned_chunks
            # Backfill mode also respects locks so it can co-exist with a
            # running live worker. The live worker's recent tier owns the
            # most recent few partitions; backfill skips and moves on.
            await build_partition(spec, pid, skip_if_locked=True)
            completed_set.add((mat_name, pid))
            done += 1
            args["completed_chunks"] = [list(c) for c in sorted(completed_set)]
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
        log.exception("job %s failed", job_id)
        args["completed_chunks"] = [list(c) for c in sorted(completed_set)]
        await _write_status(
            job_id=job_id, job_type=job_type, args=args, status="failed",
            progress=(done / total) if total else 0.0,
            started_at=started_at, finished_at=_utcnow(), error=str(exc),
        )
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m data_processor.backfill <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
