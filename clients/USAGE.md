# tradernick-data-provider — Complete Usage Guide

> A self-contained reference for integrating `tradernick-data-provider` into
> your own project. Written to be exhaustive: every namespace, every query
> builder, every chainable, every terminator, plus the gotchas. If you are an
> LLM agent wiring this into a codebase, you can implement from this file alone.

---

## 1. What this is

`tradernick-data-provider` is an **async Python client** for the TraderNick
`data_provider` HTTP service. That service reads market/on-chain data out of
ClickHouse and returns it as parquet, so reads are sub-second on tables where a
raw upstream fetch would be slow.

It is **drop-in compatible** with
[`horatio-data-provider`](https://pypi.org/project/horatio-data-provider/): the
class is `DataProviderClient`, the namespaces (`evm`, `tron`, `btc`, `binance`,
`hyperliquid`, `wallets`, `jobs`) match, and the chainable builders +
`as_pandas()` / `as_polars()` / `as_parquet()` terminators are identical. To
migrate from Horatio you change **one import** and the **server URL**:

```python
# from horatio_data_provider import DataProviderClient
from tradernick_data_provider import DataProviderClient
```

- **Requires:** Python ≥ 3.10.
- **Depends on:** `httpx`, `pyarrow`, `pandas`, `polars`, `pytz` (installed
  automatically).
- **Talks to:** a running `data_provider` server (default host port `10005`).

---

## 2. Install

```sh
pip install tradernick-data-provider
```

You also need network access to a `data_provider` instance. In the TraderNick
stack it is exposed at `http://<host>:10005`.

---

## 3. Mental model (read this first)

Every data read follows the same three-step shape:

```
client.<namespace>.<method>(...)     # 1. pick an endpoint  -> a Query builder
      .time_range(since, until)      # 2. chain filters     -> the same builder
      .as_polars()                   # 3. terminate         -> awaited result
```

1. **Namespace method** returns a *query builder* object. It does **no I/O**.
2. **Chainable filters** (`.time_range(...)`, `.network(...)`, `.involving(...)`,
   …) each mutate the builder and `return self`, so you can chain freely. Still
   no I/O.
3. **Terminator** (`await .as_polars()` / `.as_pandas()` / `.as_parquet(key)`)
   performs the single HTTP request and returns the data. **This is the only
   step you `await`.**

Because steps 1–2 are pure, you can build a query, inspect it, or pass it around
before executing.

---

## 4. Connecting

The client wraps one `httpx.AsyncClient`. Always close it — use the async
context manager:

```python
import asyncio
from tradernick_data_provider import DataProviderClient

URL = "http://localhost:10005"

async def main():
    async with DataProviderClient(URL) as client:
        ok = await client.health()          # -> True (raises on failure)
        df = await client.binance.ohlcv("BTC", "1h") \
                 .time_range("2026-07-01", "2026-07-08") \
                 .as_polars()
        print(df)

asyncio.run(main())
```

Without the context manager, call `await client.close()` yourself:

```python
client = DataProviderClient(URL)
try:
    ...
finally:
    await client.close()
```

The client is safe to reuse across many queries and to share concurrently
(`asyncio.gather(...)`), since httpx multiplexes.

---

## 5. Terminators (how you get data out)

Every query builder supports exactly these three terminators. There is **no
`as_arrow()`**.

| Terminator | Returns | Notes |
|---|---|---|
| `await q.as_polars()` | `polars.DataFrame` | `time` normalized to `Datetime('ms', UTC)`, sorted by time |
| `await q.as_pandas()` | `pandas.DataFrame` | same normalization; aggregate/OHLCV frames are time-indexed |
| `await q.as_parquet(key)` | `None` | saves the result server-side under `key` (see §12) |

Client-side post-processing applied by `as_polars` / `as_pandas`:
- a `window` column (aggregate responses) is renamed to `time`;
- rows are sorted by `timestamp`/`time`;
- the time column is cast to millisecond precision, UTC.

Convert as needed: `(await q.as_polars()).to_pandas()` or vice versa.

---

## 6. Time ranges & accepted date formats

`.time_range(since, until)` is available on every read builder. Both arguments
accept any of:

| Form | Example |
|---|---|
| ISO 8601 with `Z` | `"2026-07-10T06:30:00Z"` |
| Date only | `"2026-07-10"` (→ `00:00:00Z`) |
| Space-separated | `"2026-07-10 06:30:00"` |
| `datetime` object | `datetime(2026, 7, 10, 6, 30, tzinfo=timezone.utc)` (naive → treated as UTC) |
| Integer epoch **milliseconds** | `1783641600000` |

All are normalized to `YYYY-MM-DDTHH:MM:SSZ` before being sent. `until` is
exclusive on most endpoints.

> **Tip:** the backing tables are billions of rows. Keep windows tight
> (minutes/hours for tick data; days for candles) unless you are deliberately
> pulling a large range, in which case save to a snapshot (§12) instead of
> materializing in memory.

---

## 7. Binance

Perp/futures market lives directly on `client.binance`; the **spot** market is a
fully separate dataset under `client.binance.spot`.

### 7.1 Perp / futures

```python
b = client.binance

b.ohlcv(token, window)          # 1m raw or resampled: "1m","5m","15m","1h","4h","1d",...
b.raw_trades(token)             # tick trades; .add_symbol() .with_id()
b.book_depth(token)             # order-book depth snapshots
b.open_interest(token)          # OI + OI value
b.funding_rate(token)           # funding rate
b.long_short_ratios(token)      # top-trader + global long/short ratios
```

Examples:

```python
candles = await b.ohlcv("BTC", "1h").time_range("2026-07-01", "2026-07-08").as_polars()
# columns: time, token, open, close, high, low, volume,
#          buyer_taker_volume, seller_taker_volume, trade_count

trades = await b.raw_trades("ETH").with_id().add_symbol() \
             .time_range("2026-07-10T00:00:00Z", "2026-07-10T00:01:00Z").as_polars()
# columns: time, token, amount, price, buy (+ id, +symbol when requested)

funding = await b.funding_rate("BTC").time_range("2026-07-01", "2026-07-08").as_polars()
oi      = await b.open_interest("BTC").time_range("2026-07-01", "2026-07-08").as_polars()
lsr     = await b.long_short_ratios("BTC").time_range("2026-07-01", "2026-07-08").as_polars()
depth   = await b.book_depth("BTC").time_range("2026-07-10T00:00:00Z", "2026-07-10T00:05:00Z").as_polars()
```

### 7.2 Spot (`client.binance.spot`)

Spot is a **separate dataset** from perp — different tables, potentially
different volumes/prices. Only `ohlcv` and `raw_trades` exist for spot
(book depth / OI / funding are perp-only concepts).

```python
s = client.binance.spot

candles = await s.ohlcv("BTC", "1h").time_range("2026-07-01", "2026-07-08").as_polars()
trades  = await s.raw_trades("BTC").with_id().add_symbol() \
              .time_range("2026-07-10T00:00:00Z", "2026-07-10T00:01:00Z").as_polars()
```

Column shapes are identical to their perp counterparts.

---

## 8. Hyperliquid

All accessors hang off `client.hyperliquid` and return the **same** query type,
which carries a rich set of chainables.

```python
hl = client.hyperliquid

hl.fills()              # individual fills (maker/taker, price, side)
hl.trades()             # trades
hl.ohlcv()              # candles
hl.funding()            # funding
hl.transfers()          # ledger transfers — NOT token-scoped (no .tokens())
hl.vaults()             # vault data     — NOT token-scoped (no .tokens())
hl.realized_performance()  # PnL/fees/funding/volume per wallet-token; needs tokens/wallets
hl.positions()          # position snapshots (downsampled) OR .aggregate() action-flow; REQUIRES .window()
hl.sends()              # EXPOSED BUT EMPTY (see §16)
hl.spot_transfers()     # EXPOSED BUT EMPTY (see §16)
```

Chainables (in addition to `.time_range()`):

| Method | Effect |
|---|---|
| `.tokens(*symbols)` | restrict to tokens — varargs or a list. **Not on `transfers()` / `vaults()`** (no token column). |
| `.wallets(*addresses)` | restrict to wallet addresses (varargs or a list). Matches `wallet` (or buyer **or** seller for `trades()`). **Not on `ohlcv()`** (candles are market-wide). |
| `.wallet_groups(*groups)` | like `.wallets()` but pass **group name(s)** — resolved to member addresses server-side. Available wherever `.wallets()` is; unions with `.wallets()`. |
| `.window(size)` | bucket size — **only on `ohlcv()`, `positions()`, `realized_performance()`**. e.g. `.window("1h")`. `realized_performance`: min 15m. `positions`: **required**, a 15m multiple. |
| `.aggregate(flag=True)` | per-**(token, window)** totals (drops `wallet`). **`realized_performance()`**: SUMs PnL/volume metrics, **requires** `.wallets()`/`.wallet_groups()`. **`positions()`**: the open-position **book** from snapshots (side/net_size/counts/sizes/avg_entry), wallets optional. |
| `.aggregate_change(flag=True)` | **`positions()` only** — per-**(token, window)** position-**action** `$` flow from fills (opened/increased/decreased/closed long/short, flips, net_pos_change/flip/flow). Wallets optional. |
| `.per_token(flag=True)` | per-token breakdown |
| `.skip_hip3(flag=True)` | exclude HIP-3 markets |
| `.market_type(t)` | e.g. `"perp"` / `"spot"` |
| `.limit(n)` | cap rows |
| `.with_extra_cols()` | **`fills()` only** — include the columns it drops by default: `fee_token`, `builder_fee`, `crossed`, `tid`, `oid`, `hash` |

Examples:

```python
# fills — by default drops fee_token/builder_fee/crossed/tid/oid/hash
fills = await hl.fills().tokens("BTC").time_range(
    "2026-07-10T00:00:00Z", "2026-07-10T00:01:00Z").as_polars()

# ...pass .with_extra_cols() to keep them
fills_full = await hl.fills().tokens("BTC").with_extra_cols().time_range(
    "2026-07-10T00:00:00Z", "2026-07-10T00:01:00Z").as_polars()

# realized_performance REQUIRES tokens or wallets. No .window() → daily
# absolute-cumulative snapshots; .window("15m"+) → per-window realized deltas.
snap = await hl.realized_performance().wallets("0xabc...").time_range(
    "2026-07-01", "2026-07-08").as_polars()
win  = await hl.realized_performance().wallets("0xabc...").window("1h").time_range(
    "2026-07-01", "2026-07-02").as_polars()

# transfers / vaults are wallet-scoped only (no .tokens())
vaults = await hl.vaults().wallets("0xabc...").time_range(
    "2026-07-10", "2026-07-11").as_polars()
```

### `realized_performance` — snapshot vs windowed

Columns: `time, wallet, token, pnl, fees, net_pnl, funding, volume, buy_volume,
sell_volume, trade_count`. `net_pnl = pnl − fees` (funding is a **separate**
column). `volume = buy_volume + sell_volume` (two-sided notional; buy/sell = the
side of each fill).

- **Snapshot mode** (no `.window()`) — raw **daily absolute-cumulative** rows:
  each metric is a running total from the dataset's inception. `time` is
  **start-aligned**: a row at `D 00:00` is the cumulative through the *start* of
  day D (excludes day D), so `snapshot@(D+1) − snapshot@(D)` = day D's realized
  activity. `time` is a full `Datetime` (00:00) so it merges cleanly.
- **Windowed mode** (`.window("15m"+)`, **min 15m**) — **per-window realized**
  (relative) metrics, computed from fills + funding and stamped at the **window
  start**. A `(wallet, token, window)` row appears when the window had ≥1 trade
  **or** any funding (funding-only windows show `pnl/volume=0, funding≠0`). Sum
  over a day reconciles exactly with the snapshot delta.

```python
# realized PnL/volume/funding of a wallet in 1h buckets
h = await hl.realized_performance().wallets("0xabc...").tokens("BTC") \
    .window("1h").time_range("2026-07-01", "2026-07-02").as_polars()
# columns are period deltas; time = each hour's start

# a whole wallet GROUP, AGGREGATED to one row per (token, hour):
agg = await hl.realized_performance().wallet_groups("Whales").tokens("BTC") \
    .window("1h").aggregate().time_range("2026-07-01", "2026-07-02").as_polars()
# -> time, token, pnl, fees, net_pnl, funding, volume, buy/sell_volume, trade_count
#    (summed across the group's wallets; no `wallet` column)
```

`.aggregate()` sums every metric across the selected wallets, one row per
`(token, window)` — requires `.wallets()` or `.wallet_groups()` (aggregating an
unbounded set is rejected). Works in snapshot mode too (per token+day).

> **Tip:** for a single window's total, sum the windowed rows (or diff two
> snapshots). Only reach for `fills` when you need per-trade detail.

### `positions` — snapshots, snapshot-aggregate, change-aggregate

`positions()` **requires `.window()`** (a 15m multiple, e.g. `"15m"` / `"1h"` /
`"4h"`) and `tokens`/`wallets`/`wallet_groups`. **Three** modes. For both
aggregates `.wallets()`/`.wallet_groups()` are **optional** — with only
`.tokens()` they cover **all** wallets for those tokens.

**1. Downsampled snapshots** (no aggregate) — the position-state snapshots
**downsampled** to the window: the **last snapshot in each window** per
`(wallet, token)`, stamped at the **window start** (sparse — a window with no
snapshot produces no row; no carry-forward). Columns: `time, wallet, token,
side, amount, avg_entry, opened_at, mark_price, size, unrealized_pnl, funding,
fee, exact_avg_price`.

**2. Snapshot aggregate** (`.aggregate()`) — per-`(token, window)` **open-position
book** built from snapshots (mirrors the Group Snapshot view). `size` is `$`
notional. Pass `.aggregate(pos_recency_hrs=n)` to drop **stale** positions (keeps
a position only if the wallet had a fill in that token within `n` hours of the
snapshot). Columns:

  | Column | Meaning |
  |---|---|
  | `side` | `long`/`short`/`flat` — sign of `net_size` |
  | `net_size` | `longs_size − shorts_size` (`$`) |
  | `total_count` | # open positions (`longs_count + shorts_count`) |
  | `longs_size` / `shorts_size` | Σ `$` size on each side |
  | `longs_count` / `shorts_count` | # positions on each side |
  | `avg_entry` | `Σ(size·avg_entry)/Σ(size)` — `$`-size-weighted over all positions |

**3. Change aggregate** (`.aggregate_change()`) — per-`(token, window)`
position-**action** flow in **`$` notional** (`price × size`), computed from
**fills** and classified by each fill's transition. Columns:

  | Column | Meaning (all `$` notional) |
  |---|---|
  | `opened_long` / `opened_short` | flat → long / short |
  | `increased_long` / `increased_short` | added to an existing long / short |
  | `decreased_long` / `decreased_short` | partial close of a long / short |
  | `closed_long` / `closed_short` | long / short → flat |
  | `flip_ls` / `flip_sl` | long → short / short → long |
  | `net_pos_change` | `increased_long + decreased_short − increased_short − decreased_long` (directional inc/dec flow; excludes opens/closes/flips) |
  | `net_flip` | `flip_sl − flip_ls` (net flips into long) |
  | `net_flow` | full directional net: `(open/inc long + close/dec short + flip S→L) − (open/inc short + close/dec long + flip L→S)` |
  | `abs_flow` | gross flow: the sum of **all ten** action columns (every change's `$` notional, direction-agnostic). `abs_flow ≥ |net_flow|` |
  | `buy_size` / `sell_size` | `$` notional of long-oriented (buy) vs short-oriented (sell) fills. `buy_size` = open/inc long + close/dec short + flip S→L; `sell_size` = the 5 sell types. `buy_size + sell_size = abs_flow`, `buy_size − sell_size = net_flow` |
  | `buy_taker_size` / `sell_taker_size` | same as `buy_size`/`sell_size` but only `crossed=1` (taker / market-order) fills |

> **Dust rounding:** aggregated `$` metrics (the `positions` aggregates and
> `realized_performance` metrics) are snapped to `0` when `|value| < $0.001`, so a
> balanced wallet set shows `net_flow = 0` rather than float-cancellation dust like
> `-1e-9`. Prices, funding rates and coin amounts are never snapped.

```python
# 1. position snapshots resampled to hourly (last-in-hour, time = hour start)
snaps = await hl.positions().tokens("BTC").wallets("0xabc...") \
    .window("1h").time_range("2026-07-18", "2026-07-19").as_polars()

# 2. hourly open-position book for BTC across all wallets, non-stale (24h)
book = await hl.positions().tokens("BTC").window("1h") \
    .aggregate(pos_recency_hrs=24).time_range("2026-07-18", "2026-07-19").as_polars()
# -> time, token, side, net_size, total_count, longs/shorts_size+count, avg_entry

# 3. a wallet GROUP's hourly position-action $ flow for BTC
flow = await hl.positions().tokens("BTC").wallet_groups("Whales") \
    .window("1h").aggregate_change().time_range("2026-07-18", "2026-07-19").as_polars()
# -> time, token, opened_long, ..., flip_sl, net_pos_change, net_flip, net_flow
```

---

## 9. EVM (`client.evm`)

Sub-namespaces: `erc20`, `aave`, `uniswap`, `lido`, `spark`, `morpho`,
`aerodrome`, plus `client.evm.native_transfers()`.

Most EVM reads take a `.network(...)` (e.g. `"ethereum"`, `"base"`). A **list**
fans out across networks (§11).

### 9.1 ERC-20 transfers — `client.evm.erc20`

```python
client.evm.erc20.transfers(tokens: list[str])   # tokens MUST be a list
```

`transfers()` returns a builder with the wallet-filter surface described in
**§9.2 Wallet filters** below.

```python
# USDC transfers >= $1,000,000 on Ethereum
big = await (client.evm.erc20.transfers(["USDC"])
             .network("ethereum")
             .min_amount(1_000_000)
             .time_range("2026-07-10", "2026-07-11")
             .as_polars())
```

> **Common mistake:** `transfers("USDC")` (a bare string) raises `TypeError`.
> Always pass a list: `transfers(["USDC"])`.

### 9.2 Wallet filters (one surface, everywhere)

There is **one** wallet-selection filter surface, shared by every transfer
query (`evm.erc20`, `evm.native_transfers`, `tron.trc20`,
`tron.native_transfers`, `btc.native_transfers`) **and** by `scan_parquet`
(§12.4). The same method behaves by context:

- On a **read** → pushed into ClickHouse (indexed, efficient).
- On a **`scan_parquet`** → the server resolves the selection to member
  addresses and filters the snapshot in DuckDB.

Every filter accepts **`str | list[str]`** (a single value or "any of"), across
three roles × four dimensions, plus `exclude_` variants:

| | address | label / entity | category | group |
|---|---|---|---|---|
| **involving** (sender OR receiver) | `.involving(v)` | `.involving_label(v)` / `.involving_entity(v)` | `.involving_category(v)` | `.involving_groups(v)` |
| **sender** | `.sender(v)` | `.sender_label(v)` / `.sender_entity(v)` | `.sender_category(v)` | `.sender_groups(v)` |
| **receiver** | `.receiver(v)` | `.receiver_label(v)` / `.receiver_entity(v)` | `.receiver_category(v)` | `.receiver_groups(v)` |
| **exclude** | `.exclude_<role>(v)` | `.exclude_<role>_label(v)` | `.exclude_<role>_category(v)` | `.exclude_<role>_groups(v)` |

Plus amount bounds: `.min_amount(x)` / `.max_amount(x)`.

Dimensions:
- **address** — raw wallet address.
- **label / entity** — synonyms; the wallet's `entity` tag (e.g. `"Binance"`).
- **category** — a wallet category (e.g. `"CEX"`, `"Hot-Wallet"`).
- **groups** — one or more named wallet **groups** (from the dashboard's
  `/wallets` page), resolved to their member addresses.

All values are matched case-insensitively. `exclude_involving` means *neither*
side matches.

```python
# whales OR smart-money, excluding hot wallets, over $1M
flow = await (client.evm.erc20.transfers(["USDC", "USDT"])
              .network("ethereum")
              .involving_groups(["Whales", "Smart-Money"])
              .exclude_sender_category("Hot-Wallet")
              .involving_label("Binance")
              .min_amount(1_000_000)
              .time_range("2026-07-10", "2026-07-11")
              .as_polars())
```

Notes:
- Group / category / entity names are **case-insensitive**. An inclusive filter
  that resolves to no addresses returns 0 rows; an exclude of an empty selection
  is a no-op.
- Groups are scoped to the local user (no multi-user auth yet). The special
  **Default** group may not match — it's synthesized in the UI and may lack a
  stored record.
- **Resolving a selection yourself:** `client.wallets.addresses(...)` returns
  the addresses a selection resolves to (see §13).

### 9.3 Aave — `client.evm.aave`

Six events, each a method returning a builder. Extra chainable
`.eth_market_type(str)` (e.g. `"core"`).

```python
client.evm.aave.deposits()      # deposit
client.evm.aave.withdrawals()   # withdraw
client.evm.aave.borrows()       # borrow
client.evm.aave.repays()        # repay
client.evm.aave.flashloans()    # flashloan
client.evm.aave.liquidations()  # liquidation
```

```python
borrows = await (client.evm.aave.borrows()
                 .network("ethereum")
                 .eth_market_type("core")
                 .involving("0xabc...")
                 .time_range("2026-07-10", "2026-07-11")
                 .as_polars())
```

### 9.4 Uniswap (v3) — `client.evm.uniswap`

Methods take `(symbol0, symbol1, fee)`:

```python
client.evm.uniswap.swaps("WETH", "USDC", 3000)
client.evm.uniswap.deposits("WETH", "USDC", 3000)
client.evm.uniswap.withdrawals("WETH", "USDC", 3000)
client.evm.uniswap.collects("WETH", "USDC", 3000)
```

```python
swaps = await (client.evm.uniswap.swaps("WETH", "USDC", 3000)
               .network("ethereum")
               .time_range("2026-07-10", "2026-07-11")
               .as_polars())
```

### 9.5 Lido — `client.evm.lido`

```python
client.evm.lido.deposits()               # deposit
client.evm.lido.withdrawal_requests()    # withdrawal_request
client.evm.lido.withdrawals_claimed()    # withdrawal_claimed
client.evm.lido.l2_deposits()            # l2_deposit
client.evm.lido.l2_withdrawal_requests() # l2_withdrawal_request
```

### 9.6 Spark (TN-exclusive) — `client.evm.spark`

Same six-event surface as Aave, byte-for-byte:

```python
client.evm.spark.deposits() / .withdrawals() / .borrows()
                / .repays() / .flashloans() / .liquidations()
```

### 9.7 Morpho (TN-exclusive) — `client.evm.morpho`

Seven events; extra chainable `.market_id(hex)` to filter to one market.

```python
client.evm.morpho.supplies()             # supply
client.evm.morpho.withdrawals()          # withdraw
client.evm.morpho.borrows()              # borrow
client.evm.morpho.repays()               # repay
client.evm.morpho.supply_collaterals()   # supply_collateral
client.evm.morpho.withdraw_collaterals() # withdraw_collateral
client.evm.morpho.liquidations()         # liquidation
```

```python
b = await (client.evm.morpho.borrows()
           .network("ethereum")
           .market_id("0xdead...")
           .time_range("2026-07-10", "2026-07-11")
           .as_polars())
```

### 9.8 Aerodrome (TN-exclusive, Base) — `client.evm.aerodrome`

Two sub-namespaces. **Pass `.network("base")`** — aerodrome is Base-only and the
namespace does not auto-set it.

```python
# Concentrated (Uni-v3-style): (symbol0, symbol1, tick_spacing=None)
client.evm.aerodrome.concentrated.swaps("WETH", "USDC", tick_spacing=100)
client.evm.aerodrome.concentrated.deposits(...) / .withdrawals(...) / .collects(...)

# Basic (v2-style AMM): (symbol0, symbol1, stable=None)
client.evm.aerodrome.basic.swaps("WETH", "USDC", stable=False)
client.evm.aerodrome.basic.deposits(...) / .withdrawals(...) / .claims(...)
```

```python
swaps = await (client.evm.aerodrome.concentrated.swaps("WETH", "USDC")
               .network("base")
               .time_range("2026-07-10", "2026-07-11")
               .as_polars())
```

---

## 10. Tron & Bitcoin

### 10.1 Tron — `client.tron`

Network is auto-set to `"TRON"`.

```python
# TRC-20 (tokens must be a list)
usdt = await client.tron.trc20.transfers(["USDT"]) \
           .min_amount(10_000).time_range("2026-07-10", "2026-07-11").as_polars()

# native TRX
trx = await client.tron.native_transfers() \
          .time_range("2026-07-10", "2026-07-11").as_polars()
```

Both carry the same transfer-filter surface (min/max amount, sender/receiver
+label/category, exclude_*, involving*).

### 10.2 Bitcoin — `client.btc`

```python
btc = await client.btc.native_transfers() \
          .min_amount(1.0).time_range("2026-07-10", "2026-07-11").as_polars()
```

`client.btc.mined()` exists in the API but is **not supported** on the server
yet (404) — see §16.

---

## 11. Multi-network fan-out

Pass a **list** to `.network([...])` on any EVM read. The client issues one
request per network concurrently, concatenates the results, and (for >1 network)
auto-adds a `network`/`with_network` tag so rows stay distinguishable. Opt out
with `.with_network(False)`.

```python
multi = await (client.evm.erc20.transfers(["USDC"])
               .network(["ethereum", "base"])
               .time_range("2026-07-10", "2026-07-11")
               .as_polars())
```

---

## 12. Snapshots (save / load / list / delete / scan)

Snapshots are named parquet files persisted on the **server** (`SNAPSHOTS_DIR`).
Saving does not stream the data back through your process, so it's the right way
to persist large result sets.

### 12.1 Save — `q.as_parquet(key)`

```python
await (client.binance.spot.raw_trades("BTC")
       .time_range("2026-07-01", "2026-07-08")
       .as_parquet("btc_spot_july"))         # returns None; data stays server-side
```

For transfer queries the save is streamed + merged server-side (bounded memory),
and a multi-network fan-out is concatenated into a single snapshot:

```python
await (client.evm.erc20.transfers(["USDC"])
       .network(["ethereum", "base"])
       .time_range("2026-07-01", "2026-07-08")
       .as_parquet("usdc_flows_july"))
```

The snapshot **read/manage** surface lives under **`client.snapshot.*`**:
`list` / `load` / `scan` / `delete`. (There is no `snapshot.save` — snapshots are
*written* by any read query's `.as_parquet(key)` terminal, §12.1.)

> **2.0.0 breaking change:** the old top-level `client.load_parquet` /
> `list_snapshots` / `list_snapshots_detailed` / `scan_parquet` /
> `delete_snapshot` were **removed**. Use the `client.snapshot.*` equivalents
> below.

### 12.2 Load — `client.snapshot.load(key, since=None, until=None)`

Returns a builder; pick the output type with a terminal:

```python
df    = await client.snapshot.load("btc_spot_july").as_polars()   # polars
df_pd = await client.snapshot.load("btc_spot_july").as_pandas()   # pandas
tbl   = await client.snapshot.load("btc_spot_july").as_arrow()    # pyarrow.Table
raw   = await client.snapshot.load("btc_spot_july").bytes()       # raw parquet bytes

# client-side [since, until) slice (ctor kwarg or .time_range()):
df = await client.snapshot.load("btc_spot_july",
                                since="2026-07-03", until="2026-07-05").as_polars()
```

`time` is normalized to `Datetime('ms', UTC)` (on `as_polars`/`as_pandas`/`as_arrow`;
`bytes()` returns the raw stored file) so a loaded snapshot joins cleanly with live
query frames.

### 12.3 List / delete

```python
keys = await client.snapshot.list()            # -> ["btc_spot_july", ...]
await client.snapshot.delete("btc_spot_july")   # hard remove, no undo

# With sizes (human-readable) + a roster-wide total:
info = await client.snapshot.list(detailed=True)   # or client.snapshot.list_detailed()
# {
#   "snapshots": [{"key": "btc_spot_july", "bytes": 27648,
#                  "size": "27.0 KB", "modified": "2026-08-03T07:15:40Z"}, ...],
#   "count": 131, "total_bytes": 46826578719, "total_size": "43.6 GB",
# }
for s in info["snapshots"]:
    print(f'{s["size"]:>10}  {s["key"]}')
print("total:", info["total_size"])
```

### 12.4 Scan — filter a saved snapshot — `client.snapshot.scan(key, ...)`

Uses the **same wallet-filter surface** as the reads (§9.2) — `involving` /
`sender` / `receiver` + `_label`/`_entity`/`_category`/`_groups` + `exclude_*`,
each `str | list`, plus `.min_amount()` / `.max_amount()` and `.time_range()`.
Chain filters, then a terminator (`as_polars` / `as_pandas` / `as_parquet`):

```python
df = await (client.snapshot.scan("usdc_flows_july")
            .involving_label("Binance")
            .exclude_sender_category("Hot-Wallet")
            .sender_groups(["Whales"])
            .as_polars())

# re-save the filtered subset as a new snapshot (bytes never leave the server)
await (client.snapshot.scan("usdc_flows_july")
       .involving_groups(["Whales"])
       .as_parquet("usdc_whales_only"))
```

How it works: the server resolves each selection (group / category / entity) to
member addresses via ClickHouse, then filters the snapshot in **DuckDB** (the
snapshot parquet as one table, each address set as another). Unlike older
versions, **category / entity filters now apply on a scan** — they reduce to an
address set. Only the matching subset is returned; the snapshot never leaves the
server.

`snapshot.scan` options: `since`, `until`, `engine`, `normalize_addresses`.

> A filter that references a column the snapshot doesn't have (e.g.
> `sender_category` on a binance-ohlcv snapshot) is a harmless no-op.

---

## 13. Wallet labels — `client.wallets`

```python
rows = await client.wallets.list(category="CEX", entity=None, search=None,
                                  limit=100, offset=0)   # -> list[dict]
one  = await client.wallets.get("0xabc...")              # -> dict | None (None on 404)
await client.wallets.upsert(df_or_bytes)                 # pandas/polars DF or parquet bytes
await client.wallets.delete("0xabc...")
```

**Resolve a selection → addresses** — `client.wallets.addresses(...)` returns the
addresses a wallet selection resolves to (the same resolver the `scan_parquet`
filters use). The result is the **union** across every given dimension
(`labels` is a synonym for `entities`):

```python
addrs = await client.wallets.addresses(groups=["Whales", "Smart-Money"])
addrs = await client.wallets.addresses(categories="CEX", entities=["Binance"])
# -> ["0xabc...", "0xdef...", ...]  (distinct, lowercased)
```

---

## 14. Jobs (admin) — `client.jobs`

Proxies the ingestion job queue:

```python
jobs = await client.jobs.list(status=None, limit=200)  # -> list[dict]
job  = await client.jobs.get(job_id)                    # -> dict
await client.jobs.cancel(job_id)                        # -> dict
final = await client.jobs.wait(job_id, poll_interval=2.0, timeout=None)  # poll to terminal
await client.jobs.submit(path, body=None)               # generic POST helper
```

> **Removed in 0.8.0:** the `client.cache.*` namespace, the per-namespace
> `flush` / `compact` / `dedup` maintenance methods, and query `.cache()` /
> `.parallel()` — all were Horatio-era no-ops (data_provider reads live from
> ClickHouse, which manages its own caches and ReplacingMergeTree merges).

---

## 15. Error handling

```python
from tradernick_data_provider import DataProviderError, DataProviderHTTPError

try:
    df = await client.binance.ohlcv("BTC", "5x").as_polars()  # bad window
except DataProviderHTTPError as e:
    print(e.status_code, e)     # e.g. 400 "invalid window"
except DataProviderError:
    ...                         # base class for all client errors
```

- The server signals failures with a JSON body `{"error": "..."}`; the client
  raises `DataProviderHTTPError(status_code, message)`.
- `DataProviderHTTPError` subclasses `DataProviderError`.
- An empty result is **not** an error — you get a 0-row frame with the correct
  columns.

---

## 16. Not exposed / unsupported (important)

These are surfaced by the API for Horatio parity but **do not return real data**
in the current TN server. Do not build on them expecting data:

| Surface | Status |
|---|---|
| `.aggregate(...)` (on every builder) | **404 — no server aggregate route.** Aggregate client-side from the raw `.as_polars()` frame instead. |
| `client.btc.mined()` | **404 — no server route/table** (Bitcoin coinbase payouts not ingested). |
| `client.hyperliquid.sends()` | **Exposed but empty.** Returns the correct schema with **0 rows** (not ingested yet). |
| `client.hyperliquid.spot_transfers()` | **Exposed but empty.** Same as above. |
| `client.evm.stader`, `client.evm.threshold` | **Removed** (not ingested). |
| `gmx.*` | **Not available.** |

Everything else in this guide returns real data against a populated server.

---

## 17. Full endpoint reference

| Call | Server route |
|---|---|
| `binance.ohlcv(t,w)` | `POST /binance/ohlcv/read` |
| `binance.raw_trades(t)` | `POST /binance/raw_trades/read` |
| `binance.book_depth(t)` | `POST /binance/book_depth/read` |
| `binance.open_interest(t)` | `POST /binance/open_interest/read` |
| `binance.funding_rate(t)` | `POST /binance/funding_rate/read` |
| `binance.long_short_ratios(t)` | `POST /binance/long_short_ratios/read` |
| `binance.spot.ohlcv(t,w)` | `POST /binance/spot/ohlcv/read` |
| `binance.spot.raw_trades(t)` | `POST /binance/spot/raw_trades/read` |
| `hyperliquid.<x>()` | `POST /hyperliquid/<x>/read` |
| `evm.erc20.transfers([...])` | `POST /evm/erc20_transfers/read` (`/read/min` with `.min_amount()`) |
| `evm.native_transfers()` | `POST /evm/native_transfers/read` (`/read/min`) |
| `evm.aave.<event>()` | `POST /evm/aave/read` |
| `evm.uniswap.<event>(...)` | `POST /evm/uniswap/read` |
| `evm.lido.<event>()` | `POST /evm/lido/read` |
| `evm.spark.<event>()` | `POST /evm/spark/read` |
| `evm.morpho.<event>()` | `POST /evm/morpho/read` |
| `evm.aerodrome.concentrated.<event>(...)` | `POST /evm/aerodrome/concentrated/read` |
| `evm.aerodrome.basic.<event>(...)` | `POST /evm/aerodrome/basic/read` |
| `tron.trc20.transfers([...])` | `POST /tron/trc20_transfers/read` (`/read/min`) |
| `tron.native_transfers()` | `POST /tron/native_transfers/read` (`/read/min`) |
| `btc.native_transfers()` | `POST /btc/native_transfers/read` (`/read/min`) |
| `snapshot.{load, list, list_detailed, scan, delete}` | `POST /snapshots/{load,delete,scan}`, `GET /snapshots/list`, `GET /snapshots/list_detailed` |
| `wallets.*` | `GET/POST/DELETE /wallets` |
| `jobs.*` | `GET/POST /jobs/...` |
| `health()` | `GET /health` |

---

## 18. Copy-paste starter

```python
import asyncio
from tradernick_data_provider import DataProviderClient, DataProviderHTTPError

URL = "http://localhost:10005"

async def main():
    async with DataProviderClient(URL) as client:
        assert await client.health()

        # Binance spot trades
        spot = await (client.binance.spot.raw_trades("BTC")
                      .time_range("2026-07-10T00:00:00Z", "2026-07-10T00:05:00Z")
                      .as_polars())
        print("spot trades:", spot.height)

        # Large USDC transfers on Ethereum
        try:
            big = await (client.evm.erc20.transfers(["USDC"])
                         .network("ethereum")
                         .min_amount(1_000_000)
                         .time_range("2026-07-10", "2026-07-11")
                         .as_polars())
            print("whale transfers:", big.height)
        except DataProviderHTTPError as e:
            print("request failed:", e.status_code, e)

        # Persist a big range as a server-side snapshot, then reload
        await (client.binance.spot.ohlcv("BTC", "1m")
               .time_range("2026-06-01", "2026-07-01")
               .as_parquet("btc_spot_june"))
        june = await client.snapshot.load("btc_spot_june").as_polars()
        print("snapshot rows:", june.height)

asyncio.run(main())
```

---

## 19. Version notes

- **1.1.0** — `positions().aggregate_change()` gains an **`abs_flow`** column: the
  gross flow, i.e. the sum of all ten action columns (every change's `$` notional,
  direction-agnostic; `abs_flow ≥ |net_flow|`). Additive — no other change.
- **1.0.0** — First stable release; the public API is now considered stable.
  Consolidates the `0.12`–`0.13` line: the `hyperliquid.positions()` endpoint
  (below), the IDE/jedi fluent-builder fix, and `$`-metric dust rounding. No API
  change vs `0.13.2`.
- **0.13.2** — Aggregated `$` metrics are snapped to `0` when `|value| < $0.001`
  (kills float-cancellation dust like a `-1e-9` `net_flow` on a balanced wallet
  set). Prices / funding rates / coin amounts / counts are never snapped.
- **0.13.1** — `positions().aggregate()` takes `pos_recency_hrs=` as a keyword arg
  (was a separate `.pos_recency_hrs()` chain call).
- **0.13.0** — `positions`: renamed the fills-based aggregate to
  **`.aggregate_change()`**, and **`.aggregate()`** is now the snapshot
  open-position book (side / net_size / counts / sizes / avg_entry), with an
  optional `pos_recency_hrs=` staleness filter. Neither aggregate requires a
  wallet set (tokens-only → all wallets).
- **0.12.1** — `positions().aggregate()` (change-aggregate) no longer requires
  `.wallets()`/`.wallet_groups()` — with only `.tokens()` it covers all wallets.
- **0.12.0** — `hyperliquid.position_history()` → **`positions()`** (renamed).
  `.window()` is now **required** (a 15m multiple); default mode downsamples the
  position snapshots to the window (last-in-window, start-aligned), and
  `.aggregate()` returns the per-`(token, window)` fills action-flow frame.
- **0.11.2 / 0.11.3** — IDE/jedi fix: the fluent builders use the explicit
  self-type TypeVar idiom (`def m(self: _T, ...) -> _T`) instead of `Self`, which
  jedi follows through the mixin hierarchy so chained calls keep autocompleting;
  dropped the `typing_extensions` dependency. `0.11.3` corrected stale `scan_parquet`
  docstrings.
- **0.11.1** — typing/IDE fix: import `Self` from `typing_extensions` (stdlib
  `typing.Self` is 3.11+ only, but the package supports 3.10), so editors, jedi,
  and type checkers follow the fluent builders' chained return types on every
  supported Python. Added the `typing_extensions` dependency; fixed `wallets`
  annotations that were shadowed by the `.list` method name. (`py.typed` already
  shipped.) No API change.
- **0.11.0** — Hyperliquid wallet filtering: **`.wallet_groups(...)`** on every
  wallet-aware endpoint (pass group names; resolved to member addresses server-side,
  unions with `.wallets()`); **`ohlcv()` drops `.wallets()`** (candles are market-wide
  — it was a silent no-op); and **`realized_performance().aggregate()`** sums metrics
  across the selected wallets → one row per `(token, window)` (requires
  `.wallets()`/`.wallet_groups()`).
- **0.10.0** — `hyperliquid.trade_history()` → **`realized_performance()`** (renamed).
  Adds the `funding` column and an optional **`.window("15m"+)`** for per-window
  realized metrics (fills+funding, window-start aligned) vs the default daily
  cumulative snapshots. Snapshot `time` is now **start-aligned** (`D 00:00` row
  excludes day D; `snap@(D+1)−snap@(D)` = day D). `trade_history()` removed.
- **0.9.0** — `hyperliquid.fills()` now **drops** `fee_token`, `builder_fee`,
  `crossed`, `tid`, `oid`, `hash` by default (server-side) — pass
  `.with_extra_cols()` to keep them. And `transfers()` / `vaults()` are no longer
  token-scoped: `.tokens()` is removed from them (it was a silent no-op — those
  tables have no `token` column); filter them by `.wallets()`.
- **0.8.0** — removed the Horatio-era **no-ops**: query `.cache()` / `.parallel()`,
  the `client.cache.*` namespace, and all per-namespace `flush` / `compact` /
  `dedup` maintenance methods. They did nothing (data_provider reads live from
  ClickHouse). If you called them, just delete the calls.
- **0.7.1** — `hyperliquid.tokens()` / `.wallets()` now accept a list as well as
  varargs (`.tokens(["BTC","ETH"])` == `.tokens("BTC","ETH")`); a bare list no
  longer nests silently.
- **0.7.0** — **BREAKING: unified filter API.** One wallet-filter surface (§9.2)
  used by both reads and `scan_parquet`; every filter accepts `str | list[str]`.
  The `local_*` methods are **removed** — use the unprefixed methods everywhere
  (on a read they push into ClickHouse; on a scan the server resolves the
  selection to addresses and filters the snapshot in DuckDB, so category/entity
  filters now work on snapshots). Added `client.wallets.addresses(...)`.
  Migration: `.local_involving_categories([...])` → `.involving_category([...])`,
  `.sender("0x…")` still works (now also accepts a list), etc.
- **0.6.0** — wallet-group filters on all transfer queries (list-valued,
  name-based, resolved server-side).
- **0.5.1** — ships this usage guide inside the package (docs only).
- **0.5.0** — added `binance.spot.{ohlcv, raw_trades}`; fixed
  `evm.erc20.transfers(...).min_amount(...)` (previously 404'd); first test suite.
- **0.4.0** — transfer wallet-selection pushdown; multi-network `as_parquet`.
- **0.3.0** — Spark / Morpho / Aerodrome (TN-exclusive).
- **0.2.0** — Horatio read-parity (binance / aave / lido / uniswap / hyperliquid
  / transfers).
