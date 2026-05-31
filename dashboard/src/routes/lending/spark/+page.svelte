<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    SPARK_CHART_KINDS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', ...SPARK_CHART_KINDS];
  const LENDING_DEFAULT_TOKEN = 'USDC+USDT';

  function defaultLayout(): ChartInstanceT[] {
    return SPARK_CHART_KINDS.map((kind) => {
      const inst = newChartInstance(kind, { token: LENDING_DEFAULT_TOKEN, chain: 'ETH' });
      inst.interval = '4h';
      return inst;
    });
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
