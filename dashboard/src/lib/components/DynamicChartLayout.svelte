<script lang="ts">
  import { onMount } from 'svelte';
  import { flip } from 'svelte/animate';
  import { dndzone, type DndEvent } from 'svelte-dnd-action';
  import PlusCircle from '@lucide/svelte/icons/plus-circle';
  import ChartInstance from '$lib/components/ChartInstance.svelte';
  import {
    CHART_KIND_LABELS,
    MAX_MAS,
    chartKindGroup,
    chartKindGroupOrder,
    chartKindShortLabel,
    defaultMAs,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind,
    type ChartTemplate,
    type MAConfig
  } from '$lib/components/charts/config';
  import type { ChainGroup, Interval, TokenGroup, TransferStream } from '$lib/api';
  import type { View } from '$lib/chart-zoom';

  let {
    tokens,
    streams = [],
    uniPools = [],
    lidoChains = [],
    gmxMarkets = [],
    tokenGroups = [],
    chainGroups = [],
    storageKey,
    availableKinds,
    templates = [],
    defaultLayout,
    defaultToken,
    defaultChain
  }: {
    tokens: string[];
    streams?: TransferStream[];
    uniPools?: import('$lib/api').UniswapStream[];
    lidoChains?: { event: string; chain: string; rows: number }[];
    gmxMarkets?: { event: string; chain: string; market: string; rows: number }[];
    tokenGroups?: TokenGroup[];
    chainGroups?: ChainGroup[];
    storageKey: string;
    availableKinds: ChartKind[];
    templates?: ChartTemplate[];
    defaultLayout: () => ChartInstanceT[];
    defaultToken?: string;
    defaultChain?: string;
  } = $props();

  const MAX_CHARTS = 20;
  const FLIP_MS = 250;
  // Drive the validator off CHART_KIND_LABELS so adding a new ChartKind
  // doesn't silently invalidate every saved layout that uses it. (Previous
  // hand-maintained list missed `pc` and every `aave_*` — sanitize() would
  // return null for the whole layout if any one chart was an AAVE kind.)
  const KNOWN_KINDS = Object.keys(CHART_KIND_LABELS) as ChartKind[];

  let instances = $state<ChartInstanceT[]>(defaultLayout());
  let hydrated = $state(false);

  let syncZoom = $state(true);
  let syncToken = $state(false);
  // Master "Sync Weekend lines" toggle — flipping it propagates the value
  // to every chart's instance.showWeekLines so the page-level switch is
  // an actual master override, not just a label. The flag itself isn't
  // persisted (mirrors syncToken's ephemeral behaviour); the per-instance
  // showWeekLines values it sets DO persist via the layout save effect.
  let syncWeekLines = $state(false);
  let sharedView = $state<View>(null);
  let sharedHoverTime = $state<number | null>(null);

  let insertOpen = $state(false);
  // When set, the next addChart/addTemplate/addTemplateVariant splices the
  // new chart at this index (pushing subsequent charts down). When null, the
  // chart is appended to the end. Set by the per-chart "+" hover button so
  // the menu can be reused with the right insertion target.
  let insertIdx = $state<number | null>(null);
  // When set, the next pick from the insert menu *replaces* the instance at
  // this index instead of inserting. Set by clicking a chart's title (which
  // calls openSwapAt). Mutually exclusive with insertIdx — the same menu
  // serves both modes since the catalog of pickable kinds is identical.
  let swapIdx = $state<number | null>(null);
  // Viewport coords of the "+" that triggered the menu, used so the menu can
  // appear next to the click instead of always at the bottom pad. null when
  // the menu was triggered from the bottom "+ Insert Chart" pad.
  let insertMenuPos = $state<{ x: number; y: number } | null>(null);
  // IDs of templates whose parameter sub-list is currently expanded in the menu.
  let expandedTemplates = $state<Set<string>>(new Set());
  // Protocol-group names that are currently expanded in the Insert menu.
  // The Insert menu organises event-driven chart kinds (AAVE / Uniswap /
  // Lido / Aero / …) under a collapsible parent so the flat 18+ row list
  // doesn't dominate the menu. Single-family kinds (OHLCV, Token Flow,
  // Volume by Size, etc.) stay flat at the top.
  let expandedGroups = $state<Set<string>>(new Set());

  function toggleGroupExpand(name: string) {
    const next = new Set(expandedGroups);
    if (next.has(name)) next.delete(name); else next.add(name);
    expandedGroups = next;
  }

  function openInsert() {
    if (instances.length >= MAX_CHARTS) return;
    insertOpen = !insertOpen;
    insertIdx = null;
    swapIdx = null;
    insertMenuPos = null;
    if (!insertOpen) { expandedTemplates = new Set(); expandedGroups = new Set(); }
  }
  function openInsertAt(idx: number, ev: MouseEvent) {
    if (instances.length >= MAX_CHARTS) return;
    insertIdx = idx;
    swapIdx = null;
    // Anchor the menu to the clicked +. getBoundingClientRect would be more
    // precise; using clientX/Y is fine since the menu uses translate-X to
    // centre itself on the anchor.
    insertMenuPos = { x: ev.clientX, y: ev.clientY };
    insertOpen = true;
    expandedTemplates = new Set();
  }
  /** Open the menu to swap the chart at `idx` with a different kind. The
      replacement preserves width + height so the layout doesn't reflow; the
      id is fresh (a different chart = a different cache key). MAX_CHARTS does
      not gate this because we're replacing, not adding. */
  function openSwapAt(id: string, ev: MouseEvent) {
    const idx = instances.findIndex((i) => i.id === id);
    if (idx < 0) return;
    swapIdx = idx;
    insertIdx = null;
    insertMenuPos = { x: ev.clientX, y: ev.clientY };
    insertOpen = true;
    expandedTemplates = new Set();
  }
  function closeInsert() {
    insertOpen = false;
    insertIdx = null;
    swapIdx = null;
    insertMenuPos = null;
    expandedTemplates = new Set();
    expandedGroups = new Set();
  }
  function toggleTemplateExpand(id: string) {
    const next = new Set(expandedTemplates);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expandedTemplates = next;
  }
  /** Splice `inst` at insertIdx (or append if null) — OR — in swap mode,
      replace the chart at swapIdx, preserving its width + height so the
      layout grid doesn't reflow. Then close the menu. */
  function placeInstance(inst: ChartInstanceT) {
    const swap = swapIdx;
    if (swap !== null && swap >= 0 && swap < instances.length) {
      const old = instances[swap];
      const replaced: ChartInstanceT = { ...inst, width: old.width, height: old.height };
      instances = [...instances.slice(0, swap), replaced, ...instances.slice(swap + 1)];
      closeInsert();
      return;
    }
    const at = insertIdx;
    if (at === null || at < 0 || at >= instances.length) {
      instances = [...instances, inst];
    } else {
      instances = [...instances.slice(0, at), inst, ...instances.slice(at)];
    }
    closeInsert();
  }
  function addChart(kind: ChartKind) {
    // In swap mode, MAX_CHARTS doesn't apply (we're replacing, not adding).
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(newChartInstance(kind, { token: tk, chain: defaultChain }));
  }
  function addTemplate(t: ChartTemplate) {
    if (!t.build) return;
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(t.build({ token: tk, chain: defaultChain }));
  }
  function addTemplateVariant(
    build: (defaults: { token: string; chain?: string }) => ChartInstanceT
  ) {
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(build({ token: tk, chain: defaultChain }));
  }
  function removeChart(id: string) {
    instances = instances.filter((i) => i.id !== id);
  }

  // ---- drag-drop via svelte-dnd-action ----
  function handleSort(e: CustomEvent<DndEvent<ChartInstanceT>>) {
    instances = e.detail.items as ChartInstanceT[];
  }

  // ---- sync zoom + token ----
  function onSharedView(v: View) {
    sharedView = v;
  }
  function onSharedHover(t: number | null) {
    sharedHoverTime = t;
  }
  function toggleSync(next: boolean) {
    syncZoom = next;
  }
  function toggleSyncToken(next: boolean) {
    if (next && instances.length > 0) {
      const t = instances[0].token;
      instances = instances.map((i) => ({ ...i, token: t }));
    }
    syncToken = next;
  }
  function toggleSyncWeekLines(next: boolean) {
    // Push the new value to every chart so the toggle behaves like a
    // master override. On enable → every chart shows weekend lines; on
    // disable → every chart hides them. Per-chart toggles still work
    // afterwards but won't sync back (the master toggle is one-shot,
    // matching Sync Token).
    instances = instances.map((i) => ({ ...i, showWeekLines: next }));
    syncWeekLines = next;
  }
  function onTokenChange(id: string, newToken: string) {
    if (syncToken) {
      instances = instances.map((i) => ({ ...i, token: newToken }));
    } else {
      const idx = instances.findIndex((i) => i.id === id);
      if (idx >= 0) instances[idx].token = newToken;
    }
  }

  // ---- persistence ----
  function isChartKind(s: unknown): s is ChartKind {
    return typeof s === 'string' && (KNOWN_KINDS as string[]).includes(s);
  }
  function migrateMAs(r: Record<string, unknown>): MAConfig[] {
    const fresh = defaultMAs();
    if (Array.isArray(r.mas)) {
      const out: MAConfig[] = [];
      for (const m of r.mas) {
        if (!m || typeof m !== 'object') continue;
        const mm = m as Record<string, unknown>;
        out.push({
          enabled: mm.enabled === true,
          length: typeof mm.length === 'number' ? mm.length : 9,
          type:
            mm.type === 'ema' || mm.type === 'wma' || mm.type === 'sma'
              ? (mm.type as MAConfig['type'])
              : 'sma'
        });
        if (out.length >= MAX_MAS) break;
      }
      while (out.length < MAX_MAS) out.push(fresh[out.length]);
      return out;
    }
    // Legacy single-MA fields.
    const length = typeof r.maLength === 'number' ? r.maLength : 9;
    const type =
      r.maType === 'ema' || r.maType === 'wma' || r.maType === 'sma'
        ? (r.maType as MAConfig['type'])
        : 'sma';
    const enabled = r.showCumulative === true;
    fresh[0] = { enabled, length, type };
    return fresh;
  }

  function sanitize(arr: unknown): ChartInstanceT[] | null {
    if (!Array.isArray(arr)) return null;
    const out: ChartInstanceT[] = [];
    for (const raw of arr) {
      if (!raw || typeof raw !== 'object') return null;
      const r = raw as Record<string, unknown>;
      if (typeof r.id !== 'string') return null;
      if (!isChartKind(r.kind)) return null;
      if (typeof r.token !== 'string') return null;
      if (typeof r.interval !== 'string') return null;
      // Size migration: the old format had width ∈ {1,2} and no height. Map
      // old → new so existing saved layouts keep their look.
      //   old width=1 → new 2×2 (default)
      //   old width=2 → new 4×2 (wide)
      let width: 1 | 2 | 4;
      let height: 1 | 2;
      if (r.height === 1 || r.height === 2) {
        width = r.width === 1 || r.width === 2 || r.width === 4 ? r.width : 2;
        height = r.height;
      } else if (r.width === 2) {
        width = 4;
        height = 2;
      } else {
        width = 2;
        height = 2;
      }

      const inst: ChartInstanceT = {
        id: r.id,
        kind: r.kind,
        width,
        height,
        token: r.token,
        interval: r.interval as Interval,
        showPoint: r.showPoint !== false,
        showWeekLines: r.showWeekLines === true,
        showSum: r.showSum === true,
        mas: migrateMAs(r)
      };
      if (inst.kind === 'sz') {
        inst.under = typeof r.under === 'number' ? r.under : 10000;
        inst.over = typeof r.over === 'number' ? r.over : 100000;
        inst.underInput = typeof r.underInput === 'string' ? r.underInput : String(inst.under);
        inst.overInput = typeof r.overInput === 'string' ? r.overInput : String(inst.over);
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
      }
      if (inst.kind === 'bs') {
        // bs reads /trade_volume with exchange = binance | hl, same shape both ways.
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
      }
      if (inst.kind === 'ohlcv') {
        inst.pin = r.pin === true;
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
        inst.volumeUnit = r.volumeUnit === 'usd' ? 'usd' : 'token';
      }
      if (inst.kind === 'fr') {
        // Same exchange selector as ohlcv — defaults to Binance for
        // existing saved layouts. frDisplay toggles the y-axis between
        // 'rate8h' (Coinglass-style bps/8h, default) and 'apr' (annualized %).
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
        inst.frDisplay = r.frDisplay === 'apr' ? 'apr' : 'rate8h';
      }
      if (inst.kind === 'oi') {
        // OI: Binance reads from binance_open_interest; HL sums per-wallet
        // size across long+short from hl_position_history. The hl-only
        // display selector picks which side(s) to render — defaults to
        // 'total' so existing saved layouts (no field) keep their look.
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
        inst.oiHlDisplay = (r.oiHlDisplay === 'long' || r.oiHlDisplay === 'short' || r.oiHlDisplay === 'long_short')
          ? r.oiHlDisplay : 'total';
      }
      if (inst.kind === 'ls') {
        // L/S: Binance is the pre-aggregated source; HL is computed live
        // from hl_position_history + hl_fills.
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
      }
      if (inst.kind === 'pc') {
        // Price Comparison chart — the overlay token list is the *whole*
        // configuration alongside instance.token. Exchange selector
        // picks the close-price source (binance_ohlcv_1m vs hl_ohlcv_1m).
        inst.overlayTokens = Array.isArray(r.overlayTokens)
          ? r.overlayTokens
              .map((t) => (typeof t === 'string' ? t : ''))
              .filter((t) => t.length > 0)
              .slice(0, 5)
          : [];
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
      }
      // AAVE chart kinds (single-event + net) need a `chain` just like the
      // transfer kind. Default to the page's preferred chain. valueMode
      // ('usd' / 'amount') is restored if previously set; otherwise default
      // to 'usd' so the chart keeps its old behaviour after migration.
      // Covers `aave_v3_*`, `aave_v2_*`, and `aave_v4_*` (every kind in
      // the union shares the `aave_v…_` prefix shape).
      if (inst.kind.startsWith('aave_v')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
      }
      // Morpho / Spark — same shape (chain + valueMode), separate prefix.
      if (inst.kind.startsWith('morpho_') || inst.kind.startsWith('spark_')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
      }
      // GMX — chain (ARB-only for now), valueMode, and gmxMarket selector.
      // Empty market string = "all markets summed".
      if (inst.kind.startsWith('gmx_')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : 'ARB';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        inst.gmxMarket = typeof r.gmxMarket === 'string' ? r.gmxMarket : 'BTC/USD [WBTC-USDC]';
      }
      // Hyperliquid — static chain='HL' chip, token from the binance roster,
      // optional wallet + wallet_category whale-tracking filters.
      if (inst.kind.startsWith('hl_')) {
        inst.chain = 'HL';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        inst.hlWallet = typeof r.hlWallet === 'string' ? r.hlWallet : '';
        inst.hlWalletCategory = typeof r.hlWalletCategory === 'string' ? r.hlWalletCategory : '';
        if (inst.kind === 'hl_top_positions') {
          inst.hlSelectedWallet = typeof r.hlSelectedWallet === 'string' ? r.hlSelectedWallet : '';
        }
        if (inst.kind === 'hl_top_vaults') {
          const sort = r.hlVaultSortBy;
          inst.hlVaultSortBy = (sort === 'deposits' || sort === 'withdrawals' || sort === 'commission')
            ? sort : 'net';
        }
        if (inst.kind === 'hl_vault_detail') {
          inst.hlSelectedVault = typeof r.hlSelectedVault === 'string' ? r.hlSelectedVault : '';
        }
      }
      // Lido chart kinds need a `chain` but no token / pool. L1 kinds are
      // ETH-pinned; L2 kinds default to ARB (highest wstETH bridge volume).
      if (inst.kind.startsWith('lido_')) {
        const isL1 =
          inst.kind === 'lido_deposit' ||
          inst.kind === 'lido_withdrawal_request' ||
          inst.kind === 'lido_withdrawal_claimed' ||
          inst.kind === 'lido_net_stake' ||
          inst.kind === 'lido_net_request_stake' ||
          inst.kind === 'lido_request_pending';
        const ch = typeof r.chain === 'string' ? r.chain : (isL1 ? 'ETH' : (defaultChain ?? 'ARB'));
        inst.chain = isL1 ? 'ETH' : ch;
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
      }
      // Aerodrome basic-pool chart kinds (BASE-only, Solidly v1).
      if (inst.kind.startsWith('aero_basic_')) {
        inst.chain = 'BASE';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        const rp = r.aeroBasicPool as Record<string, unknown> | undefined;
        if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string' && typeof rp.stable === 'boolean') {
          inst.aeroBasicPool = {
            symbol0: (rp.symbol0 as string).toUpperCase(),
            symbol1: (rp.symbol1 as string).toUpperCase(),
            stable: rp.stable as boolean
          };
        } else {
          inst.aeroBasicPool = { symbol0: 'USDC', symbol1: 'WETH', stable: false };
        }
      }
      // Aerodrome concentrated chart kinds. NOTE: this branch must come
      // AFTER the aero_basic_ branch since startsWith('aero_') matches
      // both — the basic branch sets aeroBasicPool, this one sets aeroPool.
      else if (inst.kind.startsWith('aero_')) {
        inst.chain = 'BASE';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        const rp = r.aeroPool as Record<string, unknown> | undefined;
        if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string' && typeof rp.tick_spacing === 'number') {
          inst.aeroPool = {
            symbol0: (rp.symbol0 as string).toUpperCase(),
            symbol1: (rp.symbol1 as string).toUpperCase(),
            tick_spacing: rp.tick_spacing as number
          };
        } else {
          inst.aeroPool = { symbol0: 'USDC', symbol1: 'WETH', tick_spacing: 100 };
        }
      }
      // Uniswap chart kinds also need a `chain`, plus a `uniPool` 3-tuple
      // (symbol0 / symbol1 / fee). Validate the pool shape; fall back to a
      // canonical default so a corrupt save can't strand the chart.
      // valueMode supported on every uniswap_v* kind except
      // uniswap_v3_net_swap_flow (which ignores it at the chart layer).
      // V2 uses fee=0 as a sentinel for "no fee tier"; V4 carries a
      // separate uniV4Pool with extra tick_spacing + hooks fields. The
      // bare 'uniswap_v3' / 'uniswap_v2' / 'uniswap_v4' wrapper kinds
      // share the same `uniswap_v…_` prefix shape via the wrapper-kind
      // branches below — sanitize them through the same default-uniPool
      // path so a missing pool can't strand the chart after a restore.
      if (inst.kind.startsWith('uniswap_v4')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        const rp = r.uniV4Pool as Record<string, unknown> | undefined;
        if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string'
            && typeof rp.fee === 'number' && typeof rp.tick_spacing === 'number'
            && typeof rp.hooks === 'string') {
          inst.uniV4Pool = {
            symbol0: (rp.symbol0 as string).toUpperCase(),
            symbol1: (rp.symbol1 as string).toUpperCase(),
            fee: rp.fee as number,
            tick_spacing: rp.tick_spacing as number,
            hooks: rp.hooks as string
          };
        } else {
          inst.uniV4Pool = {
            symbol0: 'USDC', symbol1: 'WETH', fee: 500, tick_spacing: 10,
            hooks: '0x0000000000000000000000000000000000000000'
          };
        }
      } else if (inst.kind.startsWith('uniswap_v')) {
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        const rp = r.uniPool;
        if (
          rp && typeof rp === 'object' &&
          typeof (rp as Record<string, unknown>).symbol0 === 'string' &&
          typeof (rp as Record<string, unknown>).symbol1 === 'string' &&
          typeof (rp as Record<string, unknown>).fee === 'number'
        ) {
          inst.uniPool = {
            symbol0: ((rp as Record<string, unknown>).symbol0 as string).toUpperCase(),
            symbol1: ((rp as Record<string, unknown>).symbol1 as string).toUpperCase(),
            fee: (rp as Record<string, unknown>).fee as number
          };
        } else {
          inst.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 500 };
        }
      }
      if (inst.kind === 'transfer') {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        // Migration: the previous compound-token registry had a "Native" entry
        // that was a virtual cross-chain bundle. It's been removed; the native
        // assets (ETH on ETH/ARB/BASE, BNB on BSC, POL on POLYGON) are being
        // ingested as real streams instead. Old layouts referencing it would
        // 404 the aggregate, so reset to the page default.
        if (inst.token === 'Native') {
          inst.token = defaultToken ?? tokens[0] ?? 'BTC';
        }
        // New shape: a single `filter` field. Migrate from the older
        // `extraSeries[0].filters` if present (we keep only the first; the
        // rest are dropped now that the chart shows only one series).
        function pickFilter(src: unknown): Record<string, string[]> {
          const out: Record<string, string[]> = {};
          if (!src || typeof src !== 'object') return out;
          const rf = src as Record<string, unknown>;
          for (const k of [
            'sender_in', 'sender_ex', 'sender_all_in',
            'receiver_in', 'receiver_ex', 'receiver_all_in',
            'involving_in', 'involving_ex', 'involving_all_in',
            'sender_entity_in', 'sender_entity_ex',
            'receiver_entity_in', 'receiver_entity_ex',
            'involving_entity_in', 'involving_entity_ex',
            'sender_addr_in', 'sender_addr_ex',
            'receiver_addr_in', 'receiver_addr_ex',
            'involving_addr_in', 'involving_addr_ex'
          ]) {
            const v = rf[k];
            if (Array.isArray(v)) {
              const cleaned = v
                .map((x) => (typeof x === 'string' ? x : ''))
                .filter((x) => x.length > 0);
              if (cleaned.length) out[k] = cleaned;
            }
          }
          return out;
        }
        let filter: Record<string, string[]> = pickFilter(r.filter);
        if (Object.keys(filter).length === 0 && Array.isArray(r.extraSeries) && r.extraSeries.length > 0) {
          const first = r.extraSeries[0] as Record<string, unknown> | undefined;
          if (first && typeof first === 'object') {
            filter = pickFilter(first.filters);
          }
        }
        inst.filter = filter;
        // Netflow templates persist two locked filter sets (positive − negative).
        if (r.netFilter && typeof r.netFilter === 'object') {
          const nf = r.netFilter as Record<string, unknown>;
          const positive = pickFilter(nf.positive);
          const negative = pickFilter(nf.negative);
          if (
            Object.keys(positive).length > 0 ||
            Object.keys(negative).length > 0
          ) {
            inst.netFilter = { positive, negative };
          }
        }
        if (typeof r.templateName === 'string' && r.templateName.length > 0) {
          inst.templateName = r.templateName;
        }
      }
      if (inst.kind === 'exchange_flow') {
        // Hyperliquid is ARB-only; CeXes default to ETH. Sanitize chain
        // accordingly so a stored layout that selected HL on a non-ARB
        // chain repairs itself on load.
        const ex = r.exchangeFlowExchange;
        const validEx = ['binance','coinbase','okx','bybit','hyperliquid'];
        inst.exchangeFlowExchange = validEx.includes(ex) ? ex : 'binance';
        const ft = r.exchangeFlowType;
        inst.exchangeFlowType = ft === 'inflow' || ft === 'outflow' || ft === 'all' ? ft : 'netflow';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        if (inst.exchangeFlowExchange === 'hyperliquid') {
          inst.chain = 'ARB';
        } else {
          inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        }
      }
      out.push(inst);
      if (out.length >= MAX_CHARTS) break;
    }
    return out;
  }

  onMount(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        const restored = sanitize(parsed?.charts);
        if (restored && restored.length > 0) instances = restored;
      }
    } catch {
      // fall back to default
    }
    hydrated = true;
  });

  $effect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: 1, charts: instances }));
    } catch {
      // localStorage may be full or disabled
    }
  });

  function resetLayout() {
    if (!confirm('Reset chart layout to defaults?')) return;
    instances = defaultLayout();
  }
</script>

<div class="flex items-end justify-end gap-3 flex-wrap">
  <label class="text-xs text-zinc-400 flex items-center gap-2">
    <input
      type="checkbox"
      checked={syncZoom}
      onchange={(e) => toggleSync(e.currentTarget.checked)}
      class="accent-zinc-400"
    />
    Sync zoom
  </label>
  <label class="text-xs text-zinc-400 flex items-center gap-2">
    <input
      type="checkbox"
      checked={syncToken}
      onchange={(e) => toggleSyncToken(e.currentTarget.checked)}
      class="accent-zinc-400"
    />
    Sync Token
  </label>
  <label class="text-xs text-zinc-400 flex items-center gap-2">
    <input
      type="checkbox"
      checked={syncWeekLines}
      onchange={(e) => toggleSyncWeekLines(e.currentTarget.checked)}
      class="accent-zinc-400"
    />
    Sync Weekend lines
  </label>
  <button type="button" onclick={resetLayout} class="text-xs text-zinc-500 hover:text-zinc-200"
    >Reset layout</button
  >
</div>

<section
  use:dndzone={{ items: instances, flipDurationMs: FLIP_MS, dropTargetStyle: {} }}
  onconsider={handleSort}
  onfinalize={handleSort}
  class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6"
  style="grid-auto-rows: 320px; grid-auto-flow: dense;"
>
  {#each instances as inst, idx (inst.id)}
    <div
      animate:flip={{ duration: FLIP_MS }}
      style="grid-column: span {inst.width}; grid-row: span {inst.height};"
      class="relative insert-host"
    >
      <!-- "+" hover zone sitting in the column-gap to the left of this chart.
           Clicking opens the insert menu pre-set to insert *before* this chart. -->
      <button
        type="button"
        class="insert-plus"
        aria-label="Insert chart before this one"
        title="Insert chart here"
        onclick={(e) => openInsertAt(idx, e)}
      >
        <PlusCircle size={16} strokeWidth={1.5} class="insert-plus-icon" />
      </button>
      <ChartInstance
        bind:instance={instances[idx]}
        {tokens}
        {streams}
        {uniPools}
        {lidoChains}
        {gmxMarkets}
        {tokenGroups}
        {chainGroups}
        {syncZoom}
        {sharedView}
        {sharedHoverTime}
        {onSharedView}
        {onSharedHover}
        {onTokenChange}
        onRemove={removeChart}
        onSwap={openSwapAt}
      />
    </div>
  {/each}
</section>

{#snippet insertMenuBody()}
  {#if swapIdx !== null}
    <div class="px-3 pt-2 pb-1 text-[10px] uppercase tracking-widest text-amber-300 border-b border-zinc-800">
      Swap this chart — pick a replacement
    </div>
  {/if}
  {#if templates.length > 0}
    <div class="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-widest text-zinc-500">
      Templates
    </div>
    {#each templates as t (t.id)}
      {#if t.variants && t.variants.length > 0}
        <button
          type="button"
          onclick={() => toggleTemplateExpand(t.id)}
          class="flex items-center justify-between w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
          aria-expanded={expandedTemplates.has(t.id)}
        >
          <span>{t.label}</span>
          <span class="text-zinc-500 text-[10px] ml-2"
            >{expandedTemplates.has(t.id) ? '▾' : '▸'}</span
          >
        </button>
        {#if expandedTemplates.has(t.id)}
          <div class="bg-zinc-900/40">
            {#each t.variants as v (v.id)}
              <button
                type="button"
                onclick={() => addTemplateVariant(v.build)}
                class="block w-full text-left pl-7 pr-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
              >{v.label}</button>
            {/each}
          </div>
        {/if}
      {:else if t.build}
        <button
          type="button"
          onclick={() => addTemplate(t)}
          class="block w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
        >{t.label}</button>
      {/if}
    {/each}
    <div class="border-t border-zinc-800 my-1"></div>
  {/if}
  <div class="px-3 pt-0.5 pb-0.5 text-[10px] uppercase tracking-widest text-zinc-500">
    Blank chart
  </div>
  {@const _flat = availableKinds.filter((k) => chartKindGroup(k) === null)}
  {@const _grouped = (() => {
    // Bucket the event-driven kinds by their protocol group (AAVE V3,
    // Uniswap V4, etc.), then sort the groups by chartKindGroupOrder so
    // versions inside a family render in ascending order (V2 → V3 → V4)
    // regardless of how the page composed `availableKinds`. Items inside
    // each group preserve their page-given order so per-page customisation
    // still works for the leaf listing.
    const m = new Map<string, ChartKind[]>();
    for (const k of availableKinds) {
      const g = chartKindGroup(k);
      if (!g) continue;
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(k);
    }
    return Array.from(m.entries())
      .sort(([a], [b]) => {
        const da = chartKindGroupOrder(a);
        const db = chartKindGroupOrder(b);
        return da !== db ? da - db : a.localeCompare(b);
      });
  })()}
  <!-- Top-level (single-kind families: OHLCV, Token Flow, …). -->
  {#each _flat as k (k)}
    <button
      type="button"
      onclick={() => addChart(k)}
      class="block w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
    >{CHART_KIND_LABELS[k]}</button>
  {/each}
  <!-- Grouped (protocol families with multiple event-driven kinds). -->
  {#each _grouped as [groupName, groupKinds] (groupName)}
    <button
      type="button"
      onclick={() => toggleGroupExpand(groupName)}
      class="flex items-center justify-between w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
      aria-expanded={expandedGroups.has(groupName)}
    >
      <span>{groupName}</span>
      <span class="text-zinc-500 text-[10px] ml-2"
        >{groupKinds.length} <span class="ml-1">{expandedGroups.has(groupName) ? '▾' : '▸'}</span></span
      >
    </button>
    {#if expandedGroups.has(groupName)}
      <div class="bg-zinc-900/40">
        {#each groupKinds as k (k)}
          <button
            type="button"
            onclick={() => addChart(k)}
            class="block w-full text-left pl-7 pr-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
          >{chartKindShortLabel(k)}</button>
        {/each}
      </div>
    {/if}
  {/each}
  <div class="border-t border-zinc-800 mt-1 pt-1">
    <button
      type="button"
      onclick={closeInsert}
      class="block w-full text-left px-3 py-1 text-[10px] uppercase tracking-widest text-zinc-500 hover:text-zinc-300"
    >Cancel</button>
  </div>
{/snippet}

{#if instances.length < MAX_CHARTS}
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
    <div
      class="relative rounded-xl border border-dashed border-zinc-700 bg-zinc-950/30 min-h-[180px] flex items-center justify-center"
      role="region"
      aria-label="Insert chart"
    >
      <button
        type="button"
        onclick={openInsert}
        class="text-sm text-zinc-400 hover:text-zinc-100 px-3 py-2"
      >+ Insert Chart</button>
      {#if insertOpen && insertMenuPos === null}
        <div
          class="absolute z-30 top-12 left-1/2 -translate-x-1/2 bg-zinc-950 border border-zinc-700 rounded-md shadow-xl shadow-black/60 py-1 min-w-[260px] max-h-[60vh] overflow-y-auto"
          role="menu"
        >
          {@render insertMenuBody()}
        </div>
      {/if}
    </div>
  </div>
{:else}
  <!-- At MAX_CHARTS — the bottom Insert Chart pad and the per-chart "+"
       hover buttons are silently inert. Surface this so the user knows why
       clicking + does nothing, rather than just removing the affordance. -->
  <div
    class="rounded-xl border border-dashed border-amber-700/60 bg-amber-900/10 px-4 py-3 text-xs text-amber-300 flex items-center gap-2"
    role="status"
  >
    <span class="text-base leading-none">⚠</span>
    <span>
      <strong class="font-semibold">Max {MAX_CHARTS} charts reached.</strong>
      Remove a chart (its ✕ button) to insert another. This cap exists to
      keep page-level fetches in budget — each chart is its own data load.
    </span>
  </div>
{/if}

<!-- Floating insert menu — anchored to the per-chart "+" that opened it. -->
{#if insertOpen && insertMenuPos !== null}
  <!-- Click-outside scrim. Captures clicks anywhere on the page and closes the menu. -->
  <div
    class="fixed inset-0 z-40"
    onclick={closeInsert}
    role="presentation"
  ></div>
  <div
    class="fixed z-50 bg-zinc-950 border border-zinc-700 rounded-md shadow-xl shadow-black/60 py-1 min-w-[260px] max-h-[60vh] overflow-y-auto"
    style="left: {Math.min(Math.max(insertMenuPos.x - 130, 8), (typeof window !== 'undefined' ? window.innerWidth : 1200) - 268)}px; top: {insertMenuPos.y + 8}px;"
    role="menu"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => { if (e.key === 'Escape') closeInsert(); }}
  >
    {@render insertMenuBody()}
  </div>
{/if}

<style>
  /* Insert-between-charts affordance. Each chart wrapper hosts an absolute
     button overhanging the column gap to the LEFT of it. The button is
     invisible until the wrapper is hovered, at which point a small "+"
     circle appears centred along the left edge.

     The hit area is taller than the visible circle (24px wide × full chart
     height) so a casual hover near the left of the chart triggers it. The
     circle uses pointer-events: none so the click target is the whole bar,
     not just the dot. */
  .insert-host > .insert-plus {
    position: absolute;
    /* The grid gap between items is 1.5rem (gap-6 = 24px). Span the full
       width of that gap so the "+" is centred in the empty space, not
       overlapping the chart card. For first-column wrappers (no gap to
       the left, only page margin), the button still renders cleanly in
       that whitespace. */
    left: -1.5rem;
    top: 0;
    bottom: 0;
    width: 1.5rem;
    z-index: 20;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 120ms ease;
    background: transparent;
    border: none;
    cursor: pointer;
  }
  /* Show when the wrapper is hovered, or when the button itself is
     focus-visible (keyboard access). */
  .insert-host:hover > .insert-plus,
  .insert-host > .insert-plus:focus-visible {
    opacity: 1;
  }
  /* The icon (PlusCircle from lucide) is a single SVG so a colour change
     on the stroke is all we need for the hover state. */
  :global(.insert-plus .insert-plus-icon) {
    pointer-events: none;
    color: rgb(161 161 170);                    /* zinc-400 */
    transition: color 120ms;
  }
  .insert-host > .insert-plus:hover :global(.insert-plus-icon) {
    color: rgb(96 165 250);                     /* blue-400 */
  }
</style>
