<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    UNISWAP_CHART_KINDS,
    UNISWAP_V2_CHART_KINDS,
    UNISWAP_V4_CHART_KINDS,
    AERO_CHART_KINDS,
    AERO_BASIC_CHART_KINDS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = [
    'ohlcv',
    ...UNISWAP_CHART_KINDS,
    // Uniswap V4 hidden from the menu until pools are configured
    // (UNI_V4_POOLS / UNI_V4_LIVE_POOLS empty by default). The ChartKind
    // entries + fetch / render paths are still wired so re-enabling is
    // a one-line edit: drop the comment and re-add UNISWAP_V4_CHART_KINDS.
    // ...UNISWAP_V4_CHART_KINDS,
    ...UNISWAP_V2_CHART_KINDS,
    ...AERO_CHART_KINDS,
    ...AERO_BASIC_CHART_KINDS
  ];

  function defaultLayout(): ChartInstanceT[] {
    // One chart per Uniswap-V3 chart kind, all pinned to ETH/USDC-WETH 0.05%
    // (the deepest pool by volume). Users can swap chain + pool per chart;
    // the auto-snap effect in ChartInstance will pick a real pool from the
    // streams catalogue when this default isn't ingested.
    return UNISWAP_CHART_KINDS.map((kind) => {
      const inst = newChartInstance(kind, { token: 'USDC', chain: 'ETH' });
      inst.interval = '4h';
      return inst;
    });
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">DeX</h1>
      <div class="text-xs text-zinc-500">
        Uniswap v3 pool events (swaps / liquidity adds / removes / collects, plus net liquidity + net swap flow)
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    uniPools={data.uniPools}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:dex:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ETH"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Each chart sums {`value_usd`} per bucket for its event over the chosen pool.
    Net Liquidity = deposits − withdrawals. Net Swap Flow = $ traded token0→token1
    minus $ traded token1→token0 (positive = net buying of token1).
  </div>
</div>
