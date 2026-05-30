"""Live polling for Hyperliquid events. 8 endpoints, each with its own
cadence:

   60s   — ohlcv, trades, fills           (high-volume market data)
   5min  — position_history, trade_history, transfers
   30min — funding, vaults                (sparse)

Endpoints that accept a token filter use the same multi-token form binance
got in 2.22 (`.token(*tokens)`) so one call per tick covers all 26 tokens.
Endpoints that don't filter by token (trades, fills, funding, transfers,
vaults) make one call per tick and return ALL markets.

Per-token gap-fill uses `min_watermark_per_token` against the table's
token column; endpoints without a per-token watermark (transfers, vaults)
fall back to `latest_time` over the whole table."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import HL_EVENTS, async_client
from gap_fill import latest_time, min_watermark_per_token, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [hyperliquid_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Endpoints that REQUIRE either tokens=... or wallets=... on the server side.
# We always pass tokens, so they're equivalent to multi-token-pinned for us.
_TOKEN_REQUIRED = {"ohlcv", "position_history", "trade_history"}

# Endpoints that ARE per-token in the response (have a token column we can
# slice by). Used to pick the gap-fill watermark strategy.
_PER_TOKEN_TABLE = {"ohlcv", "trades", "fills", "funding", "position_history", "trade_history"}

# Per-endpoint live tick + gap-fill chunk size.
_CADENCE: dict[str, tuple[int, int]] = {
    # event:  (tick_seconds, gap_fill_chunk_hours)
    "ohlcv":            (60,    6),
    "trades":           (60,    6),
    "fills":            (60,    6),
    "position_history": (300,  24),
    "trade_history":    (300,  24),
    "transfers":        (300,  24),
    "funding":          (1800, 24),
    "vaults":           (1800, 24),
}

# Live overlap window = a small multiple of the tick so transient gaps
# self-heal on the next tick without needing the gap-fill task.
#
# Funding (1800s tick) gets a 24h lookback to match the binance_funding_rate
# group — its server-side response is sparse (one event/hour × ~26 tokens ×
# wallet fan-out, well under the 2.23 per-call caps), and short ingestion
# outages would otherwise leave permanent mid-range holes because the
# high-watermark gap-fill can't detect them. 24h means any sub-day outage
# self-heals on the next live tick.
_OVERLAP_MINUTES = {
    60:    3,
    300:   15,
    1800:  1440,
}


async def _fetch_and_insert(ds, *, event, tokens, since, until) -> int:
    method, table, columns, transform = HL_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            b = getattr(ds.exchange.hyperliquid, method)()
            # Only pass token filter for endpoints that need / use it; the
            # others would just silently ignore but it's cleaner to omit.
            if event in _TOKEN_REQUIRED:
                b = b.token(*tokens)
            elif event in _PER_TOKEN_TABLE:
                # Optional filter: keep responses bounded to our roster.
                b = b.token(*tokens)
            b = b.date_range(_iso(since), _iso(until))
            if event == "ohlcv":
                b = b.window("1m")
            df = await b.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            m = str(exc).lower()
            if "429" not in m and "too many" not in m and "rate limit" not in m:
                raise
    raise last_exc


def _live_loop(ds, event: str, tokens: list[str]):
    """Build the per-event live polling coroutine."""
    tick_s, _ = _CADENCE[event]
    overlap_m = _OVERLAP_MINUTES[tick_s]

    async def loop():
        while True:
            tick_end = time.monotonic() + tick_s
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=overlap_m)
            try:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=now)
                log.info("%s rows=%d", event, n)
            except Exception as exc:
                log.exception("%s fetch failed: %s", event, exc)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))
    return loop


def _gap_fill_task(ds, event: str, tokens: list[str], t_start: datetime):
    """Build the per-event gap-fill coroutine. Uses min-watermark across
    tokens for per-token tables, or whole-table latest_time for the rest."""
    method, table, _cols, _tf = HL_EVENTS[event]
    _, chunk_hours = _CADENCE[event]

    async def task():
        ch = await async_client()
        if event in _PER_TOKEN_TABLE:
            last_seen = await min_watermark_per_token(ch, table=table, tokens=tokens)
        else:
            last_seen = await latest_time(ch, table=table)
        since = resolve_since(last_seen, t_start=t_start)
        if since >= t_start:
            return
        label = f"hyperliquid/{event}"
        log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
        async def call(s, u):
            return await _fetch_and_insert(ds, event=event, tokens=tokens, since=s, until=u)
        total = await run_chunked(label=label, since=since, until=t_start, call=call, chunk_hours=chunk_hours)
        log.info("%s gap-fill done total_rows=%d", label, total)
    return task


async def main():
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.INGEST_TOKENS:
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)

    tokens = list(config.INGEST_TOKENS)
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling %d HL endpoints over %d tokens + gap-fill from min-watermark",
             len(HL_EVENTS), len(tokens))

    tasks = []
    for event in HL_EVENTS:
        tasks.append(_live_loop(ds, event, tokens)())
        tasks.append(_gap_fill_task(ds, event, tokens, t_start)())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
