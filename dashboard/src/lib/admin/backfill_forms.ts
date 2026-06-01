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
const AERO_BASIC_EVENTS = ['swap', 'deposit', 'withdraw', 'claims'];
const HL_EVENTS = ['ohlcv', 'trades', 'fills', 'funding', 'position_history', 'trade_history', 'transfers', 'vaults'];

const INGEST_TOKENS = [
  'BTC', 'ETH', 'SOL', 'ARB', 'LINK', 'BNB', 'POL', 'LTC', 'TRX',
  'AAVE', 'AERO', 'CAKE', 'COW', 'ENA', 'ETHFI', 'FET', 'FIL', 'HYPE',
  'MORPHO', 'PENDLE', 'RENDER', 'SUSHI', 'UNI', 'WLD', 'VIRTUAL', 'PAXG'
];

const ERC20_TOKENS = ['USDT', 'USDC', 'DAI', 'LINK', 'WETH'];

export const BACKFILL_FORMS: BackfillFormSpec[] = [
  {
    type: 'hyperliquid_events',
    label: 'Hyperliquid events',
    description: 'Each event runs as its own subprocess (live + gap-fill); backfill kicks an extra one-shot scan for the given window.',
    fields: [
      { name: 'events', label: 'Events', kind: 'multiselect', options: HL_EVENTS, required: true },
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: INGEST_TOKENS }
    ]
  },
  {
    type: 'evm_erc20_transfers',
    label: 'EVM ERC-20 transfers',
    fields: [
      {
        name: 'pairs',
        label: 'Chain × token pairs',
        kind: 'pair-multiselect',
        options: EVM_CHAINS,
        optionsRight: ERC20_TOKENS,
        required: true
      }
    ]
  },
  {
    type: 'evm_native_transfers',
    label: 'EVM native transfers',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: EVM_CHAINS, required: true }
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
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: ['USDT', 'USDC'], required: true }
    ]
  },
  {
    type: 'aave_events',
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
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: EVM_CHAINS, required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AAVE_V4_EVENTS, required: true }
    ]
  },
  {
    type: 'uniswap_events',
    label: 'Uniswap V3 events',
    description: 'Pools are CSV of "CHAIN:SYMBOL0/SYMBOL1/FEE_TIER" tuples.',
    fields: [
      { name: 'pools', label: 'Pools (CSV)', kind: 'pools-csv', placeholder: 'ETH:USDC/WETH/500,ETH:USDC/WETH/3000', required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: UNISWAP_V3_EVENTS, required: true }
    ]
  },
  {
    type: 'uniswap_v2_events',
    label: 'Uniswap V2 events',
    fields: [
      { name: 'pools', label: 'Pools (CSV)', kind: 'pools-csv', placeholder: 'ETH:USDC/WETH', required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: UNISWAP_V2_EVENTS, required: true }
    ]
  },
  {
    type: 'uniswap_v4_events',
    label: 'Uniswap V4 events',
    fields: [
      { name: 'pools', label: 'Pools (CSV)', kind: 'pools-csv', placeholder: 'ETH:USDC/WETH/500', required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: UNISWAP_V4_EVENTS, required: true }
    ]
  },
  {
    type: 'lido_events',
    label: 'Lido events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: EVM_CHAINS, required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: LIDO_EVENTS, required: true }
    ]
  },
  {
    type: 'morpho_events',
    label: 'Morpho events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: EVM_CHAINS, required: true },
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
    type: 'gmx_events',
    label: 'GMX V2 events',
    fields: [
      { name: 'chains', label: 'Chains', kind: 'multiselect', options: ['ARB'], required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: GMX_EVENTS, required: true }
    ]
  },
  {
    type: 'aero_events',
    label: 'Aerodrome (concentrated)',
    fields: [
      { name: 'pools', label: 'Pools (CSV)', kind: 'pools-csv', placeholder: 'BASE:USDC/WETH/500', required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AERO_EVENTS, required: true }
    ]
  },
  {
    type: 'aero_basic_events',
    label: 'Aerodrome (basic)',
    fields: [
      { name: 'pools', label: 'Pools (CSV)', kind: 'pools-csv', placeholder: 'BASE:USDC/WETH/v', required: true },
      { name: 'events', label: 'Events', kind: 'multiselect', options: AERO_BASIC_EVENTS, required: true }
    ]
  },
  {
    type: 'binance_ohlcv',
    label: 'Binance OHLCV',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: INGEST_TOKENS, required: true }
    ]
  },
  {
    type: 'binance_raw_trades',
    label: 'Binance raw trades',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: INGEST_TOKENS, required: true }
    ]
  },
  {
    type: 'binance_open_interest',
    label: 'Binance open interest',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: INGEST_TOKENS, required: true }
    ]
  },
  {
    type: 'binance_long_short_ratios',
    label: 'Binance long/short ratios',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: INGEST_TOKENS, required: true }
    ]
  },
  {
    type: 'binance_funding_rate',
    label: 'Binance funding rate',
    fields: [
      { name: 'tokens', label: 'Tokens', kind: 'multiselect', options: INGEST_TOKENS, required: true }
    ]
  }
];
