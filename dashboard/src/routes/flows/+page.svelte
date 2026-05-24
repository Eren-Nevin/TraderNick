<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind,
    type ChartTemplate
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

  // Hardcoded one-click templates surfaced in the Insert menu. Future: persist
  // user-saved templates from the chart itself.
  const TEMPLATES: ChartTemplate[] = [
    {
      id: 'tpl-non-cex-to-cex',
      label: 'Transfer: Non-CEX → CEX (inflows)',
      build: (defaults) => {
        const inst = newChartInstance('transfer', defaults);
        inst.filter = { sender_ex: ['CEX'], receiver_in: ['CEX'] };
        return inst;
      }
    },
    {
      id: 'tpl-cex-to-non-cex',
      label: 'Transfer: CEX → Non-CEX (outflows)',
      build: (defaults) => {
        const inst = newChartInstance('transfer', defaults);
        inst.filter = { sender_in: ['CEX'], receiver_ex: ['CEX'] };
        return inst;
      }
    },
    {
      id: 'tpl-deposit-inflows',
      label: 'Transfer: deposit-inflows',
      build: (defaults) => {
        const inst = newChartInstance('transfer', defaults);
        inst.filter = { receiver_in: ['Deposit'] };
        return inst;
      }
    },
    {
      id: 'tpl-hot-wallet-outflows',
      label: 'Transfer: hot-wallet-outflows',
      build: (defaults) => {
        const inst = newChartInstance('transfer', defaults);
        inst.filter = { sender_in: ['Hot-Wallet'], receiver_ex: ['CEX'] };
        return inst;
      }
    },
    {
      id: 'tpl-involving-bridge',
      label: 'Transfer: Involving Bridge',
      build: (defaults) => {
        const inst = newChartInstance('transfer', defaults);
        inst.filter = { involving_in: ['Bridge'] };
        return inst;
      }
    },
    {
      id: 'tpl-excluding-cex',
      label: 'Transfer: Excluding CEX (peer-to-peer)',
      build: (defaults) => {
        const inst = newChartInstance('transfer', defaults);
        inst.filter = { involving_ex: ['CEX'] };
        return inst;
      }
    }
  ];
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
    templates={TEMPLATES}
    defaultChain="ETH"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. Click ⚙ for chart settings. ⇔ flips between 1- and 2-column
    width. Layout is saved per-browser in localStorage.
  </div>
</div>
