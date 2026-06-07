-- ============================================================
-- TTL = 270 days everywhere (idempotent, safe to re-run)
-- 2026-06-07
--
-- - Bumps every existing TTL to 270 days (or 271 for Date-typed
--   day-keyed MVs, matching the existing +1 convention).
-- - Adds 270-day TTL to ~63 event tables that previously had none
--   (AAVE/Uniswap/Aerodrome/Lido/Morpho/Spark/GMX). These tables
--   were silently growing forever.
-- - Intentionally LEAVES three tables alone:
--     hl_transfers, hl_vaults — schema comment explicitly says
--       these are sparse / historically useful, no TTL by design.
--     ingestion_event_status — 7d cap on stream heartbeats is
--       still right; bumping to 270d would mean 380M dead rows
--       you'd never query.
-- - Reference tables (wallets, smart_selector_presets,
--   ingestion_event_state) have no TTL by design — also untouched.
--
-- TTL application is asynchronous: ClickHouse's TTL merge worker
-- evicts expired rows during the next background merge. There's
-- no blocking step; queries against fresh ranges are unaffected.
-- Disk reclamation happens in the background over hours.
-- ============================================================


-- ----- Group A: existing 180-day, DateTime time column -----
ALTER TABLE tradernick.binance_ohlcv_1m            MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.binance_open_interest       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.binance_long_short_ratios   MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.binance_funding_rate        MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.transfers                   MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.exchange_flow_minute        MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_ohlcv_1m                 MODIFY TTL time + INTERVAL 270 DAY;


-- ----- Group B: existing 180-day, DateTime64 (toDateTime wrapper) -----
ALTER TABLE tradernick.binance_raw_trades  MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_trades           MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_fills            MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_funding          MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_position_history MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_trade_history    MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;


-- ----- Group C: existing 180-day, bucket-typed MVs -----
ALTER TABLE tradernick.hl_position_history_15m MODIFY TTL bucket + INTERVAL 270 DAY;
ALTER TABLE tradernick.hl_position_history_1h  MODIFY TTL bucket + INTERVAL 270 DAY;


-- ----- Group D: existing 60-day (binance_book_depth) -----
ALTER TABLE tradernick.binance_book_depth MODIFY TTL toDateTime(time) + INTERVAL 270 DAY;


-- ----- Group E: existing 181-day Date-typed (271 keeps the +1 convention) -----
ALTER TABLE tradernick.hl_position_history_eod_wallet MODIFY TTL day + INTERVAL 271 DAY;
ALTER TABLE tradernick.hl_fills_pnl_daily             MODIFY TTL day + INTERVAL 271 DAY;
ALTER TABLE tradernick.hl_fills_vol_daily             MODIFY TTL day + INTERVAL 271 DAY;
ALTER TABLE tradernick.hl_funding_daily               MODIFY TTL day + INTERVAL 271 DAY;


-- ----- Group F: existing 180-day, updated_at column -----
ALTER TABLE tradernick.ingestion_jobs MODIFY TTL updated_at + INTERVAL 270 DAY;


-- ============================================================
-- Group G: ADD 270-day TTL to event tables that had no TTL.
-- All use `time DateTime` so the clause is uniform.
-- ============================================================

-- AAVE V3 (6 events)
ALTER TABLE tradernick.aave_deposits      MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_withdrawals   MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_borrows       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_repays        MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_flashloans    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_liquidations  MODIFY TTL time + INTERVAL 270 DAY;

-- AAVE V2 (6 events)
ALTER TABLE tradernick.aave_v2_deposits     MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v2_withdrawals  MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v2_borrows      MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v2_repays       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v2_flashloans   MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v2_liquidations MODIFY TTL time + INTERVAL 270 DAY;

-- AAVE V4 (5 events)
ALTER TABLE tradernick.aave_v4_deposits     MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v4_withdrawals  MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v4_borrows      MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v4_repays       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aave_v4_liquidations MODIFY TTL time + INTERVAL 270 DAY;

-- Uniswap V3 (4)
ALTER TABLE tradernick.uniswap_swaps       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_deposits    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_withdrawals MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_collects    MODIFY TTL time + INTERVAL 270 DAY;

-- Uniswap V2 (3)
ALTER TABLE tradernick.uniswap_v2_swaps       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_v2_deposits    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_v2_withdrawals MODIFY TTL time + INTERVAL 270 DAY;

-- Uniswap V4 (4)
ALTER TABLE tradernick.uniswap_v4_swaps        MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_v4_deposits     MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_v4_withdrawals  MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.uniswap_v4_initializes  MODIFY TTL time + INTERVAL 270 DAY;

-- Aerodrome concentrated (4)
ALTER TABLE tradernick.aero_concentrated_swaps       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aero_concentrated_deposits    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aero_concentrated_withdrawals MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aero_concentrated_collects    MODIFY TTL time + INTERVAL 270 DAY;

-- Aerodrome basic (4)
ALTER TABLE tradernick.aero_basic_swaps       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aero_basic_deposits    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aero_basic_withdrawals MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.aero_basic_claims      MODIFY TTL time + INTERVAL 270 DAY;

-- Lido (5)
ALTER TABLE tradernick.lido_deposits               MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.lido_withdrawal_requests    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.lido_withdrawal_claims      MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.lido_l2_deposits            MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.lido_l2_withdrawal_requests MODIFY TTL time + INTERVAL 270 DAY;

-- Morpho (7)
ALTER TABLE tradernick.morpho_supplies              MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.morpho_withdrawals           MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.morpho_borrows               MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.morpho_repays                MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.morpho_supply_collaterals    MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.morpho_withdraw_collaterals  MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.morpho_liquidations          MODIFY TTL time + INTERVAL 270 DAY;

-- Spark (6)
ALTER TABLE tradernick.spark_deposits     MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.spark_withdrawals  MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.spark_borrows      MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.spark_repays       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.spark_flashloans   MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.spark_liquidations MODIFY TTL time + INTERVAL 270 DAY;

-- GMX (9)
ALTER TABLE tradernick.gmx_position_increases MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_position_decreases MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_liquidations       MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_swaps              MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_deposits           MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_withdrawals        MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_funding            MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_borrowing          MODIFY TTL time + INTERVAL 270 DAY;
ALTER TABLE tradernick.gmx_fees_collected     MODIFY TTL time + INTERVAL 270 DAY;
