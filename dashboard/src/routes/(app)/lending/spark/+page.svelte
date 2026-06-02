<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The general 'spark' wrapper kind exposes an in-chart event selector
  // (Deposits / Withdrawals / Net Deposit / …), collapsing what used to be
  // 8 separate kinds in the picker into one. OHLCV stays available so the
  // page still has a price reference.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'spark'];
  const LENDING_DEFAULT_TOKEN = 'USDC+USDT';

  function defaultLayout(): ChartInstanceT[] {
    const inst = newChartInstance('spark', { token: LENDING_DEFAULT_TOKEN, chain: 'ETH' });
    inst.interval = '4h';
    return [inst];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Spark</h1>
      <div class="text-xs text-zinc-500">
        Spark Protocol events (deposits / withdrawals / borrows / repays / flash loans / liquidations)
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:lending-spark:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ETH"
    defaultToken={LENDING_DEFAULT_TOKEN}
    {defaultLayout}
  />
</div>
