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
