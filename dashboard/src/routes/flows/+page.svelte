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

  // Hardcoded one-click templates surfaced in the Insert menu. Each template
  // creates a transfer chart with a *locked* filter — the user can change
  // chain / token / interval / MAs but not the wallet-filter. Future: persist
  // user-saved templates from the chart itself.
  function buildTemplate(name: string, filter: import('$lib/components/charts/config').TransferFilters) {
    return (defaults: { token: string; chain?: string }) => {
      const inst = newChartInstance('transfer', defaults);
      inst.filter = filter;
      inst.templateName = name;
      return inst;
    };
  }
  const TEMPLATES: ChartTemplate[] = [
    {
      id: 'tpl-non-cex-to-cex',
      label: 'Total CeX Inflow',
      build: buildTemplate('Total CeX Inflow', { sender_ex: ['CEX'], receiver_in: ['CEX'] })
    },
    {
      id: 'tpl-cex-to-non-cex',
      label: 'Total CeX Outflow',
      build: buildTemplate('Total CeX Outflow', { sender_in: ['CEX'], receiver_ex: ['CEX'] })
    },
    {
      id: 'tpl-deposit-inflows',
      label: 'CeX Deposit Inflow',
      build: buildTemplate('CeX Deposit Inflow', { receiver_in: ['Deposit'] })
    },
    {
      id: 'tpl-hot-wallet-outflows',
      label: 'CeX Hot-Wallet Outflow',
      build: buildTemplate('CeX Hot-Wallet Outflow', { sender_in: ['Hot-Wallet'], receiver_ex: ['CEX'] })
    },
    {
      id: 'tpl-cex-internal',
      label: 'CeX Internal Flow',
      build: buildTemplate('CeX Internal Flow', { sender_in: ['CEX'], receiver_in: ['CEX'] })
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
