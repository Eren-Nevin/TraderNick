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
`hyperliquid`, `wallets`, `cache`, `jobs`) match, and the chainable builders +
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

### 7.3 Binance maintenance (rarely needed)

These trigger server-side cache/compaction ops and return `None` or a status
dict; most users never call them:
`flush_raw_trades`, `flush_ohlcv`, `flush_exchange`, `compact_raw_trades`,
`compact_ohlcv`, `compact_exchange`.

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
hl.transfers()          # ledger transfers (populated)
hl.vaults()             # vault data
hl.trade_history()      # PRE-AGGREGATED PnL/volume — fast path; needs tokens/wallets
hl.position_history()   # position snapshots — needs tokens/wallets
hl.sends()              # EXPOSED BUT EMPTY (see §16)
hl.spot_transfers()     # EXPOSED BUT EMPTY (see §16)
```

Chainables (in addition to `.time_range()`):

| Method | Effect |
|---|---|
| `.tokens(*symbols)` | restrict to tokens, e.g. `.tokens("BTC", "ETH")` |
| `.wallets(*addresses)` | restrict to wallet addresses |
| `.window(size)` | candle/window size, e.g. `.window("1h")` |
| `.per_token(flag=True)` | per-token breakdown |
| `.skip_hip3(flag=True)` | exclude HIP-3 markets |
| `.market_type(t)` | e.g. `"perp"` / `"spot"` |
| `.limit(n)` | cap rows |

Examples:

```python
fills = await hl.fills().tokens("BTC").time_range(
    "2026-07-10T00:00:00Z", "2026-07-10T00:01:00Z").as_polars()

# trade_history REQUIRES tokens or wallets (guards a full-table scan)
pnl = await hl.trade_history().wallets("0xabc...").time_range(
    "2026-07-01", "2026-07-08").as_polars()

positions = await hl.position_history().tokens("BTC", "ETH").time_range(
    "2026-07-10", "2026-07-11").as_polars()
```

> **Performance rule:** for PnL / volume / leaderboard questions use
> `trade_history` (pre-aggregated). Only reach for `fills` when you need
> per-trade detail (price, side, order id).

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

`transfers()` returns a builder with the full transfer-filter surface:

| Filter | Meaning |
|---|---|
| `.min_amount(x)` / `.max_amount(x)` | amount bounds |
| `.sender(addr)` / `.receiver(addr)` | exact address |
| `.sender_label(l)` / `.receiver_label(l)` | wallet label |
| `.sender_category(c)` / `.receiver_category(c)` | wallet category |
| `.exclude_sender(addr)` / `.exclude_receiver(addr)` | exclude address |
| `.exclude_sender_label(l)` / `.exclude_receiver_label(l)` | exclude by label |
| `.exclude_sender_category(c)` / `.exclude_receiver_category(c)` | exclude by category |

Plus the base `.involving(addr)` / `.involving_label(l)` / `.involving_category(c)`
and their `.exclude_involving*` variants (match sender **or** receiver).

```python
# USDC transfers >= $1,000,000 on Ethereum
big = await (client.evm.erc20.transfers(["USDC"])
             .network("ethereum")
             .min_amount(1_000_000)
             .time_range("2026-07-10", "2026-07-11")
             .as_polars())

# everything touching Binance-labeled wallets, excluding hot wallets
flow = await (client.evm.erc20.transfers(["USDC", "USDT"])
              .network("ethereum")
              .involving_label("Binance")
              .exclude_sender_category("Hot-Wallet")
              .time_range("2026-07-10", "2026-07-11")
              .as_polars())
```

> **Common mistake:** `transfers("USDC")` (a bare string) raises `TypeError`.
> Always pass a list: `transfers(["USDC"])`.

### 9.2 Native transfers — `client.evm.native_transfers()`

Same filter surface as ERC-20 transfers (`min_amount`, `sender`/`receiver`
+`_label`/`_category`, `exclude_*`, `involving*`).

```python
eth = await (client.evm.native_transfers()
             .network("ethereum")
             .min_amount(100)
             .time_range("2026-07-10", "2026-07-11")
             .as_polars())
```

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

### 12.2 Load — `client.load_parquet(key, since=None, until=None)`

```python
df = await client.load_parquet("btc_spot_july")                       # polars
df = await client.load_parquet("btc_spot_july",
                               since="2026-07-03", until="2026-07-05") # sliced on load
df_pd = (await client.load_parquet("btc_spot_july")).to_pandas()
```

`time` is normalized to `Datetime('ms', UTC)` so a loaded snapshot joins cleanly
with live query frames.

### 12.3 List / delete

```python
keys = await client.list_snapshots()          # -> ["btc_spot_july", ...]
await client.delete_snapshot("btc_spot_july")
```

### 12.4 Scan — lazy server-side filtering — `client.scan_parquet(key, ...)`

Returns a builder that pushes `local_*` filters down to the server (DuckDB engine
by default) so only matching rows come back. Chain `local_*` filters, then a
terminator (`as_polars` / `as_pandas` / `as_parquet(new_key)`):

```python
df = await (client.scan_parquet("usdc_flows_july")
            .local_involving_labels(["Binance"])
            .local_exclude_sender_categories(["Hot-Wallet"])
            .as_polars())

# re-save the filtered subset as a new snapshot
await (client.scan_parquet("usdc_flows_july")
       .local_involving_entities(["Binance"])
       .as_parquet("usdc_binance_only"))
```

`scan_parquet` options: `since`, `until`, `engine='duckdb'|'polars'`,
`normalize_addresses=None` (auto).

**`local_*` filter methods** (24 total; also usable directly on transfer read
queries): `local_involving[_labels|_categories|_entities]`,
`local_sender[...]`, `local_receiver[...]`, and every `local_exclude_*`
variant. Each takes a **list of strings** and appends one filter step
(union within a call).

> **Caveat:** the label/category/entity `local_*` filters only take effect when
> `wallet_labels` are co-mounted with the snapshot on the server. The
> **address-based** filters (`local_involving([...])`, `local_sender([...])`,
> `local_receiver([...])`) always work — prefer those if unsure.

---

## 13. Wallet labels — `client.wallets`

```python
rows = await client.wallets.list(category="CEX", entity=None, search=None,
                                  limit=100, offset=0)   # -> list[dict]
one  = await client.wallets.get("0xabc...")              # -> dict | None (None on 404)
await client.wallets.upsert(df_or_bytes)                 # pandas/polars DF or parquet bytes
await client.wallets.delete("0xabc...")
```

---

## 14. Jobs & cache (admin)

### 14.1 `client.jobs`

Proxies the ingestion job queue:

```python
jobs = await client.jobs.list(status=None, limit=200)  # -> list[dict]
job  = await client.jobs.get(job_id)                    # -> dict
await client.jobs.cancel(job_id)                        # -> dict
final = await client.jobs.wait(job_id, poll_interval=2.0, timeout=None)  # poll to terminal
await client.jobs.submit(path, body=None)               # generic POST helper
```

### 14.2 `client.cache`

```python
await client.cache.flush()                    # -> None
await client.cache.compact()                  # -> dict
await client.cache.dedup(dry_run=False)       # -> {job_id, status}
await client.cache.migrate_time()             # -> dict
```

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
| `load_parquet / list_snapshots / delete_snapshot / scan_parquet` | `POST /snapshots/*`, `GET /snapshots/list` |
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
        june = await client.load_parquet("btc_spot_june")
        print("snapshot rows:", june.height)

asyncio.run(main())
```

---

## 19. Version notes

- **0.5.1** — ships this usage guide inside the package (docs only).
- **0.5.0** — added `binance.spot.{ohlcv, raw_trades}`; fixed
  `evm.erc20.transfers(...).min_amount(...)` (previously 404'd); first test suite.
- **0.4.0** — transfer wallet-selection pushdown; multi-network `as_parquet`.
- **0.3.0** — Spark / Morpho / Aerodrome (TN-exclusive).
- **0.2.0** — Horatio read-parity (binance / aave / lido / uniswap / hyperliquid
  / transfers).
