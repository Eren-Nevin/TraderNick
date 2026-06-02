<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
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
    ...AERO_CHART_KINDS,
    ...AERO_BASIC_CHART_KINDS
  ];

  function defaultLayout(): ChartInstanceT[] {
    return AERO_CHART_KINDS.map((kind) => {
      const inst = newChartInstance(kind, { token: 'USDC', chain: 'BASE' });
      inst.interval = '4h';
      return inst;
    });
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Aerodrome</h1>
      <div class="text-xs text-zinc-500">
        Aerodrome (CL + basic) pool events on Base — swaps, liquidity flow, collects.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    uniPools={data.uniPools}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:dex-aerodrome:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="BASE"
    {defaultLayout}
  />
</div>
