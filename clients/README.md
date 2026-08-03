# tradernick-data-provider

Python client for the TraderNick `data_provider` service. Drop-in
compatible with [`horatio-data-provider`](https://pypi.org/project/horatio-data-provider/):
same `DataProviderClient` class, same namespaces (`evm`, `tron`, `btc`,
`binance`, `hyperliquid`, `wallets`, `jobs`), same chainable
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

**1.7.0 — `client.snapshot.*` namespace.** The snapshot read/manage surface is now
a namespace: **`client.snapshot.{list, load, scan, delete}`**. `list(detailed=True)`
(or `list_detailed()`) returns keys **with sizes** + a roster-wide total; `load(key)`
returns a builder with `.as_polars()` / `.as_pandas()` / `.as_arrow()` / `.bytes()`
terminals; `scan(key)` is the server-side wallet-filtered read; `delete(key)` removes.
The old top-level methods (`load_parquet` / `list_snapshots` / `list_snapshots_detailed`
/ `scan_parquet` / `delete_snapshot`) still work as **deprecated** aliases (Horatio
drop-in parity). There is no `snapshot.save` — snapshots are written by any read
query's `.as_parquet(key)` terminal.

**1.6.0 — `list_snapshots_detailed()`.** New `client.list_snapshots_detailed()`
returns saved snapshots **with their sizes**: a `snapshots` list (sorted by key)
of `{key, bytes, size (human-readable), modified (ISO-8601 UTC)}`, plus `count`,
`total_bytes` and a human-readable `total_size`. `list_snapshots()` (keys only) is
unchanged. Backed by the new `GET /snapshots/list_detailed` route.

**1.5.0 — 5m windows on the fills paths.** `positions().window()` now accepts a
**5m** multiple (down from 15m) for the fills-native paths — `.aggregate()` (default
`source="fills"`) and `.aggregate_change()`. The `position_history` backup and the
default snapshot mode still require a 15m multiple.

**1.4.0 — `aggregate()` defaults to `source="fills"`.** The snapshot aggregate now
uses the sweep-accurate, complete fills rollup by DEFAULT (was `position_history`).
Pass `source="position_history"` for the old DeFiStream-snapshot behavior (the
backup). Fixes the long/short imbalance out of the box.

**1.3.0 — `positions().aggregate()`: `source=` + dropped `avg_entry`.** New
`source=` arg on the snapshot aggregate: `"position_history"` (default — DeFiStream
snapshots, the historical backup) or `"fills"` (a sweep-accurate, complete
fills-derived rollup that fixes the long/short imbalance — complete wallet set, no
phantom same-ms sweeps). **Breaking:** the `avg_entry` column is removed from
`aggregate()` output (a $-size-weighted avg entry across all wallets/sides was
meaningless). `pos_recency_hrs=` works with both sources.

**1.1.0 — `abs_flow` on the change-aggregate.** `positions().aggregate_change()`
adds `abs_flow`: the gross flow (sum of all ten action columns, direction-agnostic).

**1.0.0 — First stable release.** The public API is now considered stable.
Consolidates the `0.12`–`0.13` line: the `hyperliquid.positions()` endpoint,
IDE/jedi fluent-builder resolution, and `$`-metric dust rounding.

**0.13.0 — `positions` (was `position_history`).** Requires `.window()` (a 15m
multiple). Default mode downsamples the position snapshots to the window; the
snapshot **`.aggregate()`** returns the per-`(token, window)` open-position book
(side / net_size / counts / sizes / avg_entry, optional `pos_recency_hrs=`
staleness filter); **`.aggregate_change()`** returns the fills-based action-flow
frame (opened/increased/decreased/closed long/short, flips,
net_pos_change/flip/flow, abs_flow, and buy/sell_size +
buy/sell_taker_size).
`0.13.2` snaps aggregated `$` metrics under `$0.001` to `0`.

**0.11.2 — IDE/jedi fluent-builder fix.** The chained query builders resolve to
their concrete types in editors/jedi (self-type TypeVar idiom), so
`.tokens(...).wallets(...)` keeps autocompleting; no external dependency.

**0.11.0 — Hyperliquid wallet groups + aggregate.** `.wallet_groups(...)` on every
wallet-aware HL endpoint (group names → member addresses, unions with `.wallets()`);
`ohlcv()` no longer exposes `.wallets()` (market-wide; was a no-op);
`realized_performance().aggregate()` sums across the selected wallets → one row per
`(token, window)`.

**0.10.0 — `realized_performance` (was `trade_history`).** Renamed; now exposes the
`funding` column and an optional `.window("15m"+)` that returns **per-window
realized** PnL/fees/funding/volume (from fills+funding, window-start aligned)
instead of the daily cumulative snapshots. Snapshot `time` is now **start-aligned**
(a row at `D 00:00` excludes day D). `trade_history()` is removed.

**0.9.0 — Leaner Hyperliquid reads.** `fills()` drops `fee_token`, `builder_fee`,
`crossed`, `tid`, `oid`, `hash` by default (add `.with_extra_cols()` to keep
them); `transfers()` / `vaults()` are wallet-scoped only — `.tokens()` removed
(it was a no-op; no token column).

**0.8.0 — Removed Horatio-era no-ops.** Query `.cache()` / `.parallel()`, the
`client.cache.*` namespace, and all per-namespace `flush` / `compact` / `dedup`
maintenance methods are gone — they did nothing (data_provider reads live from
ClickHouse). Delete any such calls.

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
- `client.jobs.{list, get, cancel, wait, submit}` — ingestion job queue
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
  realized_performance, positions}` — `realized_performance` has snapshot
  (cumulative) + windowed (`.window("15m"+)`, per-window realized) modes;
  `positions` (requires `.window()`, a 15m multiple) returns downsampled position
  snapshots, or `.aggregate()` for the per-`(token, window)` open-position book
  from snapshots (side/net_size/counts/sizes/avg_entry, optional
  `.aggregate(pos_recency_hrs=n)` staleness filter), or `.aggregate_change()` for the
  fills-based action-flow frame (opened/increased/decreased/closed long/short,
  flips, net_pos_change/flip/flow)
- `client.wallets.{list, get, upsert, delete, addresses}` — `addresses(...)`
  resolves a group/category/entity selection to its addresses
- `client.snapshot.{list, load, scan, delete}` — snapshots (write via any read
  query's `.as_parquet(key)`); `scan` filters with the SAME wallet-filter surface
  (resolved to addresses + DuckDB, so category/entity work on snapshots). Old
  top-level `load_parquet` / `scan_parquet` / `list_snapshots` / `delete_snapshot`
  remain as deprecated aliases
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
