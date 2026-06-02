<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Every lending-protocol general wrapper is selectable. Each opens with
  // an in-chart event selector (Deposits / Withdrawals / Net Deposit /
  // Borrows / Repays / Liquidations / …), so a single chart instance per
  // protocol replaces what used to be a dedicated multi-chart page.
  const AVAILABLE_KINDS: ChartKind[] = [
    'ohlcv',
    'aave_v3',
    'aave_v2',
    'aave_v4',
    'morpho',
    'spark'
  ];
  const LENDING_DEFAULT_TOKEN = 'USDC+USDT';

  function defaultLayout(): ChartInstanceT[] {
    // One general chart per protocol, AAVE V3 first (highest TVL), then
    // V2 / V4 / Morpho / Spark. Caller can drop / rearrange / size.
    return ['aave_v3', 'aave_v2', 'aave_v4', 'morpho', 'spark'].map((k) => {
      const inst = newChartInstance(k as ChartKind, {
        token: LENDING_DEFAULT_TOKEN,
        chain: 'ETH'
      });
      inst.interval = '4h';
      return inst;
    });
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Lending</h1>
      <div class="text-xs text-zinc-500">
        AAVE V2 / V3 / V4 · Morpho · Spark — per-protocol event series in one place.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:lending:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ETH"
    defaultToken={LENDING_DEFAULT_TOKEN}
    {defaultLayout}
  />
</div>
