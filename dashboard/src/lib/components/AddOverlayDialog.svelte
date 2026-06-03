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
    fmtUniPool,
    nextOverlayColor,
    sanitizeOverlay,
    type ChartOverlay,
    type ChartKind,
    type OverlaySeriesDef,
    type MAType,
    type UniPool
  } from './charts/config';
  import type { UniswapStream, TransferStream, TokenGroup, ChainGroup } from '$lib/api';

  let {
    open = false,
    initial = null as ChartOverlay | null,
    primaryToken = '',
    usedColors = [] as string[],
    tokens = [] as string[],
    tokenGroups = [] as TokenGroup[],
    chainGroups = [] as ChainGroup[],
    transferStreams = [] as TransferStream[],
    uniPools = [] as UniswapStream[],
    lidoChains = [] as { event: string; chain: string; rows: number }[],
    gmxMarkets = [] as { event: string; chain: string; market: string; rows: number }[],
    onSubmit,
    onClose
  }: {
    open: boolean;
    /** Token currently shown on the host chart. New overlays default to
     *  this token when the picked kind is exchange-style (ohlcv/oi/fr/bs/
     *  sz/tt/ls or any hl_*), so adding an OI overlay on an ETH OHLCV
     *  chart starts with ETH selected — saves a click and matches user
     *  intent. Kinds that conventionally pin to a different token
     *  (transfer/exchange_flow → USDC; aave/morpho/spark/uniswap → USDC)
     *  keep their existing defaults. */
    primaryToken?: string;
    /** When set, the dialog opens directly to step 2 with these values
     *  pre-populated. Used for editing an existing overlay (kind is
     *  locked — swap = remove + add). */
    initial: ChartOverlay | null;
    usedColors: string[];
    /** Available tokens, chains, pools, markets — forwarded from the host
     *  ChartInstance so the dialog can render the same selects the chart
     *  header offers (instead of asking the user to type free-form). */
    tokens?: string[];
    /** Server-defined compound-token bundles (e.g. "USDC+USDT", "Stables").
     *  Surfaced in the token dropdown for Token Flow and Exchange Flow
     *  overlays so the user can aggregate across a bundle in a single
     *  fetch — same as the standalone chart's token-group support. */
    tokenGroups?: TokenGroup[];
    /** Server-defined compound-chain bundles (e.g. "EVM" = ETH+ARB+BASE+…).
     *  Surfaced in the chain dropdown for kinds where the standalone chart
     *  also exposes them (Token Flow, Exchange Flow non-HL, AAVE V2/V3,
     *  Morpho, Lido L2). Same shape as `tokenGroups`. */
    chainGroups?: ChainGroup[];
    /** Transfer-stream catalogue (chain, token, kind). Used to filter the
     *  token dropdown for the Token Flow / Exchange Flow kinds. */
    transferStreams?: TransferStream[];
    uniPools?: UniswapStream[];
    lidoChains?: { event: string; chain: string; rows: number }[];
    gmxMarkets?: { event: string; chain: string; market: string; rows: number }[];
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

  /** Score one item against one query token. Returns the best score across
   *  three strategies; 0 means "no match" and the token is dropped.
   *  Higher = better. Tuned so word-boundary hits beat mid-word hits,
   *  which beat fuzzy subsequence hits.
   *
   *  - Word-prefix:   token at the start of any whitespace-separated word
   *                   in the search key (e.g. "bor" → "Borrow").  Score 1000+.
   *  - Substring:     token appears anywhere else.                Score  500+.
   *  - Subsequence:   token's chars appear in order with gaps     Score  100+.
   *                   (e.g. "uniw3" → "Uniswap V3"). Score decays
   *                   with the span of the match so tighter wins.
   *
   *  Each strategy adds a small bonus for an earlier match position so that
   *  e.g. "aave" outranks items where it appears deep in the description.
   */
  function scoreToken(key: string, token: string): number {
    if (!token) return 0;
    // Word-prefix anywhere (start-of-string counts as a word boundary).
    let i = key.indexOf(token);
    while (i !== -1) {
      const isWordStart = i === 0 || /[\s:/(-]/.test(key[i - 1]);
      if (isWordStart) return 1000 + Math.max(0, 100 - i);
      i = key.indexOf(token, i + 1);
    }
    // Plain substring.
    const sub = key.indexOf(token);
    if (sub !== -1) return 500 + Math.max(0, 100 - sub);
    // Subsequence fallback.
    let ki = 0, ti = 0, first = -1, last = -1;
    while (ki < key.length && ti < token.length) {
      if (key[ki] === token[ti]) {
        if (first < 0) first = ki;
        last = ki;
        ti++;
      }
      ki++;
    }
    if (ti < token.length) return 0;
    const span = last - first + 1;
    // span === token.length is the tightest possible run; decay as it
    // stretches over more of the key.
    return 100 + Math.max(0, 80 - (span - token.length));
  }

  /** Multi-token AND scoring. Every whitespace-separated token in the
   *  query must score > 0; the total is their sum. Returns 0 to drop the
   *  item from results. */
  function fuzzyScore(key: string, queryTokens: string[]): number {
    if (queryTokens.length === 0) return 0;
    let total = 0;
    for (const tok of queryTokens) {
      const s = scoreToken(key, tok);
      if (s <= 0) return 0;
      total += s;
    }
    return total;
  }

  type DialogRow =
    | { type: 'header'; level: 1 | 2; key: string; label: string; expanded: boolean; count: number; scope: 'category' | 'provider' }
    | { type: 'leaf'; kind: ChartKind; label: string; indent: 0 | 1 | 2; group: string | null; showGroup: boolean };

  let dialogRows = $derived.by((): DialogRow[] => {
    const q = filterText.trim().toLowerCase();
    if (q) {
      // Token-AND fuzzy scoring — each whitespace-separated token of the
      // query must match somewhere in searchKey (word-prefix > substring >
      // subsequence). Results are ranked by total score, ties broken by
      // alphabetical label so the order is stable.
      const tokens = q.split(/\s+/).filter((t) => t.length > 0);
      const scored = flatItems
        .map((it) => ({ it, score: fuzzyScore(it.searchKey, tokens) }))
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score || a.it.label.localeCompare(b.it.label));
      return scored.map(({ it }) => ({
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
    // Inherit the host chart's token when the overlay kind plots one of the
    // same exchange-style series (HL families + ohlcv/oi/fr/bs/sz/tt/ls).
    // Falls back to 'BTC' when the host chart doesn't have a token set
    // (e.g. lending/transfer charts that pin to a stablecoin).
    const inherit = primaryToken || 'BTC';
    if (k.startsWith('hl_')) { formToken = inherit; formChain = 'HL'; }
    else if (k === 'transfer' || k === 'exchange_flow') { formToken = 'USDC'; formChain = 'ETH'; }
    else if (k === 'ohlcv' || k === 'oi' || k === 'fr' || k === 'bs' || k === 'sz' || k === 'tt' || k === 'ls') {
      formToken = inherit;
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
    // Apply any per-kind locks AFTER the family defaults so e.g. aave_v4
    // (locked ETH) overrides any earlier branch and tt (locked binance)
    // forces the exchange. Mirrors the standalone chart's auto-snap effect.
    const lc = lockedChain(k);
    if (lc) formChain = lc;
    const lt = lockedToken(k);
    if (lt) formToken = lt;
    const le = lockedExchange(k);
    if (le) formExchange = le;
  }

  function loadInitial(o: ChartOverlay) {
    formSeriesKey = o.seriesKey;
    // A stored tokenGroup wins over `token`: when present, the dropdown's
    // selected value is the group name itself (e.g. "USDC+USDT"). submit()
    // decides which field to persist based on whether the selection
    // matches a known group name.
    formToken = o.tokenGroup ?? o.token ?? '';
    // A stored chainGroup wins over `chain`: when present, the dropdown's
    // selected value is the group name itself (e.g. "EVM"). submit()
    // decides which field to persist based on whether the selection
    // matches a known group name.
    formChain = o.chainGroup ?? o.chain ?? '';
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

  // ── Lock helpers ────────────────────────────────────────────────────
  // Mirror the standalone chart's per-kind constraints — a kind whose
  // header pins a value to a static chip (rather than offering a select)
  // gets the same treatment here. lockedX(kind) returns the pinned value
  // or null when the field is user-selectable.
  function lockedChain(k: ChartKind): string | null {
    if (k.startsWith('aave_v4_')) return 'ETH';
    if (k.startsWith('spark_'))   return 'ETH';
    if (k.startsWith('aero_cl_') || k.startsWith('aero_basic_')) return 'BASE';
    if (k.startsWith('gmx_'))     return 'ARB';
    if (k.startsWith('hl_'))      return 'HL';
    // Lido L1 kinds are ETH-only; the L2 family (lido_l2_*) keeps its
    // selector.
    if (k.startsWith('lido_') && !(k === 'lido_l2_deposit' || k === 'lido_l2_withdrawal_request' || k === 'lido_l2_net')) {
      return 'ETH';
    }
    return null;
  }
  function lockedToken(k: ChartKind): string | null {
    // Bridge/vault flows are USDC-only — no token dimension.
    if (k === 'hl_transfers' || k === 'hl_vault_net') return 'USDC';
    return null;
  }
  function lockedExchange(k: ChartKind): 'binance' | 'hl' | null {
    // Top-trader L/S is a Binance-only product concept.
    if (k === 'tt') return 'binance';
    return null;
  }
  /** Dynamic exchange_flow constraint: with `exchangeFlowExchange = hyperliquid`,
   *  the chain is forced to ARB and the token to USDC (matches the standalone
   *  chart header's behaviour). */
  let exchangeFlowHLLocked = $derived(
    pickedKind === 'exchange_flow' && formExchangeFlowExchange === 'hyperliquid'
  );

  /** Dynamic OI constraint: any long/short slot (USD or token) and the
   *  long-to-short ratio are HL-only — Binance OI has no side split. The
   *  total-OI series in either unit works for both exchanges and is left
   *  unlocked. */
  let oiHlLocked = $derived(
    pickedKind === 'oi'
    && (formSeriesKey === 'long_oi_value'
        || formSeriesKey === 'short_oi_value'
        || formSeriesKey === 'long_oi'
        || formSeriesKey === 'short_oi'
        || formSeriesKey === 'long_to_short_oi')
  );

  // Effective locks combine static (per-kind) + dynamic (exchange_flow → HL,
  // oi long/short → HL).
  function effectiveLockedChain(k: ChartKind): string | null {
    if (k === 'exchange_flow' && exchangeFlowHLLocked) return 'ARB';
    return lockedChain(k);
  }
  function effectiveLockedToken(k: ChartKind): string | null {
    if (k === 'exchange_flow' && exchangeFlowHLLocked) return 'USDC';
    return lockedToken(k);
  }
  function effectiveLockedExchange(k: ChartKind): 'binance' | 'hl' | null {
    if (k === 'oi' && oiHlLocked) return 'hl';
    return lockedExchange(k);
  }

  // ── Field visibility helpers ────────────────────────────────────────
  // A field's selector renders only when the kind needs it AND its value
  // isn't pinned. Pinned fields render as a small read-only chip instead
  // (see the template below).
  function tokenFieldKindUsesIt(k: ChartKind): boolean {
    return k === 'ohlcv' || k === 'oi' || k === 'fr' || k === 'bs' || k === 'sz' || k === 'tt' || k === 'ls'
        || k === 'transfer' || k === 'exchange_flow'
        || k.startsWith('aave_') || k.startsWith('morpho_') || k.startsWith('spark_')
        || k === 'hl_pnl' || k === 'hl_unrealized_pnl';
  }
  function chainFieldKindUsesIt(k: ChartKind): boolean {
    return k === 'transfer' || k === 'exchange_flow'
        || k.startsWith('aave_v2_') || k.startsWith('aave_v3_') || k.startsWith('aave_v4_')
        || k.startsWith('morpho_') || k.startsWith('spark_')
        || k.startsWith('aero_') || k.startsWith('gmx_') || k.startsWith('hl_')
        || k.startsWith('lido_') || k === 'lido'
        || k.startsWith('uniswap_');
  }
  function exchangeFieldKindUsesIt(k: ChartKind): boolean {
    return k === 'ohlcv' || k === 'oi' || k === 'fr' || k === 'bs' || k === 'sz' || k === 'ls' || k === 'tt';
  }
  // Show the interactive widget only when (used AND not locked).
  function showsTokenField(k: ChartKind): boolean {
    return tokenFieldKindUsesIt(k) && effectiveLockedToken(k) === null;
  }
  function showsChainField(k: ChartKind): boolean {
    return chainFieldKindUsesIt(k) && effectiveLockedChain(k) === null;
  }
  function showsExchangeField(k: ChartKind): boolean {
    return exchangeFieldKindUsesIt(k) && effectiveLockedExchange(k) === null;
  }
  function showsUniPool(k: ChartKind): boolean {
    return k.startsWith('uniswap_v2_') || k.startsWith('uniswap_v3_') || k === 'uniswap_v3_net_swap_flow';
  }
  function showsUniV4Pool(k: ChartKind): boolean { return k.startsWith('uniswap_v4_'); }
  function showsAeroPool(k: ChartKind): boolean { return k.startsWith('aero_cl_'); }
  function showsAeroBasicPool(k: ChartKind): boolean { return k.startsWith('aero_basic_'); }
  function showsGmxMarket(k: ChartKind): boolean { return k.startsWith('gmx_'); }
  function showsHlWallet(k: ChartKind): boolean { return k === 'hl_pnl' || k === 'hl_unrealized_pnl'; }
  function showsValueMode(k: ChartKind): boolean {
    return k === 'transfer' || k.startsWith('aave_') || k.startsWith('morpho_') || k.startsWith('spark_')
        || k.startsWith('uniswap_') || k.startsWith('aero_') || k.startsWith('gmx_')
        || k.startsWith('lido_') || k === 'lido' || k.startsWith('hl_');
  }
  function showsExchangeFlowExchange(k: ChartKind): boolean { return k === 'exchange_flow'; }

  // Whenever exchange_flow swaps to/from hyperliquid, mirror the chart-
  // header's auto-correct: pin chain=ARB, token=USDC under HL; restore
  // sensible defaults otherwise. Without this the chip would still show
  // the locked value but a stale formChain / formToken would persist
  // through submit().
  $effect(() => {
    if (pickedKind !== 'exchange_flow') return;
    if (formExchangeFlowExchange === 'hyperliquid') {
      if (formChain !== 'ARB') formChain = 'ARB';
      if (formToken !== 'USDC') formToken = 'USDC';
    }
  });

  // OI long/short series only exist on the HL table — snap the picked
  // exchange to 'hl' so submit() captures it even if Binance was the prior
  // selection. The chip rendered in place of the exchange selector reads
  // the same effectiveLockedExchange() so the UI stays consistent.
  $effect(() => {
    if (oiHlLocked && formExchange !== 'hl') formExchange = 'hl';
  });

  // ── Per-kind dropdown sources ───────────────────────────────────────
  // Common chain set used by transfer / exchange_flow / AAVE V3 / Morpho /
  // GMX / etc. The chart header offers the same options.
  const COMMON_CHAINS = ['ETH', 'ARB', 'BASE', 'BSC', 'POLYGON', 'OPT', 'AVAX'];
  function chainOptions(k: ChartKind): string[] {
    if (k.startsWith('aave_v2_')) return ['ETH', 'POLYGON'];
    if (k.startsWith('aave_v3_')) return ['ETH', 'ARB', 'BASE', 'OPT', 'POLYGON', 'AVAX'];
    if (k.startsWith('aave_v4_')) return ['ETH'];
    if (k.startsWith('morpho_'))  return ['ETH', 'BASE'];
    if (k.startsWith('spark_'))   return ['ETH'];
    if (k === 'lido_l2_deposit' || k === 'lido_l2_withdrawal_request' || k === 'lido_l2_net') {
      const fromStreams = Array.from(new Set(lidoChains.filter((c) => c.chain !== 'ETH').map((c) => c.chain))).sort();
      return fromStreams.length > 0 ? fromStreams : ['ARB', 'BASE', 'OPT'];
    }
    if (k.startsWith('uniswap_v2_') || k.startsWith('uniswap_v3_') || k === 'uniswap_v3_net_swap_flow'
        || k.startsWith('uniswap_v4_')) {
      const fromStreams = Array.from(new Set(uniPools.map((p) => p.chain))).sort();
      return fromStreams.length > 0 ? fromStreams : ['ETH', 'ARB', 'BASE', 'BSC', 'POLYGON'];
    }
    if (k === 'transfer' || k === 'exchange_flow') return COMMON_CHAINS;
    return COMMON_CHAINS;
  }

  /** Uniswap V2/V3 pools available on the currently picked chain, ordered
   *  by total rows descending so the busiest pools surface first. */
  let uniPoolsForChain = $derived.by((): UniPool[] => {
    if (!pickedKind || !(pickedKind.startsWith('uniswap_v2_') || pickedKind.startsWith('uniswap_v3_') || pickedKind === 'uniswap_v3_net_swap_flow')) return [];
    const wantChain = formChain.trim().toUpperCase();
    if (!wantChain) return [];
    const dedup = new Map<string, { pool: UniPool; rows: number }>();
    for (const p of uniPools) {
      if (p.chain !== wantChain) continue;
      const key = `${p.symbol0}|${p.symbol1}|${p.fee_tier}`;
      const prev = dedup.get(key);
      const rows = (prev?.rows ?? 0) + p.rows;
      dedup.set(key, { pool: { symbol0: p.symbol0, symbol1: p.symbol1, fee: p.fee_tier }, rows });
    }
    return Array.from(dedup.values()).sort((a, b) => b.rows - a.rows).map((x) => x.pool);
  });

  function poolKey(p: { symbol0: string; symbol1: string; fee: number }): string {
    return `${p.symbol0}|${p.symbol1}|${p.fee}`;
  }
  let currentPoolKey = $derived(poolKey({ symbol0: formPoolSym0, symbol1: formPoolSym1, fee: formPoolFee }));
  function onPoolPick(v: string) {
    const [s0, s1, fee] = v.split('|');
    formPoolSym0 = s0; formPoolSym1 = s1; formPoolFee = Number(fee);
  }

  /** GMX markets available for the kind's event family. */
  let gmxMarketsForOverlay = $derived.by((): string[] => {
    if (!pickedKind || !pickedKind.startsWith('gmx_')) return [];
    return Array.from(new Set(gmxMarkets.map((m) => m.market))).sort();
  });

  // ── Stream-filtered token list ──────────────────────────────────────
  // Some protocols only have data for a handful of tokens on a given
  // chain — picking "ETH" on AAVE V3 returns an empty series because
  // every AAVE position is denominated in WETH, not native ETH. The
  // chart header gets away with showing every token because the user
  // sees the empty chart and switches; in the dialog we'd rather show
  // only what's loadable. We lazy-fetch the per-protocol `/streams`
  // catalogue when a kind+chain combo is picked and cache per (kind,
  // chain). Falls back to the host-page `tokens` list when no
  // protocol-specific endpoint exists (Binance/HL kinds, etc.).
  const _streamTokenCache = new Map<string, string[]>();
  let availableTokens = $state<string[] | null>(null);

  function streamsEndpointFor(k: ChartKind): string | null {
    if (k.startsWith('aave_v2_')) return '/api/aave_v2/streams';
    if (k.startsWith('aave_v4_')) return '/api/aave_v4/streams';
    if (k.startsWith('aave_v3_')) return '/api/aave/streams';
    if (k.startsWith('morpho_'))  return '/api/morpho/streams';
    if (k.startsWith('spark_'))   return '/api/spark/streams';
    return null;
  }

  async function loadAvailableTokens(k: ChartKind, chain: string) {
    if (k === 'transfer' || k === 'exchange_flow') {
      // Use the host-passed transferStreams catalogue — no extra fetch.
      const set = new Set<string>();
      for (const s of transferStreams) {
        if (s.chain !== chain) continue;
        if (typeof s.token === 'string' && s.token.length > 0 && !s.token.startsWith('0x')) {
          set.add(s.token.toUpperCase());
        }
      }
      const arr = Array.from(set).sort();
      availableTokens = arr.length > 0 ? arr : null;
      return;
    }
    const endpoint = streamsEndpointFor(k);
    if (!endpoint) { availableTokens = null; return; }
    const cacheKey = `${endpoint}|${chain}`;
    if (_streamTokenCache.has(cacheKey)) {
      const arr = _streamTokenCache.get(cacheKey)!;
      availableTokens = arr.length > 0 ? arr : null;
      return;
    }
    try {
      const res = await fetch(endpoint);
      if (!res.ok) { availableTokens = null; return; }
      const body = await res.json();
      const streams = (body.streams ?? []) as Array<{ chain?: string; token?: string }>;
      const set = new Set<string>();
      for (const s of streams) {
        if (s.chain && s.chain !== chain) continue;
        // Skip raw contract addresses (some protocols return them when
        // the symbol couldn't be resolved). The dropdown shows symbols
        // only — if a user really wants an address they can edit the
        // chart's token field directly.
        if (typeof s.token === 'string' && s.token.length > 0 && !s.token.startsWith('0x')) {
          set.add(s.token.toUpperCase());
        }
      }
      const arr = Array.from(set).sort();
      _streamTokenCache.set(cacheKey, arr);
      availableTokens = arr.length > 0 ? arr : null;
    } catch {
      availableTokens = null;
    }
  }

  // Reload the available-tokens list whenever the kind or chain changes.
  // When a chain GROUP is selected (e.g. "EVM"), don't hit any single-
  // chain /streams endpoint — fall through to the static roster so the
  // dropdown mirrors what the standalone chart shows.
  $effect(() => {
    if (!pickedKind || !showsTokenField(pickedKind)) { availableTokens = null; return; }
    if (knownChainGroupNames.has(formChain)) { availableTokens = null; return; }
    const c = formChain.trim().toUpperCase();
    if (!c && (pickedKind.startsWith('aave_') || pickedKind.startsWith('morpho_')
               || pickedKind.startsWith('spark_') || pickedKind === 'transfer'
               || pickedKind === 'exchange_flow')) {
      availableTokens = null;
      return;
    }
    loadAvailableTokens(pickedKind, c);
  });

  /** Token-group names that apply to the current kind. Mirrors the
   *  standalone chart's token-dropdown: kinds whose chart header shows the
   *  `Σ <group>` entries also get them in the overlay form. AAVE V4
   *  hardcodes its token roster and shows no groups standalone, so it's
   *  excluded here too. Hyperliquid CeX flows are USDC-only, so groups
   *  are hidden under that path. */
  function applicableTokenGroups(k: ChartKind): TokenGroup[] {
    if (k === 'exchange_flow' && exchangeFlowHLLocked) return [];
    const eligible =
      k === 'transfer' || k === 'exchange_flow'
      || k.startsWith('aave_v2_') || k.startsWith('aave_v3_')
      || k.startsWith('morpho_')  || k.startsWith('spark_');
    if (!eligible) return [];
    return tokenGroups;
  }
  /** Chain-group names that apply to the current kind. Same matrix as
   *  the standalone chart: kinds whose chain dropdown shows `Σ <group>`
   *  entries get them here too. Kinds that are chain-locked (AAVE V4,
   *  Spark, GMX, Aero, HL, Lido L1, exchange_flow under HL) return [].
   *  Pool-model DEX kinds also return [] — the standalone chart doesn't
   *  group them. */
  function applicableChainGroups(k: ChartKind): ChainGroup[] {
    if (chainGroups.length === 0) return [];
    if (effectiveLockedChain(k) !== null) return [];
    const eligible =
      k === 'transfer' || k === 'exchange_flow'
      || k.startsWith('aave_v2_') || k.startsWith('aave_v3_')
      || k.startsWith('morpho_')
      || k === 'lido_l2_deposit' || k === 'lido_l2_withdrawal_request' || k === 'lido_l2_net';
    if (!eligible) return [];
    return chainGroups;
  }
  let knownGroupNames = $derived.by(() => {
    if (!pickedKind) return new Set<string>();
    return new Set(applicableTokenGroups(pickedKind).map((g) => g.name));
  });
  let knownChainGroupNames = $derived.by(() => {
    if (!pickedKind) return new Set<string>();
    return new Set(applicableChainGroups(pickedKind).map((g) => g.name));
  });

  /** Token list to surface in the dropdown.
   *
   *  Preference order:
   *   1. Stream-derived tokens (real data on this chain) when available.
   *   2. Host-page `tokens` prop (binance roster for Exchange/HL kinds).
   *   3. Small built-in fallback so the dropdown is never empty.
   *
   *  The current formToken is always appended so a custom entry survives
   *  edit-mode even if it's not in any catalogue. Server-defined token
   *  groups (USDC+USDT, Stables, …) appear at the bottom for the kinds
   *  that support them. */
  function tokenOptions(): { kind: 'token' | 'group'; value: string; label: string }[] {
    let base: string[];
    if (availableTokens && availableTokens.length > 0) base = availableTokens.slice();
    else if (tokens.length > 0) base = tokens.slice();
    else base = ['BTC', 'ETH', 'USDC', 'USDT', 'DAI', 'WETH', 'WBTC'];
    if (formToken && !base.includes(formToken) && !knownGroupNames.has(formToken)) base.push(formToken);
    const out: { kind: 'token' | 'group'; value: string; label: string }[] = base.map((t) => ({ kind: 'token', value: t, label: t }));
    if (pickedKind) {
      for (const g of applicableTokenGroups(pickedKind)) {
        out.push({ kind: 'group', value: g.name, label: `Σ ${g.label}` });
      }
    }
    return out;
  }

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
    // Token / chain / exchange are written whether the field is interactive
    // OR pinned via a lock helper — the locked value still has to land on
    // the overlay's persisted state so the fetch issues the correct query.
    const lt = effectiveLockedToken(k);
    if (lt) {
      o.token = lt;
    } else if (tokenFieldKindUsesIt(k)) {
      // When the selection matches a known server token-group, persist as
      // tokenGroup (so the fetch sends ?token_group=...); otherwise as
      // a single token symbol.
      const sel = formToken.trim();
      if (knownGroupNames.has(sel)) {
        o.tokenGroup = sel;
      } else {
        o.token = sel.toUpperCase();
      }
    }
    const lc = effectiveLockedChain(k);
    if (lc) {
      o.chain = lc;
    } else if (chainFieldKindUsesIt(k)) {
      // When the selection matches a known server chain-group, persist as
      // chainGroup (so the fetch sends ?chain_group=...); otherwise as a
      // single chain symbol. Group names are server-defined and keep
      // their original casing.
      const sel = formChain.trim();
      if (knownChainGroupNames.has(sel)) {
        o.chainGroup = sel;
      } else {
        o.chain = sel.toUpperCase();
      }
    }
    const le = lockedExchange(k);
    if (le) o.exchange = le;
    else if (exchangeFieldKindUsesIt(k)) o.exchange = formExchange;
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
          {#if pickedKind && tokenFieldKindUsesIt(pickedKind)}
            {#if effectiveLockedToken(pickedKind)}
              <div class="flex items-center gap-2">
                <span class="w-32 text-zinc-400">Token</span>
                <span class="text-zinc-300 text-xs px-2 py-1 rounded bg-zinc-900 border border-zinc-700 font-mono">{effectiveLockedToken(pickedKind)}</span>
              </div>
            {:else}
              <label class="flex items-center gap-2">
                <span class="w-32 text-zinc-400">Token</span>
                <select bind:value={formToken} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono">
                  {#each tokenOptions() as opt (opt.value + ':' + opt.kind)}
                    <option value={opt.value}>{opt.label}</option>
                  {/each}
                </select>
              </label>
            {/if}
          {/if}
          {#if pickedKind && chainFieldKindUsesIt(pickedKind)}
            {#if effectiveLockedChain(pickedKind)}
              <div class="flex items-center gap-2">
                <span class="w-32 text-zinc-400">Chain</span>
                <span class="text-zinc-300 text-xs px-2 py-1 rounded bg-zinc-900 border border-zinc-700 font-mono">{effectiveLockedChain(pickedKind)}</span>
              </div>
            {:else}
              <label class="flex items-center gap-2">
                <span class="w-32 text-zinc-400">Chain</span>
                <select bind:value={formChain} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono">
                  {#if applicableChainGroups(pickedKind).length > 0}
                    <optgroup label="Chain">
                      {#each chainOptions(pickedKind) as c (c)}
                        <option value={c}>{c}</option>
                      {/each}
                    </optgroup>
                    <optgroup label="Chain group">
                      {#each applicableChainGroups(pickedKind) as g (g.name)}
                        <option value={g.name} title={g.description}>Σ {g.label}</option>
                      {/each}
                    </optgroup>
                  {:else}
                    {#each chainOptions(pickedKind) as c (c)}
                      <option value={c}>{c}</option>
                    {/each}
                  {/if}
                  {#if formChain && !chainOptions(pickedKind).includes(formChain) && !knownChainGroupNames.has(formChain)}
                    <option value={formChain}>{formChain}</option>
                  {/if}
                </select>
              </label>
            {/if}
          {/if}
          {#if pickedKind && exchangeFieldKindUsesIt(pickedKind)}
            {#if effectiveLockedExchange(pickedKind)}
              <div class="flex items-center gap-2">
                <span class="w-32 text-zinc-400">Exchange</span>
                <span class="text-zinc-300 text-xs px-2 py-1 rounded bg-zinc-900 border border-zinc-700 font-mono">{effectiveLockedExchange(pickedKind) === 'hl' ? 'Hyperliquid' : 'Binance'}</span>
              </div>
            {:else}
              <label class="flex items-center gap-2">
                <span class="w-32 text-zinc-400">Exchange</span>
                <select bind:value={formExchange} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1">
                  <option value="binance">Binance</option>
                  <option value="hl">Hyperliquid</option>
                </select>
              </label>
            {/if}
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
              {#if uniPoolsForChain.length > 0}
                <select
                  value={currentPoolKey}
                  onchange={(e) => onPoolPick((e.target as HTMLSelectElement).value)}
                  class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono"
                >
                  {#each uniPoolsForChain as p (poolKey(p))}
                    <option value={poolKey(p)}>{fmtUniPool(p)}</option>
                  {/each}
                  {#if !uniPoolsForChain.some((p) => poolKey(p) === currentPoolKey)}
                    <option value={currentPoolKey}>{fmtUniPool({ symbol0: formPoolSym0, symbol1: formPoolSym1, fee: formPoolFee })}</option>
                  {/if}
                </select>
              {:else}
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
              {#if gmxMarketsForOverlay.length > 0}
                <select bind:value={formGmxMarket} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono">
                  <option value="">Σ All markets</option>
                  {#each gmxMarketsForOverlay as m (m)}
                    <option value={m}>{m}</option>
                  {/each}
                  {#if formGmxMarket && !gmxMarketsForOverlay.includes(formGmxMarket)}
                    <option value={formGmxMarket}>{formGmxMarket}</option>
                  {/if}
                </select>
              {:else}
                <input type="text" bind:value={formGmxMarket} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 font-mono" />
              {/if}
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
