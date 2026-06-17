<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { Interval } from '$lib/api';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'pc', 'oi', 'volume', 'fr', 'book_depth', 'bs', 'sz', 'tt', 'ls'];

  function defaultLayout(): ChartInstanceT[] {
    const tk = data.tokens?.[0] ?? 'BTC';
    const mk = (k: ChartKind) => {
      const inst = newChartInstance(k, { token: tk });
      inst.interval = (data.interval as Interval) ?? '1h';
      return inst;
    };
    return [mk('ohlcv'), mk('oi'), mk('fr'), mk('bs'), mk('sz'), mk('tt'), mk('ls')];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Trades</h1>
      <div class="text-xs text-zinc-500">Binance OHLCV + raw trades via DeFiStream</div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    storageKey="tradernick:trades:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. ⚙ opens chart-specific settings. ⇔ flips between 1 and 2
    columns. Layout is saved per-browser in localStorage.
  </div>
</div>
