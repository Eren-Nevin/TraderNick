<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Every DeX-protocol general wrapper. Each opens an in-chart event
  // selector (Swaps / Deposits / Withdrawals / Net Liquidity / …) plus
  // the pool/chain selectors specific to that protocol.
  const AVAILABLE_KINDS: ChartKind[] = [
    'ohlcv',
    'uniswap_v3',
    'uniswap_v2',
    'uniswap_v4',
    'aero_cl',
    'aero_basic'
  ];

  function defaultLayout(): ChartInstanceT[] {
    // One general chart per Uniswap version + both Aerodrome variants.
    // USDC/WETH ETH pool default for Uniswap; Aerodrome's BASE-only
    // defaults are baked into newChartInstance.
    return ['uniswap_v3', 'uniswap_v2', 'uniswap_v4', 'aero_cl', 'aero_basic'].map((k) => {
      const inst = newChartInstance(k as ChartKind, { token: 'USDC', chain: 'ETH' });
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
        Uniswap V2 / V3 / V4 · Aerodrome CL · Aerodrome Basic — per-protocol pool events.
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
</div>
