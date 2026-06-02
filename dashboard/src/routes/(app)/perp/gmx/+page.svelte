<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The general 'gmx_v2' wrapper exposes an in-chart event selector
  // (Position Open / Position Close / Net Position / Liquidations /
  // Swaps / LP Deposits / LP Withdrawals / Net LP), collapsing what
  // used to be 8 separate kinds in the picker into one. OHLCV stays
  // available as a price reference.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'gmx_v2'];
  const PERP_DEFAULT_MARKET = 'BTC/USD [WBTC-USDC]';

  function defaultLayout(): ChartInstanceT[] {
    const inst = newChartInstance('gmx_v2', { token: 'USDC', chain: 'ARB' });
    inst.gmxMarket = PERP_DEFAULT_MARKET;
    inst.interval = '4h';
    return [inst];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">GMX</h1>
      <div class="text-xs text-zinc-500">
        GMX V2 events (positions / liquidations / swaps / LP flow) on Arbitrum.
        Per-market chart selector.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    gmxMarkets={data.gmxMarkets}
    storageKey="tradernick:perp-gmx:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ARB"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. Click the title to swap it for another chart kind.
    Each chart sums per 1m / 1h / 1d bucket against the selected market —
    "Σ All markets" sums every active perp on ARB.
  </div>
</div>
