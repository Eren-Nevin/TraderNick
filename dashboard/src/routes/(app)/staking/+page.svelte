<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Lido is the only staking protocol wired up today; new protocols (e.g.
  // Stader / Frax) just need adding to AVAILABLE_KINDS + the picker.
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
      <h1 class="text-xl font-semibold">Staking</h1>
      <div class="text-xs text-zinc-500">
        Lido — liquid-staking flows (mainnet stake / unstake queue and L2 wstETH bridge).
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    lidoChains={data.lidoChains}
    chainGroups={data.chainGroups}
    storageKey="tradernick:staking:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ARB"
    {defaultLayout}
  />
</div>
