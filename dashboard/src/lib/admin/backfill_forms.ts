// Schema describing each backfill endpoint's accepted fields. Drives the
// generic <BackfillForm> component in /admin: for each type the form
// renders one input per field, validates client-side, and POSTs the
// resulting body to /api/admin/jobs/backfill/<type>.
//
// Fields all share `since` (required) + `until` (defaults to now) + `force`
// (optional checkbox). Type-specific fields are listed under `fields`.
//
// Option lists for chains / events / etc. mirror the constants in
// services/ingestion/src/config.py — kept in lockstep manually for now.

export type FieldKind = 'multiselect' | 'pair-multiselect' | 'tokens-csv' | 'pools-csv';

export type FieldSpec = {
  name: string;            // form field name + query payload key
  label: string;           // UI label
  kind: FieldKind;
  options?: string[];      // for multiselect / pair-multiselect (left side of pair)
  optionsRight?: string[]; // for pair-multiselect (right side of pair)
  // Default selection seeded when the form opens. For multiselect each entry
  // is one of `options`; for pair-multiselect each entry is "left/right".
  // Mirrors the live job's configured tokens/chains/pairs so the common case
  // ("backfill what we're polling live") is one click. Kept in lockstep with
  // .env manually.
  defaultSelected?: string[];
  placeholder?: string;
  required?: boolean;
};

export type BackfillFormSpec = {
  type: string;            // /jobs/backfill/<type>
  label: string;           // UI label
  description?: string;
  fields: FieldSpec[];
};

const EVM_CHAINS = ['ETH', 'ARB', 'BASE', 'BSC', 'POLYGON'];
const ETH_MARKETS = ['Core', 'Prime', 'EtherFi'];
const AAVE_V3_EVENTS = ['deposit', 'withdraw', 'borrow', 'repay', 'flashloan', 'liquidation'];
const AAVE_V2_EVENTS = ['deposit', 'withdraw', 'borrow', 'repay', 'flashloan', 'liquidation'];
const AAVE_V4_EVENTS = ['deposit', 'withdraw', 'borrow', 'repay', 'liquidation'];
const UNISWAP_V3_EVENTS = ['swap', 'deposit', 'withdraw', 'collect'];
const UNISWAP_V2_EVENTS = ['swap', 'deposit', 'withdraw'];
const UNISWAP_V4_EVENTS = ['swap', 'deposit', 'withdraw'];
const LIDO_EVENTS = ['deposit', 'withdrawal_request', 'withdrawal_claimed', 'l2_deposit', 'l2_withdrawal_request'];
const MORPHO_EVENTS = ['supply', 'withdraw', 'borrow', 'repay', 'supply_collateral', 'withdraw_collateral', 'liquidation'];
const SPARK_EVENTS = ['deposit', 'withdraw', 'borrow', 'repay', 'flashloan', 'liquidation'];
const GMX_EVENTS = ['position_increase', 'position_decrease', 'liquidation', 'swap', 'deposit', 'withdraw', 'funding', 'borrowing', 'fees_collected'];
const AERO_EVENTS = ['swap', 'deposit', 'withdraw', 'collect'];
// Backend canonical event names (services/ingestion/src/clickhouse.py:1378+):
// the dict key is `claim` (singular) even though the CH table is `aero_basic_claims`.
const AERO_BASIC_EVENTS = ['swap', 'deposit', 'withdraw', 'claim'];
const HL_EVENTS = ['ohlcv', 'trades', 'fills', 'funding', 'position_history', 'trade_history', 'transfers', 'vaults'];

const INGEST_TOKENS = [
  'BTC', 'ETH', 'SOL', 'ARB', 'LINK', 'BNB', 'POL', 'LTC', 'TRX',
  'AAVE', 'AERO', 'CAKE', 'COW', 'ENA', 'ETHFI', 'FET', 'FIL', 'HYPE',
  'MORPHO', 'PENDLE', 'RENDER', 'SUSHI', 'UNI', 'WLD', 'VIRTUAL', 'PAXG', 'ZEC',
  'TON', 'NEAR', 'DOGE', 'TAO'
];

// Universe of ERC-20 token symbols pickable in the backfill form. Wider than
// the live-job set (which is just the 5 stables+majors actively polled) so
// ad-hoc backfills can target other tokens we already track for prices/HL/
// Binance. Some symbols are chain-specific (AERO=Base, CAKE=BSC); DeFiStream
// will return a clear error for invalid (chain, token) combos.
const ERC20_TOKENS = [
  'USDT', 'USDC', 'DAI', 'LINK', 'WETH', 'WBTC',
  'ARB', 'AAVE', 'UNI', 'MORPHO', 'PENDLE', 'ETHFI', 'ENA',
  'AERO', 'COW', 'SUSHI', 'RENDER', 'FET', 'FIL', 'WLD', 'VIRTUAL',
  'PAXG', 'CAKE',
];

// Live chain×token pairs (mirrors EVM_ERC20_TRANSFERS in .env). Used as the
// default selection for the erc20 backfill form so "backfill what's live"
// is one click.
const LIVE_ERC20_PAIRS = [
  'ETH/USDT', 'ETH/USDC', 'ETH/DAI', 'ETH/LINK', 'ETH/WETH',
  'ARB/USDT', 'ARB/USDC', 'ARB/DAI', 'ARB/LINK', 'ARB/WETH',
  'POLYGON/USDT', 'POLYGON/USDC', 'POLYGON/LINK', 'POLYGON/WETH',
  'BASE/USDT', 'BASE/USDC', 'BASE/LINK', 'BASE/WETH',
  'BSC/USDT', 'BSC/USDC', 'BSC/LINK', 'BSC/WETH',
];
const LIVE_EVM_NATIVE_CHAINS = ['ETH', 'ARB', 'BASE', 'BSC', 'POLYGON'];
const LIVE_TRON_TRC20_TOKENS = ['USDT'];

export const BACKFILL_FORMS: BackfillFormSpec[] = [
  {
    type: 'hyperliquid_events',
    label: 'Hyperliquid events',
    description: 'Defaults to all events × the live INGEST_TOKENS roster — same set the per-event live streams poll. Deselect to narrow.',
    fields: [
      { name: 'events', label: 'Events', kind: 'multiselect',
        options: HL_EVENTS, defaultSelected: HL_EVENTS, required: true },
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },
  {
    type: 'evm_erc20_transfers',
    label: 'EVM ERC-20 transfers',
    description: 'Tokens come from the live job’s roster (EVM_ERC20_TRANSFERS env) — ' +
      'one multi-token call per chain with .ignore_non_existing() filtering.',
    fields: [
      {
        name: 'chains',
        label: 'Chains',
        kind: 'multiselect',
        options: EVM_CHAINS,
        defaultSelected: EVM_CHAINS,
        required: true
      }
    ]
  },
  {
    type: 'evm_native_transfers',
    label: 'EVM native transfers',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect',
        options: EVM_CHAINS, defaultSelected: LIVE_EVM_NATIVE_CHAINS, required: true }
    ]
  },
  {
    type: 'btc_transfers',
    label: 'BTC transfers',
    description: 'No type-specific fields — fills the entire window for the BTC mainnet.',
    fields: []
  },
  {
    type: 'tron_native_transfers',
    label: 'Tron native transfers',
    description: 'No type-specific fields.',
    fields: []
  },
  {
    type: 'tron_trc20_transfers',
    label: 'Tron TRC-20 transfers',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: ['USDT', 'USDC'], defaultSelected: LIVE_TRON_TRC20_TOKENS, required: true }
    ]
  },
  {
    type: 'aave_v3_events',
    label: 'AAVE V3 events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: EVM_CHAINS, required: true },
      { name: 'eth_markets', label: 'ETH markets', kind: 'multiselect', options: ETH_MARKETS },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AAVE_V3_EVENTS, required: true }
    ]
  },
  {
    type: 'aave_v2_events',
    label: 'AAVE V2 events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: EVM_CHAINS, required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AAVE_V2_EVENTS, required: true }
    ]
  },
  {
    type: 'aave_v4_events',
    label: 'AAVE V4 events',
    description: 'AAVE V4 is mainnet-only — DeFiStream supports network=ETH only.',
    fields: [
      // Restricted to ETH: AAVE V4 is not deployed elsewhere yet.
      { name: 'chains', label: 'Chains', kind: 'multiselect',
        options: ['ETH'], defaultSelected: ['ETH'], required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AAVE_V4_EVENTS, required: true }
    ]
  },
  {
    type: 'uniswap_v3_events',
    label: 'Uniswap V3 events',
    description: 'Leave Pools empty to use the same pool set the live worker polls ' +
      '(UNI_V3_LIVE_POOLS, falls back to UNI_V3_POOLS). Override with a CSV of ' +
      '"CHAIN:SYMBOL0/SYMBOL1/FEE_TIER" tuples for ad-hoc backfills.',
    fields: [
      { name: 'pools', label: 'Pools (CSV — optional, defaults to live)', kind: 'pools-csv',
        placeholder: 'ETH:USDC/WETH/500,ETH:USDC/WETH/3000' },
      { name: 'events', label: 'Events', kind: 'multiselect', options: UNISWAP_V3_EVENTS, required: true }
    ]
  },
  {
    type: 'uniswap_v2_events',
    label: 'Uniswap V2 events',
    description: 'Leave Pools empty to use the live worker\'s set ' +
      '(UNI_V2_LIVE_POOLS, falls back to UNI_V2_POOLS).',
    fields: [
      { name: 'pools', label: 'Pools (CSV — optional, defaults to live)', kind: 'pools-csv',
        placeholder: 'ETH:USDC/WETH' },
      { name: 'events', label: 'Events', kind: 'multiselect', options: UNISWAP_V2_EVENTS, required: true }
    ]
  },
  {
    type: 'uniswap_v4_events',
    label: 'Uniswap V4 events',
    description: 'Leave Pools empty to use the live worker\'s set ' +
      '(UNI_V4_LIVE_POOLS, falls back to UNI_V4_POOLS).',
    fields: [
      { name: 'pools', label: 'Pools (CSV — optional, defaults to live)', kind: 'pools-csv',
        placeholder: 'ETH:USDC/WETH/500/10' },
      { name: 'events', label: 'Events', kind: 'multiselect', options: UNISWAP_V4_EVENTS, required: true }
    ]
  },
  {
    type: 'lido_events',
    label: 'Lido events',
    description: 'Defaults match the live worker: ETH + LIDO_L2_CHAINS (ARB, BASE). ' +
      'Mainnet events fire only on ETH; L2 bridge events only on L2s — driver ' +
      'auto-filters invalid combos and fast-forwards unsupported (chain, event) pairs.',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect',
        options: ['ETH', 'ARB', 'BASE'],
        defaultSelected: ['ETH', 'ARB', 'BASE'], required: true },
      { name: 'events', label: 'Events', kind: 'multiselect',
        options: LIDO_EVENTS, defaultSelected: LIDO_EVENTS, required: true }
    ]
  },
  {
    type: 'morpho_events',
    label: 'Morpho events',
    description: 'Defaults to the live worker\'s chain set (MORPHO_CHAINS = ETH, BASE). ' +
      'Tighten / widen the selection here for ad-hoc backfills.',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect',
        options: EVM_CHAINS, defaultSelected: ['ETH', 'BASE'], required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: MORPHO_EVENTS, required: true }
    ]
  },
  {
    type: 'spark_events',
    label: 'Spark events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: ['ETH'], required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: SPARK_EVENTS, required: true }
    ]
  },
  {
    type: 'gmx_v2_events',
    label: 'GMX V2 events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: ['ARB'], required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: GMX_EVENTS, required: true }
    ]
  },
  {
    type: 'aero_concentrated_events',
    label: 'Aerodrome (concentrated)',
    description: 'Leave Pools empty to use the live worker\'s set ' +
      '(AERO_LIVE_POOLS, falls back to AERO_POOLS).',
    fields: [
      { name: 'pools', label: 'Pools (CSV — optional, defaults to live)', kind: 'pools-csv',
        placeholder: 'BASE:USDC/WETH/100' },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AERO_EVENTS, required: true }
    ]
  },
  {
    type: 'aero_basic_events',
    label: 'Aerodrome (basic)',
    description: 'Leave Pools empty to use the live worker\'s set ' +
      '(AERO_BASIC_LIVE_POOLS, falls back to AERO_BASIC_POOLS).',
    fields: [
      { name: 'pools', label: 'Pools (CSV — optional, defaults to live)', kind: 'pools-csv',
        placeholder: 'BASE:USDC/WETH/v' },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AERO_BASIC_EVENTS, required: true }
    ]
  },
  // Binance forms — all 5 use the same INGEST_TOKENS roster as the live
  // workers (config.INGEST_TOKENS). Pre-select all so the default backfill
  // mirrors live; deselect for narrower runs. Tokens field is no longer
  // required — empty submission also falls back to the live set on the
  // backend (app.py:_create_backfill).
  {
    type: 'binance_ohlcv',
    label: 'Binance OHLCV',
    description: 'Defaults to the live INGEST_TOKENS roster. Leave empty / pre-selected to mirror live.',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },
  {
    type: 'binance_raw_trades',
    label: 'Binance raw trades',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },
  {
    type: 'binance_open_interest',
    label: 'Binance open interest',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },
  {
    type: 'binance_long_short_ratios',
    label: 'Binance long/short ratios',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },
  {
    type: 'binance_funding_rate',
    label: 'Binance funding rate',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },
  {
    type: 'binance_book_depth',
    label: 'Binance book depth',
    description: 'BPS-level depth snapshots (12 rows / snapshot, ~every 30s). ' +
      'Quota cost is 100/day/token — much higher than the other Binance feeds. ' +
      'Live stream ships disabled by default; enable in Live streams when ready.',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect',
        options: INGEST_TOKENS, defaultSelected: INGEST_TOKENS }
    ]
  },

  // Data process — derived-MV maintenance backfills. Single-shot rebuilds
  // FROM the current state of upstream tables; no time window.
  {
    type: 'exchange_flow_minute',
    label: 'Exchange flow rebuild',
    description: 'Rebuild tradernick.exchange_flow_minute from transfers FINAL ' +
      'using the staging-swap-recover pattern. The chart never sees an empty ' +
      'rollup during the rebuild. Idempotent — re-running is safe.',
    fields: []
  },
  {
    type: 'transfers_rematerialize',
    label: 'Transfers rematerialize (post-wallet upload)',
    description: 'Refresh dictionary + MATERIALIZE COLUMN ×4 (sender/receiver ' +
      'categories + entity) + DROP/ADD/MATERIALIZE INDEX ×4 on transfers. ' +
      'Fire this after uploading a new wallet_labels parquet so historical ' +
      'rows pick up the new mapping. Also rebuilds exchange_flow_minute by ' +
      'default (uncheck to skip).',
    fields: []
  }
];
