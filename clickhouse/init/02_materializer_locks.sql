-- Cross-process per-(materializer, partition) lock for the data_processor
-- worker. Multiple processes can race to rebuild the same target partition
-- (live recent-tier vs live sweep-tier, live vs backfill, two concurrent
-- backfill jobs) — without a coordination point they would duplicate work
-- and waste CH CPU. A row in this table records who is currently rebuilding
-- which partition.
--
-- Acquire is `INSERT IF NOT EXISTS` semantics via a read-after-write check
-- against `tradernick.materializer_locks FINAL`; release is a DELETE.
-- The TTL via `expires_at` is a backstop for processes that die without
-- releasing — after the deadline another caller can take the lock.

CREATE TABLE IF NOT EXISTS tradernick.materializer_locks
(
    materializer  LowCardinality(String),
    partition_id  String,
    owner_pid     UInt32,
    owner_host    LowCardinality(String),
    acquired_at   DateTime  DEFAULT now()  CODEC(DoubleDelta, ZSTD(3)),
    expires_at    DateTime                  CODEC(DoubleDelta, ZSTD(3))
)
ENGINE = ReplacingMergeTree(acquired_at)
ORDER BY (materializer, partition_id);
