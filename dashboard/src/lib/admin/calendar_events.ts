// Frontend mirror of services/ingestion/src/gap_detection.py
// CALENDAR_EVENTS catalogue — used by the per-provider backfill page
// to enumerate which fill boards to render. Keys MUST stay identical
// to the Python registry; the dashboard sends the key as the ?event=
// query param.

import type { Provider } from './providers';

export type CalendarEvent = {
  event_key: string;   // 'aave_v3.deposit'
  label: string;       // 'AAVE V3 Deposit'
  provider: Provider;
};

const EVENTS: CalendarEvent[] = [
  // Hyperliquid — 8 events
  { event_key: 'hyperliquid.ohlcv',            label: 'HL OHLCV 1m',            provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.funding',          label: 'HL Funding',             provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.trades',           label: 'HL Trades',              provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.fills',            label: 'HL Fills',               provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.position_history', label: 'HL Position History',    provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.trade_history',    label: 'HL Trade History',       provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.transfers',        label: 'HL Transfers',           provider: 'Hyperliquid' },
  { event_key: 'hyperliquid.vaults',           label: 'HL Vaults',              provider: 'Hyperliquid' },

  // Binance — 6 feeds
  { event_key: 'binance.ohlcv',             label: 'Binance OHLCV 1m',          provider: 'Binance' },
  { event_key: 'binance.open_interest',     label: 'Binance Open Interest',     provider: 'Binance' },
  { event_key: 'binance.long_short_ratios', label: 'Binance Long/Short Ratios', provider: 'Binance' },
  { event_key: 'binance.funding_rate',      label: 'Binance Funding Rate',      provider: 'Binance' },
  { event_key: 'binance.book_depth',        label: 'Binance Book Depth',        provider: 'Binance' },
  { event_key: 'binance.raw_trades',        label: 'Binance Raw Trades',        provider: 'Binance' },

  // Transfers — 5 sub-feeds
  { event_key: 'transfers.btc',         label: 'BTC Transfers',         provider: 'Transfers' },
  { event_key: 'transfers.evm_native',  label: 'EVM Native Transfers',  provider: 'Transfers' },
  { event_key: 'transfers.evm_erc20',   label: 'EVM ERC-20 Transfers',  provider: 'Transfers' },
  { event_key: 'transfers.tron_native', label: 'Tron Native Transfers', provider: 'Transfers' },
  { event_key: 'transfers.tron_trc20',  label: 'Tron TRC-20 Transfers', provider: 'Transfers' },

  // AAVE V3 — 6 events
  { event_key: 'aave_v3.deposit',     label: 'AAVE V3 Deposit',     provider: 'AAVE V3' },
  { event_key: 'aave_v3.withdraw',    label: 'AAVE V3 Withdraw',    provider: 'AAVE V3' },
  { event_key: 'aave_v3.borrow',      label: 'AAVE V3 Borrow',      provider: 'AAVE V3' },
  { event_key: 'aave_v3.repay',       label: 'AAVE V3 Repay',       provider: 'AAVE V3' },
  { event_key: 'aave_v3.flashloan',   label: 'AAVE V3 Flashloan',   provider: 'AAVE V3' },
  { event_key: 'aave_v3.liquidation', label: 'AAVE V3 Liquidation', provider: 'AAVE V3' },

  // AAVE V2 — 6 events
  { event_key: 'aave_v2.deposit',     label: 'AAVE V2 Deposit',     provider: 'AAVE V2' },
  { event_key: 'aave_v2.withdraw',    label: 'AAVE V2 Withdraw',    provider: 'AAVE V2' },
  { event_key: 'aave_v2.borrow',      label: 'AAVE V2 Borrow',      provider: 'AAVE V2' },
  { event_key: 'aave_v2.repay',       label: 'AAVE V2 Repay',       provider: 'AAVE V2' },
  { event_key: 'aave_v2.flashloan',   label: 'AAVE V2 Flashloan',   provider: 'AAVE V2' },
  { event_key: 'aave_v2.liquidation', label: 'AAVE V2 Liquidation', provider: 'AAVE V2' },

  // AAVE V4 — 5 events (no flashloan)
  { event_key: 'aave_v4.deposit',     label: 'AAVE V4 Deposit',     provider: 'AAVE V4' },
  { event_key: 'aave_v4.withdraw',    label: 'AAVE V4 Withdraw',    provider: 'AAVE V4' },
  { event_key: 'aave_v4.borrow',      label: 'AAVE V4 Borrow',      provider: 'AAVE V4' },
  { event_key: 'aave_v4.repay',       label: 'AAVE V4 Repay',       provider: 'AAVE V4' },
  { event_key: 'aave_v4.liquidation', label: 'AAVE V4 Liquidation', provider: 'AAVE V4' },

  // Uniswap V3 — 4 events
  { event_key: 'uniswap_v3.swap',     label: 'Uniswap V3 Swap',     provider: 'Uniswap V3' },
  { event_key: 'uniswap_v3.deposit',  label: 'Uniswap V3 Deposit',  provider: 'Uniswap V3' },
  { event_key: 'uniswap_v3.withdraw', label: 'Uniswap V3 Withdraw', provider: 'Uniswap V3' },
  { event_key: 'uniswap_v3.collect',  label: 'Uniswap V3 Collect',  provider: 'Uniswap V3' },

  // Uniswap V2 — 3 events (no collect)
  { event_key: 'uniswap_v2.swap',     label: 'Uniswap V2 Swap',     provider: 'Uniswap V2' },
  { event_key: 'uniswap_v2.deposit',  label: 'Uniswap V2 Deposit',  provider: 'Uniswap V2' },
  { event_key: 'uniswap_v2.withdraw', label: 'Uniswap V2 Withdraw', provider: 'Uniswap V2' },

  // Uniswap V4 — 3 events
  { event_key: 'uniswap_v4.swap',     label: 'Uniswap V4 Swap',     provider: 'Uniswap V4' },
  { event_key: 'uniswap_v4.deposit',  label: 'Uniswap V4 Deposit',  provider: 'Uniswap V4' },
  { event_key: 'uniswap_v4.withdraw', label: 'Uniswap V4 Withdraw', provider: 'Uniswap V4' },

  // Aerodrome (concentrated) — 4 events
  { event_key: 'aerodrome.swaps',       label: 'Aerodrome Swaps',       provider: 'Aerodrome' },
  { event_key: 'aerodrome.deposits',    label: 'Aerodrome Deposits',    provider: 'Aerodrome' },
  { event_key: 'aerodrome.withdrawals', label: 'Aerodrome Withdrawals', provider: 'Aerodrome' },
  { event_key: 'aerodrome.collects',    label: 'Aerodrome Collects',    provider: 'Aerodrome' },

  // Aerodrome basic — 4 events
  { event_key: 'aerodrome_basic.swaps',       label: 'Aerodrome Basic Swaps',       provider: 'Aerodrome Basic' },
  { event_key: 'aerodrome_basic.deposits',    label: 'Aerodrome Basic Deposits',    provider: 'Aerodrome Basic' },
  { event_key: 'aerodrome_basic.withdrawals', label: 'Aerodrome Basic Withdrawals', provider: 'Aerodrome Basic' },
  { event_key: 'aerodrome_basic.claims',      label: 'Aerodrome Basic Claims',      provider: 'Aerodrome Basic' },

  // Lido — 5 events
  { event_key: 'lido.deposit',               label: 'Lido Deposit',               provider: 'Lido' },
  { event_key: 'lido.withdrawal_request',    label: 'Lido Withdrawal Request',    provider: 'Lido' },
  { event_key: 'lido.withdrawal_claimed',    label: 'Lido Withdrawal Claimed',    provider: 'Lido' },
  { event_key: 'lido.l2_deposit',            label: 'Lido L2 Deposit',            provider: 'Lido' },
  { event_key: 'lido.l2_withdrawal_request', label: 'Lido L2 Withdrawal Request', provider: 'Lido' },

  // Morpho — 7 events
  { event_key: 'morpho.supply',              label: 'Morpho Supply',              provider: 'Morpho' },
  { event_key: 'morpho.withdraw',            label: 'Morpho Withdraw',            provider: 'Morpho' },
  { event_key: 'morpho.borrow',              label: 'Morpho Borrow',              provider: 'Morpho' },
  { event_key: 'morpho.repay',               label: 'Morpho Repay',               provider: 'Morpho' },
  { event_key: 'morpho.supply_collateral',   label: 'Morpho Supply Collateral',   provider: 'Morpho' },
  { event_key: 'morpho.withdraw_collateral', label: 'Morpho Withdraw Collateral', provider: 'Morpho' },
  { event_key: 'morpho.liquidation',         label: 'Morpho Liquidation',         provider: 'Morpho' },

  // Spark — 6 events
  { event_key: 'spark.deposit',     label: 'Spark Deposit',     provider: 'Spark' },
  { event_key: 'spark.withdraw',    label: 'Spark Withdraw',    provider: 'Spark' },
  { event_key: 'spark.borrow',      label: 'Spark Borrow',      provider: 'Spark' },
  { event_key: 'spark.repay',       label: 'Spark Repay',       provider: 'Spark' },
  { event_key: 'spark.flashloan',   label: 'Spark Flashloan',   provider: 'Spark' },
  { event_key: 'spark.liquidation', label: 'Spark Liquidation', provider: 'Spark' },

  // GMX — 9 events
  { event_key: 'gmx.position_increase', label: 'GMX Position Increase', provider: 'GMX' },
  { event_key: 'gmx.position_decrease', label: 'GMX Position Decrease', provider: 'GMX' },
  { event_key: 'gmx.liquidation',       label: 'GMX Liquidation',       provider: 'GMX' },
  { event_key: 'gmx.swap',              label: 'GMX Swap',              provider: 'GMX' },
  { event_key: 'gmx.deposit',           label: 'GMX Deposit',           provider: 'GMX' },
  { event_key: 'gmx.withdraw',          label: 'GMX Withdraw',          provider: 'GMX' },
  { event_key: 'gmx.funding',           label: 'GMX Funding',           provider: 'GMX' },
  { event_key: 'gmx.borrowing',         label: 'GMX Borrowing',         provider: 'GMX' },
  { event_key: 'gmx.fees_collected',    label: 'GMX Fees Collected',    provider: 'GMX' },
];

export function calendarEventsForProvider(p: Provider): CalendarEvent[] {
  return EVENTS.filter((e) => e.provider === p);
}
