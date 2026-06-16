import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import sweep
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows
from gap_fill import latest_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [evm_erc20_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(
    ds: AsyncDeFiStream, chain: str, tokens: list[str], since: datetime, until: datetime,
) -> int:
    """Multi-token request: one call covering every configured token for
    `chain`. `.ignore_non_existing()` makes DeFiStream silently skip tokens
    that aren't deployed on this chain rather than 400-ing the whole call,
    so the same roster can be applied to every chain without curating per
    deployment. Each returned row carries its own `token` column."""
    df = await (
        ds.evm.erc20.transfers(*tokens)
        .network(chain)
        .time_range(_iso(since), _iso(until))
        .verbose()
        .with_value()
        .ignore_non_existing()
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    # token_override=None — let transfers_df_to_rows pick the per-row token
    # from the df's `token` column (populated by DeFiStream in multi-token mode).
    rows = transfers_df_to_rows(df, kind="erc20", chain=chain, token_override=None)
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    by_chain = config.EVM_ERC20_BY_CHAIN
    if not by_chain:
        log.info("no EVM_ERC20_TRANSFERS configured; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    chains = list(by_chain.keys())
    total_tokens = sum(len(t) for t in by_chain.values())
    log.info(
        "erc20 chains=%s tokens=%d total (multi-token batched per chain); live=%ss sweep=%ss",
        chains, total_tokens, POLL_INTERVAL_SECONDS, sweep_cadence,
    )

    async def live_loop():
        jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            _live_t0 = time.monotonic()
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - sweep.LIVE_OVERLAP
            total_rows = 0
            err: str | None = None
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            # One call per chain — 5 calls/tick total regardless of token count.
            for chain, tokens in by_chain.items():
                try:
                    n = await fetch_and_insert(ds, chain, tokens, since, now)
                    log.info("%s tokens=%d rows=%d", chain, len(tokens), n)
                    total_rows += n
                except Exception as exc:
                    err = f"{chain}: {type(exc).__name__}: {exc}"[:1000]
                    log.exception("%s fetch failed: %s", chain, exc)
            if stream_name:
                await ch_status.write_tick(
                    stream_name, total_rows, error=err,
                    duration_s=time.monotonic() - _live_t0,
                )
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop(once: bool = False):
        if not once:
            jitter = sweep.sweep_jitter_s(sweep_cadence)
            log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
            await asyncio.sleep(jitter)
        ch = await async_client()
        while True:
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_t0 = time.monotonic()
            _sweep_err: str | None = None

            async def _one(chain: str, tokens: list[str]):
                nonlocal _sweep_err
                try:
                    # Watermark: MIN across all (chain, token) latest_time rows
                    # — sweep needs to reach the oldest gap, not the newest.
                    last_seens = []
                    for tok in tokens:
                        ls = await latest_time(
                            ch, table="tradernick.transfers",
                            where="kind = 'erc20' AND chain = {chain:String} AND token = {token:String}",
                            parameters={"chain": chain, "token": tok},
                        )
                        if ls is not None:
                            last_seens.append(ls)
                    last_seen = min(last_seens) if last_seens else None
                    since = sweep.sweep_since(
                        now=now,
                        sweep_cadence_seconds=sweep_cadence,
                        last_seen=last_seen,
                        # DeFiStream EVM parquet event endpoints cap each request
                        # at 7 days (100k blocks). Leave 1 day of slack so the
                        # 5-min live overlap + clock skew can never push us over.
                        max_window_seconds=6 * 24 * 3600,
                        stream_name=stream_name,
                    )
                    if since >= now:
                        return
                    label = f"evm_erc20_transfers/{chain}"
                    n = await fetch_and_insert(ds, chain, tokens, since, now)
                    log.info("%s sweep window=%s..%s rows=%d (last_seen=%s)", label, since, now, n, last_seen)
                except Exception as exc:
                    _sweep_err = f"sweep {chain}: {type(exc).__name__}: {exc}"[:1000]
                    log.exception("sweep failed for %s: %s", chain, exc)

            await asyncio.gather(*(_one(c, t) for c, t in by_chain.items()), return_exceptions=True)
            if stream_name:
                await ch_status.write_sweep(
                    stream_name, time.monotonic() - _sweep_t0, error=_sweep_err,
                )
            if once:
                return
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    # Boot-sweep — run one sweep iteration to completion BEFORE the live
    # loop starts, so a restart after a long stop recovers the full
    # [last_seen, now] gap instead of live_loop advancing the watermark
    # past it (mirrors streams/_hl_common.py).
    log.info("boot-sweep: recovering pre-restart gap before live loop starts")
    await sweep_loop(once=True)
    await asyncio.gather(live_loop(), sweep_loop())


if __name__ == "__main__":
    asyncio.run(main())
