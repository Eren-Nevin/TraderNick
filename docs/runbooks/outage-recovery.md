# Runbook: multi-hour / multi-day outage recovery

What to do when ClickHouse (or ingestion) has been down long enough to leave a
data gap. Written from the 2026-07-03/04 incident (ClickHouse stopped ~12h).

The golden rule: **every raw ingest table is a `ReplacingMergeTree`, so
re-inserting is idempotent — backfilling can never permanently duplicate.** Most
of recovery is (1) get the DB back, (2) find the gap, (3) re-fetch it, (4) rebuild
the derived rollups.

---

## 0. TL;DR — the happy path

```bash
# 1. Is ClickHouse up?
docker compose ps -a clickhouse                 # look for "Exited"
docker compose up -d clickhouse                 # start it; wait for (healthy)

# 2. Backend serving again?
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://localhost:10000/api/ohlcv?exchange=binance&token=BTC&interval=1h&since=$(date -u -d '3 hours ago' +%FT%TZ)&until=$(date -u +%FT%TZ)"   # want 200

# 3. Restart ingestion so it reconnects AND boot-gap-fills the hole
docker compose restart hyperliquid_live binance_live data_process_live \
  transfers_live aave_live uniswap_live morpho_live spark_live lido_live gmx_live aerodrome_live

# 4. Audit the gap (section 4), backfill raw tables (section 5),
#    OPTIMIZE (section 6), rebuild derived rollups in the admin panel (section 7).
```

Ports: dashboard/proxy `:10000`, admin_server (jobs API, auth) `:10001`.

---

## 1. Detection

Symptoms of ClickHouse being down:
- `data_server` logs / API return **500** with `Cannot connect to host clickhouse:8123`
  / `Temporary failure in name resolution`.
- Dashboard charts empty or stale; ingestion logs show the same connect error.

`data_server` and the ingesters stay **"Up"** while blind — container status alone
is not enough. Check ClickHouse specifically:

```bash
docker compose ps -a clickhouse
```

---

## 2. Diagnose what stopped ClickHouse (before assuming a crash)

```bash
uptime -s                                        # host reboot? (was it up the whole time)
docker inspect tradernick-clickhouse-1 --format \
  'Finished={{.State.FinishedAt}} Exit={{.State.ExitCode}} OOM={{.State.OOMKilled}} Restart={{.HostConfig.RestartPolicy.Name}}'
# CH's own shutdown log (persists in the volume across restarts):
docker compose exec -T clickhouse sh -c \
  "grep -iE 'Received signal|termination signal|shutdown' /var/log/clickhouse-server/clickhouse-server.log | tail"
```

Read the signals, don't guess:
- **Exit 0 + `OOMKilled=false` + host uptime long** = a graceful `SIGTERM` (signal 15),
  i.e. a *targeted* `docker stop` / `compose stop clickhouse` (or a partial compose
  command) — **not** a crash, OOM, or reboot. Confirm no *other* container restarted
  at the same time (a docker-daemon restart or host reboot would hit them all).
- **Exit 137 / `OOMKilled=true`** = OOM-killed. Check `free -g` and CH peak memory.
- **Host rebooted** (`uptime -s` near the stop time) = everything stopped together.

### Why the 2026-07 outage lasted 12h (the real lesson)
ClickHouse was the **only** service with `restart: no` (every other service is
`restart: unless-stopped`). A stray `SIGTERM` therefore left it down until noticed.
**Fixed:** `clickhouse` now has `restart: unless-stopped` in `docker-compose.yml`.
If you ever see `RestartPolicy=no` on it again, re-apply:
```bash
docker update --restart unless-stopped tradernick-clickhouse-1   # live, no downtime
```

---

## 3. Bring it back

```bash
docker compose up -d clickhouse
# wait for health
until docker compose ps clickhouse --format '{{.Status}}' | grep -q healthy; do sleep 3; done
```

Then verify the backend (section 0, step 2 → expect `200`).

---

## 4. Restart ingestion + audit the gap

`data_server` reconnects on its own (per-request connections). The ingesters mostly
self-reconnect too, **but restarting them triggers a boot gap-fill** that backfills
the hole. Note the split we learned:

- **HL + Binance** ingesters **boot-gap-fill** the hole automatically on restart.
- **EVM providers** (transfers/aave/uniswap/morpho/spark/lido/gmx/aero) reconnect to
  *live* but only resume from *now* — the historical hole needs a restart's
  boot-gap-fill **or** a manual backfill (section 5).

```bash
docker compose restart hyperliquid_live binance_live data_process_live \
  transfers_live aave_live uniswap_live morpho_live spark_live lido_live gmx_live aerodrome_live
```

### Audit which tables actually have a hole
Coverage over the outage window vs the **same window the day before** (baseline).
`out_core ≈ baseline` = filled; `out_core = 0` with a large baseline = a real hole.
Sparse tables (some liquidations/flashloans) legitimately have few rows — the
baseline comparison disambiguates sparse-vs-holed.

```sql
-- adjust the two windows to your outage; both are [start, end)
SELECT
  countIf(time >= '2026-07-03 17:00:00' AND time < '2026-07-04 04:00:00') AS out_core,
  countIf(time >= '2026-07-02 17:00:00' AND time < '2026-07-03 04:00:00') AS baseline,
  toString(max(time)) AS freshest
FROM tradernick.<table>
WHERE time >= '2026-07-02 17:00:00' AND time < '2026-07-04 04:00:00';
```

Time column is `time` on raw tables and `exchange_flow_minute`; `bucket` on
`hl_position_history_15m`/`_1h`; `day` on the `*_daily` / `_eod_wallet` rollups.

### First rule out an upstream (DeFiStream) gap
If a provider's live loop logs `rows=0` for the *current* window, the data may not
be ours to fill. Query DeFiStream directly before backfilling (key is in `.env`,
header `X-API-Key`):

```bash
KEY=$(grep -oE 'DEFISTREAM_API_KEY=[^[:space:]]+' .env | cut -d= -f2)
curl -s -H "X-API-Key: $KEY" \
  "https://api.defistream.dev/v1/evm/lido/events/deposit?wallet_namespace=public&network=ETH&since=<gap-start>&until=<gap-end>&format=json" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d if isinstance(d,list) else d.get('data',[])),'rows')"
```
`0 rows` for both the gap **and** recent windows → the upstream feed is down; our
ingestion is fine, nothing to backfill until it resumes (this is what happened to
Lido on 2026-07-03).

---

## 5. Backfill the raw tables

Every raw table is `ReplacingMergeTree` → **re-inserting is safe** (dupes collapse
on the sort key, which carries the event identity: `tx_id`+`log_index`, `tid`,
`id`, or a natural `(token,time,…)` key).

- **Do NOT use `force`.** `force` = purge the range first (a `DELETE`), only needed
  for a clean-slate rewrite of *suspect* data. For a gap it's pointless; for an
  overlap, RMT dedups anyway.
- Backfill via the **admin panel → Data Process backfill** per-source forms (they
  hit `POST /jobs/backfill/<source>` on admin_server, chunked with progress), or the
  job API directly. **`since` is required** and must be explicit (a guard against
  runaway backfills). Use a `since` a bit before the gap; `until` defaults to now.
- It's fine to overlap the live edge — RMT collapses the overlap.

### After backfilling: collapse the transient dupes
Backfilling (and the fills 60s re-fetch) leaves duplicate rows until merges run.
Force it per table (all tables partition by `toYYYYMM(time)`; the outage month here
was `202607`):

```bash
for t in transfers hl_fills binance_raw_trades aave_deposits ...; do
  docker compose exec -T clickhouse clickhouse-client --receive_timeout=3600 \
    -q "OPTIMIZE TABLE tradernick.$t PARTITION 202607 FINAL"
done
```
Run these **sequentially** (one big merge at a time) so you don't overload CH
alongside live ingestion. Huge tables (`transfers`, `binance_raw_trades`) take
minutes and may not single-part fully in one pass — the remainder collapses via
background merges. This is belt-and-suspenders: queries that sum over RMT rows
already use `FINAL` (section 8), so correctness doesn't depend on the OPTIMIZE.

---

## 6. Rebuild the derived rollups (do NOT insert into them)

The 9 derived tables are `AggregatingMergeTree` / `SummingMergeTree` — a raw
re-insert would **double-count** (Summing sums; Aggregating merges states). They are
rebuilt by the data_processor via **`REPLACE PARTITION`** (atomic drop+rebuild,
dup-safe), reading their sources with `FINAL`/`argMaxMerge` (so correct even while
raw dupes are still merging).

**Admin panel → Data Process backfill → "Data processor rebuild (derived tables)":**
- **materializers** (select all 9): `exchange_flow_minute`, `hl_position_history_15m`,
  `hl_position_history_1h`, `hl_position_history_eod_wallet`, `hl_fills_pnl_daily`,
  `hl_fills_vol_daily`, `hl_funding_daily`, `hl_trade_history_wallet_daily`,
  `hl_position_history_oi_wallet_daily`
- **since** = start of the first affected day (e.g. `2026-07-03T00:00:00Z` — the
  daily rollups rebuild whole-day partitions); **until** = blank (now)
- **force** = off — it's **ignored** here; the rebuild always `REPLACE PARTITION`s.

Notes:
- "Exchange flow rebuild" (if present) is just `exchange_flow_minute` alone — a subset
  of the above; don't run it separately.
- "Transfers rematerialize" is unrelated to outages — it re-tags transfers' wallet
  category/entity columns after a *wallet-labels upload*. Skip unless you uploaded
  labels. (Backfilled transfers already carry their ingestion-time enrichment.)

---

## 7. Verify

Re-run the section-4 audit on every raw table and re-check the rollups:

```sql
-- daily rollups: outage days should match a baseline day (07-04 = today is partial)
SELECT countIf(day='2026-07-02') base, countIf(day='2026-07-03') outage_day
FROM tradernick.<rollup> WHERE day >= '2026-07-02' AND day <= '2026-07-04';
```
`outage_day ≈ base` = good. Also confirm no upstream feed is still dark
(section 4).

---

## 8. Dedup / FINAL cheat-sheet

- **`ReplacingMergeTree` (all raw tables):** re-insert = idempotent. Queries that
  `sum()`/`count()`/`avg()` over raw rows **need `FINAL`** (else transient dupes
  double-count). `argMax`/`argMin`/`max`, `ASOF` price joins, and `SELECT DISTINCT`
  are dedup-safe **without** `FINAL`. `OPTIMIZE … FINAL` makes non-FINAL queries
  correct too.
- **`AggregatingMergeTree` (`*_daily`, `_15m`, `_1h`, `_eod_wallet`):** read via
  `argMaxMerge`/`sumMerge`; rebuilt by REPLACE PARTITION — no dup risk, no FINAL.
- **`SummingMergeTree` (`exchange_flow_minute`):** `sum()` without `FINAL` is
  correct (summing un-merged parts = the merged total); never direct-insert.

If you add a new `sum()`/`count()` over a raw table, add `FINAL` — audit with:
```bash
grep -rnE "FROM tradernick\.<table>\b" services/data_server/src   # check each for FINAL
```

---

## 9. Prevention checklist

- [x] `clickhouse` has `restart: unless-stopped` (was `no` — the 12h-outage cause).
- [ ] Alert on ClickHouse being down / API 5xx / data-freshness lag per table.
- [ ] Alert on a provider's live loop returning `rows=0` for N consecutive ticks
      (distinguishes an upstream feed outage early).
