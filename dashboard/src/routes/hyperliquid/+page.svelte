<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    HL_CHART_KINDS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', ...HL_CHART_KINDS];

  // Default-layout kinds, in the order they appear on the page. Eight
  // charts chosen so they tile cleanly at 2×1 on a wide layout (4 across,
  // 2 rows).
  const DEFAULT_KINDS: ChartKind[] = [
    'ohlcv',              // BTC candles (exchange=hl is set below)
    'hl_trade_volume',    // BTC trade volume
    'hl_taker_volume',    // BTC taker flow (in place of net-OI until position_history is re-enabled)
    'fr',                 // BTC funding rate (exchange=hl set below)
    'hl_pnl',             // BTC realized PnL (no wallet filter)
    'hl_top_traders',     // BTC top traders leaderboard
    'hl_transfers',       // bridge in/out (no token)
    'hl_vault_net'        // vault net flow (no token)
  ];

  function defaultLayout(): ChartInstanceT[] {
    return DEFAULT_KINDS.map((kind) => {
      const inst = newChartInstance(kind, { token: 'BTC', chain: 'HL' });
      inst.interval = '4h';
      // OHLCV + funding-rate charts on /hyperliquid read the HL source.
      if (kind === 'ohlcv' || kind === 'fr') inst.exchange = 'hl';
      return inst;
    });
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Hyperliquid</h1>
      <div class="text-xs text-zinc-500">
        On-chain perp DEX. Every event carries a wallet identity — use the
        wallet/category filter on any chart to drill into specific traders
        or smart-money categories.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    storageKey="tradernick:hyperliquid:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="HL"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. Click the title to swap chart kind.
    Wallet selector takes an EVM address (lowercased server-side) or a
    smart-money label. "Σ All wallets" sums every trader for the selected
    token.
  </div>
</div>
