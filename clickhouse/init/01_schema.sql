CREATE DATABASE IF NOT EXISTS tradernick;

CREATE TABLE IF NOT EXISTS tradernick.binance_ohlcv_1m
(
    token                LowCardinality(String),
    time                 DateTime           CODEC(DoubleDelta, ZSTD(3)),
    open                 Float64            CODEC(Gorilla, ZSTD(3)),
    close                Float64            CODEC(Gorilla, ZSTD(3)),
    high                 Float64            CODEC(Gorilla, ZSTD(3)),
    low                  Float64            CODEC(Gorilla, ZSTD(3)),
    volume               Float64            CODEC(Gorilla, ZSTD(3)),
    buyer_taker_volume   Float64            CODEC(Gorilla, ZSTD(3)),
    seller_taker_volume  Float64            CODEC(Gorilla, ZSTD(3)),
    trade_count          UInt32             CODEC(T64, ZSTD(3)),
    ingested_at          DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (token, time)
TTL time + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS tradernick.binance_raw_trades
(
    token        LowCardinality(String),
    time         DateTime64(3)  CODEC(DoubleDelta, ZSTD(3)),
    amount       Float64        CODEC(Gorilla, ZSTD(3)),
    price        Float64        CODEC(Gorilla, ZSTD(3)),
    buy          Bool           CODEC(ZSTD(3)),
    id           UInt64         CODEC(DoubleDelta, ZSTD(3)),
    ingested_at  DateTime       DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (token, time, id)
TTL toDateTime(time) + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS tradernick.transfers
(
    kind         LowCardinality(String),
    chain        LowCardinality(String),
    token        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    sender       String             CODEC(ZSTD(3)),
    receiver     String             CODEC(ZSTD(3)),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, sender, receiver, amount, tx_id, log_index)
TTL time + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS tradernick.binance_open_interest
(
    token                LowCardinality(String),
    time                 DateTime  CODEC(DoubleDelta, ZSTD(3)),
    open_interest        Float64   CODEC(Gorilla, ZSTD(3)),
    open_interest_value  Float64   CODEC(Gorilla, ZSTD(3)),
    ingested_at          DateTime  DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (token, time)
TTL time + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS tradernick.binance_long_short_ratios
(
    token                       LowCardinality(String),
    time                        DateTime  CODEC(DoubleDelta, ZSTD(3)),
    top_trader_count_ratio      Float32   CODEC(Gorilla, ZSTD(3)),
    top_trader_vol_ratio        Float32   CODEC(Gorilla, ZSTD(3)),
    long_short_count_ratio      Float32   CODEC(Gorilla, ZSTD(3)),
    taker_long_short_vol_ratio  Float32   CODEC(Gorilla, ZSTD(3)),
    ingested_at                 DateTime  DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (token, time)
TTL time + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS tradernick.binance_funding_rate
(
    token        LowCardinality(String),
    time         DateTime  CODEC(DoubleDelta, ZSTD(3)),
    rate         Float32   CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime  DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (token, time)
TTL time + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS tradernick.ingestion_jobs
(
    job_id       String,
    job_type     LowCardinality(String),
    args         String             CODEC(ZSTD(6)),
    status       LowCardinality(String),
    progress     Float32            DEFAULT 0 CODEC(Gorilla, ZSTD(3)),
    started_at   DateTime           CODEC(DoubleDelta, ZSTD(3)),
    finished_at  Nullable(DateTime) CODEC(DoubleDelta, ZSTD(3)),
    error        Nullable(String)   CODEC(ZSTD(3)),
    updated_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (job_id)
TTL updated_at + INTERVAL 30 DAY;

-- Wallet labels imported from Horatio's chain-analysis (or any equivalent source).
-- Addresses are stored verbatim from the source file; the bootstrap loader adds a
-- lowercase variant for every `0x…` row so EVM lookups (which use lower(sender)) match
-- regardless of source casing. BTC/TRON addresses go in case-preserved.
-- NOTE: the `transfers` table also carries four MATERIALIZED columns
-- populated from `tradernick.wallet_labels` at insert time:
--   sender_categories   Array(LowCardinality(String))
--   receiver_categories Array(LowCardinality(String))
--   sender_entity       LowCardinality(Nullable(String))
--   receiver_entity     LowCardinality(Nullable(String))
-- They are added via ALTER (see below) rather than the original CREATE
-- because they reference the wallet_labels dictionary which depends on
-- the `wallets` table that's defined later in this file. Skip indices
-- of TYPE set() let CH prune granules that don't contain the requested
-- category/entity. When `wallets` is reloaded the materialized values
-- are stale until a refresh — see /admin/refresh-categories below.
CREATE TABLE IF NOT EXISTS tradernick.wallets
(
    address     String              CODEC(ZSTD(3)),
    categories  Array(String)       CODEC(ZSTD(3)),
    entity      Nullable(String)    CODEC(ZSTD(3)),
    loaded_at   DateTime            DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (address);

-- Two extra attributes alongside `categories` / `entity`: their case-folded
-- counterparts. Computing them inside the dictionary QUERY means lowering
-- runs once per dictionary refresh (LIFETIME below), not per transfer row
-- at filter time. The aggregate queries in services/data_server use the
-- _lower attributes for filtering; /transfers/categories and
-- /transfers/entities keep returning the original-case values for display.
CREATE DICTIONARY IF NOT EXISTS tradernick.wallet_labels
(
    address          String,
    categories       Array(String),
    entity           Nullable(String) DEFAULT NULL,
    categories_lower Array(String),
    entity_lower     Nullable(String) DEFAULT NULL
)
PRIMARY KEY address
SOURCE(CLICKHOUSE(
    HOST 'localhost'
    PORT 9000
    USER 'tradernick'
    PASSWORD 'tradernick'
    DB 'tradernick'
    QUERY '
        SELECT
            address,
            categories,
            entity,
            arrayMap(c -> lower(c), categories) AS categories_lower,
            lower(entity)                        AS entity_lower
        FROM tradernick.wallets FINAL
    '
))
LAYOUT(HASHED())
LIFETIME(MIN 300 MAX 600);

-- Materialized wallet-label columns on transfers (declared here, after the
-- dictionary is defined, because the MATERIALIZED expressions reference it).
-- IF NOT EXISTS makes this idempotent so repeated container starts don't
-- error. Set() skip indices let CH prune granules during category/entity
-- filters; granularity=4 keeps the index small while still pruning well.
ALTER TABLE tradernick.transfers
  ADD COLUMN IF NOT EXISTS sender_categories Array(LowCardinality(String)) MATERIALIZED
    dictGet('tradernick.wallet_labels', 'categories_lower',
      if(chain IN ('ETH','ARB','POLYGON','BASE','BSC','OP','AVAX'), lower(sender), sender)),
  ADD COLUMN IF NOT EXISTS receiver_categories Array(LowCardinality(String)) MATERIALIZED
    dictGet('tradernick.wallet_labels', 'categories_lower',
      if(chain IN ('ETH','ARB','POLYGON','BASE','BSC','OP','AVAX'), lower(receiver), receiver)),
  ADD COLUMN IF NOT EXISTS sender_entity LowCardinality(Nullable(String)) MATERIALIZED
    dictGet('tradernick.wallet_labels', 'entity_lower',
      if(chain IN ('ETH','ARB','POLYGON','BASE','BSC','OP','AVAX'), lower(sender), sender)),
  ADD COLUMN IF NOT EXISTS receiver_entity LowCardinality(Nullable(String)) MATERIALIZED
    dictGet('tradernick.wallet_labels', 'entity_lower',
      if(chain IN ('ETH','ARB','POLYGON','BASE','BSC','OP','AVAX'), lower(receiver), receiver)),
  ADD INDEX IF NOT EXISTS idx_sender_categories sender_categories TYPE set(100) GRANULARITY 4,
  ADD INDEX IF NOT EXISTS idx_receiver_categories receiver_categories TYPE set(100) GRANULARITY 4,
  ADD INDEX IF NOT EXISTS idx_sender_entity sender_entity TYPE set(500) GRANULARITY 4,
  ADD INDEX IF NOT EXISTS idx_receiver_entity receiver_entity TYPE set(500) GRANULARITY 4;
-- AAVE v3 events — six tables, one per event type, populated by DeFiStream's
-- /evm/aave_v3/events/<type> endpoints. Column names match DeFiStream's CSV
-- output exactly so the ingestion path is a direct df → rows mapping with
-- minimal renaming.
--
-- `eth_market` distinguishes Core / Prime / EtherFi on ETH; empty string on
-- non-ETH chains. Order key puts chain + market first so multi-market ETH
-- queries can prune by market via prefix scan.

CREATE TABLE IF NOT EXISTS tradernick.aave_deposits
(
    chain         LowCardinality(String),
    eth_market    LowCardinality(String) DEFAULT '',
    time          DateTime         CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64           CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String           CODEC(ZSTD(3)),
    log_index     UInt32           CODEC(DoubleDelta, ZSTD(3)),
    user          String           CODEC(ZSTD(3)),
    token         LowCardinality(String),
    amount        Float64          CODEC(Gorilla, ZSTD(3)),
    on_behalf_of  String           CODEC(ZSTD(3)),
    referral_code UInt32           CODEC(T64, ZSTD(3)),
    value_usd     Nullable(Float64) CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime         DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, eth_market, token, time, user, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_withdrawals
(
    chain         LowCardinality(String),
    eth_market    LowCardinality(String) DEFAULT '',
    time          DateTime         CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64           CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String           CODEC(ZSTD(3)),
    log_index     UInt32           CODEC(DoubleDelta, ZSTD(3)),
    user          String           CODEC(ZSTD(3)),
    token         LowCardinality(String),
    amount        Float64          CODEC(Gorilla, ZSTD(3)),
    recipient     String           CODEC(ZSTD(3)),
    value_usd     Nullable(Float64) CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime         DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, eth_market, token, time, user, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_borrows
(
    chain              LowCardinality(String),
    eth_market         LowCardinality(String) DEFAULT '',
    time               DateTime         CODEC(DoubleDelta, ZSTD(3)),
    block_number       UInt64           CODEC(DoubleDelta, ZSTD(3)),
    tx_id              String           CODEC(ZSTD(3)),
    log_index          UInt32           CODEC(DoubleDelta, ZSTD(3)),
    user               String           CODEC(ZSTD(3)),
    token              LowCardinality(String),
    amount             Float64          CODEC(Gorilla, ZSTD(3)),
    on_behalf_of       String           CODEC(ZSTD(3)),
    interest_rate_mode UInt8            CODEC(T64, ZSTD(3)),
    borrow_rate        Float64          CODEC(Gorilla, ZSTD(3)),
    referral_code      UInt32           CODEC(T64, ZSTD(3)),
    value_usd          Nullable(Float64) CODEC(Gorilla, ZSTD(3)),
    ingested_at        DateTime         DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, eth_market, token, time, user, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_repays
(
    chain         LowCardinality(String),
    eth_market    LowCardinality(String) DEFAULT '',
    time          DateTime         CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64           CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String           CODEC(ZSTD(3)),
    log_index     UInt32           CODEC(DoubleDelta, ZSTD(3)),
    user          String           CODEC(ZSTD(3)),
    token         LowCardinality(String),
    amount        Float64          CODEC(Gorilla, ZSTD(3)),
    repayer       String           CODEC(ZSTD(3)),
    use_a_tokens  UInt8            CODEC(T64, ZSTD(3)),
    value_usd     Nullable(Float64) CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime         DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, eth_market, token, time, user, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_flashloans
(
    chain              LowCardinality(String),
    eth_market         LowCardinality(String) DEFAULT '',
    time               DateTime         CODEC(DoubleDelta, ZSTD(3)),
    block_number       UInt64           CODEC(DoubleDelta, ZSTD(3)),
    tx_id              String           CODEC(ZSTD(3)),
    log_index          UInt32           CODEC(DoubleDelta, ZSTD(3)),
    user               String           CODEC(ZSTD(3)),
    token              LowCardinality(String),
    amount             Float64          CODEC(Gorilla, ZSTD(3)),
    target             String           CODEC(ZSTD(3)),
    interest_rate_mode UInt8            CODEC(T64, ZSTD(3)),
    premium            Float64          CODEC(Gorilla, ZSTD(3)),
    referral_code      UInt32           CODEC(T64, ZSTD(3)),
    value_usd          Nullable(Float64) CODEC(Gorilla, ZSTD(3)),
    ingested_at        DateTime         DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, eth_market, token, time, user, tx_id, log_index);

-- Liquidations are structurally different: no single (user, token) — instead
-- (owner, debt_token, debt_to_cover) + (collateral_token, liquidated_collateral_amount, liquidator).
CREATE TABLE IF NOT EXISTS tradernick.aave_liquidations
(
    chain                          LowCardinality(String),
    eth_market                     LowCardinality(String) DEFAULT '',
    time                           DateTime         CODEC(DoubleDelta, ZSTD(3)),
    block_number                   UInt64           CODEC(DoubleDelta, ZSTD(3)),
    tx_id                          String           CODEC(ZSTD(3)),
    log_index                      UInt32           CODEC(DoubleDelta, ZSTD(3)),
    owner                          String           CODEC(ZSTD(3)),
    liquidator                     String           CODEC(ZSTD(3)),
    debt_token                     LowCardinality(String),
    debt_to_cover                  Float64          CODEC(Gorilla, ZSTD(3)),
    collateral_token               LowCardinality(String),
    liquidated_collateral_amount   Float64          CODEC(Gorilla, ZSTD(3)),
    receive_a_token                UInt8            CODEC(T64, ZSTD(3)),
    value_usd                      Nullable(Float64) CODEC(Gorilla, ZSTD(3)),
    ingested_at                    DateTime         DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, eth_market, debt_token, time, owner, tx_id, log_index);
-- Uniswap V3 events — four tables, one per event type. Pool is identified by
-- (chain, symbol0, symbol1, fee_tier). DeFiStream canonicalises (symbol0,
-- symbol1) to address-order on output (USDC < WETH alphabetically too) — we
-- store the rows using that canonical pair so charts can query unambiguously.
-- ORDER BY (chain, symbol0, symbol1, fee_tier, time, ...) means a single-pool
-- query reduces to a prefix scan and the time-range walk inside it.

CREATE TABLE IF NOT EXISTS tradernick.uniswap_swaps
(
    chain             LowCardinality(String),
    symbol0           LowCardinality(String),
    symbol1           LowCardinality(String),
    fee_tier          UInt32,
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address      String             CODEC(ZSTD(3)),
    swapper           String             CODEC(ZSTD(3)),
    recipient         String             CODEC(ZSTD(3)),
    token_sold        LowCardinality(String),
    token_bought      LowCardinality(String),
    amount_sold       Float64            CODEC(Gorilla, ZSTD(3)),
    amount_bought     Float64            CODEC(Gorilla, ZSTD(3)),
    sqrt_based_price  Float64            CODEC(Gorilla, ZSTD(3)),
    liquidity         Float64            CODEC(Gorilla, ZSTD(3)),
    tick              Int32              CODEC(T64, ZSTD(3)),
    value_usd         Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee_tier, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_deposits
(
    chain             LowCardinality(String),
    symbol0           LowCardinality(String),
    symbol1           LowCardinality(String),
    fee_tier          UInt32,
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address      String             CODEC(ZSTD(3)),
    sender            String             CODEC(ZSTD(3)),
    owner             String             CODEC(ZSTD(3)),
    amount0           Float64            CODEC(Gorilla, ZSTD(3)),
    amount1           Float64            CODEC(Gorilla, ZSTD(3)),
    tick_lower        Int32              CODEC(T64, ZSTD(3)),
    tick_upper        Int32              CODEC(T64, ZSTD(3)),
    price_lower       Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd         Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee_tier, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_withdrawals
(
    chain             LowCardinality(String),
    symbol0           LowCardinality(String),
    symbol1           LowCardinality(String),
    fee_tier          UInt32,
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address      String             CODEC(ZSTD(3)),
    owner             String             CODEC(ZSTD(3)),
    amount0           Float64            CODEC(Gorilla, ZSTD(3)),
    amount1           Float64            CODEC(Gorilla, ZSTD(3)),
    tick_lower        Int32              CODEC(T64, ZSTD(3)),
    tick_upper        Int32              CODEC(T64, ZSTD(3)),
    price_lower       Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd         Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee_tier, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_collects
(
    chain             LowCardinality(String),
    symbol0           LowCardinality(String),
    symbol1           LowCardinality(String),
    fee_tier          UInt32,
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address      String             CODEC(ZSTD(3)),
    owner             String             CODEC(ZSTD(3)),
    recipient         String             CODEC(ZSTD(3)),
    amount0           Float64            CODEC(Gorilla, ZSTD(3)),
    amount1           Float64            CODEC(Gorilla, ZSTD(3)),
    tick_lower        Int32              CODEC(T64, ZSTD(3)),
    tick_upper        Int32              CODEC(T64, ZSTD(3)),
    price_lower       Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd         Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee_tier, time, tx_id, log_index);
