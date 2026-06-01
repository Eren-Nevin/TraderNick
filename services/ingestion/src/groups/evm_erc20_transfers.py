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


async def fetch_and_insert(ds: AsyncDeFiStream, chain: str, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.evm.erc20.transfers(token)
        .network(chain)
        .time_range(_iso(since), _iso(until))
        .verbose()
        .with_value()
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = transfers_df_to_rows(df, kind="erc20", chain=chain, token_override=token)
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    pairs = config.EVM_ERC20_PAIRS
    if not pairs:
        log.info("no EVM_ERC20_TRANSFERS pairs configured; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    log.info("erc20 pairs=%s; live cadence=%ss, sweep cadence=%ss",
             pairs, POLL_INTERVAL_SECONDS, sweep_cadence)

    async def live_loop():
        jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - sweep.LIVE_OVERLAP
            total_rows = 0
            err: str | None = None
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            for chain, token in pairs:
                try:
                    n = await fetch_and_insert(ds, chain, token, since, now)
                    log.info("%s:%s rows=%d", chain, token, n)
                    total_rows += n
                except Exception as exc:
                    err = f"{chain}:{token}: {type(exc).__name__}: {exc}"[:1000]
                    log.exception("%s:%s fetch failed: %s", chain, token, exc)
            if stream_name:
                await ch_status.write_tick(stream_name, total_rows, error=err)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop():
        jitter = sweep.sweep_jitter_s(sweep_cadence)
        log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
        await asyncio.sleep(jitter)
        ch = await async_client()
        while True:
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            async def _one(chain, token):
                try:
                    last_seen = await latest_time(
                        ch, table="tradernick.transfers",
                        where="kind = 'erc20' AND chain = {chain:String} AND token = {token:String}",
                        parameters={"chain": chain, "token": token},
                    )
                    since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                    if since >= now:
                        return
                    label = f"evm_erc20_transfers/{chain}:{token}"
                    n = await fetch_and_insert(ds, chain, token, since, now)
                    log.info("%s sweep window=%s..%s rows=%d (last_seen=%s)", label, since, now, n, last_seen)
                except Exception as exc:
                    log.exception("sweep failed for %s:%s: %s", chain, token, exc)

            await asyncio.gather(*(_one(c, t) for c, t in pairs), return_exceptions=True)
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    await asyncio.gather(live_loop(), sweep_loop())


if __name__ == "__main__":
    asyncio.run(main())
