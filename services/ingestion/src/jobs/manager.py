import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import config
from clickhouse import async_client

log = logging.getLogger("jobs")

JOB_TYPE_BACKFILL_OHLCV = "backfill_binance_ohlcv"
JOB_TYPE_BACKFILL_RAW_TRADES = "backfill_binance_raw_trades"
JOB_TYPE_BACKFILL_OPEN_INTEREST = "backfill_binance_open_interest"
JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS = "backfill_binance_long_short_ratios"
JOB_TYPE_BACKFILL_FUNDING_RATE = "backfill_binance_funding_rate"
JOB_TYPE_BACKFILL_BOOK_DEPTH = "backfill_binance_book_depth"
JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS = "backfill_evm_erc20_transfers"
JOB_TYPE_BACKFILL_EVM_NATIVE_TRANSFERS = "backfill_evm_native_transfers"
JOB_TYPE_BACKFILL_BTC_TRANSFERS = "backfill_btc_transfers"
JOB_TYPE_BACKFILL_TRON_NATIVE_TRANSFERS = "backfill_tron_native_transfers"
JOB_TYPE_BACKFILL_TRON_TRC20_TRANSFERS = "backfill_tron_trc20_transfers"
JOB_TYPE_BACKFILL_AAVE_EVENTS = "backfill_aave_v3_events"
JOB_TYPE_BACKFILL_UNISWAP_EVENTS = "backfill_uniswap_v3_events"
JOB_TYPE_BACKFILL_LIDO_EVENTS = "backfill_lido_events"
JOB_TYPE_BACKFILL_AAVE_V2_EVENTS = "backfill_aave_v2_events"
JOB_TYPE_BACKFILL_UNISWAP_V2_EVENTS = "backfill_uniswap_v2_events"
JOB_TYPE_BACKFILL_UNISWAP_V4_EVENTS = "backfill_uniswap_v4_events"
JOB_TYPE_BACKFILL_AERO_EVENTS = "backfill_aero_concentrated_events"
JOB_TYPE_BACKFILL_AERO_BASIC_EVENTS = "backfill_aero_basic_events"
JOB_TYPE_BACKFILL_AAVE_V4_EVENTS = "backfill_aave_v4_events"
JOB_TYPE_BACKFILL_MORPHO_EVENTS = "backfill_morpho_events"
JOB_TYPE_BACKFILL_SPARK_EVENTS = "backfill_spark_events"
JOB_TYPE_BACKFILL_GMX_EVENTS = "backfill_gmx_v2_events"
JOB_TYPE_BACKFILL_HYPERLIQUID_EVENTS = "backfill_hyperliquid_events"
# data_process — derived-MV maintenance jobs.
JOB_TYPE_BACKFILL_EXCHANGE_FLOW_MINUTE = "backfill_exchange_flow_minute"
JOB_TYPE_BACKFILL_TRANSFERS_REMATERIALIZE = "backfill_transfers_rematerialize"
# Unified materializer backfill — single job_type that takes a
# `materializers` list in args and rebuilds the requested derived
# partitions. Replaces the per-MV backfill_exchange_flow_minute /
# backfill_hl_position_history_mv / backfill_hl_fills_pnl_daily
# placeholders.
JOB_TYPE_BACKFILL_DATA_PROCESSOR = "backfill_data_processor"
JOB_MODULES = {
    JOB_TYPE_BACKFILL_OHLCV: "jobs.backfill_binance_ohlcv",
    JOB_TYPE_BACKFILL_RAW_TRADES: "jobs.backfill_binance_raw_trades",
    JOB_TYPE_BACKFILL_OPEN_INTEREST: "jobs.backfill_binance_open_interest",
    JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS: "jobs.backfill_binance_long_short_ratios",
    JOB_TYPE_BACKFILL_FUNDING_RATE: "jobs.backfill_binance_funding_rate",
    JOB_TYPE_BACKFILL_BOOK_DEPTH: "jobs.backfill_binance_book_depth",
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
    JOB_TYPE_BACKFILL_HYPERLIQUID_EVENTS: "jobs.backfill_hyperliquid_events",
    JOB_TYPE_BACKFILL_EXCHANGE_FLOW_MINUTE: "jobs.backfill_exchange_flow_minute",
    JOB_TYPE_BACKFILL_TRANSFERS_REMATERIALIZE: "jobs.backfill_transfers_rematerialize",
    JOB_TYPE_BACKFILL_DATA_PROCESSOR: "data_processor.backfill",
    # Legacy aliases — keep old job_type strings mapped to the same module
    # so in-flight rows from before a rename can still be resumed.
    "backfill_aave_events": "jobs.backfill_aave_events",
    "backfill_uniswap_events": "jobs.backfill_uniswap_events",
    "backfill_aero_events": "jobs.backfill_aero_events",
    "backfill_gmx_events": "jobs.backfill_gmx_events",
}


# Map source-backfill job_type → list of derived materializer names that
# should be rebuilt for the same [since, until) window once the parent
# job completes successfully. Empty / missing → no downstream trigger.
#
# Live data_processor would converge eventually via its sweep tier, but
# we want post-backfill convergence in minutes rather than ~6h, hence
# the explicit auto-spawn.
_BACKFILL_DOWNSTREAMS: dict[str, list[str]] = {
    # All transfer backfills feed exchange_flow_minute. The window is
    # the same as the source job's [since, until); data_processor's
    # source-FINAL pass reads whatever the source backfill landed.
    "backfill_evm_erc20_transfers":   ["exchange_flow_minute"],
    "backfill_evm_native_transfers":  ["exchange_flow_minute"],
    "backfill_btc_transfers":         ["exchange_flow_minute"],
    "backfill_tron_native_transfers": ["exchange_flow_minute"],
    "backfill_tron_trc20_transfers":  ["exchange_flow_minute"],

    # Hyperliquid backfill covers a configurable subset of HL events.
    # We over-trigger by rebuilding all 6 HL materializers regardless of
    # which events the parent job touched — the per-partition cost is
    # tiny on days with no source changes (REPLACE PARTITION of an
    # already-correct partition is a no-op-shaped operation) and over-
    # triggering is safer than under-triggering.
    "backfill_hyperliquid_events": [
        "hl_position_history_15m",
        "hl_position_history_1h",
        "hl_position_history_eod_wallet",
        "hl_fills_pnl_daily",
        "hl_fills_vol_daily",
        "hl_funding_daily",
        # Global smart_selector accelerators. trade_history rollup sums the
        # token dimension away; the OI rollup sources hl_position_history_1h
        # (listed above) so it rebuilds AFTER it — keep it last.
        "hl_trade_history_wallet_daily",
        "hl_position_history_oi_wallet_daily",
    ],
}


class JobManager:
    def __init__(self):
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Task] = {}
        # Filter JOB_MODULES down to the provider this container owns.
        # In legacy `monolith` mode (no INGESTION_PROVIDER env) every
        # job_type stays accepted — preserves the running container's
        # behaviour byte-for-byte.
        import provider_registry as pr
        provider = pr.current_provider()
        if provider is None:
            self._owned_types = set(JOB_MODULES.keys())
            self._modules = dict(JOB_MODULES)
        else:
            owned = pr.job_types_owned_by(provider)
            self._owned_types = {jt for jt in JOB_MODULES if jt in owned}
            self._modules = {jt: mod for jt, mod in JOB_MODULES.items() if jt in owned}
            log.info("JobManager: provider=%s — filtered %d/%d job_types",
                     provider, len(self._modules), len(JOB_MODULES))

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

    async def create_backfill_args(
        self,
        job_type: str,
        since: datetime,
        until: datetime,
        args_extra: dict,
    ) -> dict:
        """Create + spawn a backfill job for [since, until). Both bounds
        are explicit UTC-naive datetimes. Callers (the HTTP handlers)
        validate the values before reaching here — this method assumes
        since < until and trusts the caller's range."""
        if job_type not in JOB_MODULES:
            raise ValueError(f"unknown job_type {job_type}")
        if job_type not in self._owned_types:
            # Wrong provider for this container. The dashboard's per-
            # provider routing should already keep this from happening;
            # rejecting at the service is defence-in-depth.
            raise ValueError(
                f"job_type {job_type} is not owned by this container — "
                f"route the request to the appropriate provider's "
                f"backfill service"
            )
        if len(self._procs) >= config.MAX_CONCURRENT_BACKFILLS:
            raise RuntimeError(
                f"at MAX_CONCURRENT_BACKFILLS ({config.MAX_CONCURRENT_BACKFILLS}); try again later"
            )
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
        # Use the filtered module map — _owned_types should already gate
        # incoming requests, but resume_inflight walks every in-flight row
        # in CH and an older monolith might have left rows whose job_type
        # this container doesn't own.
        module = self._modules.get(job_type) or JOB_MODULES[job_type]
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", module, job_id)
        self._procs[job_id] = proc
        self._waiters[job_id] = asyncio.create_task(self._await(job_id, proc))
        log.info("spawned job %s (type=%s pid=%s)", job_id, job_type, proc.pid)

    async def _await(self, job_id: str, proc: asyncio.subprocess.Process):
        code = await proc.wait()
        self._procs.pop(job_id, None)
        self._waiters.pop(job_id, None)
        log.info("job %s subprocess exited code=%s", job_id, code)
        if code == 0:
            try:
                await self._maybe_spawn_downstream(job_id)
            except Exception:  # noqa: BLE001
                log.exception("downstream spawn for parent %s failed (continuing)", job_id)

    async def _maybe_spawn_downstream(self, parent_job_id: str):
        """If the just-completed parent job has a downstream materializer
        list in `_BACKFILL_DOWNSTREAMS`, enqueue a `backfill_data_processor`
        for the same [since, until) window. No-op when nothing is mapped
        or the parent didn't complete cleanly.

        Same-provider only: if this container doesn't own
        `backfill_data_processor` (e.g. we're the `transfers` provider
        and `data_process` runs elsewhere), we silently skip — the
        admin server's dashboard fan-out is responsible for cross-
        container coordination. In the monolith case `_owned_types`
        covers everything so the spawn proceeds inline."""
        parent = await self.get(parent_job_id)
        if parent is None:
            return
        if parent.get("status") != "completed":
            return
        downstreams = _BACKFILL_DOWNSTREAMS.get(parent.get("job_type") or "")
        if not downstreams:
            return
        if JOB_TYPE_BACKFILL_DATA_PROCESSOR not in self._owned_types:
            log.info(
                "parent %s would trigger data_processor backfill but this "
                "provider doesn't own it — skipping",
                parent_job_id,
            )
            return
        args = parent.get("args") or {}
        since_iso = args.get("since")
        until_iso = args.get("until")
        if not since_iso or not until_iso:
            log.warning(
                "parent %s missing since/until in args — cannot trigger downstream",
                parent_job_id,
            )
            return
        since_dt = datetime.fromisoformat(since_iso.replace("Z", ""))
        until_dt = datetime.fromisoformat(until_iso.replace("Z", ""))
        await self.create_backfill_args(
            JOB_TYPE_BACKFILL_DATA_PROCESSOR,
            since_dt, until_dt,
            {"materializers": downstreams,
             "triggered_by_job_id": parent_job_id,
             "triggered_by_job_type": parent.get("job_type")},
        )
        log.info(
            "parent %s (type=%s) → spawned data_processor backfill for materializers=%s",
            parent_job_id, parent.get("job_type"), downstreams,
        )

    async def cancel(self, job_id: str) -> bool:
        proc = self._procs.get(job_id)
        if proc is not None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return True
        # No live subprocess. Either this manager doesn't own the job
        # (foreign provider) or the row is a zombie: status still
        # 'running'/'pending' from a subprocess that died without writing
        # its terminal state (typically: SIGTERM during a tick + aiohttp
        # session torn down before the cancelled write landed — see
        # 2026-06-10 e828004f incident).
        #
        # Auto-finalize the zombie only when we own the job_type AND the
        # row hasn't been updated in a while. The staleness window must
        # be long enough that a slow tick (HTTP+insert can take 20-60s
        # on heavy chunks) never gets misclassified as dead, but short
        # enough that a stuck row clears in seconds from the UI's view.
        return await self._finalize_zombie(job_id, stale_after_s=90.0)

    async def _finalize_zombie(self, job_id: str, *, stale_after_s: float) -> bool:
        """If `job_id` is a row this manager owns, currently in a non-terminal
        state, and hasn't ticked in `stale_after_s` seconds, write a final
        'cancelled' row attributing it to subprocess loss and return True.
        Otherwise return False — the caller surfaces this as 409."""
        ch = await async_client()
        rows = await ch.query(
            """
            SELECT job_type, args, progress, started_at, updated_at, status
            FROM tradernick.ingestion_jobs FINAL
            WHERE job_id = {job_id:String}
            """,
            parameters={"job_id": job_id},
        )
        if not rows.result_rows:
            return False
        job_type, args_json, progress, started_at, updated_at, status = rows.result_rows[0]
        if job_type not in self._owned_types:
            return False
        if status not in ("running", "pending"):
            return False
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if updated_at and (now - updated_at).total_seconds() < stale_after_s:
            return False
        # Write the missing terminal row. Preserve args verbatim so the
        # UI's `completed_chunks` / `total_chunks` view stays correct.
        error_msg = (
            "auto-finalized — subprocess no longer present and row had not "
            f"ticked in {stale_after_s:.0f}s (likely SIGTERM-on-shutdown race "
            "without terminal status write)"
        )
        await ch.insert(
            "tradernick.ingestion_jobs",
            [[job_id, job_type, args_json, "cancelled", float(progress),
              started_at, now, error_msg, now]],
            column_names=["job_id", "job_type", "args", "status", "progress",
                          "started_at", "finished_at", "error", "updated_at"],
        )
        log.warning("zombie job %s (type=%s) auto-finalized as cancelled (%.0fs stale)",
                    job_id, job_type, (now - updated_at).total_seconds() if updated_at else -1)
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
        # In per-provider mode we only surface jobs we own; the dashboard
        # fan-out concatenates across every backfill service so the user
        # still sees a global view. Monolith mode returns everything.
        params: dict = {"limit": limit}
        where_sql = ""
        if self._owned_types and len(self._owned_types) < len(JOB_MODULES):
            where_sql = "WHERE job_type IN {types:Array(String)}"
            params["types"] = sorted(self._owned_types)
        rows = await ch.query(
            f"""
            SELECT job_id, job_type, args, status, progress, started_at, finished_at, error, updated_at
            FROM tradernick.ingestion_jobs FINAL
            {where_sql}
            ORDER BY started_at DESC
            LIMIT {{limit:UInt32}}
            """,
            parameters=params,
        )
        out = []
        for r in rows.result_rows:
            try:
                args = json.loads(r[2]) if r[2] else {}
            except Exception:  # noqa: BLE001
                args = {}
            out.append({
                "job_id": r[0],
                "job_type": r[1],
                "args": args,
                "status": r[3],
                "progress": float(r[4]),
                "started_at": r[5].isoformat() if r[5] else None,
                "finished_at": r[6].isoformat() if r[6] else None,
                "error": r[7],
                "updated_at": r[8].isoformat() if r[8] else None,
                "subprocess_alive": r[0] in self._procs,
            })
        return out

    async def resume_inflight(self):
        ch = await async_client()
        # Per-provider services scope the query to their own job_types so
        # they don't even consider in-flight rows owned by sibling
        # containers. Monolith mode (`_owned_types` covers everything)
        # behaves byte-for-byte as before.
        params: dict = {}
        where = "status IN ('pending', 'running')"
        if self._owned_types and len(self._owned_types) < len(JOB_MODULES):
            where += " AND job_type IN {types:Array(String)}"
            params["types"] = sorted(self._owned_types)
        rows = await ch.query(
            f"""
            SELECT job_id, job_type
            FROM tradernick.ingestion_jobs FINAL
            WHERE {where}
            ORDER BY started_at ASC
            """,
            parameters=params,
        )
        for r in rows.result_rows:
            job_id, job_type = r[0], r[1]
            if job_type not in self._modules:
                log.warning("skipping resume of unknown/foreign job_type=%s id=%s",
                            job_type, job_id)
                continue
            log.info("resuming job %s (type=%s)", job_id, job_type)
            await self._spawn(job_id, job_type)
