<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    AAVE_CHART_KINDS,
    AAVE_V2_CHART_KINDS,
    AAVE_V4_CHART_KINDS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = [
    'ohlcv',
    ...AAVE_CHART_KINDS, ...AAVE_V2_CHART_KINDS, ...AAVE_V4_CHART_KINDS
  ];
  const LENDING_DEFAULT_TOKEN = 'USDC+USDT';

  function defaultLayout(): ChartInstanceT[] {
    return AAVE_CHART_KINDS.map((kind) => {
      const inst = newChartInstance(kind, { token: LENDING_DEFAULT_TOKEN, chain: 'ETH' });
      inst.interval = '4h';
      return inst;
    });
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">AAVE</h1>
      <div class="text-xs text-zinc-500">
        AAVE V2 / V3 / V4 events (deposits / withdrawals / borrows / repays / flash loans / liquidations)
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:lending-aave:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ETH"
    defaultToken={LENDING_DEFAULT_TOKEN}
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. ⚙ opens settings. Each chart sums {`amount`} per
    1m / 1h / 1d bucket. Liquidation rows use {`debt_to_cover`} as their headline
    amount.
  </div>
</div>
