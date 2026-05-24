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
  // chain / token / interval / MAs but not the wallet-filter.
  type TF = import('$lib/components/charts/config').TransferFilters;
  function buildTemplate(name: string, filter: TF) {
    return (defaults: { token: string; chain?: string }) => {
      const inst = newChartInstance('transfer', defaults);
      // Clone so two instances built from the same template don't share state.
      inst.filter = { ...filter };
      inst.templateName = name;
      return inst;
    };
  }

  // Parameterised templates — user picks a CeX (All / Binance / Coinbase / OKX /
  // Bybit) when inserting. The selection is folded into the locked filter:
  //   - Inflow:  receiver_in = [<CeX>-Deposit]  (or 'Deposit' for All)
  //   - Outflow: sender_in   = ['Hot-Wallet']
  //              receiver_ex = ['CEX']
  //              + sender_entity_in = [<CeX>]   (skipped for All)
  const CEXES = ['All', 'Binance', 'Coinbase', 'OKX', 'Bybit'] as const;
  type Cex = (typeof CEXES)[number];

  function cexInflowBuild(cex: Cex) {
    const filter: TF =
      cex === 'All'
        ? { receiver_in: ['Deposit'] }
        : { receiver_in: [`${cex}-Deposit`] };
    const name = cex === 'All' ? 'CeX Inflow' : `${cex} Inflow`;
    return buildTemplate(name, filter);
  }
  function cexOutflowBuild(cex: Cex) {
    const filter: TF = { sender_in: ['Hot-Wallet'], receiver_ex: ['CEX'] };
    if (cex !== 'All') filter.sender_entity_in = [cex];
    const name = cex === 'All' ? 'CeX Outflow' : `${cex} Outflow`;
    return buildTemplate(name, filter);
  }

  // Netflow = Inflow − Outflow per bucket. Two parallel fetches with the
  // same filter sets as the two simple templates above; the chart subtracts
  // them client-side. Positive = net deposits into CeX (accumulation),
  // negative = net withdrawals (distribution).
  function inflowFilter(cex: Cex): TF {
    return cex === 'All'
      ? { receiver_in: ['Deposit'] }
      : { receiver_in: [`${cex}-Deposit`] };
  }
  function outflowFilter(cex: Cex): TF {
    const f: TF = { sender_in: ['Hot-Wallet'], receiver_ex: ['CEX'] };
    if (cex !== 'All') f.sender_entity_in = [cex];
    return f;
  }
  function cexNetflowBuild(cex: Cex) {
    const positive = inflowFilter(cex);
    const negative = outflowFilter(cex);
    const name = cex === 'All' ? 'CeX Netflow' : `${cex} Netflow`;
    return (defaults: { token: string; chain?: string }) => {
      const inst = newChartInstance('transfer', defaults);
      inst.netFilter = { positive: { ...positive }, negative: { ...negative } };
      inst.templateName = name;
      return inst;
    };
  }

  const TEMPLATES: ChartTemplate[] = [
    {
      id: 'tpl-cex-inflow',
      label: 'CeX Inflow',
      variants: CEXES.map((c) => ({
        id: `tpl-cex-inflow-${c.toLowerCase()}`,
        label: c,
        build: cexInflowBuild(c)
      }))
    },
    {
      id: 'tpl-cex-outflow',
      label: 'CeX Outflow',
      variants: CEXES.map((c) => ({
        id: `tpl-cex-outflow-${c.toLowerCase()}`,
        label: c,
        build: cexOutflowBuild(c)
      }))
    },
    {
      id: 'tpl-cex-netflow',
      label: 'CeX Netflow',
      variants: CEXES.map((c) => ({
        id: `tpl-cex-netflow-${c.toLowerCase()}`,
        label: c,
        build: cexNetflowBuild(c)
      }))
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
    width. Layout is saved per-browser in localStorage.
  </div>
</div>
