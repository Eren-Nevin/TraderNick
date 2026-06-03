-- Rebuild tradernick.exchange_flow_minute from tradernick.transfers FINAL.
--
-- Background: `tradernick.transfers` is a ReplacingMergeTree (dedup'd on
-- merge), but the MV target `tradernick.exchange_flow_minute` is a
-- SummingMergeTree. When backfills re-insert the same source events (with
-- force=true that purges only the source, or after a crash that retried
-- a chunk), the source dedups but the MV target accumulates — the sums
-- grow by N× the actual data. Verified for 2026-06-02 (Hyperliquid):
-- re-deriving from transfers FINAL yields $980M OUT; the stored MV says
-- $4.97B OUT (~5×).
--
-- This script TRUNCATEs the MV target and re-inserts a clean copy. It
-- uses `transfers FINAL` so the source is also dedup'd at read time
-- (slower than non-FINAL but acceptable for a one-shot rebuild).
--
-- IMPORTANT: live transfers ingestion is continuous, so to avoid a small
-- gap or double-count in the rebuild window, we pin a cutoff (now() - 5
-- minutes) and only rebuild for time < cutoff. The live MV will
-- continue to populate time >= cutoff from the live stream as usual.
-- Events with time < cutoff that arrive AFTER the rebuild (rare; the
-- live stream is real-time) will compound by their own row's values —
-- normally <0.01% drift, indistinguishable from noise.

TRUNCATE TABLE tradernick.exchange_flow_minute;

INSERT INTO tradernick.exchange_flow_minute
SELECT
    classified.1 AS direction,
    classified.2 AS exchange,
    chain,
    token,
    toStartOfMinute(time) AS time,
    sum(amount) AS sum_amount,
    sum(if(token IN ('USDC', 'USDT', 'DAI', 'USDE'), amount, coalesce(value_usd, 0.))) AS sum_value_usd,
    count() AS count
FROM tradernick.transfers FINAL
ARRAY JOIN arrayConcat(
    if(has(receiver_categories, 'binance-deposit')     AND (NOT has(sender_categories, 'cex')),  [('in', 'binance')],     CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(receiver_categories, 'coinbase-deposit')    AND (NOT has(sender_categories, 'cex')),  [('in', 'coinbase')],    CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(receiver_categories, 'okx-deposit')         AND (NOT has(sender_categories, 'cex')),  [('in', 'okx')],         CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(receiver_categories, 'bybit-deposit')       AND (NOT has(sender_categories, 'cex')),  [('in', 'bybit')],       CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(receiver_categories, 'hyperliquid-deposit') AND (NOT has(sender_categories, 'perp')), [('in', 'hyperliquid')], CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(sender_categories, 'hot-wallet') AND (coalesce(sender_entity, '') = 'binance')     AND (NOT has(receiver_categories, 'cex')),                  [('out', 'binance')],     CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(sender_categories, 'hot-wallet') AND (coalesce(sender_entity, '') = 'coinbase')    AND (NOT has(receiver_categories, 'cex')),                  [('out', 'coinbase')],    CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(sender_categories, 'hot-wallet') AND (coalesce(sender_entity, '') = 'okx')         AND (NOT has(receiver_categories, 'cex')),                  [('out', 'okx')],         CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(sender_categories, 'hot-wallet') AND (coalesce(sender_entity, '') = 'bybit')       AND (NOT has(receiver_categories, 'cex')),                  [('out', 'bybit')],       CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))')),
    if(has(sender_categories, 'hot-wallet') AND (coalesce(sender_entity, '') = 'hyperliquid') AND (coalesce(receiver_entity, '') != 'hyperliquid'),       [('out', 'hyperliquid')], CAST([], 'Array(Tuple(LowCardinality(String), LowCardinality(String)))'))
) AS classified
WHERE time < (now() - INTERVAL 5 MINUTE)
GROUP BY direction, exchange, chain, token, time;
