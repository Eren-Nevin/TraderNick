import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import async_client, delete_transfers_range, safe_ident, transfers_df_for_bulk_insert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_evm_erc20_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# 6h chunks keep each fetch+insert under ~2.5M rows on BSC (the heaviest
# chain). 24h chunks were producing ~10M-row inserts that took minutes
# each and made progress feel stuck; 6h gives 4x more progress updates
# and roughly the same total throughput.
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


def _resolve_chain_tokens(chains: list[str]) -> dict[str, list[str]]:
    """Build {chain: [tokens]} by looking up each chain in
    config.EVM_ERC20_BY_CHAIN (same roster the live job uses).
    Chains with no configured tokens are skipped."""
    out: dict[str, list[str]] = {}
    for chain in chains:
        toks = config.EVM_ERC20_BY_CHAIN.get(chain.upper(), [])
        if toks:
            out[chain.upper()] = list(toks)
    return out


def _planned_chunks(by_chain: dict[str, list[str]], since: datetime, until: datetime):
    """Yield (chain, tokens, since, until) tuples — one chunk per
    (chain × time-window). Number of chunks = chains × time-buckets."""
    chunks = []
    step = timedelta(hours=CHUNK_HOURS)
    for chain, tokens in by_chain.items():
        t = since
        while t < until:
            t_end = min(t + step, until)
            chunks.append((chain, tokens, t, t_end))
            t = t_end
    return chunks


async def _fetch_chunk(ds: AsyncDeFiStream, chain: str, tokens: list[str], since: datetime, until: datetime) -> int:
    """Multi-token batched fetch — one call per (chain, time-window).
    `.ignore_non_existing()` makes DeFiStream silently skip tokens that
    aren't deployed on this chain, so the same roster can be applied
    across chains without curating per-deployment. Each returned row
    carries its own `token` column."""
    df = await (
        ds.evm.erc20.transfers(*tokens)
        .network(chain)
        .time_range(_iso_z(since), _iso_z(until))
        .verbose()
        .with_value()
        .ignore_non_existing()
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    # token_override=None — use each row's `token` column from the multi-token response.
    pd_df = transfers_df_for_bulk_insert(df, kind="erc20", chain=chain, token_override=None)
    ch = await async_client()
    await ch.insert_df(TABLE, pd_df)
    return len(pd_df)


async def main(job_id: str):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set"); sys.exit(2)

    job = await _load_job(job_id)
    job_type = job["job_type"]
    args = job["args"]
    started_at = job["started_at"]

    # New input shape: chains-only. Tokens come from config (matching live).
    chains = args.get("chains") or []
    by_chain = _resolve_chain_tokens(chains)
    if not by_chain:
        raise RuntimeError(f"no resolvable chains in {chains}; check EVM_ERC20_BY_CHAIN")
    since = _parse_iso(args["since"])
    until = _parse_iso(args["until"])
    completed_set = {tuple(k) for k in args.get("completed_chunks", [])}

    chunks = _planned_chunks(by_chain, since, until)
    total = len(chunks)
    done = sum(1 for chain, _tokens, cs, _ in chunks if (chain, cs.isoformat()) in completed_set)

    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                       progress=(done / total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: chains=%s chunks=%d resumed_at=%d force=%s",
             job_id, list(by_chain.keys()), total, done, bool(args.get("force")))

    if args.get("force") and by_chain and done == 0:
        # Purge by chain only (not per-token) — multi-token batching means
        # any token within the configured set could have rows in this window.
        chain_clauses = " OR ".join(
            f"chain = '{safe_ident(c)}'" for c in by_chain.keys()
        )
        log.info("job %s force=true: purging existing erc20 rows for chains=%s in [%s, %s)",
                 job_id, list(by_chain.keys()), since, until)
        await delete_transfers_range(
            where_extra=f"kind = 'erc20' AND ({chain_clauses})",
            since=since,
            until=until,
        )
        log.info("job %s force purge done", job_id)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    try:
        for chain, tokens, cs, ce in chunks:
            if _stop:
                args["completed_chunks"] = sorted(map(list, completed_set))
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                   progress=(done / total) if total else 1.0, started_at=started_at,
                                   finished_at=_utcnow())
                log.info("job %s cancelled at %d/%d chunks", job_id, done, total)
                return
            key = (chain, cs.isoformat())
            if key in completed_set:
                continue
            log.info("job %s chunk %s tokens=%d %s..%s", job_id, chain, len(tokens), cs, ce)
            n = await _fetch_chunk(ds, chain, tokens, cs, ce)
            log.info("job %s chunk %s rows=%d", job_id, chain, n)
            completed_set.add(key)
            done += 1
            args["completed_chunks"] = sorted(map(list, completed_set))
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                               progress=done / total, started_at=started_at)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="completed",
                           progress=1.0, started_at=started_at, finished_at=_utcnow())
        log.info("job %s completed", job_id)
    except Exception as exc:
        log.exception("job %s failed: %s", job_id, exc)
        args["completed_chunks"] = sorted(map(list, completed_set))
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="failed",
                           progress=(done / total) if total else 0.0, started_at=started_at,
                           finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_evm_erc20_transfers <job_id>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
