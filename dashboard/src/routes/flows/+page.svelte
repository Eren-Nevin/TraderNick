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
    // Receiver must carry the deposit umbrella AND the CEX tag (excludes
    // things like the Hyperliquid bridge that's tagged 'Deposit' but not
    // 'CEX'). Sender must NOT be a CEX wallet so we don't double-count
    // CEX-internal moves — those land under "CeX Internal Flow".
    const filter: TF =
      cex === 'All'
        ? { receiver_all_in: ['Deposit', 'CEX'], sender_ex: ['CEX'] }
        : { receiver_all_in: [`${cex}-Deposit`, 'CEX'], sender_ex: ['CEX'] };
    const name = cex === 'All' ? 'CeX Inflow' : `${cex} Inflow`;
    return buildTemplate(name, filter);
  }
  function cexOutflowBuild(cex: Cex) {
    // Mirror of the Inflow tightening (symmetric): sender must carry BOTH
    // 'Hot-Wallet' AND 'CEX' (categories AND), receiver must NOT be CEX.
    // Excludes non-CEX hot wallets (perp bridges, MEV bots, …) that share
    // the 'Hot-Wallet' tag but aren't exchanges.
    const filter: TF = { sender_all_in: ['Hot-Wallet', 'CEX'], receiver_ex: ['CEX'] };
    if (cex !== 'All') filter.sender_entity_in = [cex];
    const name = cex === 'All' ? 'CeX Outflow' : `${cex} Outflow`;
    return buildTemplate(name, filter);
  }

  // Netflow = Inflow − Outflow per bucket. Two parallel fetches with the
  // same filter sets as the two simple templates above; the chart subtracts
  // them client-side. Positive = net deposits into CeX (accumulation),
  // negative = net withdrawals (distribution).
  function inflowFilter(cex: Cex): TF {
    // Same receiver intersection + sender-excludes-CEX as cexInflowBuild
    // above, applied to the netflow's positive side.
    return cex === 'All'
      ? { receiver_all_in: ['Deposit', 'CEX'], sender_ex: ['CEX'] }
      : { receiver_all_in: [`${cex}-Deposit`, 'CEX'], sender_ex: ['CEX'] };
  }
  function outflowFilter(cex: Cex): TF {
    // Same sender_all_in tightening as cexOutflowBuild above, applied to
    // the netflow's negative side.
    const f: TF = { sender_all_in: ['Hot-Wallet', 'CEX'], receiver_ex: ['CEX'] };
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

  // Parameterised perp-DEX templates. Mirrors the CeX trio but keyed off the
  // 'Perp' umbrella category + per-perp entity label. Hyperliquid is the
  // only perp currently labelled in the wallets parquet; the 'All' variant
  // matches every Perp-tagged wallet so adding GMX / Aevo / etc later is a
  // pure data change (no code edit needed).
  const PERPS = ['All', 'Hyperliquid'] as const;
  type Perp = (typeof PERPS)[number];

  function perpInflowFilter(perp: Perp): TF {
    // Mirror of the CeX Inflow tightening with 'CEX' swapped for 'Perp':
    // receiver must carry the deposit umbrella AND the Perp tag. Sender
    // must NOT be a perp wallet so we don't double-count perp-internal
    // moves between a bridge and its hot wallet.
    return perp === 'All'
      ? { receiver_all_in: ['Deposit', 'Perp'], sender_ex: ['Perp'] }
      : { receiver_all_in: [`${perp}-Deposit`, 'Perp'], sender_ex: ['Perp'] };
  }
  function perpOutflowFilter(perp: Perp): TF {
    // Mirror of the CeX Outflow tightening with 'CEX' swapped for 'Perp':
    // sender must carry BOTH 'Hot-Wallet' AND 'Perp' so non-perp hot wallets
    // are excluded. Receiver must NOT be perp.
    const f: TF = { sender_all_in: ['Hot-Wallet', 'Perp'], receiver_ex: ['Perp'] };
    if (perp !== 'All') f.sender_entity_in = [perp];
    return f;
  }
  function perpInflowBuild(perp: Perp) {
    const name = perp === 'All' ? 'Perp Inflow' : `${perp} Inflow`;
    return buildTemplate(name, perpInflowFilter(perp));
  }
  function perpOutflowBuild(perp: Perp) {
    const name = perp === 'All' ? 'Perp Outflow' : `${perp} Outflow`;
    return buildTemplate(name, perpOutflowFilter(perp));
  }
  function perpNetflowBuild(perp: Perp) {
    const positive = perpInflowFilter(perp);
    const negative = perpOutflowFilter(perp);
    const name = perp === 'All' ? 'Perp Netflow' : `${perp} Netflow`;
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
    },
    {
      id: 'tpl-perp-inflow',
      label: 'Perp Inflow',
      variants: PERPS.map((p) => ({
        id: `tpl-perp-inflow-${p.toLowerCase()}`,
        label: p,
        build: perpInflowBuild(p)
      }))
    },
    {
      id: 'tpl-perp-outflow',
      label: 'Perp Outflow',
      variants: PERPS.map((p) => ({
        id: `tpl-perp-outflow-${p.toLowerCase()}`,
        label: p,
        build: perpOutflowBuild(p)
      }))
    },
    {
      id: 'tpl-perp-netflow',
      label: 'Perp Netflow',
      variants: PERPS.map((p) => ({
        id: `tpl-perp-netflow-${p.toLowerCase()}`,
        label: p,
        build: perpNetflowBuild(p)
      }))
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
