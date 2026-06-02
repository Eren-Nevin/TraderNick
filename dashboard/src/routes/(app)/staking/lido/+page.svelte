<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The general 'lido' wrapper kind exposes an in-chart event selector
  // (Deposits / Withdrawal Requests / Net Stake / L2 Bridge / …),
  // collapsing what used to be 9 explicit per-event kinds in the picker
  // into one. OHLCV stays as a price reference.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'lido'];

  function defaultLayout(): ChartInstanceT[] {
    const inst = newChartInstance('lido', { token: 'STETH', chain: 'ETH' });
    inst.interval = '4h';
    return [inst];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Lido</h1>
      <div class="text-xs text-zinc-500">
        Lido liquid-staking flows — mainnet stake / unstake queue and L2 wstETH bridge
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    lidoChains={data.lidoChains}
    chainGroups={data.chainGroups}
    storageKey="tradernick:staking-lido:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ARB"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Each chart sums {`value_usd`} per bucket for its event. Net Stake = deposits −
    withdrawal claims (net stETH minted). Net L2 = L2 deposits − L2 withdrawal
    requests (net wstETH bridged in to that L2).
  </div>
</div>
