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

  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'transfer', 'exchange_flow'];

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
    const exFlow = newChartInstance('exchange_flow', { token: 'USDC', chain: 'ETH' });
    return [ohlcv, transfer, exFlow];
  }

  // Hardcoded one-click templates surfaced in the Insert menu. The old
  // CeX/Perp inflow/outflow/netflow trios (with their All/Binance/.../HL
  // variants) have been folded into the single 'exchange_flow' chart
  // kind, which carries interactive Exchange + Flow-type selectors so
  // the user can flip exchanges/directions in-place without re-inserting.
  // The 'CeX Internal Flow' template is preserved here — it's a distinct
  // pattern (CEX→CEX moves), not a flow-direction toggle.
  type TF = import('$lib/components/charts/config').TransferFilters;
  function buildTemplate(name: string, filter: TF) {
    return (defaults: { token: string; chain?: string }) => {
      const inst = newChartInstance('transfer', defaults);
      inst.filter = { ...filter };
      inst.templateName = name;
      return inst;
    };
  }

  const TEMPLATES: ChartTemplate[] = [
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
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:flows:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    templates={TEMPLATES}
    defaultChain="ETH"
    {defaultLayout}
  />

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. Click ⚙ for chart settings. ⇔ flips between 1- and 2-column
    width. Layout is saved per-browser in localStorage. The Exchange Flow chart kind has
    interactive Exchange + Flow-type selectors in its title bar (supersedes the deprecated
    CeX / Perp Inflow / Outflow / Netflow one-shot templates).
  </div>
</div>
