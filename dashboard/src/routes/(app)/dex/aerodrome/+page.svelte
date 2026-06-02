<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The two general wrapper kinds ('aero_cl', 'aero_basic') each expose an
  // in-chart event selector (Swaps / Deposits / Withdrawals / Collects |
  // Claims / Net Liquidity), collapsing what used to be 10 explicit
  // per-event kinds in the picker into two. OHLCV stays as a price
  // reference.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'aero_cl', 'aero_basic'];

  function defaultLayout(): ChartInstanceT[] {
    const inst = newChartInstance('aero_cl', { token: 'USDC', chain: 'BASE' });
    inst.interval = '4h';
    return [inst];
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
