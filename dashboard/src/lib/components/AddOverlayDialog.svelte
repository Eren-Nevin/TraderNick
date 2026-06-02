<script lang="ts">
  // Compound-chart "Add overlay" modal.
  //
  // Two-step flow:
  //   Step 1 — pick a chart KIND from a hierarchical list (Category → optional
  //            Provider → Kind), with a typeahead filter at the top.
  //            Reuses the same Category / Provider / Group helpers the
  //            DynamicChartLayout insert dialog uses.
  //   Step 2 — configure the picked kind: which series (when the kind emits
  //            more than one inherent line), which token / chain / pool /
  //            exchange the overlay should target, and whether to plot raw
  //            or as a moving average (with type + window).
  //
  // The dialog INHERITS the host chart's `interval` — no time-bucket selector
  // is shown. Pressing "Add overlay" calls `onSubmit(overlay)` with a fully-
  // populated ChartOverlay.

  import {
    OVERLAY_KIND_SERIES,
    OVERLAY_COLORS,
    overlayableKinds,
    overlayKindCategory,
    chartKindProvider,
    chartKindGroup,
    CHART_KIND_LABELS,
    chartKindShortLabel,
    nextOverlayColor,
    sanitizeOverlay,
    type ChartOverlay,
    type ChartKind,
    type OverlaySeriesDef,
    type MAType
  } from './charts/config';

  let {
    open = false,
    initial = null as ChartOverlay | null,
    usedColors = [] as string[],
    onSubmit,
    onClose
  }: {
    open: boolean;
    /** When set, the dialog opens directly to step 2 with these values
     *  pre-populated. Used for editing an existing overlay (kind is
     *  locked — swap = remove + add). */
    initial: ChartOverlay | null;
    usedColors: string[];
    onSubmit: (overlay: ChartOverlay) => void;
    onClose: () => void;
  } = $props();

  // ── Step / picker state ─────────────────────────────────────────────
  let step = $state<1 | 2>(initial ? 2 : 1);
  let pickedKind = $state<ChartKind | null>(initial ? initial.kind : null);

  $effect(() => {
    // Whenever the dialog opens, restart at the right step.
    if (open) {
      step = initial ? 2 : 1;
      pickedKind = initial ? initial.kind : null;
      if (initial) loadInitial(initial);
      else clearForm();
      filterText = '';
      highlightedIdx = 0;
    }
  });

  // ── Step 1: kind picker ─────────────────────────────────────────────
  let filterText = $state('');
  let highlightedIdx = $state(0);
  let listEl = $state<HTMLDivElement | null>(null);
  let expandedCategories = $state<Set<string>>(new Set(['DeX', 'Exchange']));
  let expandedProviders = $state<Set<string>>(new Set());

  type FlatItem = { kind: ChartKind; label: string; category: string; provider: string | null; searchKey: string };
  let flatItems = $derived.by((): FlatItem[] => {
    const kinds = overlayableKinds();
    const items: FlatItem[] = [];
    for (const k of kinds) {
      const cat = overlayKindCategory(k);
      if (!cat) continue;
      const prov = chartKindProvider(k);
      const provider = prov ? prov.provider : (chartKindGroup(k) ?? null);
      const label = chartKindShortLabel(k);
      items.push({
        kind: k,
        label,
        category: cat,
        provider,
        searchKey: `${cat} ${provider ?? ''} ${label} ${CHART_KIND_LABELS[k] ?? k}`.toLowerCase()
      });
    }
    return items;
  });

  type DialogRow =
    | { type: 'header'; level: 1 | 2; key: string; label: string; expanded: boolean; count: number; scope: 'category' | 'provider' }
    | { type: 'leaf'; kind: ChartKind; label: string; indent: 0 | 1 | 2; group: string | null; showGroup: boolean };

  let dialogRows = $derived.by((): DialogRow[] => {
    const q = filterText.trim().toLowerCase();
    if (q) {
      return flatItems
        .filter((it) => it.searchKey.includes(q))
        .map((it) => ({
          type: 'leaf' as const,
          kind: it.kind,
          label: CHART_KIND_LABELS[it.kind] ?? it.label,
          indent: 0,
          group: it.provider,
          showGroup: it.provider !== null
        }));
    }
    const byCat = new Map<string, FlatItem[]>();
    const order: string[] = [];
    for (const it of flatItems) {
      if (!byCat.has(it.category)) {
        byCat.set(it.category, []);
        order.push(it.category);
      }
      byCat.get(it.category)!.push(it);
    }
    const CATEGORY_ORDER = ['Exchange', 'Flows', 'Lending', 'DeX', 'Perp', 'Staking'];
    order.sort((a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b));
    const rows: DialogRow[] = [];
    for (const cat of order) {
      const items = byCat.get(cat) ?? [];
      const catExpanded = expandedCategories.has(cat);
      rows.push({
        type: 'header', level: 1, key: cat, label: cat,
        expanded: catExpanded, count: items.length, scope: 'category'
      });
      if (!catExpanded) continue;
      const provCount = new Map<string, number>();
      for (const it of items) {
        if (it.provider) provCount.set(it.provider, (provCount.get(it.provider) ?? 0) + 1);
      }
      const emittedProv = new Set<string>();
      for (const it of items) {
        const prov = it.provider;
        if (prov && (provCount.get(prov) ?? 0) >= 2) {
          if (emittedProv.has(prov)) continue;
          emittedProv.add(prov);
          const pkey = `${cat}::${prov}`;
          const pExpanded = expandedProviders.has(pkey);
          const pItems = items.filter((x) => x.provider === prov);
          rows.push({
            type: 'header', level: 2, key: pkey, label: prov,
            expanded: pExpanded, count: pItems.length, scope: 'provider'
          });
          if (pExpanded) {
            for (const pit of pItems) {
              rows.push({
                type: 'leaf', kind: pit.kind, label: pit.label,
                indent: 2, group: prov, showGroup: false
              });
            }
          }
        } else {
          rows.push({
            type: 'leaf', kind: it.kind,
            label: CHART_KIND_LABELS[it.kind] ?? it.label,
            indent: 1, group: it.provider, showGroup: false
          });
        }
      }
    }
    return rows;
  });

  $effect(() => { filterText; highlightedIdx = 0; });
  $effect(() => {
    if (!listEl) return;
    highlightedIdx;
    const el = listEl.querySelector(`[data-idx="${highlightedIdx}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: 'nearest' });
  });

  function toggleCat(k: string) {
    const n = new Set(expandedCategories);
    if (n.has(k)) n.delete(k); else n.add(k);
    expandedCategories = n;
  }
  function toggleProv(k: string) {
    const n = new Set(expandedProviders);
    if (n.has(k)) n.delete(k); else n.add(k);
    expandedProviders = n;
  }

  function onKindKey(ev: KeyboardEvent) {
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      const n = dialogRows.length;
      if (n > 0) highlightedIdx = (highlightedIdx + 1) % n;
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      const n = dialogRows.length;
      if (n > 0) highlightedIdx = (highlightedIdx - 1 + n) % n;
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      const row = dialogRows[highlightedIdx];
      if (!row) return;
      if (row.type === 'header') {
        if (row.scope === 'category') toggleCat(row.key);
        else toggleProv(row.key);
      } else {
        pickKind(row.kind);
      }
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      onClose();
    }
  }

  function focusSearchInput(node: HTMLInputElement) { node.focus(); }

  function pickKind(k: ChartKind) {
    pickedKind = k;
    initDefaultsForKind(k);
    step = 2;
  }

  // ── Step 2: config form state ───────────────────────────────────────
  let formSeriesKey = $state('');
  let formToken = $state('');
  let formChain = $state('');
  let formExchange = $state<'binance' | 'hl'>('binance');
  let formExchangeFlowExchange = $state<'binance' | 'coinbase' | 'okx' | 'bybit' | 'hyperliquid'>('binance');
  let formValueMode = $state<'usd' | 'amount'>('usd');
  let formGmxMarket = $state('');
  let formHlWallet = $state('');
  // Pool fields — used by Uniswap V2/V3/V4 + Aerodrome CL/Basic.
  let formPoolSym0 = $state('USDC');
  let formPoolSym1 = $state('WETH');
  let formPoolFee = $state(500);
  let formPoolTickSpacing = $state(10);
  let formPoolHooks = $state('0x0000000000000000000000000000000000000000');
  let formPoolStable = $state(false);
  // MA controls.
  let formMode = $state<'raw' | 'ma'>('raw');
  let formMAType = $state<MAType>('sma');
  let formMAWindow = $state(21);

  function clearForm() {
    formSeriesKey = '';
    formToken = ''; formChain = '';
    formExchange = 'binance';
    formExchangeFlowExchange = 'binance';
    formValueMode = 'usd';
    formGmxMarket = ''; formHlWallet = '';
    formPoolSym0 = 'USDC'; formPoolSym1 = 'WETH'; formPoolFee = 500;
    formPoolTickSpacing = 10; formPoolStable = false;
    formMode = 'raw'; formMAType = 'sma'; formMAWindow = 21;
  }

  function initDefaultsForKind(k: ChartKind) {
    clearForm();
    const series = OVERLAY_KIND_SERIES[k] ?? [];
    formSeriesKey = series[0]?.key ?? 'sum_value_usd';
    if (k.startsWith('hl_')) { formToken = 'BTC'; formChain = 'HL'; }
    else if (k === 'transfer' || k === 'exchange_flow') { formToken = 'USDC'; formChain = 'ETH'; }
    else if (k === 'ohlcv' || k === 'oi' || k === 'fr' || k === 'bs' || k === 'sz' || k === 'tt' || k === 'ls') {
      formToken = 'BTC';
    } else if (k.startsWith('gmx_')) {
      formChain = 'ARB'; formGmxMarket = 'BTC/USD [WBTC-USDC]';
    } else if (k.startsWith('aero_')) {
      formChain = 'BASE';
    } else if (k.startsWith('lido_') || k === 'lido') {
      formChain = 'ETH';
    } else {
      // AAVE / Morpho / Spark / Uniswap default
      formToken = 'USDC';
      formChain = 'ETH';
    }
  }

  function loadInitial(o: ChartOverlay) {
    formSeriesKey = o.seriesKey;
    formToken = o.token ?? '';
    formChain = o.chain ?? '';
    formExchange = o.exchange ?? 'binance';
    formExchangeFlowExchange = o.exchangeFlowExchange ?? 'binance';
    formValueMode = o.valueMode ?? 'usd';
    formGmxMarket = o.gmxMarket ?? '';
    formHlWallet = o.hlWallet ?? '';
    if (o.uniPool) {
      formPoolSym0 = o.uniPool.symbol0; formPoolSym1 = o.uniPool.symbol1; formPoolFee = o.uniPool.fee;
    }
    if (o.uniV4Pool) {
      formPoolSym0 = o.uniV4Pool.symbol0; formPoolSym1 = o.uniV4Pool.symbol1;
      formPoolFee = o.uniV4Pool.fee; formPoolTickSpacing = o.uniV4Pool.tick_spacing;
      formPoolHooks = o.uniV4Pool.hooks;
    }
    if (o.aeroPool) {
      formPoolSym0 = o.aeroPool.symbol0; formPoolSym1 = o.aeroPool.symbol1;
      formPoolTickSpacing = o.aeroPool.tick_spacing;
    }
    if (o.aeroBasicPool) {
      formPoolSym0 = o.aeroBasicPool.symbol0; formPoolSym1 = o.aeroBasicPool.symbol1;
      formPoolStable = o.aeroBasicPool.stable;
    }
    if (o.ma) { formMode = 'ma'; formMAType = o.ma.type; formMAWindow = o.ma.length; }
    else formMode = 'raw';
  }

  // ── Field visibility helpers ────────────────────────────────────────
  function showsTokenField(k: ChartKind): boolean {
    return k === 'ohlcv' || k === 'oi' || k === 'fr' || k === 'bs' || k === 'sz' || k === 'tt' || k === 'ls'
        || k === 'transfer' || k === 'exchange_flow'
        || k.startsWith('aave_') || k.startsWith('morpho_') || k.startsWith('spark_')
        || k === 'hl_pnl' || k === 'hl_unrealized_pnl';
  }
  function showsChainField(k: ChartKind): boolean {
    return k === 'transfer' || k === 'exchange_flow'
        || k.startsWith('aave_v2_') || k.startsWith('aave_v3_') || k.startsWith('aave_v4_')
        || k.startsWith('morpho_') || k.startsWith('spark_')
        || k === 'lido_l2_deposit' || k === 'lido_l2_withdrawal_request' || k === 'lido_l2_net';
  }
  function showsExchangeField(k: ChartKind): boolean {
    return k === 'ohlcv' || k === 'oi' || k === 'fr' || k === 'bs' || k === 'sz' || k === 'ls';
  }
  function showsUniPool(k: ChartKind): boolean {
    return k.startsWith('uniswap_v2_') || k.startsWith('uniswap_v3_') || k === 'uniswap_v3_net_swap_flow';
  }
  function showsUniV4Pool(k: ChartKind): boolean { return k.startsWith('uniswap_v4_'); }
  function showsAeroPool(k: ChartKind): boolean { return k.startsWith('aero_cl_'); }
  function showsAeroBasicPool(k: ChartKind): boolean { return k.startsWith('aero_basic_'); }
  function showsGmxMarket(k: ChartKind): boolean { return k.startsWith('gmx_'); }
  function showsHlWallet(k: ChartKind): boolean { return k.startsWith('hl_pnl') || k === 'hl_unrealized_pnl'; }
  function showsValueMode(k: ChartKind): boolean {
    return k === 'transfer' || k.startsWith('aave_') || k.startsWith('morpho_') || k.startsWith('spark_')
        || k.startsWith('uniswap_') || k.startsWith('aero_') || k.startsWith('gmx_')
        || k.startsWith('lido_') || k === 'lido' || k.startsWith('hl_');
  }
  function showsExchangeFlowExchange(k: ChartKind): boolean { return k === 'exchange_flow'; }

  // ── Submit ──────────────────────────────────────────────────────────
  function submit() {
    if (!pickedKind) return;
    const k = pickedKind;
    const o: ChartOverlay = {
      id: initial?.id ?? (typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `o-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`),
      kind: k,
      seriesKey: formSeriesKey,
      color: initial?.color ?? nextOverlayColor(usedColors)
    };
    if (formMode === 'ma') {
      o.ma = { type: formMAType, length: Math.max(2, Math.floor(formMAWindow)) };
    }
    if (showsTokenField(k)) o.token = formToken.trim().toUpperCase();
    if (showsChainField(k)) o.chain = formChain.trim().toUpperCase();
    if (showsExchangeField(k)) o.exchange = formExchange;
    if (showsValueMode(k)) o.valueMode = formValueMode;
    if (showsGmxMarket(k)) o.gmxMarket = formGmxMarket.trim();
    if (showsHlWallet(k)) o.hlWallet = formHlWallet.trim().toLowerCase();
    if (showsExchangeFlowExchange(k)) o.exchangeFlowExchange = formExchangeFlowExchange;
    if (showsUniPool(k)) {
      o.uniPool = {
        symbol0: formPoolSym0.trim().toUpperCase(),
        symbol1: formPoolSym1.trim().toUpperCase(),
        fee: k.startsWith('uniswap_v2_') ? 0 : Number(formPoolFee)
      };
    }
    if (showsUniV4Pool(k)) {
      o.uniV4Pool = {
        symbol0: formPoolSym0.trim().toUpperCase(),
        symbol1: formPoolSym1.trim().toUpperCase(),
        fee: Number(formPoolFee),
        tick_spacing: Number(formPoolTickSpacing),
        hooks: formPoolHooks.trim()
      };
    }
    if (showsAeroPool(k)) {
      o.aeroPool = {
        symbol0: formPoolSym0.trim().toUpperCase(),
        symbol1: formPoolSym1.trim().toUpperCase(),
        tick_spacing: Number(formPoolTickSpacing)
      };
    }
    if (showsAeroBasicPool(k)) {
      o.aeroBasicPool = {
        symbol0: formPoolSym0.trim().toUpperCase(),
        symbol1: formPoolSym1.trim().toUpperCase(),
        stable: !!formPoolStable
      };
    }
    const clean = sanitizeOverlay(o);
    if (clean) onSubmit(clean);
  }

  function back() { step = 1; pickedKind = null; }

  let seriesList = $derived.by((): OverlaySeriesDef[] => {
    if (!pickedKind) return [];
    return OVERLAY_KIND_SERIES[pickedKind] ?? [];
  });
</script>

{#if open}
  <div class="fixed inset-0 z-40 bg-black/55" onclick={onClose} role="presentation"></div>
  <div class="fixed z-50 inset-0 flex items-start justify-center pt-24 pointer-events-none" role="presentation">
    <div
      class="pointer-events-auto bg-zinc-950 border border-zinc-700 rounded-lg shadow-2xl shadow-black/60 w-[520px] max-w-[92vw] max-h-[70vh] flex flex-col overflow-hidden"
      role="dialog"
      aria-modal="true"
      aria-label="Add overlay series"
      onclick={(e) => e.stopPropagation()}
    >
      <div class="px-3 pt-2 pb-1 text-[10px] uppercase tracking-widest text-zinc-400 border-b border-zinc-800 flex items-center gap-2">
        <span>{step === 1 ? 'Add overlay — pick a chart' : (initial ? 'Edit overlay' : 'Configure overlay')}</span>
        {#if step === 2 && pickedKind}
          <span class="text-zinc-600">›</span>
          <span class="text-zinc-200">{CHART_KIND_LABELS[pickedKind] ?? pickedKind}</span>
        {/if}
      </div>

      {#if step === 1}
        <div class="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
          <span class="text-zinc-500 text-sm leading-none" aria-hidden="true">⌕</span>
          <input
            type="text"
            bind:value={filterText}
            onkeydown={onKindKey}
            use:focusSearchInput
            placeholder="Search — e.g. AAVE, OHLCV, Uniswap"
            class="flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
            aria-label="Search chart kinds"
          />
        </div>
        <div bind:this={listEl} class="flex-1 overflow-y-auto scrollbar-none py-1">
          {#if dialogRows.length === 0}
            <div class="px-3 py-6 text-xs text-zinc-500 text-center">No matches</div>
          {:else}
            {#each dialogRows as row, i (i)}
              {@const isHi = i === highlightedIdx}
              {#if row.type === 'header'}
                <button
                  type="button"
                  data-idx={i}
                  onclick={() => row.scope === 'category' ? toggleCat(row.key) : toggleProv(row.key)}
                  onmouseenter={() => (highlightedIdx = i)}
                  aria-expanded={row.expanded}
                  class="w-full flex items-center gap-2 text-left py-1.5 text-xs transition-colors
                         {row.level === 1 ? 'font-medium text-zinc-100' : 'text-zinc-200'}
                         {isHi ? 'bg-zinc-800' : 'hover:bg-zinc-900'}"
                  style="padding-left: {0.75 + (row.level - 1) * 1}rem; padding-right: 0.75rem;"
                >
                  <span class="text-zinc-500 text-[10px] w-3 inline-block">{row.expanded ? '▾' : '▸'}</span>
                  <span class="flex-1 truncate">{row.label}</span>
                  <span class="text-zinc-500 text-[10px]">{row.count}</span>
                </button>
              {:else}
                <button
                  type="button"
                  data-idx={i}
                  onclick={() => pickKind(row.kind)}
                  onmouseenter={() => (highlightedIdx = i)}
                  class="w-full flex items-center gap-2 text-left py-1.5 text-xs transition-colors
                         {isHi ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-300 hover:bg-zinc-900'}"
                  style="padding-left: {0.75 + row.indent * 1}rem; padding-right: 0.75rem;"
                >
                  {#if row.showGroup && row.group}
                    <span class="text-[10px] text-zinc-500 truncate">{row.group}</span>
                    <span class="text-[10px] text-zinc-600">›</span>
                  {/if}
                  <span class="truncate">{row.label}</span>
                </button>
              {/if}
            {/each}
          {/if}
        </div>
        <div class="px-3 py-1.5 border-t border-zinc-800 text-[10px] text-zinc-500 flex items-center justify-between">
          <span>↑↓ navigate · ↵ select / expand · esc close</span>
          <span>
            {#if filterText.trim()}
              {dialogRows.length} match{dialogRows.length === 1 ? '' : 'es'}
            {:else}
              {flatItems.length} kind{flatItems.length === 1 ? '' : 's'}
            {/if}
          </span>
        </div>
      {:else}
        <!-- ── Step 2 — config form ───────────────────────────────── -->
        <div class="flex-1 overflow-y-auto scrollbar-none p-3 text-xs text-zinc-200 space-y-2">
          {#if seriesList.length > 1}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Series</span>
              <select bind:value={formSeriesKey} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                {#each seriesList as s (s.key)}
                  <option value={s.key}>{s.label}</option>
                {/each}
              </select>
            </label>
          {/if}
          {#if pickedKind && showsTokenField(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Token</span>
              <input type="text" bind:value={formToken} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
            </label>
          {/if}
          {#if pickedKind && showsChainField(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Chain</span>
              <input type="text" bind:value={formChain} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
            </label>
          {/if}
          {#if pickedKind && showsExchangeField(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Exchange</span>
              <select bind:value={formExchange} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                <option value="binance">Binance</option>
                <option value="hl">Hyperliquid</option>
              </select>
            </label>
          {/if}
          {#if pickedKind && showsExchangeFlowExchange(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Exchange (flow)</span>
              <select bind:value={formExchangeFlowExchange} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                <option value="binance">Binance</option>
                <option value="coinbase">Coinbase</option>
                <option value="okx">OKX</option>
                <option value="bybit">Bybit</option>
                <option value="hyperliquid">Hyperliquid</option>
              </select>
            </label>
          {/if}
          {#if pickedKind && showsUniPool(pickedKind)}
            <div class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Pool</span>
              <input type="text" bind:value={formPoolSym0} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" placeholder="sym0" />
              <span class="text-zinc-500">/</span>
              <input type="text" bind:value={formPoolSym1} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" placeholder="sym1" />
              {#if !pickedKind.startsWith('uniswap_v2_')}
                <select bind:value={formPoolFee} class="bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                  <option value={100}>0.01%</option>
                  <option value={500}>0.05%</option>
                  <option value={3000}>0.30%</option>
                  <option value={10000}>1.00%</option>
                </select>
              {/if}
            </div>
          {/if}
          {#if pickedKind && showsUniV4Pool(pickedKind)}
            <div class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Pool</span>
              <input type="text" bind:value={formPoolSym0} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
              <span class="text-zinc-500">/</span>
              <input type="text" bind:value={formPoolSym1} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
              <input type="number" bind:value={formPoolFee} class="w-16 bg-zinc-900 border border-zinc-700 rounded px-2 py-1" placeholder="fee" />
              <input type="number" bind:value={formPoolTickSpacing} class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-1" placeholder="ts" />
            </div>
          {/if}
          {#if pickedKind && showsAeroPool(pickedKind)}
            <div class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Pool</span>
              <input type="text" bind:value={formPoolSym0} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
              <span class="text-zinc-500">/</span>
              <input type="text" bind:value={formPoolSym1} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
              <input type="number" bind:value={formPoolTickSpacing} class="w-16 bg-zinc-900 border border-zinc-700 rounded px-2 py-1" placeholder="ts" />
            </div>
          {/if}
          {#if pickedKind && showsAeroBasicPool(pickedKind)}
            <div class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Pool</span>
              <input type="text" bind:value={formPoolSym0} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
              <span class="text-zinc-500">/</span>
              <input type="text" bind:value={formPoolSym1} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono uppercase" />
              <label class="flex items-center gap-1 text-zinc-400">
                <input type="checkbox" bind:checked={formPoolStable} /> stable
              </label>
            </div>
          {/if}
          {#if pickedKind && showsGmxMarket(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Market</span>
              <input type="text" bind:value={formGmxMarket} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono" />
            </label>
          {/if}
          {#if pickedKind && showsHlWallet(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Wallet (optional)</span>
              <input type="text" bind:value={formHlWallet} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono lowercase" placeholder="0x… or blank" />
            </label>
          {/if}
          {#if pickedKind && showsValueMode(pickedKind)}
            <label class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">Value mode</span>
              <select bind:value={formValueMode} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                <option value="usd">USD</option>
                <option value="amount">Amount (token)</option>
              </select>
            </label>
          {/if}

          <div class="border-t border-zinc-800 my-2"></div>

          <div class="flex items-center gap-2">
            <span class="w-32 text-zinc-400">Mode</span>
            <label class="flex items-center gap-1">
              <input type="radio" bind:group={formMode} value="raw" /> Raw
            </label>
            <label class="flex items-center gap-1">
              <input type="radio" bind:group={formMode} value="ma" /> MA
            </label>
          </div>
          {#if formMode === 'ma'}
            <div class="flex items-center gap-2">
              <span class="w-32 text-zinc-400">MA</span>
              <select bind:value={formMAType} class="bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                <option value="sma">SMA</option>
                <option value="ema">EMA</option>
                <option value="wma">WMA</option>
              </select>
              <input type="number" min="2" max="500" bind:value={formMAWindow} class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-1" />
              <span class="text-zinc-500">buckets</span>
            </div>
          {/if}
        </div>
        <div class="px-3 py-2 border-t border-zinc-800 flex items-center justify-between">
          {#if !initial}
            <button type="button" class="text-xs text-zinc-400 hover:text-zinc-200" onclick={back}>← Back</button>
          {:else}
            <span></span>
          {/if}
          <div class="flex items-center gap-2">
            <button type="button" class="text-xs text-zinc-400 hover:text-zinc-200 px-2 py-1" onclick={onClose}>Cancel</button>
            <button
              type="button"
              class="text-xs bg-blue-600 hover:bg-blue-500 text-white px-3 py-1 rounded"
              onclick={submit}
            >{initial ? 'Save' : 'Add overlay'}</button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}
