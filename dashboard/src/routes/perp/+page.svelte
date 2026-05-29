<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    GMX_CHART_KINDS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', ...GMX_CHART_KINDS];

  // Default to the canonical BTC/USD perp — concrete enough for the user
  // to see real data on first load. Every chart's market selector lists
  // every other resolved market sorted by activity.
  const PERP_DEFAULT_MARKET = 'BTC/USD [WBTC-USDC]';

  function defaultLayout(): ChartInstanceT[] {
    // One chart per GMX_CHART_KINDS entry (8 charts: 3 position-flow,
    // 1 liquidation, 1 swap, 3 LP-flow). 4 fit per row at 2×1; the user
    // can swap any of them via the click-the-title menu.
    return GMX_CHART_KINDS.map((kind) => {
      const inst = newChartInstance(kind, { token: 'USDC', chain: 'ARB' });
      inst.gmxMarket = PERP_DEFAULT_MARKET;
      inst.interval = '4h';
      return inst;
    });
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Perp</h1>
      <div class="text-xs text-zinc-500">
        GMX V2 events (positions / liquidations / swaps / LP flow) on Arbitrum.
        Per-market chart selector.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    gmxMarkets={data.gmxMarkets}
    storageKey="tradernick:perp:layout:v1"
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
