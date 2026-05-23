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
