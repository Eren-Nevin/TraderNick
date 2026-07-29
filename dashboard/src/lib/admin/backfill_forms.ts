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

// 'token-batches' renders a multiselect of ingestion token BATCHES (fetched
// at runtime from /api/admin/config/token_batches). The selected batches are
// expanded to their union of tokens and sent as the existing `tokens` arg, so
// the backend stays token-based. Lets an operator backfill e.g. just "Batch 2"
// instead of all-or-none. See BackfillForm.svelte.
export type FieldKind = 'multiselect' | 'pair-multiselect' | 'tokens-csv' | 'pools-csv' | 'token-batches';

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
  hideForce?: boolean;     // hide the Force toggle (no-op for this backfill type)
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
    description: 'Defaults to all events × all token batches — same set the per-event live streams poll. Deselect a batch to narrow (e.g. backfill only a newly-added batch).',
    fields: [
      { name: 'events', label: 'Events', kind: 'multiselect',
        options: HL_EVENTS, defaultSelected: HL_EVENTS, required: true },
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
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
  // Binance forms — token selection is by batch (same batches as HL). All
  // batches are pre-selected so the default backfill mirrors live; deselect a
  // batch to narrow. Empty submission falls back to the full live roster on
  // the backend (app.py:_create_backfill).
  {
    type: 'binance_ohlcv',
    label: 'Binance OHLCV',
    description: 'Defaults to the live INGEST_TOKENS roster. Leave empty / pre-selected to mirror live.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_raw_trades',
    label: 'Binance raw trades',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_spot_ohlcv',
    label: 'Binance spot OHLCV',
    description: 'Binance SPOT market (separate dataset from perp). Defaults to ' +
      'the live INGEST_TOKENS roster; spot-less tokens just return no rows. ' +
      'Spot upstream currently lags — recent days may be empty.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_spot_raw_trades',
    label: 'Binance spot raw trades',
    description: 'Binance SPOT market (separate dataset from perp). Defaults to ' +
      'the live INGEST_TOKENS roster. Spot upstream currently lags.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_open_interest',
    label: 'Binance open interest',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_long_short_ratios',
    label: 'Binance long/short ratios',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_funding_rate',
    label: 'Binance funding rate',
    description: 'Defaults to the live INGEST_TOKENS roster.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },
  {
    type: 'binance_book_depth',
    label: 'Binance book depth',
    description: 'BPS-level depth snapshots (12 rows / snapshot, ~every 30s). ' +
      'Quota cost is 100/day/token — much higher than the other Binance feeds. ' +
      'Live stream ships disabled by default; enable in Live streams when ready.',
    fields: [
      { name: 'tokens', label: 'Token batches', kind: 'token-batches' }
    ]
  },

  // Data process — derived-MV maintenance backfills. Single-shot rebuilds
  // FROM the current state of upstream tables; no time window.
  // NOTE: the standalone "Exchange flow rebuild" was removed — it only rebuilt
  // exchange_flow_minute, which is already one of the materializers in the
  // "Data processor rebuild" form below (select it there to rebuild just it).
  {
    type: 'transfers_rematerialize',
    label: 'Transfers rematerialize (post-wallet upload)',
    description: 'Refresh dictionary + MATERIALIZE COLUMN ×4 (sender/receiver ' +
      'categories + entity) + DROP/ADD/MATERIALIZE INDEX ×4 on transfers. ' +
      'Fire this after uploading a new wallet_labels parquet so historical ' +
      'rows pick up the new mapping. Also rebuilds exchange_flow_minute by ' +
      'default (uncheck to skip). This is a monolithic operation — no ' +
      'chunk progress, may take hours on the 971M-row transfers table.',
    fields: []
  },
  // Data processor — rebuild any combination of the 7 derived tables for
  // a specific window via REPLACE PARTITION from source FINAL. The single
  // job_type covers exchange_flow_minute + all 6 HL aggregates; pick which
  // ones to rebuild via the multiselect.
  {
    type: 'data_processor',
    label: 'Data processor rebuild (derived tables)',
    description: 'Rebuild selected derived-table partitions from the source ' +
      'FINAL via REPLACE PARTITION. Idempotent — partitions outside the ' +
      'window are untouched. Window narrows to the partitions overlapping ' +
      'since/until. (REPLACE PARTITION is always a full rebuild.) ' +
      '⚑ After a hl_fills backfill: rebuild the FILLS-sourced dailies here for ' +
      'the window — hl_fills_pnl_daily, hl_fills_vol_daily (the hl_position_history_* ' +
      'rollups are position-history-sourced and unaffected by a fills backfill). ' +
      'The fills-derived position rollups (hl_positions_now = current, ' +
      'hl_positions_bucketed = historical 5m) are NOT in this list: both are ' +
      'auto-maintained by live materialized views on hl_fills, so a fills backfill ' +
      '(and live fills) flow into them idempotently (argMax/max) with NO action ' +
      'needed. Manual full rebuild is recovery-only (MV downtime); the same ' +
      'argMaxState INSERT re-seeds them (hl_positions_bucketed adds ' +
      'toStartOfInterval(time,INTERVAL 300 SECOND) AS bucket and GROUP BY token, ' +
      'wallet, bucket). E.g. hl_positions_now: ' +
      'INSERT INTO tradernick.hl_positions_now SELECT token, wallet, ' +
      "argMaxState(start_position+if(side='B',size,-size),(time,(start_position+if(side='B',size,-size)))), " +
      "argMaxState(start_position+if(side='B',size,-size),(time,-(start_position+if(side='B',size,-size)))), " +
      "argMaxState(toInt8(if(side='B',1,-1)),(time,tid)), argMaxState(price,(time,tid)), maxState(time) " +
      'FROM tradernick.hl_fills GROUP BY token, wallet;',
    hideForce: true,
    fields: [
      { name: 'materializers', label: 'Materializers', kind: 'multiselect',
        options: [
          'exchange_flow_minute',
          'hl_position_history_15m',
          'hl_position_history_1h',
          'hl_position_history_eod_wallet',
          'hl_fills_pnl_daily',
          'hl_fills_vol_daily',
          'hl_funding_daily',
          'hl_trade_history_wallet_daily',
          // sources hl_position_history_1h → keep last so it rebuilds after it.
          'hl_position_history_oi_wallet_daily',
        ],
        defaultSelected: [
          'hl_position_history_15m',
          'hl_position_history_1h',
          'hl_position_history_eod_wallet',
          'hl_fills_pnl_daily',
          'hl_fills_vol_daily',
          'hl_funding_daily',
          'hl_trade_history_wallet_daily',
          'hl_position_history_oi_wallet_daily',
        ] }
    ]
  }
];
