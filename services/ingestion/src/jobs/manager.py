import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from clickhouse import async_client

log = logging.getLogger("jobs")

JOB_TYPE_BACKFILL_OHLCV = "backfill_binance_ohlcv"
JOB_TYPE_BACKFILL_RAW_TRADES = "backfill_binance_raw_trades"
JOB_TYPE_BACKFILL_OPEN_INTEREST = "backfill_binance_open_interest"
JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS = "backfill_binance_long_short_ratios"
JOB_TYPE_BACKFILL_FUNDING_RATE = "backfill_binance_funding_rate"
JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS = "backfill_evm_erc20_transfers"
JOB_TYPE_BACKFILL_EVM_NATIVE_TRANSFERS = "backfill_evm_native_transfers"
JOB_TYPE_BACKFILL_BTC_TRANSFERS = "backfill_btc_transfers"
JOB_TYPE_BACKFILL_TRON_NATIVE_TRANSFERS = "backfill_tron_native_transfers"
JOB_TYPE_BACKFILL_TRON_TRC20_TRANSFERS = "backfill_tron_trc20_transfers"
JOB_TYPE_BACKFILL_AAVE_EVENTS = "backfill_aave_events"
JOB_TYPE_BACKFILL_UNISWAP_EVENTS = "backfill_uniswap_events"
JOB_TYPE_BACKFILL_LIDO_EVENTS = "backfill_lido_events"
JOB_TYPE_BACKFILL_AAVE_V2_EVENTS = "backfill_aave_v2_events"
JOB_TYPE_BACKFILL_UNISWAP_V2_EVENTS = "backfill_uniswap_v2_events"
JOB_TYPE_BACKFILL_UNISWAP_V4_EVENTS = "backfill_uniswap_v4_events"
JOB_TYPE_BACKFILL_AERO_EVENTS = "backfill_aero_events"
JOB_TYPE_BACKFILL_AERO_BASIC_EVENTS = "backfill_aero_basic_events"
JOB_TYPE_BACKFILL_AAVE_V4_EVENTS = "backfill_aave_v4_events"
JOB_TYPE_BACKFILL_MORPHO_EVENTS = "backfill_morpho_events"
JOB_TYPE_BACKFILL_SPARK_EVENTS = "backfill_spark_events"
JOB_TYPE_BACKFILL_GMX_EVENTS = "backfill_gmx_events"
JOB_MODULES = {
    JOB_TYPE_BACKFILL_OHLCV: "jobs.backfill_binance_ohlcv",
    JOB_TYPE_BACKFILL_RAW_TRADES: "jobs.backfill_binance_raw_trades",
    JOB_TYPE_BACKFILL_OPEN_INTEREST: "jobs.backfill_binance_open_interest",
    JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS: "jobs.backfill_binance_long_short_ratios",
    JOB_TYPE_BACKFILL_FUNDING_RATE: "jobs.backfill_binance_funding_rate",
    JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS: "jobs.backfill_evm_erc20_transfers",
    JOB_TYPE_BACKFILL_EVM_NATIVE_TRANSFERS: "jobs.backfill_evm_native_transfers",
    JOB_TYPE_BACKFILL_BTC_TRANSFERS: "jobs.backfill_btc_transfers",
    JOB_TYPE_BACKFILL_TRON_NATIVE_TRANSFERS: "jobs.backfill_tron_native_transfers",
    JOB_TYPE_BACKFILL_TRON_TRC20_TRANSFERS: "jobs.backfill_tron_trc20_transfers",
    JOB_TYPE_BACKFILL_AAVE_EVENTS: "jobs.backfill_aave_events",
    JOB_TYPE_BACKFILL_UNISWAP_EVENTS: "jobs.backfill_uniswap_events",
    JOB_TYPE_BACKFILL_LIDO_EVENTS: "jobs.backfill_lido_events",
    JOB_TYPE_BACKFILL_AAVE_V2_EVENTS: "jobs.backfill_aave_v2_events",
    JOB_TYPE_BACKFILL_UNISWAP_V2_EVENTS: "jobs.backfill_uniswap_v2_events",
    JOB_TYPE_BACKFILL_UNISWAP_V4_EVENTS: "jobs.backfill_uniswap_v4_events",
    JOB_TYPE_BACKFILL_AERO_EVENTS: "jobs.backfill_aero_events",
    JOB_TYPE_BACKFILL_AERO_BASIC_EVENTS: "jobs.backfill_aero_basic_events",
    JOB_TYPE_BACKFILL_AAVE_V4_EVENTS: "jobs.backfill_aave_v4_events",
    JOB_TYPE_BACKFILL_MORPHO_EVENTS: "jobs.backfill_morpho_events",
    JOB_TYPE_BACKFILL_SPARK_EVENTS: "jobs.backfill_spark_events",
    JOB_TYPE_BACKFILL_GMX_EVENTS: "jobs.backfill_gmx_events",
}


class JobManager:
    def __init__(self):
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Task] = {}

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def _insert_row(
        self,
        *,
        job_id: str,
        job_type: str,
        args: dict,
        status: str,
        progress: float,
        started_at: datetime,
        finished_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ):
        ch = await async_client()
        await ch.insert(
            "tradernick.ingestion_jobs",
            [[
                job_id,
                job_type,
                json.dumps(args),
                status,
                float(progress),
                started_at,
                finished_at,
                error,
                self._utcnow(),
            ]],
            column_names=[
                "job_id", "job_type", "args", "status", "progress",
                "started_at", "finished_at", "error", "updated_at",
            ],
        )

    async def create_backfill(self, job_type: str, tokens: list[str], days: int) -> dict:
        return await self.create_backfill_args(job_type, days, {"tokens": tokens})

    async def create_backfill_args(self, job_type: str, days: int, args_extra: dict) -> dict:
        if job_type not in JOB_MODULES:
            raise ValueError(f"unknown job_type {job_type}")
        if len(self._procs) >= config.MAX_CONCURRENT_BACKFILLS:
            raise RuntimeError(
                f"at MAX_CONCURRENT_BACKFILLS ({config.MAX_CONCURRENT_BACKFILLS}); try again later"
            )

        until = self._utcnow().replace(second=0, microsecond=0)
        since = until - timedelta(days=days)
        job_id = uuid.uuid4().hex
        args = {
            **args_extra,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "completed_chunks": [],
        }
        await self._insert_row(
            job_id=job_id,
            job_type=job_type,
            args=args,
            status="pending",
            progress=0.0,
            started_at=self._utcnow(),
        )
        await self._spawn(job_id, job_type)
        return await self.get(job_id)

    async def _spawn(self, job_id: str, job_type: str):
        module = JOB_MODULES[job_type]
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", module, job_id)
        self._procs[job_id] = proc
        self._waiters[job_id] = asyncio.create_task(self._await(job_id, proc))
        log.info("spawned job %s (type=%s pid=%s)", job_id, job_type, proc.pid)

    async def _await(self, job_id: str, proc: asyncio.subprocess.Process):
        code = await proc.wait()
        self._procs.pop(job_id, None)
        self._waiters.pop(job_id, None)
        log.info("job %s subprocess exited code=%s", job_id, code)

    async def cancel(self, job_id: str) -> bool:
        proc = self._procs.get(job_id)
        if proc is None:
            return False
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        return True

    async def get(self, job_id: str) -> Optional[dict]:
        ch = await async_client()
        rows = await ch.query(
            """
            SELECT job_id, job_type, args, status, progress, started_at, finished_at, error, updated_at
            FROM tradernick.ingestion_jobs FINAL
            WHERE job_id = {job_id:String}
            """,
            parameters={"job_id": job_id},
        )
        if not rows.result_rows:
            return None
        r = rows.result_rows[0]
        is_alive = job_id in self._procs
        return {
            "job_id": r[0],
            "job_type": r[1],
            "args": json.loads(r[2]),
            "status": r[3],
            "progress": float(r[4]),
            "started_at": r[5].isoformat() if r[5] else None,
            "finished_at": r[6].isoformat() if r[6] else None,
            "error": r[7],
            "updated_at": r[8].isoformat() if r[8] else None,
            "subprocess_alive": is_alive,
        }

    async def list_jobs(self, limit: int = 100) -> list[dict]:
        ch = await async_client()
        rows = await ch.query(
            """
            SELECT job_id, job_type, status, progress, started_at, finished_at, error, updated_at
            FROM tradernick.ingestion_jobs FINAL
            ORDER BY started_at DESC
            LIMIT {limit:UInt32}
            """,
            parameters={"limit": limit},
        )
        return [
            {
                "job_id": r[0],
                "job_type": r[1],
                "status": r[2],
                "progress": float(r[3]),
                "started_at": r[4].isoformat() if r[4] else None,
                "finished_at": r[5].isoformat() if r[5] else None,
                "error": r[6],
                "updated_at": r[7].isoformat() if r[7] else None,
                "subprocess_alive": r[0] in self._procs,
            }
            for r in rows.result_rows
        ]

    async def resume_inflight(self):
        ch = await async_client()
        rows = await ch.query(
            """
            SELECT job_id, job_type
            FROM tradernick.ingestion_jobs FINAL
            WHERE status IN ('pending', 'running')
            ORDER BY started_at ASC
            """
        )
        for r in rows.result_rows:
            job_id, job_type = r[0], r[1]
            if job_type not in JOB_MODULES:
                log.warning("skipping resume of unknown job_type=%s id=%s", job_type, job_id)
                continue
            log.info("resuming job %s (type=%s)", job_id, job_type)
            await self._spawn(job_id, job_type)
