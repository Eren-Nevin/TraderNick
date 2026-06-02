<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The three general wrapper kinds ('uniswap_v3', 'uniswap_v2', 'uniswap_v4')
  // each expose an in-chart event selector (Swaps / Deposits / Withdrawals /
  // Collects / Net Liquidity / …), collapsing what used to be 15 explicit
  // per-event kinds in the picker into three. OHLCV stays as a price
  // reference.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'uniswap_v3', 'uniswap_v2', 'uniswap_v4'];

  function defaultLayout(): ChartInstanceT[] {
    const inst = newChartInstance('uniswap_v3', { token: 'USDC', chain: 'ETH' });
    inst.interval = '4h';
    return [inst];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Uniswap</h1>
      <div class="text-xs text-zinc-500">
        Uniswap V2 / V3 / V4 pool events (swaps / liquidity adds / removes / collects, plus net liquidity + net swap flow)
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    uniPools={data.uniPools}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:dex-uniswap:layout:v1"
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
