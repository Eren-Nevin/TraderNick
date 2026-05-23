<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'transfer'];

  function pickDefaultChainToken(): { chain: string; token: string } {
    // Prefer LINK on ETH if it's in the ingested streams; otherwise pick the first stream.
    const preferred = data.streams.find((s) => s.chain === 'ETH' && s.token === 'LINK');
    if (preferred) return { chain: preferred.chain, token: preferred.token };
    const first = data.streams[0];
    if (first) return { chain: first.chain, token: first.token };
    return { chain: 'ETH', token: 'LINK' };
  }

  function defaultLayout(): ChartInstanceT[] {
    const tk = data.tokens?.[0] ?? 'BTC';
    const { chain, token } = pickDefaultChainToken();
    const ohlcv = newChartInstance('ohlcv', { token: tk });
    const transfer = newChartInstance('transfer', { token, chain });
    return [ohlcv, transfer];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Flows</h1>
      <div class="text-xs text-zinc-500">On-chain token transfers via DeFiStream</div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    streams={data.streams}
    storageKey="tradernick:flows:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    defaultChain="ETH"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. Click ⚙ for chart settings. ⇔ flips between 1- and 2-column
    width. Layout is saved per-browser in localStorage.
  </div>
</div>
