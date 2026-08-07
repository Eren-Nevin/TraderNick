-- Extend data-table TTL from 270d → 2 years (730d; Date-typed daily rollups 731d).
--
-- Why: the prior 270-day TTL was actively pruning history at the edge — every
-- table's oldest row sat exactly at today-270, so backfills older than 270d were
-- deleted on the next merge. This raises retention to 2 years.
--
-- Idempotent + safe to re-run. materialize_ttl_after_modify=0 keeps this a cheap
-- metadata change (no rewrite of the 500GB+ transfers table); extending a TTL
-- never deletes rows — future merges apply the new expression.
--
-- NOT touched (deliberately short-lived): smart_wallet*_cache (1-3d),
-- notification_triggers (1d), ingestion_event_status (7d).

SET materialize_ttl_after_modify = 0;

ALTER TABLE tradernick.aave_borrows MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_flashloans MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_liquidations MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_repays MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v2_borrows MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v2_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v2_flashloans MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v2_liquidations MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v2_repays MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v2_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v4_borrows MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v4_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v4_liquidations MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v4_repays MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_v4_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aave_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_basic_claims MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_basic_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_basic_swaps MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_basic_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_concentrated_collects MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_concentrated_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_concentrated_swaps MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.aero_concentrated_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_book_depth MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_funding_rate MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_long_short_ratios MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_ohlcv_1m MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_open_interest MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_raw_spot_trades MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_raw_trades MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.binance_spot_ohlcv_1m MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.exchange_flow_minute MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_borrowing MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_fees_collected MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_funding MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_liquidations MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_position_decreases MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_position_increases MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_swaps MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.gmx_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_fills MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_fills_pnl_daily MODIFY TTL day + INTERVAL 731 DAY;
ALTER TABLE tradernick.hl_fills_vol_daily MODIFY TTL day + INTERVAL 731 DAY;
ALTER TABLE tradernick.hl_funding_daily MODIFY TTL day + INTERVAL 731 DAY;
ALTER TABLE tradernick.hl_funding MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_ohlcv_1m MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_position_history_15m MODIFY TTL bucket + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_position_history_1h MODIFY TTL bucket + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_position_history_eod_wallet MODIFY TTL day + INTERVAL 731 DAY;
ALTER TABLE tradernick.hl_position_history MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_position_history_oi_wallet_daily MODIFY TTL day + INTERVAL 731 DAY;
ALTER TABLE tradernick.hl_positions_bucketed MODIFY TTL bucket + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_trade_history MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.hl_trade_history_wallet_daily MODIFY TTL day + INTERVAL 731 DAY;
ALTER TABLE tradernick.hl_trades MODIFY TTL toDateTime(time) + INTERVAL 730 DAY;
ALTER TABLE tradernick.ingestion_jobs MODIFY TTL toDateTime(updated_at) + INTERVAL 730 DAY;
ALTER TABLE tradernick.lido_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.lido_l2_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.lido_l2_withdrawal_requests MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.lido_withdrawal_claims MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.lido_withdrawal_requests MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_borrows MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_liquidations MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_repays MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_supplies MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_supply_collaterals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.morpho_withdraw_collaterals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.spark_borrows MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.spark_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.spark_flashloans MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.spark_liquidations MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.spark_repays MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.spark_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.transfers MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_collects MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_swaps MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v2_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v2_swaps MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v2_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v4_deposits MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v4_initializes MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v4_swaps MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_v4_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
ALTER TABLE tradernick.uniswap_withdrawals MODIFY TTL time + INTERVAL 730 DAY;
