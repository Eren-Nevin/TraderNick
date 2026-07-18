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

> **📖 Full usage guide:** see [`USAGE.md`](USAGE.md) — an exhaustive reference
> covering every namespace, query builder, filter, snapshot operation, and the
> unsupported surface. It ships inside the installed package.

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

**0.7.0 — Unified filter API (BREAKING).** One wallet-selection filter surface,
used by both transfer reads **and** `scan_parquet`. The `local_*` methods are
**removed** — use the unprefixed methods everywhere; every filter accepts
`str | list[str]`:
- `.involving/.sender/.receiver` + `_label`/`_entity`/`_category`/`_groups` +
  `exclude_*`. On a read they push into ClickHouse; on a `scan_parquet` the
  server resolves the selection to member addresses and filters the snapshot in
  **DuckDB** — so **category/entity filters now work on snapshots** too.
- New `client.wallets.addresses(groups=/categories=/entities=/...)` → resolve a
  selection to its addresses. See [`USAGE.md`](USAGE.md) §9.2 & §12.4.
- Migration: `.local_involving_categories([...])` → `.involving_category([...])`;
  scalar calls like `.sender("0x…")` still work (now also take a list).

**0.6.0 — Wallet-group filters** (list-valued, resolved server-side).

**0.5.0 — Binance spot markets + erc20 `min_amount` fix + first test suite.**
- **`client.binance.spot.{ohlcv, raw_trades}`** — the Binance *spot* market, a
  fully separate dataset from perp/futures. Same shapes as the perp
  `binance.{ohlcv, raw_trades}`; `raw_trades` keeps `add_symbol` / `with_id`.
- **Fix:** `evm.erc20.transfers([...]).min_amount(x)` now resolves server-side.
  The client routed `.min_amount()` to `/evm/erc20_transfers/read/min`, but that
  alias was never registered on the server (it 404'd). erc20 is now in the
  `/read` + `/read/min` alias set alongside native/tron/btc transfers.
- **Tests:** a `pytest` suite (`clients/tests/`) — respx-mocked unit tests for
  body/path construction + response transforms, plus an env-gated live
  integration tier (`-m integration`, needs `DATA_PROVIDER_URL`).

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
- `binance.spot.{ohlcv, raw_trades}` — spot market (separate from perp)
- maintenance: `binance.{flush,compact}_{raw_trades,ohlcv,exchange}`,
  `client.cache.{flush, compact, dedup, migrate_time}`,
  `client.jobs.{list, get, cancel, wait, submit}`
- `evm.aave.{deposit, withdraw, borrow, repay, flashloan, liquidation}`
  with `involving`, `exclude_involving`, `eth_market_type`
- `evm.uniswap.{swap, deposit, withdraw, collect}` — V3
- `evm.lido.{deposit, withdrawal_request, withdrawal_claimed,
  l2_deposit, l2_withdrawal_request}`
- `evm.erc20.transfers`, `evm.native_transfers` with the unified wallet-filter
  surface: `involving`/`sender`/`receiver` + `_label`/`_entity`/`_category`/
  `_groups` + `exclude_*` (all `str | list`) + `min_amount`/`max_amount`
- `tron.{native, trc20}.transfers`, `btc.native.transfers`
- `hyperliquid.{ohlcv, trades, fills, funding, transfers, vaults,
  trade_history, position_history}`
- `client.wallets.{list, get, upsert, delete, addresses}` — `addresses(...)`
  resolves a group/category/entity selection to its addresses
- `client.{load_parquet, scan_parquet, list_snapshots, delete_snapshot,
  as_parquet}` — snapshots; `scan_parquet` filters with the SAME wallet-filter
  surface (resolved to addresses + DuckDB, so category/entity work on snapshots)
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

Not yet exposed / unsupported:
- `.aggregate(...)` — present on every builder (Horatio parity) but the server
  has **no aggregate read route**, so it 404s. Aggregate client-side from the
  raw `.as_polars()` frame for now; a server-side aggregate is a future release.
- `client.btc.mined()` — no server route/table yet (TN doesn't ingest Bitcoin
  coinbase payouts); calling it 404s.
- `evm.{stader, threshold}` — dropped in 0.4.0 (TN doesn't ingest the
  upstream yet); re-add when ingestion lands
- `gmx.*` namespace — not planned (out of scope for this platform)
- `hyperliquid.{sends, spot_transfers}` — exposed but hollow: DeFiStream
  provides them ("Tier 3") but TN doesn't ingest them yet, so the endpoints
  return the correct empty schema (0 rows). A follow-up will add the CH tables
  + ingestion streams and make them return real data.

## Compatibility

Broadly Horatio-shaped (same `DataProviderClient`, namespaces, and
`as_pandas`/`as_polars`/`as_parquet` terminators). As of **0.7.0** the wallet
filter surface diverges from Horatio: the `local_*` methods are gone and filters
are unified + `str | list`-valued (see [`USAGE.md`](USAGE.md) §9.2). Reads and
snapshot scans share one filter surface.
