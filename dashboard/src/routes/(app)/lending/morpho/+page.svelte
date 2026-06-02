<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The general 'morpho' wrapper kind exposes an in-chart event selector
  // (Supplies / Withdrawals / Net Supply / …), collapsing what used to be
  // 10 separate kinds in the picker into one. OHLCV stays available so the
  // page still has a price reference.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'morpho'];
  const LENDING_DEFAULT_TOKEN = 'USDC+USDT';

  function defaultLayout(): ChartInstanceT[] {
    const inst = newChartInstance('morpho', { token: LENDING_DEFAULT_TOKEN, chain: 'ETH' });
    inst.interval = '4h';
    return [inst];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Morpho</h1>
      <div class="text-xs text-zinc-500">
        Morpho events (supplies / withdrawals / borrows / repays / collateral flows / liquidations)
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:lending-morpho:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ETH"
    defaultToken={LENDING_DEFAULT_TOKEN}
    {defaultLayout}
  />
</div>
