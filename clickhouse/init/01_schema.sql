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

-- ---------------------------------------------------------------------------
-- Lido liquid-staking events
-- ---------------------------------------------------------------------------
-- DeFiStream exposes 5 Lido event types: 3 mainnet (deposit / withdrawal_request
-- / withdrawal_claimed — staking + the unstake state machine) and 2 L2 bridge
-- events (l2_deposit = bridging WSTETH onto L2; l2_withdrawal_request = burning
-- bridged WSTETH to redeem on mainnet). Each is its own table so the row shape
-- stays narrow; the chain column lets a single L2 table cover all 9 L2s we
-- ingest. ORDER BY (chain, time, tx_id, log_index) prunes by chain first then
-- time-range. ReplacingMergeTree(ingested_at) dedupes if we re-fetch a chunk.

CREATE TABLE IF NOT EXISTS tradernick.lido_deposits
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    sender        String             CODEC(ZSTD(3)),
    referral      String             CODEC(ZSTD(3)),
    minted_amount Float64            CODEC(Gorilla, ZSTD(3)),
    minted_token  LowCardinality(String),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.lido_withdrawal_requests
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    request_id    UInt64             CODEC(DoubleDelta, ZSTD(3)),
    requestor     String             CODEC(ZSTD(3)),
    owner         String             CODEC(ZSTD(3)),
    burned_amount Float64            CODEC(Gorilla, ZSTD(3)),
    burned_token  LowCardinality(String),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.lido_withdrawal_claims
(
    chain           LowCardinality(String),
    time            DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number    UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id           String             CODEC(ZSTD(3)),
    log_index       UInt32             CODEC(DoubleDelta, ZSTD(3)),
    request_id      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    receiver        String             CODEC(ZSTD(3)),
    owner           String             CODEC(ZSTD(3)),
    withdraw_amount Float64            CODEC(Gorilla, ZSTD(3)),
    withdraw_token  LowCardinality(String),
    burned_token    LowCardinality(String),
    value_usd       Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at     DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.lido_l2_deposits
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    sender        String             CODEC(ZSTD(3)),
    receiver      String             CODEC(ZSTD(3)),
    minted_amount Float64            CODEC(Gorilla, ZSTD(3)),
    minted_token  LowCardinality(String),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.lido_l2_withdrawal_requests
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    sender        String             CODEC(ZSTD(3)),
    receiver      String             CODEC(ZSTD(3)),
    burned_amount Float64            CODEC(Gorilla, ZSTD(3)),
    burned_token  LowCardinality(String),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- AAVE v2 events (legacy mainnet + Polygon pools)
-- ---------------------------------------------------------------------------
-- V2 uses a single pool per chain (no eth_market_type axis like V3) and only
-- supports ETH + POLYGON in DeFiStream. Schemas are the V3 ones minus the
-- eth_market column. Borrows + flashloans both lack borrow_rate / premium
-- on V2 vs V3, but we keep the fields for forward compatibility.

CREATE TABLE IF NOT EXISTS tradernick.aave_v2_deposits
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user          String             CODEC(ZSTD(3)),
    token         LowCardinality(String),
    amount        Float64            CODEC(Gorilla, ZSTD(3)),
    on_behalf_of  String             CODEC(ZSTD(3)),
    referral_code UInt32             CODEC(T64, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v2_withdrawals
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    recipient    String             CODEC(ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v2_borrows
(
    chain              LowCardinality(String),
    time               DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number       UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id              String             CODEC(ZSTD(3)),
    log_index          UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user               String             CODEC(ZSTD(3)),
    token              LowCardinality(String),
    amount             Float64            CODEC(Gorilla, ZSTD(3)),
    on_behalf_of       String             CODEC(ZSTD(3)),
    interest_rate_mode UInt8              CODEC(T64, ZSTD(3)),
    borrow_rate        Float64            CODEC(Gorilla, ZSTD(3)),
    referral_code      UInt32             CODEC(T64, ZSTD(3)),
    value_usd          Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at        DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v2_repays
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    repayer      String             CODEC(ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v2_flashloans
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user          String             CODEC(ZSTD(3)),
    token         LowCardinality(String),
    amount        Float64            CODEC(Gorilla, ZSTD(3)),
    target        String             CODEC(ZSTD(3)),
    premium       Float64            CODEC(Gorilla, ZSTD(3)),
    referral_code UInt32             CODEC(T64, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v2_liquidations
(
    chain                        LowCardinality(String),
    time                         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number                 UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id                        String             CODEC(ZSTD(3)),
    log_index                    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    owner                        String             CODEC(ZSTD(3)),
    liquidator                   String             CODEC(ZSTD(3)),
    debt_token                   LowCardinality(String),
    debt_to_cover                Float64            CODEC(Gorilla, ZSTD(3)),
    collateral_token             LowCardinality(String),
    liquidated_collateral_amount Float64            CODEC(Gorilla, ZSTD(3)),
    receive_a_token              UInt8              CODEC(T64, ZSTD(3)),
    value_usd                    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at                  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, debt_token, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- Uniswap V2 events
-- ---------------------------------------------------------------------------
-- V2 has only 3 events (swap / deposit / withdrawal — no collect because LP
-- fees auto-compound into the pool token). No fee_tier (V2 = fixed 0.30%),
-- no tick/range fields. Pool identity is just (chain, symbol0, symbol1).
-- DeFiStream's V2 client uses camelCase for swap fields (tokenSold,
-- amountSold) — we normalise to snake_case in the transform layer.

CREATE TABLE IF NOT EXISTS tradernick.uniswap_v2_swaps
(
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pair_address  String             CODEC(ZSTD(3)),
    swapper       String             CODEC(ZSTD(3)),
    recipient     String             CODEC(ZSTD(3)),
    token_sold    LowCardinality(String),
    token_bought  LowCardinality(String),
    amount_sold   Float64            CODEC(Gorilla, ZSTD(3)),
    amount_bought Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_v2_deposits
(
    chain        LowCardinality(String),
    symbol0      LowCardinality(String),
    symbol1      LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pair_address String             CODEC(ZSTD(3)),
    sender       String             CODEC(ZSTD(3)),
    amount0      Float64            CODEC(Gorilla, ZSTD(3)),
    amount1      Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_v2_withdrawals
(
    chain        LowCardinality(String),
    symbol0      LowCardinality(String),
    symbol1      LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pair_address String             CODEC(ZSTD(3)),
    owner        String             CODEC(ZSTD(3)),
    recipient    String             CODEC(ZSTD(3)),
    amount0      Float64            CODEC(Gorilla, ZSTD(3)),
    amount1      Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- Uniswap V4 events
-- ---------------------------------------------------------------------------
-- V4 pool identity is the 6-tuple (chain, sym0, sym1, fee, tick_spacing, hooks)
-- — fee is per-pool not per-tier (V4 supports dynamic fees via hooks), and
-- the hooks address differentiates otherwise-identical pools. ORDER BY drops
-- hooks (almost always 0x0 in V1) but keeps it as a regular column so we
-- can support hook-bearing pools without a schema change.
--
-- LP events (deposit/withdraw) ONLY emit liquidity_delta — V4 doesn't put
-- amount0/amount1 on the LP log, since amounts derive from the range +
-- current price. We store liquidity_delta as the headline amount;
-- per-token amount columns aren't applicable.

CREATE TABLE IF NOT EXISTS tradernick.uniswap_v4_swaps
(
    chain            LowCardinality(String),
    symbol0          LowCardinality(String),
    symbol1          LowCardinality(String),
    fee              UInt32,
    tick_spacing     UInt32,
    hooks            LowCardinality(String),
    time             DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number     UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id            String             CODEC(ZSTD(3)),
    log_index        UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_id          String             CODEC(ZSTD(3)),
    sender           String             CODEC(ZSTD(3)),
    token_sold       LowCardinality(String),
    token_bought     LowCardinality(String),
    amount_sold      Float64            CODEC(Gorilla, ZSTD(3)),
    amount_bought    Float64            CODEC(Gorilla, ZSTD(3)),
    sqrt_based_price Float64            CODEC(Gorilla, ZSTD(3)),
    liquidity        Float64            CODEC(Gorilla, ZSTD(3)),
    tick             Int32              CODEC(T64, ZSTD(3)),
    value_usd        Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at      DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee, tick_spacing, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_v4_deposits
(
    chain            LowCardinality(String),
    symbol0          LowCardinality(String),
    symbol1          LowCardinality(String),
    fee              UInt32,
    tick_spacing     UInt32,
    hooks            LowCardinality(String),
    time             DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number     UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id            String             CODEC(ZSTD(3)),
    log_index        UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_id          String             CODEC(ZSTD(3)),
    sender           String             CODEC(ZSTD(3)),
    tick_lower       Int32              CODEC(T64, ZSTD(3)),
    tick_upper       Int32              CODEC(T64, ZSTD(3)),
    price_lower      Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper      Float64            CODEC(Gorilla, ZSTD(3)),
    liquidity_delta  Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd        Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at      DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee, tick_spacing, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.uniswap_v4_withdrawals
(
    chain            LowCardinality(String),
    symbol0          LowCardinality(String),
    symbol1          LowCardinality(String),
    fee              UInt32,
    tick_spacing     UInt32,
    hooks            LowCardinality(String),
    time             DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number     UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id            String             CODEC(ZSTD(3)),
    log_index        UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_id          String             CODEC(ZSTD(3)),
    sender           String             CODEC(ZSTD(3)),
    tick_lower       Int32              CODEC(T64, ZSTD(3)),
    tick_upper       Int32              CODEC(T64, ZSTD(3)),
    price_lower      Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper      Float64            CODEC(Gorilla, ZSTD(3)),
    liquidity_delta  Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd        Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at      DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee, tick_spacing, time, tx_id, log_index);

-- initialize fires once per pool deployment — useful for a "new pool count"
-- chart on the DeX page. The symbol0/symbol1 columns come from the V4 client
-- args (the wire emits raw currency addresses).
CREATE TABLE IF NOT EXISTS tradernick.uniswap_v4_initializes
(
    chain             LowCardinality(String),
    symbol0           LowCardinality(String),
    symbol1           LowCardinality(String),
    fee               UInt32,
    tick_spacing      UInt32,
    hooks             LowCardinality(String),
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_id           String             CODEC(ZSTD(3)),
    currency0_addr    String             CODEC(ZSTD(3)),
    currency1_addr    String             CODEC(ZSTD(3)),
    initial_sqrt_x96  Float64            CODEC(Gorilla, ZSTD(3)),
    initial_tick      Int32              CODEC(T64, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, fee, tick_spacing, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- Aerodrome concentrated-pool events (BASE only)
-- ---------------------------------------------------------------------------
-- Aero has two pool families — `basic` (Solidly-style) and `concentrated`
-- (V3-style cl pools). DeFiStream's `claims` event and the `basic` pool
-- shape are currently broken server-side (decode_worker error / stable
-- flag rejection), so V1 covers only concentrated pools: swap / deposit /
-- withdraw / collect. Pool identity is (BASE, symbol0, symbol1, tick_spacing).

CREATE TABLE IF NOT EXISTS tradernick.aero_concentrated_swaps
(
    chain            LowCardinality(String),
    symbol0          LowCardinality(String),
    symbol1          LowCardinality(String),
    tick_spacing     UInt32,
    time             DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number     UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id            String             CODEC(ZSTD(3)),
    log_index        UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address     String             CODEC(ZSTD(3)),
    swapper          String             CODEC(ZSTD(3)),
    recipient        String             CODEC(ZSTD(3)),
    token_sold       LowCardinality(String),
    token_bought     LowCardinality(String),
    amount_sold      Float64            CODEC(Gorilla, ZSTD(3)),
    amount_bought    Float64            CODEC(Gorilla, ZSTD(3)),
    sqrt_based_price Float64            CODEC(Gorilla, ZSTD(3)),
    liquidity        Float64            CODEC(Gorilla, ZSTD(3)),
    tick             Int32              CODEC(T64, ZSTD(3)),
    value_usd        Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at      DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, tick_spacing, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aero_concentrated_deposits
(
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    tick_spacing  UInt32,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    sender        String             CODEC(ZSTD(3)),
    owner         String             CODEC(ZSTD(3)),
    amount0       Float64            CODEC(Gorilla, ZSTD(3)),
    amount1       Float64            CODEC(Gorilla, ZSTD(3)),
    tick_lower    Int32              CODEC(T64, ZSTD(3)),
    tick_upper    Int32              CODEC(T64, ZSTD(3)),
    price_lower   Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper   Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, tick_spacing, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aero_concentrated_withdrawals
(
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    tick_spacing  UInt32,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    owner         String             CODEC(ZSTD(3)),
    amount0       Float64            CODEC(Gorilla, ZSTD(3)),
    amount1       Float64            CODEC(Gorilla, ZSTD(3)),
    tick_lower    Int32              CODEC(T64, ZSTD(3)),
    tick_upper    Int32              CODEC(T64, ZSTD(3)),
    price_lower   Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper   Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, tick_spacing, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aero_concentrated_collects
(
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    tick_spacing  UInt32,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    owner         String             CODEC(ZSTD(3)),
    recipient     String             CODEC(ZSTD(3)),
    amount0       Float64            CODEC(Gorilla, ZSTD(3)),
    amount1       Float64            CODEC(Gorilla, ZSTD(3)),
    tick_lower    Int32              CODEC(T64, ZSTD(3)),
    tick_upper    Int32              CODEC(T64, ZSTD(3)),
    price_lower   Float64            CODEC(Gorilla, ZSTD(3)),
    price_upper   Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, tick_spacing, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- Aerodrome basic-pool events (Solidly-style v1 AMM, BASE only)
-- ---------------------------------------------------------------------------
-- Aero basic pools come in two variants per pair via the `stable` flag:
--   stable=0 → vAMM (constant-product, like Uniswap V2)
--   stable=1 → sAMM (stableswap curve for like-priced pairs)
-- Pool identity is (BASE, sym0, sym1, stable) — no fee tier, no tick range.
-- 4 events: swap, deposit, withdraw, claims (basic pools have a gauge-style
-- claim event for veAERO holders; this is NOT the same as concentrated's
-- per-position collect).

CREATE TABLE IF NOT EXISTS tradernick.aero_basic_swaps (
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    stable        UInt8,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    swapper       String             CODEC(ZSTD(3)),
    recipient     String             CODEC(ZSTD(3)),
    token_sold    LowCardinality(String),
    token_bought  LowCardinality(String),
    amount_sold   Float64            CODEC(Gorilla, ZSTD(3)),
    amount_bought Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, stable, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aero_basic_deposits (
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    stable        UInt8,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    sender        String             CODEC(ZSTD(3)),
    amount0       Float64            CODEC(Gorilla, ZSTD(3)),
    amount1       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, stable, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aero_basic_withdrawals (
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    stable        UInt8,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    owner         String             CODEC(ZSTD(3)),
    recipient     String             CODEC(ZSTD(3)),
    amount0       Float64            CODEC(Gorilla, ZSTD(3)),
    amount1       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, stable, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aero_basic_claims (
    chain         LowCardinality(String),
    symbol0       LowCardinality(String),
    symbol1       LowCardinality(String),
    stable        UInt8,
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    pool_address  String             CODEC(ZSTD(3)),
    sender        String             CODEC(ZSTD(3)),
    recipient     String             CODEC(ZSTD(3)),
    amount0       Float64            CODEC(Gorilla, ZSTD(3)),
    amount1       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, symbol0, symbol1, stable, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- AAVE v4 events (ETH only)
-- ---------------------------------------------------------------------------
-- V4 introduces hub-and-spoke architecture: each event carries a `spoke`
-- (the spoke contract address), `reserve_id` (numeric ID of the reserve
-- inside that spoke), and `shares` (aToken shares minted/burned alongside
-- the underlying amount). No eth_market axis (replaced by spoke). No
-- flashloan event in V4. No interest_rate_mode / borrow_rate (rate model
-- unified). 5 events total: deposit / withdraw / borrow / repay / liquidation.

CREATE TABLE IF NOT EXISTS tradernick.aave_v4_deposits
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    spoke        String             CODEC(ZSTD(3)),
    reserve_id   UInt32             CODEC(T64, ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v4_withdrawals
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    spoke        String             CODEC(ZSTD(3)),
    reserve_id   UInt32             CODEC(T64, ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v4_borrows
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    spoke        String             CODEC(ZSTD(3)),
    reserve_id   UInt32             CODEC(T64, ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v4_repays
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    spoke        String             CODEC(ZSTD(3)),
    reserve_id   UInt32             CODEC(T64, ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.aave_v4_liquidations
(
    chain             LowCardinality(String),
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    spoke             String             CODEC(ZSTD(3)),
    user              String             CODEC(ZSTD(3)),
    liquidator        String             CODEC(ZSTD(3)),
    collateral_token  LowCardinality(String),
    collateral_amount Float64            CODEC(Gorilla, ZSTD(3)),
    debt_token        LowCardinality(String),
    debt_amount       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd         Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, debt_token, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- Morpho events (ETH + BASE)
-- ---------------------------------------------------------------------------
-- Morpho Blue's isolated-market architecture: each (loan_token, collateral_
-- token, oracle, irm, lltv) tuple is hashed into a market_id (32-byte) that
-- uniquely identifies an isolated lending market. Supply/withdraw/borrow/
-- repay events carry market_id + assets + shares + token. Collateral events
-- have no shares (collateral isn't share-accounted). Liquidations have a
-- rich shape with repaid + seized + bad-debt amounts. Skip flashloans —
-- DeFiStream's decode worker is broken for that event today.

CREATE TABLE IF NOT EXISTS tradernick.morpho_supplies
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id    String             CODEC(ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    on_behalf    String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    assets       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.morpho_withdrawals
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id    String             CODEC(ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    on_behalf    String             CODEC(ZSTD(3)),
    receiver     String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    assets       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.morpho_borrows
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id    String             CODEC(ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    on_behalf    String             CODEC(ZSTD(3)),
    receiver     String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    assets       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.morpho_repays
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id    String             CODEC(ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    on_behalf    String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    assets       Float64            CODEC(Gorilla, ZSTD(3)),
    shares       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.morpho_supply_collaterals
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id    String             CODEC(ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    on_behalf    String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    assets       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.morpho_withdraw_collaterals
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id    String             CODEC(ZSTD(3)),
    caller       String             CODEC(ZSTD(3)),
    on_behalf    String             CODEC(ZSTD(3)),
    receiver     String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    assets       Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.morpho_liquidations
(
    chain             LowCardinality(String),
    time              DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number      UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id             String             CODEC(ZSTD(3)),
    log_index         UInt32             CODEC(DoubleDelta, ZSTD(3)),
    market_id         String             CODEC(ZSTD(3)),
    caller            String             CODEC(ZSTD(3)),
    borrower          String             CODEC(ZSTD(3)),
    loan_token        LowCardinality(String),
    collateral_token  LowCardinality(String),
    repaid_assets     Float64            CODEC(Gorilla, ZSTD(3)),
    repaid_shares     Float64            CODEC(Gorilla, ZSTD(3)),
    seized_assets     Float64            CODEC(Gorilla, ZSTD(3)),
    bad_debt_assets   Float64            CODEC(Gorilla, ZSTD(3)),
    bad_debt_shares   Float64            CODEC(Gorilla, ZSTD(3)),
    value_usd         Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at       DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, loan_token, time, tx_id, log_index);

-- ---------------------------------------------------------------------------
-- Spark events (ETH only)
-- ---------------------------------------------------------------------------
-- Spark is a Maker/Sky-managed fork of AAVE V3 — same 6-event taxonomy and
-- per-event shape minus the eth_market axis (Spark has a single market).

CREATE TABLE IF NOT EXISTS tradernick.spark_deposits
(
    chain         LowCardinality(String),
    time          DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number  UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id         String             CODEC(ZSTD(3)),
    log_index     UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user          String             CODEC(ZSTD(3)),
    token         LowCardinality(String),
    amount        Float64            CODEC(Gorilla, ZSTD(3)),
    on_behalf_of  String             CODEC(ZSTD(3)),
    referral_code UInt32             CODEC(T64, ZSTD(3)),
    value_usd     Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at   DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.spark_withdrawals
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    recipient    String             CODEC(ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.spark_borrows
(
    chain              LowCardinality(String),
    time               DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number       UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id              String             CODEC(ZSTD(3)),
    log_index          UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user               String             CODEC(ZSTD(3)),
    token              LowCardinality(String),
    amount             Float64            CODEC(Gorilla, ZSTD(3)),
    on_behalf_of       String             CODEC(ZSTD(3)),
    interest_rate_mode UInt8              CODEC(T64, ZSTD(3)),
    borrow_rate        Float64            CODEC(Gorilla, ZSTD(3)),
    referral_code      UInt32             CODEC(T64, ZSTD(3)),
    value_usd          Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at        DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.spark_repays
(
    chain        LowCardinality(String),
    time         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id        String             CODEC(ZSTD(3)),
    log_index    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user         String             CODEC(ZSTD(3)),
    token        LowCardinality(String),
    amount       Float64            CODEC(Gorilla, ZSTD(3)),
    repayer      String             CODEC(ZSTD(3)),
    use_a_tokens UInt8              CODEC(T64, ZSTD(3)),
    value_usd    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.spark_flashloans
(
    chain              LowCardinality(String),
    time               DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number       UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id              String             CODEC(ZSTD(3)),
    log_index          UInt32             CODEC(DoubleDelta, ZSTD(3)),
    user               String             CODEC(ZSTD(3)),
    token              LowCardinality(String),
    amount             Float64            CODEC(Gorilla, ZSTD(3)),
    target             String             CODEC(ZSTD(3)),
    interest_rate_mode UInt8              CODEC(T64, ZSTD(3)),
    premium            Float64            CODEC(Gorilla, ZSTD(3)),
    referral_code      UInt32             CODEC(T64, ZSTD(3)),
    value_usd          Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at        DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, token, time, tx_id, log_index);

CREATE TABLE IF NOT EXISTS tradernick.spark_liquidations
(
    chain                        LowCardinality(String),
    time                         DateTime           CODEC(DoubleDelta, ZSTD(3)),
    block_number                 UInt64             CODEC(DoubleDelta, ZSTD(3)),
    tx_id                        String             CODEC(ZSTD(3)),
    log_index                    UInt32             CODEC(DoubleDelta, ZSTD(3)),
    owner                        String             CODEC(ZSTD(3)),
    liquidator                   String             CODEC(ZSTD(3)),
    debt_token                   LowCardinality(String),
    debt_to_cover                Float64            CODEC(Gorilla, ZSTD(3)),
    collateral_token             LowCardinality(String),
    liquidated_collateral_amount Float64            CODEC(Gorilla, ZSTD(3)),
    receive_a_token              UInt8              CODEC(T64, ZSTD(3)),
    value_usd                    Nullable(Float64)  CODEC(Gorilla, ZSTD(3)),
    ingested_at                  DateTime           DEFAULT now() CODEC(DoubleDelta, ZSTD(3))
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(time)
ORDER BY (chain, debt_token, time, tx_id, log_index);
