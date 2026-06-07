# tradernick-data-provider

Python client for the TraderNick `data_provider` service. Drop-in
compatible with [`horatio-data-provider`](https://pypi.org/project/horatio-data-provider/):
same `DataProviderClient` class, same namespaces (`evm`, `tron`, `btc`,
`binance`, `hyperliquid`, `wallets`, `cache`, `jobs`), same chainable
builder methods, same `as_pandas()` / `as_polars()` / `as_parquet()`
terminators. The only visible difference is the import path.

```python
# Before
from horatio_data_provider import DataProviderClient

# After
from tradernick_data_provider import DataProviderClient
```

The server URL passed to the constructor is the only thing you need to
change at the call site.

## Install

```sh
pip install tradernick-data-provider
```

## Usage

```python
import asyncio
from tradernick_data_provider import DataProviderClient

async def main():
    async with DataProviderClient("http://localhost:10005") as client:
        df = await client.binance.ohlcv("BTC", "1h") \
            .time_range("2026-06-01T00:00:00Z", "2026-06-08T00:00:00Z") \
            .as_polars()
        print(df)

asyncio.run(main())
```

All Horatio query builders work unchanged. The server delegates to
ClickHouse instead of DeFiStream, so reads stay sub-second on tables
where Horatio has to pay a fresh upstream fetch.

## Status

**0.4.0 — Transfer wallet selection + multi-network `as_parquet` lands.**
- All transfer queries (`evm.erc20`, `evm.native_transfers`, `tron.native`,
  `tron.trc20`, `btc.native`) now accept full Horatio wallet-selection
  pushdown: `sender_label` / `receiver_label` / `involving_label`,
  `sender_category` / `receiver_category` / `involving_category`, and every
  `exclude_*` variant.
- `query.network([...]).as_parquet(key)` now works for all transfer
  queries — server-side fan-out across networks, in-process concat, single
  parquet under `key`. Optional `with_network` column auto-toggles on
  multi-network calls.
- **Breaking:** `evm.stader` / `evm.threshold` namespaces removed. TN
  doesn't ingest those upstreams; the stubs were giving false-positive
  "namespace exists" signals. Re-add when TN ingestion picks them up.

**0.3.0 — Phase 4 TN-exclusive protocols.** Adds Spark, Morpho, and
Aerodrome (concentrated + basic). New namespaces live under
`client.evm.{spark, morpho, aerodrome}`; existing `aave / uniswap / lido /
erc20 / native` paths are unchanged.

**0.2.0 — Phase 1+2 read parity.** Validated against horatio-data-provider
on 2026-06-07:
- Column-shape parity: 100% across binance / aave / lido / uniswap /
  hyperliquid / transfers.
- Row-count + value parity: exact match on stable tables
  (binance.ohlcv 1m, raw_trades, lido.deposit, aave.borrows/repays,
  evm.native with `min_amount`); within ±2 rows on AAVE event tables
  where DeFiStream's sweep loop occasionally re-fetches windows with
  slightly different cuts.

What works:
- `binance.{ohlcv, raw_trades, book_depth, open_interest, funding_rate,
  long_short_ratios}` (with `with_id`, `add_symbol`)
- `evm.aave.{deposit, withdraw, borrow, repay, flashloan, liquidation}`
  with `involving`, `exclude_involving`, `eth_market_type`
- `evm.uniswap.{swap, deposit, withdraw, collect}` — V3
- `evm.lido.{deposit, withdrawal_request, withdrawal_claimed,
  l2_deposit, l2_withdrawal_request}`
- `evm.erc20.transfers`, `evm.native_transfers` with full filter set
  (sender / receiver / involving / exclude_* / min_amount / max_amount)
- `tron.{native, trc20}.transfers`, `btc.native.transfers`
- `hyperliquid.{ohlcv, trades, fills, funding, transfers, vaults,
  trade_history, position_history}`
- `client.{wallets.list, wallets.get, wallets.upsert, wallets.delete}`
- `client.{load_parquet, scan_parquet, list_snapshots, delete_snapshot,
  as_parquet}` — server-side parquet snapshots with `local_*` filters
- `client.jobs.{list, get, cancel}` — proxies to the ingestion job queue

TN-exclusive (since 0.3.0):
- `evm.spark.{deposits, withdrawals, borrows, repays, flashloans,
  liquidations}` — same six-event surface as `evm.aave` byte-for-byte
- `evm.morpho.{supplies, withdrawals, borrows, repays, supply_collaterals,
  withdraw_collaterals, liquidations}` with `.market_id(...)` filter
- `evm.aerodrome.concentrated.{swaps, deposits, withdrawals, collects}`
  with optional `tick_spacing`
- `evm.aerodrome.basic.{swaps, deposits, withdrawals, claims}` with
  optional `stable` flag

Not yet exposed:
- `evm.{stader, threshold}` — dropped in 0.4.0 (TN doesn't ingest the
  upstream yet); re-add when ingestion lands
- `gmx.*` namespace planned for a future release (GMX has ~10 per-event
  schemas; bigger lift)
- `hyperliquid.{sends, spot_transfers}` — return empty schemas
- `client.scan_parquet` only honors address-based `local_*` filters;
  label/category/entity variants are wired but no-op without
  wallet_labels co-mounted on the snapshot.

## Compatibility

Drop-in compatible with horatio-data-provider 4.x.
