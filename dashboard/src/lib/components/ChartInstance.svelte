<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import LineChart from '$lib/components/LineChart.svelte';
  import TableChart from '$lib/components/TableChart.svelte';
  import HlTopPositionsChart from '$lib/components/HlTopPositionsChart.svelte';
  import SignedBarChart from '$lib/components/SignedBarChart.svelte';
  import { onMount } from 'svelte';
  import {
    INTERVALS,
    type Candle,
    type FundingRateRow,
    type Interval,
    type LongShortRow,
    type OpenInterestRow,
    type TransferBucket,
    type TransferStream,
    type UniswapStream,
    type WalletCategory,
    type VolumeBucket
  } from '$lib/api';
  import {
    BUYER_SELLER_LINES,
    BUYER_SELLER_SERIES,
    CHART_KIND_LABELS,
    LS_LINES,
    MA_COLORS,
    NEUTRAL_REF,
    OI_LINES,
    SIZE_CYCLE,
    TOP_TRADERS_LINES,
    defaultView,
    fmtAmountAxis,
    fmtAmountTooltip,
    fmtUsdAxis,
    fmtUsdTooltip,
    fmtUsdCompact,
    lookbackWindow,
    maArray,
    sizeLines,
    sizeSeries,
    unixSec,
    weekBoundariesSec,
    AAVE_KIND_TO_EVENT,
    AAVE_NET_KIND_TO_EVENTS,
    isAaveKind,
    AAVE_V2_KIND_TO_EVENT,
    AAVE_V2_NET_KIND_TO_EVENTS,
    isAaveV2Kind,
    AAVE_V4_KIND_TO_EVENT,
    AAVE_V4_NET_KIND_TO_EVENTS,
    isAaveV4Kind,
    MORPHO_KIND_TO_EVENT,
    MORPHO_NET_KIND_TO_EVENTS,
    isMorphoKind,
    SPARK_KIND_TO_EVENT,
    SPARK_NET_KIND_TO_EVENTS,
    isSparkKind,
    GMX_KIND_TO_EVENT,
    GMX_NET_KIND_TO_EVENTS,
    GMX_PRIMARY_FIELD,
    isGmxKind,
    HL_KIND_TO_EVENT,
    HL_PRIMARY_FIELD,
    isHlKind,
    UNISWAP_KIND_TO_EVENT,
    UNISWAP_NET_KIND_TO_EVENTS,
    isUniswapKind,
    UNISWAP_V2_KIND_TO_EVENT,
    UNISWAP_V2_NET_KIND_TO_EVENTS,
    isUniswapV2Kind,
    UNISWAP_V4_KIND_TO_EVENT,
    UNISWAP_V4_NET_KIND_TO_EVENTS,
    isUniswapV4Kind,
    AERO_KIND_TO_EVENT,
    AERO_NET_KIND_TO_EVENTS,
    isAeroKind,
    AERO_BASIC_KIND_TO_EVENT,
    AERO_BASIC_NET_KIND_TO_EVENTS,
    isAeroBasicKind,
    fmtUniPool,
    LIDO_KIND_TO_EVENT,
    LIDO_NET_KIND_TO_EVENTS,
    LIDO_L1_KINDS,
    isLidoKind,
    type ChartHeight,
    type ChartInstance as ChartInstanceT,
    type ChartWidth,
    type TransferFilters,
    type UniPool
  } from '$lib/components/charts/config';
  import type { View } from '$lib/chart-zoom';
  import { queuedFetch } from '$lib/fetch-queue';

  // Module-scope cache survives component remounts. svelte-dnd-action
  // destroys and recreates the chart component when its DOM node moves
  // (drag-drop reorder), which would otherwise reset loadedKey and trigger
  // a fresh fetch for data that hasn't actually changed. Keyed by
  // `instance.id` (stable per chart) + cache invalidates whenever loadKey
  // differs, so any real change still re-fetches.
  type LoadCacheEntry = {
    key: string;
    data: AnyDatum[];
    since: string;
    until: string;
    localView: View;
    overlayData?: Record<string, Candle[]>;
  };
  const loadCache: Map<string, LoadCacheEntry> = new Map();

  type AnyDatum =
    | Candle
    | OpenInterestRow
    | FundingRateRow
    | LongShortRow
    | VolumeBucket
    | TransferBucket;

  let {
    instance = $bindable(),
    tokens,
    streams = [],
    uniPools = [],
    lidoChains = [],
    gmxMarkets = [],
    tokenGroups = [],
    chainGroups = [],
    syncZoom,
    sharedView,
    sharedHoverTime,
    onSharedView,
    onSharedHover,
    onTokenChange,
    onRemove,
    onSwap
  }: {
    instance: ChartInstanceT;
    tokens: string[];
    streams?: TransferStream[];
    uniPools?: UniswapStream[];
    lidoChains?: { event: string; chain: string; rows: number }[];
    gmxMarkets?: { event: string; chain: string; market: string; rows: number }[];
    tokenGroups?: import('$lib/api').TokenGroup[];
    chainGroups?: import('$lib/api').ChainGroup[];
    syncZoom: boolean;
    sharedView: View;
    sharedHoverTime: number | null;
    onSharedView: (v: View) => void;
    onSharedHover: (t: number | null) => void;
    onTokenChange: (id: string, token: string) => void;
    onRemove: (id: string) => void;
    onSwap: (id: string, ev: MouseEvent) => void;
  } = $props();

  // ---- uniswap-kind helpers (derived from `uniPools`) ----
  // `uniPools` is the response from /uniswap/streams: one row per
  // (event, chain, symbol0, symbol1, fee_tier). We collapse to a unique-
  // pool set so the selector doesn't show the same WETH/USDC 0.05% four
  // times (once per event).
  let uniChains = $derived(
    Array.from(new Set(uniPools.map((p) => p.chain))).sort()
  );
  // Per-chain map of unique pools, ordered by total row-count (popular pools
  // float to the top of the dropdown).
  let uniPoolsByChain = $derived.by(() => {
    const out = new Map<string, UniPool[]>();
    const counts = new Map<string, number>();
    const dedup = new Map<string, UniPool>();
    for (const p of uniPools) {
      const k = `${p.chain}|${p.symbol0}|${p.symbol1}|${p.fee_tier}`;
      counts.set(k, (counts.get(k) ?? 0) + p.rows);
      if (!dedup.has(k)) {
        dedup.set(k, { symbol0: p.symbol0, symbol1: p.symbol1, fee: p.fee_tier });
      }
    }
    // Group by chain, sort each group by total rows desc.
    const grouped = new Map<string, { pool: UniPool; rows: number }[]>();
    for (const [k, pool] of dedup) {
      const chain = k.split('|', 1)[0];
      if (!grouped.has(chain)) grouped.set(chain, []);
      grouped.get(chain)!.push({ pool, rows: counts.get(k) ?? 0 });
    }
    for (const [chain, list] of grouped) {
      list.sort((a, b) => b.rows - a.rows);
      out.set(chain, list.map((x) => x.pool));
    }
    return out;
  });
  let uniPoolsForChain = $derived(uniPoolsByChain.get(instance.chain ?? '') ?? []);
  // Auto-snap pool when the chain narrows and the current pool isn't on it.
  $effect(() => {
    if (!isUniswapKind(instance.kind)) return;
    const list = uniPoolsForChain;
    if (list.length === 0) return;
    const current = instance.uniPool;
    if (
      current &&
      list.some(
        (p) => p.symbol0 === current.symbol0 && p.symbol1 === current.symbol1 && p.fee === current.fee
      )
    ) return;
    instance.uniPool = list[0];
  });

  // Encode/decode (symbol0|symbol1|fee) as a single string for the <select>
  // bind, since Svelte's <select> doesn't preserve object identity across
  // value changes.
  function uniPoolKey(p: UniPool | undefined): string {
    return p ? `${p.symbol0}|${p.symbol1}|${p.fee}` : '';
  }
  let currentUniPoolKey = $derived(uniPoolKey(instance.uniPool));
  function onUniPoolChange(v: string) {
    const [s0, s1, fee] = v.split('|');
    if (!s0 || !s1 || !fee) return;
    instance.uniPool = { symbol0: s0, symbol1: s1, fee: Number(fee) };
  }

  // ---- lido-kind helpers (derived from `lidoChains`) ----
  // L1 kinds are ETH-pinned; L2 kinds get the dropdown of L2 chains that
  // actually have data for THIS kind's underlying event. Returns the
  // chain list to surface in the selector — empty (fallback) goes to the
  // hard-coded L2 list so the dropdown still works pre-backfill.
  const _L2_FALLBACK = ['ARB', 'BASE', 'OP', 'ZK', 'MANTLE', 'MODE', 'SONEIUM', 'UNI', 'ZIRCUIT'];
  let lidoChainsForKind = $derived.by<string[]>(() => {
    if (!isLidoKind(instance.kind)) return [];
    if (LIDO_L1_KINDS.has(instance.kind)) return ['ETH'];
    // L2 kinds: filter streams to the relevant event(s) for the kind.
    const targetEvents: string[] = (() => {
      const single = LIDO_KIND_TO_EVENT[instance.kind];
      if (single) return [single];
      const net = LIDO_NET_KIND_TO_EVENTS[instance.kind];
      return net ? [net[0], net[1]] : [];
    })();
    const seen = new Set<string>();
    for (const s of lidoChains) {
      if (!targetEvents.includes(s.event)) continue;
      if (s.chain === 'ETH') continue;
      seen.add(s.chain);
    }
    const list = seen.size > 0 ? Array.from(seen).sort() : _L2_FALLBACK;
    return list;
  });
  // Auto-snap when the chosen chain isn't in the available list. A chain
  // group selection (e.g. 'EVM') counts as valid — skip the snap so the
  // user's compound choice survives a chains-list refetch.
  $effect(() => {
    if (!isLidoKind(instance.kind)) return;
    const list = lidoChainsForKind;
    if (list.length === 0) return;
    if (LIDO_L1_KINDS.has(instance.kind)) {
      if (instance.chain !== 'ETH') instance.chain = 'ETH';
      return;
    }
    if (activeChainGroup) return;
    if (!instance.chain || !list.includes(instance.chain)) {
      instance.chain = list[0];
    }
  });

  // ---- GMX-kind helpers (derived from `gmxMarkets`) ----
  // `gmxMarkets` is the response from /gmx/streams: one row per (event,
  // chain, market_name). For the selector we collapse to unique markets
  // ranked by total rows so the most-active perp floats to the top.
  let gmxMarketsForKind = $derived.by<{ market: string; rows: number }[]>(() => {
    if (!isGmxKind(instance.kind)) return [];
    // Identify which underlying events the kind reads from.
    const evs: string[] = (() => {
      const single = GMX_KIND_TO_EVENT[instance.kind];
      if (single) return [single];
      const net = GMX_NET_KIND_TO_EVENTS[instance.kind];
      return net ? [net[0], net[1]] : [];
    })();
    const chain = instance.chain ?? 'ARB';
    // Sum rows across all relevant events per market, then drop any unresolved
    // `?/USD` markets (server-side index-token resolution failures — not
    // useful to chart on).
    const totals = new Map<string, number>();
    for (const r of gmxMarkets) {
      if (r.chain !== chain) continue;
      if (!evs.includes(r.event)) continue;
      if (!r.market || r.market.startsWith('?')) continue;
      totals.set(r.market, (totals.get(r.market) ?? 0) + r.rows);
    }
    return Array.from(totals.entries())
      .map(([market, rows]) => ({ market, rows }))
      .sort((a, b) => b.rows - a.rows);
  });

  // ---- transfer-kind helpers (derived from `streams`) ----
  let chains = $derived(
    Array.from(new Set(streams.map((s) => s.chain))).sort()
  );
  // Two orthogonal axes can each be a singleton OR a server-defined group:
  //   - `instance.chain` ∈ {real chain names} ∪ {chain group names}
  //   - `instance.token` ∈ {real token names} ∪ {token group names}
  // When either holds a group name, the chart fetches via ?chain_group= /
  // ?token_group=; otherwise the legacy single-stream path.
  let chainGroupByName = $derived(new Map(chainGroups.map((g) => [g.name, g])));
  let tokenGroupNames = $derived(new Set(tokenGroups.map((g) => g.name)));
  let activeChainGroup = $derived(chainGroupByName.get(instance.chain ?? '') ?? null);
  let activeTokenGroup = $derived(tokenGroupNames.has(instance.token) ? instance.token : null);
  // Chains the token dropdown should reflect: a single chain when chain is a
  // real chain, the union across the group's members otherwise.
  let resolvedChainSet = $derived(
    activeChainGroup
      ? new Set(activeChainGroup.chains)
      : new Set([instance.chain ?? ''])
  );
  let tokensForChain = $derived(
    Array.from(
      new Set(streams.filter((s) => resolvedChainSet.has(s.chain)).map((s) => s.token))
    ).sort()
  );
  let transferKind = $derived(
    streams.find((s) => s.chain === instance.chain && s.token === instance.token)?.kind ?? 'erc20'
  );
  // Auto-snap token when the chain narrows and current token isn't reachable.
  // Skip when a token group is selected (groups span chains by design).
  $effect(() => {
    if (instance.kind !== 'transfer') return;
    if (activeTokenGroup !== null) return;
    if (tokensForChain.length > 0 && !tokensForChain.includes(instance.token)) {
      instance.token = tokensForChain[0];
    }
  });

  // Wallet-category + entity catalogues (for filter input <datalist> suggestions).
  let walletCategories = $state<WalletCategory[]>([]);
  let walletEntities = $state<WalletCategory[]>([]);
  onMount(async () => {
    if (instance.kind !== 'transfer') return;
    try {
      const [catsRes, entsRes] = await Promise.all([
        queuedFetch('/api/transfers/categories'),
        queuedFetch('/api/transfers/entities')
      ]);
      if (catsRes.ok) walletCategories = (await catsRes.json()).categories ?? [];
      if (entsRes.ok) walletEntities = (await entsRes.json()).entities ?? [];
    } catch {
      // ignore — inputs still work without autocomplete
    }
  });

  // ---- transfer "extra series" form state ----
  type FilterKey =
    | 'sender_in' | 'sender_ex' | 'sender_all_in'
    | 'receiver_in' | 'receiver_ex' | 'receiver_all_in'
    | 'involving_in' | 'involving_ex' | 'involving_all_in'
    | 'sender_entity_in' | 'sender_entity_ex'
    | 'receiver_entity_in' | 'receiver_entity_ex'
    | 'involving_entity_in' | 'involving_entity_ex'
    | 'sender_addr_in' | 'sender_addr_ex'
    | 'receiver_addr_in' | 'receiver_addr_ex'
    | 'involving_addr_in' | 'involving_addr_ex';
  const CAT_FILTER_KEYS: FilterKey[] = [
    'sender_in', 'sender_ex', 'sender_all_in',
    'receiver_in', 'receiver_ex', 'receiver_all_in',
    'involving_in', 'involving_ex', 'involving_all_in'
  ];
  const ENT_FILTER_KEYS: FilterKey[] = [
    'sender_entity_in', 'sender_entity_ex',
    'receiver_entity_in', 'receiver_entity_ex',
    'involving_entity_in', 'involving_entity_ex'
  ];
  const ADDR_FILTER_KEYS: FilterKey[] = [
    'sender_addr_in', 'sender_addr_ex',
    'receiver_addr_in', 'receiver_addr_ex',
    'involving_addr_in', 'involving_addr_ex'
  ];
  const FILTER_KEYS: FilterKey[] = [...CAT_FILTER_KEYS, ...ENT_FILTER_KEYS, ...ADDR_FILTER_KEYS];
  const EMPTY_PENDING: Record<FilterKey, string> = {
    sender_in: '',
    sender_ex: '',
    sender_all_in: '',
    receiver_in: '',
    receiver_ex: '',
    receiver_all_in: '',
    involving_in: '',
    involving_ex: '',
    involving_all_in: '',
    sender_entity_in: '',
    sender_entity_ex: '',
    receiver_entity_in: '',
    receiver_entity_ex: '',
    involving_entity_in: '',
    involving_entity_ex: '',
    sender_addr_in: '',
    sender_addr_ex: '',
    receiver_addr_in: '',
    receiver_addr_ex: '',
    involving_addr_in: '',
    involving_addr_ex: ''
  };
  let pendingFilter = $state<Record<FilterKey, string>>({ ...EMPTY_PENDING });

  function parseFilterCsv(s: string): string[] {
    return s
      .split(',')
      .map((v) => v.trim())
      .filter((v) => v.length > 0);
  }
  function joinFilterCsv(arr: string[] | undefined): string {
    return (arr ?? []).join(', ');
  }
  function buildPendingFilterSet(): TransferFilters {
    const next: TransferFilters = {};
    for (const k of FILTER_KEYS) {
      const arr = parseFilterCsv(pendingFilter[k as FilterKey]);
      if (arr.length) next[k] = arr;
    }
    return next;
  }
  /** Snake-case structured name: involving sections first, then from_ / to_
   *  sections. Each section is `[not_]<prefix>_<cat1+cat2…>`. Sections are
   *  joined with `_`. Example: sender_ex=CEX & receiver_in=Deposit
   *  → `from_not_CEX_to_Deposit`. */
  function autoNameFromFilters(f: TransferFilters): string {
    const parts: string[] = [];
    if (f.involving_in?.length) parts.push(`involving_${f.involving_in.join('+')}`);
    if (f.involving_ex?.length) parts.push(`not_involving_${f.involving_ex.join('+')}`);
    if (f.involving_all_in?.length) parts.push(`involving_all_${f.involving_all_in.join('+')}`);
    if (f.sender_in?.length) parts.push(`from_${f.sender_in.join('+')}`);
    if (f.sender_ex?.length) parts.push(`from_not_${f.sender_ex.join('+')}`);
    if (f.sender_all_in?.length) parts.push(`from_all_${f.sender_all_in.join('+')}`);
    if (f.receiver_in?.length) parts.push(`to_${f.receiver_in.join('+')}`);
    if (f.receiver_ex?.length) parts.push(`to_not_${f.receiver_ex.join('+')}`);
    if (f.receiver_all_in?.length) parts.push(`to_all_${f.receiver_all_in.join('+')}`);
    if (f.involving_entity_in?.length) parts.push(`involving_ent_${f.involving_entity_in.join('+')}`);
    if (f.involving_entity_ex?.length) parts.push(`not_involving_ent_${f.involving_entity_ex.join('+')}`);
    if (f.sender_entity_in?.length) parts.push(`from_ent_${f.sender_entity_in.join('+')}`);
    if (f.sender_entity_ex?.length) parts.push(`from_not_ent_${f.sender_entity_ex.join('+')}`);
    if (f.receiver_entity_in?.length) parts.push(`to_ent_${f.receiver_entity_in.join('+')}`);
    if (f.receiver_entity_ex?.length) parts.push(`to_not_ent_${f.receiver_entity_ex.join('+')}`);
    return parts.join('_');
  }

  // When instance.filter changes (e.g. layout restore), seed the form text.
  $effect(() => {
    if (instance.kind !== 'transfer') return;
    const f = instance.filter ?? {};
    const next: Record<FilterKey, string> = { ...EMPTY_PENDING };
    for (const k of FILTER_KEYS) {
      next[k] = joinFilterCsv((f as Record<string, string[] | undefined>)[k]);
    }
    pendingFilter = next;
  });

  let activeFilter = $derived(instance.filter ?? {});
  let activeFilterIsAny = $derived(FILTER_KEYS.some((k) => (activeFilter[k] ?? []).length > 0));
  let activeFilterLabel = $derived(autoNameFromFilters(activeFilter));
  let pendingDiffers = $derived.by(() => {
    for (const k of FILTER_KEYS) {
      const live = (activeFilter[k] ?? []).join('\x00');
      const pend = parseFilterCsv(pendingFilter[k as FilterKey]).join('\x00');
      if (live !== pend) return true;
    }
    return false;
  });

  function applyFilter() {
    instance.filter = buildPendingFilterSet();
  }
  function clearFilter() {
    pendingFilter = { ...EMPTY_PENDING };
    instance.filter = {};
  }

  // ---- transient state (not persisted) ----
  let data = $state<AnyDatum[]>([]);
  // Overlay token candle arrays, keyed by token symbol. Populated for the
  // pc (Price Comparison) chart kind from each entry in
  // instance.overlayTokens. Each value is the same Candle[] shape as the
  // main `data` array; we rebase to % below for plotting.
  let overlayData = $state<Record<string, Candle[]>>({});
  let since = $state<string>(new Date(0).toISOString());
  let until = $state<string>(new Date(0).toISOString());
  let loadedKey = $state<string>('');
  // True while a load() is in flight — drives the indeterminate progress
  // strip in the chart header so the user gets feedback when changing
  // chain / token / interval / filter takes more than a tick.
  let loading = $state(false);
  // Abort handle for the currently-active load(). When a fresh load() starts
  // (selector change, refresh button) we abort this so the prior fetch frees
  // its queue slot immediately instead of dragging the queue with stale work.
  let currentLoad: AbortController | null = null;
  let localView = $state<View>(null);
  let localHoverTime = $state<number | null>(null);
  let error = $state<string | null>(null);

  // ---- effective view + hoverTime ----
  let effectiveView = $derived(syncZoom ? sharedView : localView);
  let effectiveHoverTime = $derived(syncZoom ? sharedHoverTime : localHoverTime);

  function handleView(v: View) {
    if (syncZoom) onSharedView(v);
    else localView = v;
  }
  function handleHover(t: number | null) {
    if (syncZoom) onSharedHover(t);
    else localHoverTime = t;
  }

  let xExtent = $derived<[number, number]>([unixSec(since), unixSec(until)]);
  // Optional week-marker overlay: dotted vertical lines at the start of each
  // Saturday and Monday (UTC) inside the loaded window. Skipped when the
  // toggle is off so we don't waste CPU computing boundaries.
  //   - zinc-400 (#a1a1aa) so the lines actually read against the dark chart
  //     background — chart-grid (zinc-700) was too dim to see.
  //   - dash "2,4" + width 1 keeps them narrow / clearly "annotation"-style
  //     rather than competing with the data series.
  let weekVRefLines = $derived(
    instance.showWeekLines
      ? weekBoundariesSec(xExtent[0], xExtent[1]).map((t) => ({
          time: t,
          // 8-char hex for alpha — zinc-400 at ~33% opacity. SVG stroke
          // accepts #RRGGBBAA so we don't need a separate opacity prop on
          // the chart components.
          color: '#a1a1aa55',
          dash: '2,4'
        }))
      : []
  );

  // ---- loader: dispatch on kind ----

  function transferFilterKey(): string {
    const f = instance.filter ?? {};
    const main = FILTER_KEYS.map((k) => (f[k as FilterKey] ?? []).join(',')).join('|');
    // Netflow templates have two filter sets — fold both into the cache key
    // so switching CeX variants busts correctly.
    if (instance.netFilter) {
      const p = instance.netFilter.positive;
      const n = instance.netFilter.negative;
      const pPart = FILTER_KEYS.map((k) => (p[k as FilterKey] ?? []).join(',')).join('|');
      const nPart = FILTER_KEYS.map((k) => (n[k as FilterKey] ?? []).join(',')).join('|');
      return `${main}|net+:${pPart}|net-:${nPart}`;
    }
    return main;
  }

  function loadKey(): string {
    if (instance.kind === 'sz') {
      const ex = instance.exchange ?? 'binance';
      return `${instance.kind}|${instance.token}|${ex}|${instance.interval}|${instance.under ?? 0}|${instance.over ?? 0}`;
    }
    if (instance.kind === 'transfer') {
      // Key encodes whether each axis is singleton or group so cache busts
      // when the user toggles between e.g. ETH-USDC and EVM-USDC.
      const cPart = activeChainGroup ? `cg:${activeChainGroup.name}` : (instance.chain ?? '');
      const tPart = activeTokenGroup !== null ? `tg:${activeTokenGroup}` : instance.token;
      return `${instance.kind}|${cPart}|${tPart}|${instance.interval}|${transferFilterKey()}`;
    }
    if (instance.kind === 'pc') {
      // Overlay tokens influence the rendered chart, so they belong in the
      // cache key. Sorted so order-of-add doesn't bust the key.
      const ov = [...(instance.overlayTokens ?? [])].sort().join(',');
      return `${instance.kind}|${instance.token}|${instance.interval}|ov:${ov}`;
    }
    if (isAaveKind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind)) {
      // AAVE charts (single-event + net) depend on chain + token (event_type
      // derived from kind). Either axis may be a group name — fold the
      // group flag into the key so toggling busts the cache. Same shape
      // for AAVE V2 (different endpoint, identical key shape).
      const cPart = activeChainGroup ? `cg:${activeChainGroup.name}` : (instance.chain ?? '');
      const tPart = activeTokenGroup !== null ? `tg:${activeTokenGroup}` : instance.token;
      return `${instance.kind}|${cPart}|${tPart}|${instance.interval}`;
    }
    if (isGmxKind(instance.kind)) {
      // GMX charts depend on chain (ARB-only for now) + market_name selector.
      // Empty market = "all markets summed" — folded into the key so toggling
      // busts the cache.
      const cPart = instance.chain ?? 'ARB';
      const mPart = instance.gmxMarket ? `m:${instance.gmxMarket}` : 'all';
      return `${instance.kind}|${cPart}|${mPart}|${instance.interval}`;
    }
    if (isHlKind(instance.kind)) {
      // HL: per-token + optional wallet OR wallet_category filter (mutually
      // exclusive). Empty wallet filter = aggregate across all traders.
      const wPart = instance.hlWallet
        ? `w:${instance.hlWallet.toLowerCase()}`
        : (instance.hlWalletCategory ? `wc:${instance.hlWalletCategory}` : 'all');
      return `${instance.kind}|${instance.token}|${wPart}|${instance.interval}`;
    }
    if (isUniswapKind(instance.kind) || isUniswapV2Kind(instance.kind)) {
      // Uniswap V2/V3 charts: pool keyed by (sym0, sym1, fee) — fee=0 marks V2.
      const cPart = instance.chain ?? '';
      const pPart = uniPoolKey(instance.uniPool);
      return `${instance.kind}|${cPart}|${pPart}|${instance.interval}`;
    }
    if (isUniswapV4Kind(instance.kind)) {
      // V4 pool keyed by 5-tuple (sym0, sym1, fee, tick_spacing, hooks).
      const p = instance.uniV4Pool;
      const pPart = p ? `${p.symbol0}|${p.symbol1}|${p.fee}|${p.tick_spacing}|${p.hooks}` : '';
      return `${instance.kind}|${instance.chain ?? ''}|${pPart}|${instance.interval}`;
    }
    if (isAeroKind(instance.kind)) {
      const p = instance.aeroPool;
      const pPart = p ? `${p.symbol0}|${p.symbol1}|${p.tick_spacing}` : '';
      return `${instance.kind}|${instance.chain ?? 'BASE'}|${pPart}|${instance.interval}`;
    }
    if (isAeroBasicKind(instance.kind)) {
      const p = instance.aeroBasicPool;
      const pPart = p ? `${p.symbol0}|${p.symbol1}|${p.stable ? 's' : 'v'}` : '';
      return `${instance.kind}|${instance.chain ?? 'BASE'}|${pPart}|${instance.interval}`;
    }
    if (isLidoKind(instance.kind)) {
      // Lido charts are keyed by (kind, chain | chain_group, interval). L1
      // kinds are ETH-pinned but we include the axis anyway. The cg: prefix
      // makes "EVM" (group) cache-distinct from a literal "EVM" chain name.
      const cPart = activeChainGroup
        ? `cg:${activeChainGroup.name}`
        : (instance.chain ?? '');
      return `${instance.kind}|${cPart}|${instance.interval}`;
    }
    if (instance.kind === 'ohlcv' || instance.kind === 'fr' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'oi') {
      // Exchange selector busts the cache so flipping Binance ↔ HL re-fetches.
      const ex = instance.exchange ?? 'binance';
      return `${instance.kind}|${instance.token}|${ex}|${instance.interval}`;
    }
    return `${instance.kind}|${instance.token}|${instance.interval}`;
  }

  $effect(() => {
    const key = loadKey();
    if (key === loadedKey) return;
    // Remount fast-path: if we previously loaded the exact same key for this
    // chart id (e.g. the user just drag-reordered and svelte-dnd-action
    // recreated the component), restore from cache and skip the fetch.
    const cached = loadCache.get(instance.id);
    if (cached && cached.key === key) {
      data = cached.data;
      overlayData = cached.overlayData ?? {};
      since = cached.since;
      until = cached.until;
      localView = cached.localView;
      loadedKey = key;
      return;
    }
    void load();
  });

  /** Single fetch — the main line IS the (possibly filtered) series. The six
   *  legacy single-filter params on /transfers/aggregate apply pre-aggregation
   *  in CH, so the returned `sum_amount` is the filtered sum. MAs then compute
   *  from those filtered values automatically. */
  /** Build the chain/kind/token + chain_group/token_group portion of the
   *  query string — shared between the single-filter and netflow paths. */
  function transferBaseQS(sinceIso: string, untilIso: string): URLSearchParams {
    const qs = new URLSearchParams({
      interval: instance.interval,
      since: sinceIso,
      until: untilIso,
      limit: '10000'
    });
    if (activeChainGroup) {
      qs.set('chain_group', activeChainGroup.name);
    } else {
      qs.set('chain', instance.chain ?? 'ETH');
    }
    if (activeTokenGroup !== null) {
      qs.set('token_group', activeTokenGroup);
    } else {
      qs.set('token', instance.token);
      if (!activeChainGroup) qs.set('kind', transferKind);
    }
    return qs;
  }

  async function loadTransferMerged(
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal,
    forceFresh = false
  ) {
    // Netflow path: two parallel fetches (the two locked filter sets) merged
    // by time bucket. Net = positive.sum_value_usd − negative.sum_value_usd.
    if (instance.netFilter) {
      const buildQS = (filter: TransferFilters) => {
        const qs = transferBaseQS(sinceIso, untilIso);
        for (const k of FILTER_KEYS) {
          const arr = filter[k as FilterKey] ?? [];
          if (arr.length) qs.set(k, arr.join(','));
        }
        if (forceFresh) qs.set('fresh', '1');
        return qs;
      };
      const [posRes, negRes] = await Promise.all([
        queuedFetch(`/api/transfers/aggregate?${buildQS(instance.netFilter.positive)}`, { signal }),
        queuedFetch(`/api/transfers/aggregate?${buildQS(instance.netFilter.negative)}`, { signal })
      ]);
      if (!posRes.ok) throw new Error(`transfers inflow ${posRes.status}`);
      if (!negRes.ok) throw new Error(`transfers outflow ${negRes.status}`);
      const posBody = await posRes.json();
      const negBody = await negRes.json();
      const negByTime = new Map<number, { amount: number; usd: number }>();
      for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
        negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd });
      }
      const out: Record<string, number>[] = [];
      const seen = new Set<number>();
      for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
        const n = negByTime.get(r.time) ?? { amount: 0, usd: 0 };
        out.push({
          time: r.time,
          sum_amount: r.sum_amount - n.amount,
          sum_value_usd: r.sum_value_usd - n.usd,
          count: r.count
        });
        seen.add(r.time);
      }
      // Buckets present only in the outflow series → net is negative.
      for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
        if (seen.has(r.time)) continue;
        out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
      }
      out.sort((a, b) => a.time - b.time);
      data = out as unknown as AnyDatum[];
      return;
    }

    // Single-filter path (legacy).
    const qs = transferBaseQS(sinceIso, untilIso);
    const f = instance.filter ?? {};
    for (const k of FILTER_KEYS) {
      const arr = f[k as FilterKey] ?? [];
      if (arr.length) qs.set(k, arr.join(','));
    }
    if (forceFresh) qs.set('fresh', '1');
    const res = await queuedFetch(`/api/transfers/aggregate?${qs}`, { signal });
    if (!res.ok) throw new Error(`transfers ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    const out: Record<string, number>[] = rows.map((b) => ({
      time: b.time,
      sum_amount: b.sum_amount,
      sum_value_usd: b.sum_value_usd,
      count: b.count
    }));
    data = out as unknown as AnyDatum[];
  }

  async function load(forceFresh = false) {
    // Cancel any prior load so it frees its queue slot immediately.
    if (currentLoad) currentLoad.abort();
    const controller = new AbortController();
    currentLoad = controller;
    const signal = controller.signal;
    error = null;
    loading = true;
    try {
      // Transfer + AAVE event kinds use a fixed 30-day window regardless of
      // interval (they're sparse compared to OHLCV); other kinds use the
      // per-interval lookback window.
      let sinceIso: string;
      let untilIso: string;
      const isWideWindowKind =
        instance.kind === 'transfer' ||
        isAaveKind(instance.kind) ||
        isAaveV2Kind(instance.kind) ||
        isAaveV4Kind(instance.kind) ||
        isMorphoKind(instance.kind) ||
        isSparkKind(instance.kind) ||
        isGmxKind(instance.kind) ||
        isHlKind(instance.kind) ||
        isUniswapKind(instance.kind) ||
        isUniswapV2Kind(instance.kind) ||
        isUniswapV4Kind(instance.kind) ||
        isAeroKind(instance.kind) ||
        isAeroBasicKind(instance.kind) ||
        isLidoKind(instance.kind);
      if (isWideWindowKind) {
        const now = new Date();
        const tu = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
        const ts = new Date(tu.getTime() - 30 * 24 * 60 * 60 * 1000);
        sinceIso = ts.toISOString();
        untilIso = tu.toISOString();
      } else {
        const w = lookbackWindow(instance.interval);
        sinceIso = w.since.toISOString();
        untilIso = w.until.toISOString();
      }
      const baseQS = {
        token: instance.token,
        interval: instance.interval,
        since: sinceIso,
        until: untilIso,
        limit: '5000'
      };

      let url = '';
      let pickArr: (body: Record<string, unknown>) => AnyDatum[] = () => [];
      // Morpho chart kinds — ETH + BASE. 7 events including the unique
      // supply_collateral / withdraw_collateral pair for Morpho Blue's
      // isolated-market architecture. Same (chain, token) fetch shape.
      if (isMorphoKind(instance.kind)) {
        const buildMorphoQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = MORPHO_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/morpho/aggregate?${buildMorphoQs(posEvent)}`, { signal }),
            queuedFetch(`/api/morpho/aggregate?${buildMorphoQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd, count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const morphoEvent = MORPHO_KIND_TO_EVENT[instance.kind];
        if (morphoEvent) {
          const res = await queuedFetch(`/api/morpho/aggregate?${buildMorphoQs(morphoEvent)}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          data = (body.series ?? []) as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // Spark chart kinds — ETH-only, 6 events (AAVE V3 fork).
      if (isSparkKind(instance.kind)) {
        const buildSparkQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = SPARK_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/spark/aggregate?${buildSparkQs(posEvent)}`, { signal }),
            queuedFetch(`/api/spark/aggregate?${buildSparkQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd, count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const sparkEvent = SPARK_KIND_TO_EVENT[instance.kind];
        if (sparkEvent) {
          const res = await queuedFetch(`/api/spark/aggregate?${buildSparkQs(sparkEvent)}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          data = (body.series ?? []) as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // GMX V2 chart kinds — ARB-only, per-market selector (market_name).
      // Per-kind we pick which response field is the primary metric via
      // GMX_PRIMARY_FIELD — needed because upstream `swap.amount_in` and
      // `withdraw.value_usd` ship in inconsistent units (decimals bug in
      // defistream's GMX decoder), so we deliberately read sum_value_usd
      // for swaps and sum_amount (long+short token-units) for LP events.
      // The chart layer treats the chosen field as both `sum_amount` and
      // `sum_value_usd` on the rendered datum so the LineChart's USD/Amount
      // formatter falls back to USD-style labels by default.
      if (isGmxKind(instance.kind)) {
        const buildGmxQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'ARB',
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (instance.gmxMarket && instance.gmxMarket.length > 0) {
            qs.set('market', instance.gmxMarket);
          }
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const primary = GMX_PRIMARY_FIELD[instance.kind] ?? 'sum_value_usd';
        // Normalise a server bucket onto a single value picked by primary.
        // Returned datum mirrors both fields so downstream code can keep
        // reading sum_amount / sum_value_usd uniformly.
        const pick = (r: Record<string, number>): number => Number(r[primary] ?? 0);

        const netEvs = GMX_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/gmx/aggregate?${buildGmxQs(posEvent)}`, { signal }),
            queuedFetch(`/api/gmx/aggregate?${buildGmxQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { val: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { val: pick(r), count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { val: 0, count: 0 };
            const diff = pick(r) - n.val;
            out.push({ time: r.time, sum_amount: diff, sum_value_usd: diff, count: r.count + n.count });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -pick(r), sum_value_usd: -pick(r), count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const gmxEvent = GMX_KIND_TO_EVENT[instance.kind];
        if (gmxEvent) {
          const res = await queuedFetch(`/api/gmx/aggregate?${buildGmxQs(gmxEvent)}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          const out: Record<string, number>[] = [];
          for (const r of (body.series ?? []) as Array<Record<string, number>>) {
            const v = pick(r);
            out.push({ time: r.time, sum_amount: v, sum_value_usd: v, count: r.count });
          }
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // Hyperliquid: per-token + optional wallet/wallet_category filters.
      // The hl_top_traders kind takes a different path (leaderboard endpoint
      // → TableChart render) and is handled in the render branch — its
      // fetch happens here via the same code path with a stub data array.
      // For the position_*_size kinds we read sum_amount/sum_value_usd
      // from the same hl/aggregate response.
      // Hyperliquid bridge flows: directional view of HL's Arbitrum
      // bridge. Three series — deposit (positive), withdrawal (negated,
      // shown below zero), and net = deposit + withdrawal — so the lines
      // visually add up. Replaces the single-sum_amount view the generic
      // hl_transfers fetch used to produce.
      if (instance.kind === 'hl_transfers') {
        const qs = new URLSearchParams({
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '5000'
        });
        const res = await queuedFetch(`/api/hyperliquid/bridge_flows?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as unknown as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Hyperliquid OI split: long / short / total open interest per
      // token over time. Same state-aware aggregation as unrealized_pnl
      // (argMax per wallet then sum across wallets). USD notional is the
      // primary view; token-unit fields are also returned for future use.
      if (instance.kind === 'hl_oi_split') {
        const qs = new URLSearchParams({
          token: instance.token,
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '5000'
        });
        const res = await queuedFetch(`/api/hyperliquid/oi_split?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as unknown as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Hyperliquid unrealized PnL: its own state-aware endpoint that
      // collapses per-wallet snapshots to last-in-bucket before summing.
      // Response carries (long_pnl, short_pnl, net_pnl) — three series
      // rendered as three lines instead of the single sum_value_usd line
      // every other HL kind uses.
      if (instance.kind === 'hl_unrealized_pnl') {
        const qs = new URLSearchParams({
          token: instance.token,
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '5000'
        });
        if (instance.hlWallet && instance.hlWallet.length > 0) {
          qs.set('wallet', instance.hlWallet);
        }
        const res = await queuedFetch(`/api/hyperliquid/unrealized_pnl?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as unknown as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      if (isHlKind(instance.kind) && instance.kind !== 'hl_top_traders') {
        const event = HL_KIND_TO_EVENT[instance.kind];
        if (event) {
          const qs = new URLSearchParams({
            event,
            token: instance.token,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (instance.hlWallet && instance.hlWallet.length > 0) {
            qs.set('wallet', instance.hlWallet);
          } else if (instance.hlWalletCategory && instance.hlWalletCategory.length > 0) {
            qs.set('wallet_category', instance.hlWalletCategory);
          }
          if (forceFresh) qs.set('fresh', '1');
          const res = await queuedFetch(`/api/hyperliquid/aggregate?${qs}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          const primary = HL_PRIMARY_FIELD[instance.kind] ?? 'sum_value_usd';
          const out: Record<string, number>[] = [];
          for (const r of (body.series ?? []) as Array<Record<string, number>>) {
            const v = Number(r[primary] ?? 0);
            out.push({ time: r.time, sum_amount: v, sum_value_usd: v, count: r.count });
          }
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // Hyperliquid top-traders: leaderboard endpoint returns ranked rows
      // (wallet, net_pnl, volume, …, categories). We stash the full response
      // on a side-channel and render via TableChart instead of LineChart.
      if (instance.kind === 'hl_top_traders') {
        const qs = new URLSearchParams({
          token: instance.token,
          since: sinceIso,
          until: untilIso,
          order_by: 'net_pnl',
          limit: '50'
        });
        const res = await queuedFetch(`/api/hyperliquid/wallets/leaderboard?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        // Carry the leader rows as a single datum payload — the render
        // branch reads `data[0].leaders` instead of iterating time buckets.
        data = [{ leaders: body.leaders ?? [] } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Hyperliquid top-positions: one fetch returns top-10 wallets AND
      // every wallet's full position breakdown. Switching the wallet in
      // the dropdown is instant — no re-fetch. since/until are ignored
      // (the endpoint always returns the latest snapshot).
      if (instance.kind === 'hl_top_positions') {
        const qs = new URLSearchParams({ limit: '10' });
        if (instance.token && instance.token.length > 0) qs.set('token', instance.token);
        const res = await queuedFetch(`/api/hyperliquid/top_positions?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ wallets: body.wallets ?? [], as_of: body.as_of } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // AAVE V4 chart kinds — ETH-only, 5 events (no flashloan). Same
      // (chain, token) fetch shape as V2/V3 minus eth_market.
      if (isAaveV4Kind(instance.kind)) {
        const buildV4Qs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netV4Events = AAVE_V4_NET_KIND_TO_EVENTS[instance.kind];
        if (netV4Events) {
          const [posEvent, negEvent] = netV4Events;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/aave_v4/aggregate?${buildV4Qs(posEvent)}`, { signal }),
            queuedFetch(`/api/aave_v4/aggregate?${buildV4Qs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd, count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const v4Event = AAVE_V4_KIND_TO_EVENT[instance.kind];
        if (v4Event) {
          const res = await queuedFetch(`/api/aave_v4/aggregate?${buildV4Qs(v4Event)}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          data = (body.series ?? []) as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // AAVE V2 chart kinds — same fetch shape as V3 minus eth_market.
      if (isAaveV2Kind(instance.kind)) {
        const buildV2Qs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netV2Events = AAVE_V2_NET_KIND_TO_EVENTS[instance.kind];
        if (netV2Events) {
          const [posEvent, negEvent] = netV2Events;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/aave_v2/aggregate?${buildV2Qs(posEvent)}`, { signal }),
            queuedFetch(`/api/aave_v2/aggregate?${buildV2Qs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd, count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const v2Event = AAVE_V2_KIND_TO_EVENT[instance.kind];
        if (v2Event) {
          const res = await queuedFetch(`/api/aave_v2/aggregate?${buildV2Qs(v2Event)}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          data = (body.series ?? []) as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // AAVE net kinds (Net Deposit = deposits − withdrawals; Net Borrow =
      // borrows − repays) fire two parallel /api/aave/aggregate calls and
      // subtract on the client. Same (chain, token, interval) shape as the
      // single-event kinds — the only difference is the dual fetch.
      const aaveNetEvents = AAVE_NET_KIND_TO_EVENTS[instance.kind];
      if (aaveNetEvents) {
        const [posEvent, negEvent] = aaveNetEvents;
        const buildAaveQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const [posRes, negRes] = await Promise.all([
          queuedFetch(`/api/aave/aggregate?${buildAaveQs(posEvent)}`, { signal }),
          queuedFetch(`/api/aave/aggregate?${buildAaveQs(negEvent)}`, { signal })
        ]);
        if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
        if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
        const posBody = await posRes.json();
        const negBody = await negRes.json();
        const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
        for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
          negByTime.set(r.time, {
            amount: r.sum_amount,
            usd: r.sum_value_usd,
            count: r.count
          });
        }
        const out: Record<string, number>[] = [];
        const seen = new Set<number>();
        for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
          const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
          out.push({
            time: r.time,
            sum_amount: r.sum_amount - n.amount,
            sum_value_usd: r.sum_value_usd - n.usd,
            count: r.count + n.count
          });
          seen.add(r.time);
        }
        for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
          if (seen.has(r.time)) continue;
          out.push({
            time: r.time,
            sum_amount: -r.sum_amount,
            sum_value_usd: -r.sum_value_usd,
            count: r.count
          });
        }
        out.sort((a, b) => a.time - b.time);
        data = out as unknown as AnyDatum[];
        since = sinceIso;
        until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // AAVE chart kinds all hit the same /api/aave/aggregate endpoint with
      // a different `event=` param — handle them together up front to keep
      // the per-kind switch tidy. Token groups (USDC+USDT, Stables) map to
      // ?token_group=... server-side instead of a single token.
      // Lido chart kinds — chain-only, no token axis. The 4 single-event
      // kinds hit /api/lido/aggregate once; the 2 net kinds (Net Stake,
      // Net L2) fire two parallel fetches and subtract per bucket — same
      // pattern as AAVE Net Deposit / Net Borrow.
      if (isLidoKind(instance.kind)) {
        const buildLidoQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          // L2 kinds can select a chain group (EVM / All) which the server
          // expands to a `chain IN (...)` predicate. L1 kinds stay ETH-pinned
          // — the auto-snap effect forces instance.chain to 'ETH' so no
          // group case is reachable for them.
          if (activeChainGroup) {
            qs.set('chain_group', activeChainGroup.name);
          } else {
            qs.set('chain', instance.chain ?? 'ETH');
          }
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const lidoNetEvents = LIDO_NET_KIND_TO_EVENTS[instance.kind];
        if (lidoNetEvents) {
          const [posEvent, negEvent] = lidoNetEvents;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/lido/aggregate?${buildLidoQs(posEvent)}`, { signal }),
            queuedFetch(`/api/lido/aggregate?${buildLidoQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd, count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso;
          until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const lidoEvent = LIDO_KIND_TO_EVENT[instance.kind];
        if (!lidoEvent) throw new Error(`unmapped lido kind ${instance.kind}`);
        const res = await queuedFetch(
          `/api/lido/aggregate?${buildLidoQs(lidoEvent)}`,
          { signal }
        );
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as AnyDatum[];
        since = sinceIso;
        until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Uniswap V4 chart kinds. Pool keyed by (chain, sym0, sym1, fee,
      // tick_spacing, hooks). LP events only emit liquidity_delta (no
      // amount0/amount1) so net_liquidity does that subtraction and the
      // dual-axis Amount mode degenerates to a single line.
      if (isUniswapV4Kind(instance.kind)) {
        const pool = instance.uniV4Pool;
        if (!pool || !instance.chain) {
          data = []; since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const buildV4Qs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'ETH',
            symbol0: pool.symbol0,
            symbol1: pool.symbol1,
            fee: String(pool.fee),
            tick_spacing: String(pool.tick_spacing),
            hooks: pool.hooks,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = UNISWAP_V4_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/uniswap_v4/aggregate?${buildV4Qs(posEvent)}`, { signal }),
            queuedFetch(`/api/uniswap_v4/aggregate?${buildV4Qs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          const negByTime = new Map<number, { amount: number; usd: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd, count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({ time: r.time, sum_amount: -r.sum_amount, sum_value_usd: -r.sum_value_usd, count: r.count });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const eventForKind = UNISWAP_V4_KIND_TO_EVENT[instance.kind];
        if (!eventForKind) throw new Error(`unmapped uniswap_v4 kind ${instance.kind}`);
        const res = await queuedFetch(`/api/uniswap_v4/aggregate?${buildV4Qs(eventForKind)}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Aerodrome BASIC pools (Solidly v1, BASE only). Pool keyed by
      // (sym0, sym1, stable). Same fetch shape as concentrated minus
      // tick_spacing plus the stable flag.
      if (isAeroBasicKind(instance.kind)) {
        const pool = instance.aeroBasicPool;
        if (!pool) {
          data = []; since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const buildAeroBasicQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'BASE',
            symbol0: pool.symbol0,
            symbol1: pool.symbol1,
            stable: pool.stable ? '1' : '0',
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = AERO_BASIC_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/aero_basic/aggregate?${buildAeroBasicQs(posEvent)}`, { signal }),
            queuedFetch(`/api/aero_basic/aggregate?${buildAeroBasicQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          type Row = { amount: number; usd: number; count: number; amount0: number; amount1: number };
          const negByTime = new Map<number, Row>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, {
              amount: r.sum_amount, usd: r.sum_value_usd, count: r.count,
              amount0: r.sum_amount0 ?? 0, amount1: r.sum_amount1 ?? 0
            });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0, amount0: 0, amount1: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              sum_amount0: (r.sum_amount0 ?? 0) - n.amount0,
              sum_amount1: (r.sum_amount1 ?? 0) - n.amount1,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({
              time: r.time,
              sum_amount: -r.sum_amount,
              sum_value_usd: -r.sum_value_usd,
              sum_amount0: -(r.sum_amount0 ?? 0),
              sum_amount1: -(r.sum_amount1 ?? 0),
              count: r.count
            });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const eventForKind = AERO_BASIC_KIND_TO_EVENT[instance.kind];
        if (!eventForKind) throw new Error(`unmapped aero_basic kind ${instance.kind}`);
        const res = await queuedFetch(`/api/aero_basic/aggregate?${buildAeroBasicQs(eventForKind)}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Aerodrome (concentrated pools, BASE only).
      if (isAeroKind(instance.kind)) {
        const pool = instance.aeroPool;
        if (!pool) {
          data = []; since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const buildAeroQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'BASE',
            symbol0: pool.symbol0,
            symbol1: pool.symbol1,
            tick_spacing: String(pool.tick_spacing),
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = AERO_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/aero/aggregate?${buildAeroQs(posEvent)}`, { signal }),
            queuedFetch(`/api/aero/aggregate?${buildAeroQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          type Row = { amount: number; usd: number; count: number; amount0: number; amount1: number };
          const negByTime = new Map<number, Row>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, {
              amount: r.sum_amount, usd: r.sum_value_usd, count: r.count,
              amount0: r.sum_amount0 ?? 0, amount1: r.sum_amount1 ?? 0
            });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0, amount0: 0, amount1: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              sum_amount0: (r.sum_amount0 ?? 0) - n.amount0,
              sum_amount1: (r.sum_amount1 ?? 0) - n.amount1,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({
              time: r.time,
              sum_amount: -r.sum_amount,
              sum_value_usd: -r.sum_value_usd,
              sum_amount0: -(r.sum_amount0 ?? 0),
              sum_amount1: -(r.sum_amount1 ?? 0),
              count: r.count
            });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const eventForKind = AERO_KIND_TO_EVENT[instance.kind];
        if (!eventForKind) throw new Error(`unmapped aero kind ${instance.kind}`);
        const res = await queuedFetch(`/api/aero/aggregate?${buildAeroQs(eventForKind)}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Uniswap V2 chart kinds — same fetch shape as V3 minus the fee_tier
      // axis (V2 has no fee tier — single 0.30% pool per pair). Net
      // liquidity also tracks per-token amounts for dual-axis Amount mode.
      if (isUniswapV2Kind(instance.kind)) {
        const pool = instance.uniPool;
        if (!pool || !instance.chain) {
          data = [];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const buildUniV2Qs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'ETH',
            symbol0: pool.symbol0,
            symbol1: pool.symbol1,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = UNISWAP_V2_NET_KIND_TO_EVENTS[instance.kind];
        if (netEvs) {
          const [posEvent, negEvent] = netEvs;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/uniswap_v2/aggregate?${buildUniV2Qs(posEvent)}`, { signal }),
            queuedFetch(`/api/uniswap_v2/aggregate?${buildUniV2Qs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          type Row = { amount: number; usd: number; count: number; amount0: number; amount1: number };
          const negByTime = new Map<number, Row>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, {
              amount: r.sum_amount, usd: r.sum_value_usd, count: r.count,
              amount0: r.sum_amount0 ?? 0, amount1: r.sum_amount1 ?? 0
            });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0, amount0: 0, amount1: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              sum_amount0: (r.sum_amount0 ?? 0) - n.amount0,
              sum_amount1: (r.sum_amount1 ?? 0) - n.amount1,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({
              time: r.time,
              sum_amount: -r.sum_amount,
              sum_value_usd: -r.sum_value_usd,
              sum_amount0: -(r.sum_amount0 ?? 0),
              sum_amount1: -(r.sum_amount1 ?? 0),
              count: r.count
            });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const eventForKind = UNISWAP_V2_KIND_TO_EVENT[instance.kind];
        if (!eventForKind) throw new Error(`unmapped uniswap_v2 kind ${instance.kind}`);
        const res = await queuedFetch(
          `/api/uniswap_v2/aggregate?${buildUniV2Qs(eventForKind)}`, { signal }
        );
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Uniswap chart kinds. The single-event ones hit /api/uniswap/aggregate
      // once. uniswap_net_liquidity needs two parallel calls (deposit −
      // withdraw, by sum_amount of amount0+amount1). uniswap_net_swap_flow
      // makes a single swap call and uses the server's directional split
      // (sum_value_usd_t0t1 − sum_value_usd_t1t0) — no second fetch.
      if (isUniswapKind(instance.kind)) {
        const pool = instance.uniPool;
        if (!pool || !instance.chain) {
          // Render an empty series — the auto-snap effect will retry once
          // pools arrive.
          data = [];
          since = sinceIso;
          until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        const buildUniQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'ETH',
            symbol0: pool.symbol0,
            symbol1: pool.symbol1,
            fee_tier: String(pool.fee),
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '5000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const uniNetEvents = UNISWAP_NET_KIND_TO_EVENTS[instance.kind];
        if (uniNetEvents) {
          const [posEvent, negEvent] = uniNetEvents;
          const [posRes, negRes] = await Promise.all([
            queuedFetch(`/api/uniswap/aggregate?${buildUniQs(posEvent)}`, { signal }),
            queuedFetch(`/api/uniswap/aggregate?${buildUniQs(negEvent)}`, { signal })
          ]);
          if (!posRes.ok) throw new Error(`${instance.kind} ${posRes.status}`);
          if (!negRes.ok) throw new Error(`${instance.kind} ${negRes.status}`);
          const posBody = await posRes.json();
          const negBody = await negRes.json();
          // Uniswap net liquidity also tracks per-token amounts so amount
          // mode can render token0 and token1 on separate axes.
          type UniNegRow = {
            amount: number; usd: number; count: number;
            amount0: number; amount1: number;
          };
          const negByTime = new Map<number, UniNegRow>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, {
              amount: r.sum_amount,
              usd: r.sum_value_usd,
              count: r.count,
              amount0: r.sum_amount0 ?? 0,
              amount1: r.sum_amount1 ?? 0
            });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { amount: 0, usd: 0, count: 0, amount0: 0, amount1: 0 };
            out.push({
              time: r.time,
              sum_amount: r.sum_amount - n.amount,
              sum_value_usd: r.sum_value_usd - n.usd,
              sum_amount0: (r.sum_amount0 ?? 0) - n.amount0,
              sum_amount1: (r.sum_amount1 ?? 0) - n.amount1,
              count: r.count + n.count
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({
              time: r.time,
              sum_amount: -r.sum_amount,
              sum_value_usd: -r.sum_value_usd,
              sum_amount0: -(r.sum_amount0 ?? 0),
              sum_amount1: -(r.sum_amount1 ?? 0),
              count: r.count
            });
          }
          out.sort((a, b) => a.time - b.time);
          data = out as unknown as AnyDatum[];
          since = sinceIso;
          until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
          return;
        }
        // Single-event path (including uniswap_net_swap_flow, which uses the
        // swap endpoint and computes net from its directional t0t1/t1t0 split).
        const eventForKind =
          instance.kind === 'uniswap_net_swap_flow'
            ? 'swap'
            : UNISWAP_KIND_TO_EVENT[instance.kind];
        if (!eventForKind) {
          throw new Error(`unmapped uniswap kind ${instance.kind}`);
        }
        const res = await queuedFetch(
          `/api/uniswap/aggregate?${buildUniQs(eventForKind)}`,
          { signal }
        );
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        const series = (body.series ?? []) as Array<Record<string, number>>;
        if (instance.kind === 'uniswap_net_swap_flow') {
          // Net swap flow = $ moved token0 → token1 minus $ moved token1 →
          // token0. Positive = net buying of token1 (= selling token0).
          data = series.map((r) => ({
            time: r.time,
            sum_amount: 0,
            sum_value_usd: (r.sum_value_usd_t0t1 ?? 0) - (r.sum_value_usd_t1t0 ?? 0),
            count: r.count
          })) as unknown as AnyDatum[];
        } else {
          data = series as unknown as AnyDatum[];
        }
        since = sinceIso;
        until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      const aaveEvent = AAVE_KIND_TO_EVENT[instance.kind];
      if (aaveEvent) {
        const qs = new URLSearchParams({
          event: aaveEvent,
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '5000'
        });
        if (activeChainGroup) {
          qs.set('chain_group', activeChainGroup.name);
        } else {
          qs.set('chain', instance.chain ?? 'ETH');
        }
        if (activeTokenGroup !== null) {
          qs.set('token_group', activeTokenGroup);
        } else {
          qs.set('token', instance.token);
        }
        if (forceFresh) qs.set('fresh', '1');
        const res = await queuedFetch(`/api/aave/aggregate?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as AnyDatum[];
        since = sinceIso;
        until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      switch (instance.kind) {
        case 'ohlcv': {
          // OHLCV chart routes its read to whichever exchange the
          // instance is pinned to. Default 'binance' for back-compat;
          // 'hl' reads from tradernick.hl_ohlcv_1m server-side.
          const ohlcvQs = new URLSearchParams(baseQS);
          ohlcvQs.set('exchange', instance.exchange ?? 'binance');
          url = `/api/ohlcv?${ohlcvQs}`;
          pickArr = (b) => (b.candles ?? []) as AnyDatum[];
          break;
        }
        case 'pc': {
          // Price Comparison — main token + each instance.overlayTokens
          // fetched in parallel from /api/ohlcv, then rebased to % from
          // the leftmost close in the render path.
          const overlays = (instance.overlayTokens ?? []).filter(
            (t) => t && t !== instance.token
          );
          const buildOhlcvQs = (tok: string) => {
            const q = new URLSearchParams({ ...baseQS, token: tok });
            if (forceFresh) q.set('fresh', '1');
            return q;
          };
          const [mainRes, ...ovRes] = await Promise.all([
            queuedFetch(`/api/ohlcv?${buildOhlcvQs(instance.token)}`, { signal }),
            ...overlays.map((t) => queuedFetch(`/api/ohlcv?${buildOhlcvQs(t)}`, { signal }))
          ]);
          if (!mainRes.ok) throw new Error(`pc ${mainRes.status}`);
          const mainBody = await mainRes.json();
          data = ((mainBody.candles ?? []) as AnyDatum[]);
          const nextOverlay: Record<string, Candle[]> = {};
          for (let i = 0; i < overlays.length; i++) {
            const tok = overlays[i];
            const r = ovRes[i];
            if (!r.ok) continue;
            const body = await r.json();
            nextOverlay[tok] = (body.candles ?? []) as Candle[];
          }
          overlayData = nextOverlay;
          since = sinceIso;
          until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(instance.id, { key: loadedKey, data, since, until, localView, overlayData });
          return;
        }
        case 'oi':
          url = `/api/open_interest?${new URLSearchParams({
            ...baseQS,
            exchange: instance.exchange ?? 'binance'
          })}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        case 'fr': {
          // Same Binance / HL exchange selector pattern as the ohlcv kind.
          const frQs = new URLSearchParams(baseQS);
          frQs.set('exchange', instance.exchange ?? 'binance');
          url = `/api/funding_rate?${frQs}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        }
        case 'tt':
        case 'ls':
          url = `/api/long_short_ratios?${new URLSearchParams(baseQS)}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        case 'bs':
          url = `/api/trade_volume?${new URLSearchParams({
            ...baseQS,
            exchange: instance.exchange ?? 'binance',
            under: '10000',
            over: '100000'
          })}`;
          pickArr = (b) => (b.buckets ?? []) as AnyDatum[];
          break;
        case 'sz':
          url = `/api/trade_volume?${new URLSearchParams({
            ...baseQS,
            exchange: instance.exchange ?? 'binance',
            under: String(instance.under ?? 10000),
            over: String(instance.over ?? 100000)
          })}`;
          pickArr = (b) => (b.buckets ?? []) as AnyDatum[];
          break;
        case 'transfer': {
          // Transfer kind does its own multi-fetch + merge so the chart can show
          // a main (unfiltered) line alongside up to MAX_EXTRA_SERIES filtered
          // overlays. Skip the single-URL pickArr path below.
          await loadTransferMerged(sinceIso, untilIso, signal, forceFresh);
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          since = sinceIso;
          until = untilIso;
          loadCache.set(instance.id, {
            key: loadedKey,
            data,
            since,
            until,
            localView
          });
          return;
        }
      }
      const res = await queuedFetch(url, { signal });
      if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
      const body = await res.json();
      data = pickArr(body);
      since = sinceIso;
      until = untilIso;
      loadedKey = loadKey();
      localView = defaultView(sinceIso, untilIso);
      loadCache.set(instance.id, {
        key: loadedKey,
        data,
        since,
        until,
        localView
      });
    } catch (e) {
      // Superseded by a newer load() — silent. The newer load owns `loading`
      // and will surface its own state.
      if (signal.aborted && (e as DOMException)?.name === 'AbortError') {
        return;
      }
      error = e instanceof Error ? e.message : String(e);
    } finally {
      // Only clear loading if we're still the active load (a newer load that
      // aborted us has already taken over `currentLoad` and its own loading).
      if (currentLoad === controller) {
        currentLoad = null;
        loading = false;
      }
    }
  }

  /** Force a fresh fetch — bypasses the `loadedKey === key` short-circuit,
   *  evicts this chart's entry from the remount cache, and sends `?fresh=1`
   *  so the data_server's response cache also recomputes. Wired to the
   *  header refresh button. Allowed mid-load: load() will abort the prior
   *  in-flight fetch via currentLoad so a stuck request can be replaced. */
  async function reload() {
    loadedKey = '';
    loadCache.delete(instance.id);
    await load(true);
  }

  // ---- derived series / lines / extra computed data ----
  // Funding-rate normalization. Server returns the raw per-event rate; each
  // exchange's per-event cadence differs (Binance = per-8h, HL = per-1h), so
  // we normalize on the client based on the display-mode toggle:
  //   'rate8h' → bps over an 8-hour window (Coinglass convention; HL × 8).
  //   'apr'    → annualized percent (rate × events/year × 100).
  // Switching is instant (no refetch) because both modes derive from the same
  // raw rate.
  let frHoursPerEvent = $derived(
    (instance.exchange ?? 'binance') === 'hl' ? 1 : 8
  );
  let frIsApr = $derived((instance.frDisplay ?? 'rate8h') === 'apr');
  let frBpsData = $derived(
    instance.kind === 'fr'
      ? (data as FundingRateRow[]).map((d) => ({
          ...d,
          rate_bps: frIsApr
            ? d.rate * (24 / frHoursPerEvent) * 365 * 100
            : d.rate * (8 / frHoursPerEvent) * 10000
        }))
      : []
  );

  // Swap the candle's `volume` / `*_taker_volume` fields to USD when the
  // user picks the 'usd' toggle, so CandlestickChart renders the right
  // sub-pane without needing any chart-component changes. Falls back to
  // the raw token volume if the server didn't supply the USD field (older
  // payloads or stale caches).
  let ohlcvCandles = $derived.by(() => {
    if (instance.kind !== 'ohlcv') return [] as Candle[];
    const src = data as Candle[];
    if ((instance.volumeUnit ?? 'token') !== 'usd') return src;
    return src.map((c) => ({
      ...c,
      volume: c.volume_usd ?? c.volume,
      buyer_taker_volume: c.buyer_taker_volume_usd ?? c.buyer_taker_volume,
      seller_taker_volume: c.seller_taker_volume_usd ?? c.seller_taker_volume
    }));
  });

  // Per-MA sub-line dash patterns (for kinds where one MA config emits multiple lines).
  const SUB_DASH = ['5,3', '2,2', '6,2,2,2'];

  let anyMaEnabled = $derived(instance.mas.some((m) => m.enabled));
  // Kinds where a running cumulative sum is meaningful: event-driven charts
  // whose per-bucket value is itself a sum (deposits, transfers, swaps, …).
  // Ratios (bs / sz / tt / ls) and point-in-time series (ohlcv / oi / fr / pc)
  // are excluded because summing them produces nonsense.
  let canSum = $derived(
    instance.kind === 'transfer'
      || isAaveKind(instance.kind)
      || isAaveV2Kind(instance.kind)
      || isAaveV4Kind(instance.kind)
      || isMorphoKind(instance.kind)
      || isSparkKind(instance.kind)
      || isLidoKind(instance.kind)
      || isAeroKind(instance.kind)
      || isAeroBasicKind(instance.kind)
      // GMX charts already pick a single value field per kind (via
      // GMX_PRIMARY_FIELD); the running sum of that field is meaningful
      // — total position-open USD over the visible window, total swap USD,
      // etc.
      || isGmxKind(instance.kind)
      // Uniswap: only when plotting a single USD line — amount mode is
      // dual-axis (token0 + token1) so the secondary axis is already taken
      // and there's no single "the amount" to sum.
      || ((isUniswapKind(instance.kind) || isUniswapV2Kind(instance.kind) || instance.kind === 'uniswap_v4_swap')
            && (instance.valueMode ?? 'usd') === 'usd')
  );

  let cumulativeLines = $derived.by(() => {
    if (data.length === 0) return [] as unknown[];
    if (!anyMaEnabled && !(canSum && instance.showSum)) return [] as unknown[];
    const out: unknown[] = [];
    for (let idx = 0; idx < instance.mas.length; idx++) {
      const ma = instance.mas[idx];
      if (!ma.enabled) continue;
      const color = MA_COLORS[idx] ?? '#fbbf24';
      const tag = `${ma.type.toUpperCase()}(${ma.length})`;
      switch (instance.kind) {
        case 'ohlcv': {
          const arr = maArray((data as Candle[]).map((c) => c.close), ma.length, ma.type);
          out.push({
            key: `cum_close_${idx}`,
            label: `Close ${tag}`,
            color,
            compute: (_d: Candle, i: number) => arr[i]
          });
          break;
        }
        case 'oi': {
          const arr = maArray(
            (data as OpenInterestRow[]).map((d) => d.open_interest_value),
            ma.length,
            ma.type
          );
          out.push({
            key: `cum_oi_${idx}`,
            label: `OI ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: OpenInterestRow, i: number) => arr[i]
          });
          break;
        }
        case 'fr': {
          const arr = maArray(frBpsData.map((d) => d.rate_bps), ma.length, ma.type);
          out.push({
            key: `cum_fr_${idx}`,
            label: `Rate ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: FundingRateRow, i: number) => arr[i]
          });
          break;
        }
        case 'bs': {
          const arr = data as VolumeBucket[];
          const buyerMA = maArray(arr.map((b) => b.buyer_taker_usd), ma.length, ma.type);
          const totalMA = maArray(
            arr.map((b) => b.buyer_taker_usd + b.seller_taker_usd),
            ma.length,
            ma.type
          );
          out.push({
            key: `cum_buyer_${idx}`,
            label: `% Buyer ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: VolumeBucket, i: number) =>
              totalMA[i] > 0 ? (buyerMA[i] / totalMA[i]) * 100 : 0
          });
          break;
        }
        case 'sz': {
          const arr = data as VolumeBucket[];
          const u = instance.under ?? 10000;
          const o = instance.over ?? 100000;
          const smallMA = maArray(arr.map((b) => b.small_usd), ma.length, ma.type);
          const largeMA = maArray(arr.map((b) => b.large_usd), ma.length, ma.type);
          const totalMA = maArray(
            arr.map((b) => b.small_usd + b.mid_usd + b.large_usd),
            ma.length,
            ma.type
          );
          out.push({
            key: `cum_small_${idx}`,
            label: `% < $${u} ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: VolumeBucket, i: number) =>
              totalMA[i] > 0 ? (smallMA[i] / totalMA[i]) * 100 : 0
          });
          out.push({
            key: `cum_large_${idx}`,
            label: `% > $${o} ${tag}`,
            color,
            dash: SUB_DASH[1],
            compute: (_d: VolumeBucket, i: number) =>
              totalMA[i] > 0 ? (largeMA[i] / totalMA[i]) * 100 : 0
          });
          break;
        }
        case 'tt': {
          const arr = data as LongShortRow[];
          const countMA = maArray(arr.map((d) => d.top_trader_count_ratio), ma.length, ma.type);
          const volMA = maArray(arr.map((d) => d.top_trader_vol_ratio), ma.length, ma.type);
          out.push({
            key: `cum_top_ct_${idx}`,
            label: `Top count ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: LongShortRow, i: number) => countMA[i]
          });
          out.push({
            key: `cum_top_vol_${idx}`,
            label: `Top vol ${tag}`,
            color,
            dash: SUB_DASH[1],
            compute: (_d: LongShortRow, i: number) => volMA[i]
          });
          break;
        }
        case 'ls': {
          const arr = data as LongShortRow[];
          const allCountMA = maArray(
            arr.map((d) => d.long_short_count_ratio),
            ma.length,
            ma.type
          );
          const takerVolMA = maArray(
            arr.map((d) => d.taker_long_short_vol_ratio),
            ma.length,
            ma.type
          );
          out.push({
            key: `cum_all_ct_${idx}`,
            label: `All L/S ct ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: LongShortRow, i: number) => allCountMA[i]
          });
          out.push({
            key: `cum_taker_vol_${idx}`,
            label: `Taker vol ${tag}`,
            color,
            dash: SUB_DASH[1],
            compute: (_d: LongShortRow, i: number) => takerVolMA[i]
          });
          break;
        }
        case 'transfer': {
          const arr = data as TransferBucket[];
          // MAs follow whatever the main series is plotting — USD value by
          // default, raw token amount when the chart is in Amount mode.
          const useUsd = (instance.valueMode ?? 'usd') === 'usd';
          const arrMa = maArray(
            arr.map((b) => (useUsd ? b.sum_value_usd : b.sum_amount)),
            ma.length, ma.type
          );
          out.push({
            key: `cum_transfer_${idx}`,
            label: `${useUsd ? 'USD' : 'Amount'} ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: TransferBucket, i: number) => arrMa[i]
          });
          break;
        }
      }
    }
    // Cumulative sum series (single line, secondary axis). Reads from the
    // same source the main series plots so the user can compare the per-
    // bucket flow against the running total inside the visible window.
    // The y-axis label is purposely terse ("Σ") so the legend stays clean.
    if (canSum && instance.showSum) {
      const useUsd = (instance.valueMode ?? 'usd') === 'usd';
      const valLabel = useUsd ? 'USD' : 'Amount';
      // Source array: every applicable kind exposes the same shape
      // ({sum_value_usd, sum_amount}) on each row.
      const src = (data as unknown as Record<string, number>[]).map(
        (d) => (useUsd ? d.sum_value_usd : d.sum_amount) ?? 0
      );
      const running: number[] = new Array(src.length);
      let acc = 0;
      for (let i = 0; i < src.length; i++) { acc += src[i] || 0; running[i] = acc; }
      out.push({
        key: 'cum_sum',
        label: `Σ ${valLabel}`,
        color: '#a78bfa',                 // violet-400 — distinct from MA palette
        axis: 'secondary' as const,
        compute: (_d: unknown, i: number) => running[i]
      });
    }
    return out;
  });

  // Transfer-kind single line: USD value of the (optionally filtered) sum.
  // The backend prices each transfer with the ASOF-nearest 1m OHLCV close,
  // so this number is honest dollars — no longer token amount with a "$"
  // formatter slapped on.
  // For *template* transfer charts (CeX Inflow, Hyperliquid Outflow, etc.)
  // the tooltip legend should read "<TemplateName> <Token>" — much more
  // useful than the generic "Netflow" / "Total" / auto-named filter blob
  // when comparing multiple template charts side-by-side. The token can be
  // a compound (token group name) — instance.token holds either form, so
  // a single field works for both. Non-template transfer charts keep the
  // previous Netflow / auto-name / Total fallback.
  let transferMainLabel = $derived.by(() => {
    if (typeof instance.templateName === 'string' && instance.templateName.length > 0) {
      return `${instance.templateName} ${instance.token}`;
    }
    if (instance.netFilter) return 'Netflow';
    return activeFilterIsAny ? activeFilterLabel : 'Total';
  });
  // Transfer chart respects instance.valueMode — same toggle plumbing as
  // AAVE / Lido. Default 'usd'; the netflow merge already carries both
  // sum_value_usd and sum_amount so amount mode flips cleanly without a
  // refetch. The label gets an "(amount)" suffix when the chart is in
  // amount mode so the tooltip / legend stay self-describing.
  let transferUseUsd = $derived((instance.valueMode ?? 'usd') === 'usd');
  let transferLinesD = $derived([
    ...(instance.showPoint
      ? [{
          key: 'main',
          label: transferMainLabel + (transferUseUsd ? '' : ' (amount)'),
          color: '#06b6d4',
          compute: (d: TransferBucket & Record<string, number>) =>
            (transferUseUsd ? d.sum_value_usd : d.sum_amount) ?? 0
        }]
      : []),
    ...cumulativeLines
  ]);

  // For event-driven kinds (AAVE / Lido) the user can toggle between
  // sum_value_usd (default) and sum_amount via instance.valueMode. Both
  // fields come back from /aave/aggregate + /lido/aggregate today, so no
  // server change is needed — the toggle just picks which one to plot
  // and swaps the axis/tooltip formatter to match.
  let useUsdValue = $derived((instance.valueMode ?? 'usd') === 'usd');
  let valueField = $derived(useUsdValue ? 'sum_value_usd' : 'sum_amount');
  let valueAxisFn = $derived(useUsdValue ? fmtUsdAxis : fmtAmountAxis);
  let valueTooltipFn = $derived(useUsdValue ? fmtUsdTooltip : fmtAmountTooltip);

  // AAVE event lines — cyan main series + the chart's MAs. With
  // valueMode='amount' we plot sum_amount (raw token units, summed across
  // whatever the chain/token-group selector resolves to) instead of USD.
  // Shared between V2 and V3 (same shape of {sum_amount, sum_value_usd}).
  let aaveLinesD = $derived([
    ...(instance.showPoint
      ? [{
          key: 'main',
          label: (CHART_KIND_LABELS[instance.kind] ?? 'AAVE') + (useUsdValue ? '' : ' (amount)'),
          color: '#06b6d4',
          compute: (d: Record<string, number>) =>
            (d[valueField] ?? 0) || (d.sum_value_usd ?? 0) || (d.sum_amount ?? 0)
        }]
      : []),
    ...cumulativeLines
  ]);

  // HL Unrealized PnL: three lines from the {long_pnl, short_pnl, net_pnl}
  // row shape the /hyperliquid/unrealized_pnl endpoint returns. Colors
  // mirror the buyer/seller convention used elsewhere on /hyperliquid:
  // long = green (positive direction), short = red, net = cyan accent.
  let hlUnrealizedLinesD = $derived(
    instance.showPoint
      ? [
          { key: 'long',  label: 'Long',  color: '#22c55e',
            compute: (d: Record<string, number>) => d.long_pnl ?? 0 },
          { key: 'short', label: 'Short', color: '#ef4444',
            compute: (d: Record<string, number>) => d.short_pnl ?? 0 },
          { key: 'net',   label: 'Net',   color: '#06b6d4',
            compute: (d: Record<string, number>) => d.net_pnl ?? 0 }
        ]
      : []
  );

  // HL OI split: long / short / total open interest from
  // /hyperliquid/oi_split. Plots USD notional by default (the *_value
  // fields); the token-unit fields are present on the row for future
  // toggles. Same color convention as the unrealized chart: long green,
  // short red, net/total cyan.
  let hlOiSplitLinesD = $derived(
    instance.showPoint
      ? [
          { key: 'long',  label: 'Long OI',  color: '#22c55e',
            compute: (d: Record<string, number>) => d.long_oi_value ?? 0 },
          { key: 'short', label: 'Short OI', color: '#ef4444',
            compute: (d: Record<string, number>) => d.short_oi_value ?? 0 },
          { key: 'total', label: 'Total OI', color: '#06b6d4',
            compute: (d: Record<string, number>) => d.total_oi_value ?? 0 }
        ]
      : []
  );

  // HL Bridge Flows: directional USDC bridge view. Deposit (capital in)
  // and withdrawal (capital out) are both rendered as POSITIVE magnitudes
  // so the operator can compare absolute flow sizes side-by-side. Net is
  // signed (deposit - withdrawal) and floats through zero to show the
  // directional bias.
  let hlBridgeFlowsLinesD = $derived(
    instance.showPoint
      ? [
          { key: 'deposit',    label: 'Deposit',    color: '#22c55e',
            compute: (d: Record<string, number>) => d.deposit ?? 0 },
          { key: 'withdrawal', label: 'Withdrawal', color: '#ef4444',
            compute: (d: Record<string, number>) => d.withdrawal ?? 0 },
          { key: 'net',        label: 'Net',        color: '#06b6d4',
            compute: (d: Record<string, number>) => d.net ?? 0 }
        ]
      : []
  );

  // Uniswap chart lines. USD mode (default): one cyan sum_value_usd line.
  // Amount mode: two lines (token0 on primary axis, token1 on secondary
  // axis) — each token has its own scale because t0/t1 magnitudes can be
  // off by 4-6 orders (e.g. USDC vs WETH). The toggle is hidden in the
  // settings UI for uniswap_net_swap_flow (which is intrinsically a
  // directional USD chart with no clean per-token amount split).
  // V4 LP events (deposit/withdraw/initialize) and net_liquidity have no
  // per-token amount split — only liquidity_delta. Force USD mode for
  // those even if the toggle is on (the server returns 0 for sum_amount0/1).
  let uniswapValueModeEffective = $derived(
    instance.kind === 'uniswap_net_swap_flow'
      || instance.kind === 'uniswap_v4_deposit'
      || instance.kind === 'uniswap_v4_withdraw'
      || instance.kind === 'uniswap_v4_net_liquidity'
      || instance.kind === 'uniswap_v4_initialize'
      ? 'usd'
      : (instance.valueMode ?? 'usd')
  );
  let uniswapLinesD = $derived.by(() => {
    if (uniswapValueModeEffective === 'amount') {
      // Pick the right pool object based on which protocol family the chart
      // is in. V4 / Aero hold their own pool shapes alongside uniPool (V3/V2).
      const sym0 = (instance.uniV4Pool?.symbol0
                 ?? instance.aeroPool?.symbol0
                 ?? instance.aeroBasicPool?.symbol0
                 ?? instance.uniPool?.symbol0
                 ?? 't0');
      const sym1 = (instance.uniV4Pool?.symbol1
                 ?? instance.aeroPool?.symbol1
                 ?? instance.aeroBasicPool?.symbol1
                 ?? instance.uniPool?.symbol1
                 ?? 't1');
      const base = instance.showPoint
        ? [
            {
              key: 'amt0',
              label: sym0,
              color: '#06b6d4',
              axis: 'primary' as const,
              compute: (d: Record<string, number>) => d.sum_amount0 ?? 0
            },
            {
              key: 'amt1',
              label: sym1,
              color: '#f59e0b',
              axis: 'secondary' as const,
              compute: (d: Record<string, number>) => d.sum_amount1 ?? 0
            }
          ]
        : [];
      return [...base, ...cumulativeLines];
    }
    return [
      ...(instance.showPoint
        ? [{
            key: 'main',
            label: CHART_KIND_LABELS[instance.kind] ?? 'Uniswap',
            color: '#06b6d4',
            compute: (d: Record<string, number>) =>
              (d.sum_value_usd ?? 0) || (d.sum_amount ?? 0)
          }]
        : []),
      ...cumulativeLines
    ];
  });

  // Lido event lines — identical shape to AAVE. With valueMode='amount' the
  // series shows raw token units (stETH for L1 deposits/requests, ETH for
  // claims, wstETH for L2 events); USD mode is the default. Useful for
  // tracking unit flow when ETH price swings make USD totals noisy.
  let lidoLinesD = $derived([
    ...(instance.showPoint
      ? [{
          key: 'main',
          label: (CHART_KIND_LABELS[instance.kind] ?? 'Lido') + (useUsdValue ? '' : ' (amount)'),
          color: '#06b6d4',
          compute: (d: Record<string, number>) =>
            (d[valueField] ?? 0) || (d.sum_value_usd ?? 0) || (d.sum_amount ?? 0)
        }]
      : []),
    ...cumulativeLines
  ]);

  // bs / sz: bar series (Point toggle controls visibility)
  let bsBars = $derived(instance.showPoint ? BUYER_SELLER_SERIES : []);
  let szBars = $derived(
    instance.showPoint ? sizeSeries(instance.under ?? 10000, instance.over ?? 100000) : []
  );

  let bsLines = $derived(anyMaEnabled ? [...BUYER_SELLER_LINES, ...cumulativeLines] : []);
  let szLinesD = $derived(
    anyMaEnabled
      ? [...sizeLines(instance.under ?? 10000, instance.over ?? 100000), ...cumulativeLines]
      : []
  );
  let oiLinesD = $derived([
    ...(instance.showPoint ? OI_LINES : []),
    ...cumulativeLines
  ]);
  let ttLinesD = $derived([
    ...(instance.showPoint ? TOP_TRADERS_LINES : []),
    ...cumulativeLines
  ]);
  let lsLinesD = $derived([
    ...(instance.showPoint ? LS_LINES : []),
    ...cumulativeLines
  ]);
  // Rebase an array of {close} (Candle-shaped) rows so the first non-null
  // close is 0%, every subsequent value is `(close - base) / base * 100`.
  // Used by the pc (Price Comparison) chart kind.
  function rebasedCloses(rows: { close?: number }[] | undefined | null): number[] {
    if (!rows || rows.length === 0) return [];
    let base = 0;
    for (const r of rows) {
      if (r && typeof r.close === 'number' && r.close !== 0) { base = r.close; break; }
    }
    if (!base) return rows.map(() => 0);
    return rows.map((r) => {
      const c = r && typeof r.close === 'number' ? r.close : base;
      return ((c - base) / base) * 100;
    });
  }
  let mainRebased = $derived(
    instance.kind === 'pc'
      ? rebasedCloses(data as unknown as { close?: number }[])
      : []
  );
  // Each overlay's rebased array indexed by *main's* time bucket position.
  // We assume Binance OHLCV at the same interval/window aligns 1-to-1 across
  // tokens; if an overlay is missing a bucket, the per-index lookup returns
  // undefined and the line just gaps.
  let overlayRebasedByToken = $derived.by<Record<string, number[]>>(() => {
    const out: Record<string, number[]> = {};
    if (instance.kind !== 'pc') return out;
    for (const tok of instance.overlayTokens ?? []) {
      const rows = overlayData[tok];
      out[tok] = rebasedCloses(rows);
    }
    return out;
  });
  // Distinct palette for the pc chart's lines — main is cyan, the rest pick
  // from this palette in order.
  const OVERLAY_COLORS = ['#fbbf24', '#a855f7', '#22c55e', '#ef4444', '#ec4899'] as const;
  // OHLCV chart is back to its original behaviour: candles + MAs only.
  let ohlcvLinesD = $derived(cumulativeLines);
  // Lines for the Price Comparison chart — main token + every overlay,
  // each rebased to %.
  let pcLinesD = $derived([
    {
      key: 'pc_main',
      label: instance.token,
      color: '#06b6d4',
      compute: (_d: Candle, i: number) => mainRebased[i] ?? 0
    },
    ...(instance.overlayTokens ?? []).map((tok, idx) => ({
      key: `pc_ovl_${tok}`,
      label: tok,
      color: OVERLAY_COLORS[idx % OVERLAY_COLORS.length],
      compute: (_d: Candle, i: number) => (overlayRebasedByToken[tok] ?? [])[i] ?? 0
    }))
  ]);
  let frLinesD = $derived(cumulativeLines);

  // ---- sz threshold apply ----
  function applySzThresholds() {
    const u = Number(instance.underInput);
    const o = Number(instance.overInput);
    if (!Number.isFinite(u) || !Number.isFinite(o) || u < 0 || u >= o) {
      error = 'Require 0 ≤ under < over';
      return;
    }
    instance.under = u;
    instance.over = o;
  }

  // ---- chart-area sizing ----
  // The chart canvas fills whatever's left of the panel after the header.
  // `bind:clientHeight` on the wrapper below feeds the measured height in
  // here; we subtract the loadbar's 2px and clamp to a sensible minimum so
  // the chart still has somewhere to draw before the first layout pass.
  // This makes every size combo (1×1 / 2×1 / 4×1 / 1×2 / 2×2 / 4×2) just
  // fit by construction — no more per-cell hand-tuned canvas heights.
  let chartAreaHeight = $state(0);
  let chartCanvasHeight = $derived(Math.max(140, chartAreaHeight - 2));

  // Encode/decode (width, height) as a single string so we can drive the
  // <select> with a normal bind-style onchange. Mirrors SIZE_CYCLE order.
  let sizeValue = $derived(`${instance.width}x${instance.height}`);
  function onSizeChange(v: string) {
    const [w, h] = v.split('x').map(Number);
    const match = SIZE_CYCLE.find((s) => s.width === w && s.height === h);
    if (!match) return;
    instance.width = match.width as ChartWidth;
    instance.height = match.height as ChartHeight;
  }

  let kindLabel = $derived(CHART_KIND_LABELS[instance.kind]);
  let isTemplate = $derived(typeof instance.templateName === 'string' && instance.templateName.length > 0);
  let displayTitle = $derived(isTemplate ? (instance.templateName as string) : kindLabel);
  let panelTitle = $derived(
    `${displayTitle} — ${isUniswapKind(instance.kind) && instance.uniPool ? fmtUniPool(instance.uniPool) : instance.token} ${instance.interval}` +
      (instance.kind === 'sz' ? ` (< $${instance.under} / > $${instance.over})` : '')
  );

  let settingsOpen = $state(false);
</script>

<div
  class={'rounded-xl border border-zinc-700 bg-zinc-950 overflow-hidden flex flex-col h-full ' +
    (instance.pin && instance.kind === 'ohlcv' ? 'sticky top-0 z-20 shadow-xl shadow-black/60 ' : '')}
  role="region"
  aria-label={panelTitle}
>
  <div
    class={[
      'px-4 py-2 border-b border-zinc-800 chart-titlebar-bg',
      // 1×1 stacks title above controls; bigger sizes keep them side-by-side.
      instance.width === 1
        ? 'flex flex-col items-stretch gap-1.5'
        : 'flex items-center justify-between gap-3'
    ].join(' ')}
  >
    <!-- Title block — click to swap this chart for another kind. The same
         button is also the drag handle (dnd-action distinguishes a click from
         a drag by the mousedown→move sequence). -->
    <button
      type="button"
      onclick={(e) => onSwap(instance.id, e)}
      title="Drag to reorder · Click to swap chart"
      class="cursor-grab active:cursor-grabbing flex items-center gap-2 min-w-0 text-left"
    >
      <span class="text-zinc-500 text-base leading-none select-none">⠿</span>
      <span class="text-zinc-100 text-sm font-semibold tracking-tight truncate">
        {displayTitle}
      </span>
      {#if isTemplate}
        <span
          class="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-900/40 border border-amber-700/40 text-[9px] uppercase tracking-widest text-amber-300"
          title="Template — filter locked"
        >tpl</span>
      {/if}
    </button>

    <!-- Primary controls (always visible) -->
    <div
      class={[
        'flex items-center gap-1.5',
        instance.width === 1 ? 'flex-wrap' : ''
      ].join(' ')}
    >
      {#if isLidoKind(instance.kind)}
        <!-- Lido kinds: chain dropdown. L1 kinds are ETH-pinned (disabled).
             L2 kinds list the chains DeFiStream has actually delivered for
             this kind's event PLUS any compound chain groups (EVM, All)
             surfaced by the server. Selecting a group resolves to
             `chain IN (...)` server-side. -->
        <select
          bind:value={instance.chain}
          disabled={LIDO_L1_KINDS.has(instance.kind)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {#if !LIDO_L1_KINDS.has(instance.kind) && chainGroups.length > 0}
            <optgroup label="Chain">
              {#each lidoChainsForKind as c (c)}
                <option value={c}>{c}</option>
              {/each}
            </optgroup>
            <optgroup label="Chain group">
              {#each chainGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {:else}
            {#each lidoChainsForKind as c (c)}
              <option value={c}>{c}</option>
            {/each}
          {/if}
        </select>
      {:else if isUniswapKind(instance.kind)}
        <!-- Uniswap kinds: chain dropdown (only chains that have ingested
             pools) + a pool dropdown filtered to that chain. Pools are
             sorted by total rows desc so the most-traded pool floats to
             the top of the list. -->
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each (uniChains.length > 0 ? uniChains : ['ETH','ARB','BASE','BSC','POLYGON']) as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
        <select
          value={currentUniPoolKey}
          onchange={(e) => onUniPoolChange(e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 min-w-[10rem]"
        >
          {#if uniPoolsForChain.length === 0}
            {#if instance.uniPool}
              <option value={uniPoolKey(instance.uniPool)}>{fmtUniPool(instance.uniPool)}</option>
            {:else}
              <option value="">(no pools)</option>
            {/if}
          {:else}
            {#each uniPoolsForChain as p ((p.symbol0 + '|' + p.symbol1 + '|' + p.fee))}
              <option value={p.symbol0 + '|' + p.symbol1 + '|' + p.fee}>{fmtUniPool(p)}</option>
            {/each}
          {/if}
        </select>
      {:else if isMorphoKind(instance.kind)}
        <!-- Morpho: ETH + BASE. Same token selector pattern as V2/V3.
             Markets (market_id) are summed across — they aren't a chart
             dimension in V1. -->
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each ['ETH','BASE'] as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          <optgroup label="Tokens">
            {#each ['USDC','USDT','DAI','PYUSD','WETH','WBTC','CBBTC','WSTETH','WEETH'] as t (t)}
              <option value={t}>{t}</option>
            {/each}
            {#if instance.token && !['USDC','USDT','DAI','PYUSD','WETH','WBTC','CBBTC','WSTETH','WEETH'].includes(instance.token) && !tokenGroups.some((g) => g.name === instance.token)}
              <option value={instance.token}>{instance.token}</option>
            {/if}
          </optgroup>
          {#if tokenGroups.length > 0}
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {/if}
        </select>
      {:else if isSparkKind(instance.kind)}
        <!-- Spark: ETH-only (AAVE V3 fork by Sky/Maker). Static ETH chip
             + token selector identical to V3. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">ETH</span>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          <optgroup label="Tokens">
            {#each ['USDC','USDT','DAI','USDS','WETH','WBTC','WSTETH','WEETH','RETH','SDAI'] as t (t)}
              <option value={t}>{t}</option>
            {/each}
            {#if instance.token && !['USDC','USDT','DAI','USDS','WETH','WBTC','WSTETH','WEETH','RETH','SDAI'].includes(instance.token) && !tokenGroups.some((g) => g.name === instance.token)}
              <option value={instance.token}>{instance.token}</option>
            {/if}
          </optgroup>
          {#if tokenGroups.length > 0}
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {/if}
        </select>
      {:else if isGmxKind(instance.kind)}
        <!-- GMX V2: ARB-only (server-side AVAX is "not configured" in 2.18).
             Static chain chip + market dropdown populated from /gmx/streams,
             sorted by row count so the busiest perp floats to the top.
             First option = "All markets" (empty string), meaning the
             aggregate endpoint sums across every market. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">ARB</span>
        <select
          value={instance.gmxMarket ?? ''}
          onchange={(e) => (instance.gmxMarket = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 min-w-[10rem]"
        >
          <option value="">Σ All markets</option>
          {#if gmxMarketsForKind.length === 0 && instance.gmxMarket}
            <!-- Fallback: keep the stored market visible even before the
                 streams list loads (or if it's empty for this kind). -->
            <option value={instance.gmxMarket}>{instance.gmxMarket}</option>
          {/if}
          {#each gmxMarketsForKind as m (m.market)}
            <option value={m.market}>{m.market}</option>
          {/each}
        </select>
      {:else if isHlKind(instance.kind)}
        <!-- Hyperliquid: static HL chip + token dropdown from the binance
             roster + optional wallet filter (free-text EVM address OR
             wallet-label category dropdown — mutually exclusive). The
             top_traders kind hides the wallet filter since it ranks ALL
             wallets by definition. The top_positions kind adds an
             "All tokens" option (empty string) since its leaderboard
             can rank wallets either per-token or by aggregate exposure.
             hl_transfers (bridge flows) is USDC-only — no token select. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">HL</span>
        {#if instance.kind !== 'hl_transfers'}
          <select
            value={instance.token}
            onchange={(e) => (instance.token = e.currentTarget.value)}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          >
            {#if instance.kind === 'hl_top_positions'}
              <option value="">All tokens</option>
            {/if}
            {#each tokens as t (t)}
              <option value={t}>{t}</option>
            {/each}
            {#if instance.token && !tokens.includes(instance.token)}
              <option value={instance.token}>{instance.token}</option>
            {/if}
          </select>
        {:else}
          <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">USDC</span>
        {/if}
        {#if instance.kind !== 'hl_top_traders' && instance.kind !== 'hl_top_positions' && instance.kind !== 'hl_transfers'}
          <input
            type="text"
            placeholder="0x… wallet"
            value={instance.hlWallet ?? ''}
            oninput={(e) => {
              instance.hlWallet = e.currentTarget.value.trim();
              if (instance.hlWallet) instance.hlWalletCategory = '';
            }}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 w-32"
            title="EVM address — case-insensitive. Setting this clears the category filter."
          />
          <select
            value={instance.hlWalletCategory ?? ''}
            onchange={(e) => {
              instance.hlWalletCategory = e.currentTarget.value;
              if (instance.hlWalletCategory) instance.hlWallet = '';
            }}
            disabled={!!instance.hlWallet}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-50"
            title="Wallet category from the smart-money labels dictionary."
          >
            <option value="">All wallets</option>
            <option value="CEX">CEX</option>
            <option value="Smart-Money">Smart Money</option>
            <option value="Whale">Whale</option>
            <option value="Bridge">Bridge</option>
            <option value="MEV-bot">MEV-bot</option>
            <option value="Deposit">Deposit</option>
            <option value="Hot-Wallet">Hot Wallet</option>
            <option value="Cold-Wallet">Cold Wallet</option>
          </select>
        {/if}
      {:else if isAaveV4Kind(instance.kind)}
        <!-- AAVE V4: ETH-only (V4 is mainnet-only currently). Static
             chain chip + the same token selector as V2/V3. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">ETH</span>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          <optgroup label="Tokens">
            {#each ['USDC','USDT','DAI','USDS','GHO','WETH','WBTC'] as t (t)}
              <option value={t}>{t}</option>
            {/each}
            {#if instance.token && !['USDC','USDT','DAI','USDS','GHO','WETH','WBTC'].includes(instance.token) && !tokenGroups.some((g) => g.name === instance.token)}
              <option value={instance.token}>{instance.token}</option>
            {/if}
          </optgroup>
          {#if tokenGroups.length > 0}
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {/if}
        </select>
      {:else if isAaveV2Kind(instance.kind)}
        <!-- AAVE V2: ETH + POLYGON only (the two chains DeFiStream has
             V2 configured for). Same token selector + token-group support
             as V3 since the data shape is identical. -->
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each ['ETH','POLYGON'] as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          <optgroup label="Tokens">
            {#each ['USDC','USDT','DAI','WETH','WBTC','LINK'] as t (t)}
              <option value={t}>{t}</option>
            {/each}
            {#if instance.token && !['USDC','USDT','DAI','WETH','WBTC','LINK'].includes(instance.token) && !tokenGroups.some((g) => g.name === instance.token)}
              <option value={instance.token}>{instance.token}</option>
            {/if}
          </optgroup>
          {#if tokenGroups.length > 0}
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {/if}
        </select>
      {:else if isUniswapV2Kind(instance.kind)}
        <!-- Uniswap V2: chain dropdown (same EVM set) + pool dropdown.
             No fee tier — fmtUniPool's "0.00%" sentinel is suppressed for
             V2 below. -->
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each ['ETH','ARB','BASE','BSC','POLYGON'] as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
        {#if instance.uniPool}
          <span class="text-zinc-100 text-xs font-medium px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">
            {instance.uniPool.symbol0}/{instance.uniPool.symbol1}
          </span>
        {/if}
      {:else if isUniswapV4Kind(instance.kind)}
        <!-- Uniswap V4: chain dropdown + a static pool tag (V4 pool
             identity is a 6-tuple — too wide for a single <select>; we
             show the currently configured pool and let saved layouts
             carry the rest). -->
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each ['ETH','ARB','BASE','BSC','POLYGON'] as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
        {#if instance.uniV4Pool}
          <span class="text-zinc-100 text-xs font-medium px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">
            {instance.uniV4Pool.symbol0}/{instance.uniV4Pool.symbol1}
            <span class="text-zinc-500 ml-1">{(instance.uniV4Pool.fee / 10000).toFixed(2)}% · ts={instance.uniV4Pool.tick_spacing}</span>
          </span>
        {/if}
      {:else if isAeroKind(instance.kind)}
        <!-- Aerodrome: chain is BASE-only; show as static label.
             Pool = (sym0, sym1, tick_spacing). -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">BASE</span>
        {#if instance.aeroPool}
          <span class="text-zinc-100 text-xs font-medium px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">
            {instance.aeroPool.symbol0}/{instance.aeroPool.symbol1}
            <span class="text-zinc-500 ml-1">ts={instance.aeroPool.tick_spacing}</span>
          </span>
        {/if}
      {:else if isAeroBasicKind(instance.kind)}
        <!-- Aerodrome basic: BASE-only; pool = (sym0, sym1, stable). The
             stable flag chip distinguishes vAMM (constant-product) from
             sAMM (stableswap curve). -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">BASE</span>
        {#if instance.aeroBasicPool}
          <span class="text-zinc-100 text-xs font-medium px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">
            {instance.aeroBasicPool.symbol0}/{instance.aeroBasicPool.symbol1}
            <span class="text-zinc-500 ml-1">{instance.aeroBasicPool.stable ? 'sAMM' : 'vAMM'}</span>
          </span>
        {/if}
      {:else if isAaveKind(instance.kind)}
        <!-- AAVE kinds: chain dropdown (5 EVMs + chain groups) + token
             <select> with a "Token group" optgroup so the user can pick
             e.g. "USDC+USDT" or "Stables" and the chart sums across the
             group's members. -->
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#if chainGroups.length > 0}
            <optgroup label="Chain">
              {#each ['ETH','ARB','BASE','BSC','POLYGON'] as c (c)}
                <option value={c}>{c}</option>
              {/each}
            </optgroup>
            <optgroup label="Chain group">
              {#each chainGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {:else}
            {#each ['ETH','ARB','BASE','BSC','POLYGON'] as c (c)}
              <option value={c}>{c}</option>
            {/each}
          {/if}
        </select>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          <optgroup label="Tokens">
            {#each ['USDC','USDT','DAI','USDE','USDS','GHO','WETH','WBTC','WSTETH','CBBTC','LINK','RLUSD','PYUSD','EURC'] as t (t)}
              <option value={t}>{t}</option>
            {/each}
            <!-- If the current token isn't in the standard list, surface it
                 as a sticky entry so the user can see what's selected. -->
            {#if instance.token && !['USDC','USDT','DAI','USDE','USDS','GHO','WETH','WBTC','WSTETH','CBBTC','LINK','RLUSD','PYUSD','EURC'].includes(instance.token) && !tokenGroups.some((g) => g.name === instance.token)}
              <option value={instance.token}>{instance.token}</option>
            {/if}
          </optgroup>
          {#if tokenGroups.length > 0}
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {/if}
        </select>
      {:else if instance.kind === 'transfer'}
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {#if chainGroups.length > 0}
            <optgroup label="Chain">
              {#each chains as c (c)}
                <option value={c}>{c}</option>
              {/each}
            </optgroup>
            <optgroup label="Chain group">
              {#each chainGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {:else}
            {#each chains as c (c)}
              <option value={c}>{c}</option>
            {/each}
          {/if}
        </select>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          disabled={tokensForChain.length <= 1 && tokenGroups.length === 0}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {#if tokenGroups.length > 0}
            <optgroup label={activeChainGroup ? `Tokens on Σ ${activeChainGroup.label}` : `Tokens on ${instance.chain}`}>
              {#each tokensForChain as t (t)}
                <option value={t}>{t}</option>
              {/each}
            </optgroup>
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {:else}
            {#each tokensForChain as t (t)}
              <option value={t}>{t}</option>
            {/each}
          {/if}
        </select>
      {:else}
        {#if instance.kind === 'ohlcv' || instance.kind === 'fr' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'oi'}
          <!-- Exchange selector picks the data source. ohlcv → *_ohlcv_1m,
               fr → binance_funding_rate / hl_funding, bs/sz → *_raw_trades /
               hl_trades. Same render path either way. -->
          <select
            value={instance.exchange ?? 'binance'}
            onchange={(e) => (instance.exchange = e.currentTarget.value as 'binance' | 'hl')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          >
            <option value="binance">Binance</option>
            <option value="hl">Hyperliquid</option>
          </select>
        {/if}
        <select
          value={instance.token}
          onchange={(e) => onTokenChange(instance.id, e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
      {/if}
      <select
        bind:value={instance.interval}
        class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
      >
        {#each INTERVALS as iv (iv)}
          <option value={iv}>{iv}</option>
        {/each}
      </select>
      <button
        type="button"
        onclick={reload}
        title={loading ? 'Loading — click to cancel and retry' : 'Refresh'}
        class="w-7 h-7 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 border border-transparent text-sm leading-none flex items-center justify-center
               {loading ? 'animate-spin' : ''}"
        aria-label="Refresh chart"
      >↻</button>
      <button
        type="button"
        onclick={() => (settingsOpen = !settingsOpen)}
        title="Chart settings"
        aria-pressed={settingsOpen}
        class="w-7 h-7 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 border border-transparent text-sm leading-none flex items-center justify-center
               {settingsOpen ? 'bg-zinc-800 text-zinc-100 border-zinc-700' : ''}"
      >⚙</button>
      <select
        value={sizeValue}
        onchange={(e) => onSizeChange(e.currentTarget.value)}
        title="Chart size"
        class="h-7 bg-zinc-900 border border-zinc-700 rounded-md px-2 text-[11px] font-mono text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
      >
        {#each SIZE_CYCLE as s (s.width + 'x' + s.height)}
          <option value="{s.width}x{s.height}">{s.width}×{s.height}</option>
        {/each}
      </select>
      <button
        type="button"
        onclick={() => onRemove(instance.id)}
        title="Remove chart"
        class="w-7 h-7 rounded-md text-zinc-400 hover:text-red-400 hover:bg-zinc-800 border border-transparent text-sm leading-none flex items-center justify-center"
      >✕</button>
    </div>
  </div>

  <div class="flex-1 relative min-h-0" bind:clientHeight={chartAreaHeight}>

  {#if settingsOpen}
    <div class="absolute inset-0 z-20 bg-zinc-950/95 overflow-y-auto">
    <div class="px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/30 flex items-center gap-3 flex-wrap text-xs">
      {#if instance.kind === 'ohlcv'}
        <label class="flex items-center gap-1.5 text-zinc-300 cursor-pointer">
          <input type="checkbox" bind:checked={instance.pin} class="accent-zinc-400" />
          Pin
        </label>
        <span class="w-px h-4 bg-zinc-800"></span>
        <!-- Volume denomination toggle. 'token' shows raw asset units (BTC,
             ETH, …); 'usd' shows sum(per-1m volume × per-1m close) so the
             volume sub-pane is comparable across assets. Switching is
             instant — the candle data is remapped client-side. -->
        <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Volume</span>
        <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
          <button
            type="button"
            onclick={() => (instance.volumeUnit = 'token')}
            class={'px-2 py-0.5 text-[11px] ' + ((instance.volumeUnit ?? 'token') === 'token'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Plot volume in token units (e.g. BTC, ETH)"
          >Token</button>
          <button
            type="button"
            onclick={() => (instance.volumeUnit = 'usd')}
            class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + (instance.volumeUnit === 'usd'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Plot sum(per-1m volume × per-1m close) — comparable across assets"
          >USD</button>
        </div>
        <span class="w-px h-4 bg-zinc-800"></span>
      {/if}
      {#if instance.kind === 'fr'}
        <!-- Display mode for funding rate. 'rate8h' (default) shows bps over
             a 8-hour window — Binance shown as-is, HL × 8 — so cross-exchange
             magnitudes line up (Coinglass convention). 'apr' annualizes to
             percent-per-year, useful for comparing funding cost against yield
             / borrow rates. Switching is instant (no refetch). -->
        <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Y axis</span>
        <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
          <button
            type="button"
            onclick={() => (instance.frDisplay = 'rate8h')}
            class={'px-2 py-0.5 text-[11px] ' + ((instance.frDisplay ?? 'rate8h') === 'rate8h'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="bps over an 8-hour window (Binance native; HL × 8)"
          >bps / 8h</button>
          <button
            type="button"
            onclick={() => (instance.frDisplay = 'apr')}
            class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + ((instance.frDisplay) === 'apr'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Annualized percent (rate × events-per-year × 100)"
          >APR %</button>
        </div>
        <span class="w-px h-4 bg-zinc-800"></span>
      {/if}
      {#if instance.kind === 'sz'}
        <span class="text-zinc-500">Under</span>
        <input
          bind:value={instance.underInput}
          type="number"
          step="100"
          min="0"
          class="w-20 bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
        />
        <span class="text-zinc-500">Over</span>
        <input
          bind:value={instance.overInput}
          type="number"
          step="100"
          min="0"
          class="w-20 bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
        />
        <button
          type="button"
          onclick={applySzThresholds}
          class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
        >Apply</button>
        <span class="w-px h-4 bg-zinc-800"></span>
      {/if}
      <label class="flex items-center gap-1.5 text-zinc-300 cursor-pointer">
        <input type="checkbox" bind:checked={instance.showPoint} class="accent-zinc-400" />
        Point
      </label>
      <label
        class="flex items-center gap-1.5 text-zinc-300 cursor-pointer"
        title="Dotted vertical lines at the start of each Saturday and Monday (UTC)"
      >
        <input type="checkbox" bind:checked={instance.showWeekLines} class="accent-zinc-400" />
        Week lines
      </label>
      {#if instance.kind === 'transfer' || isAaveKind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind) || isLidoKind(instance.kind) || (isUniswapKind(instance.kind) && instance.kind !== 'uniswap_net_swap_flow') || isUniswapV2Kind(instance.kind) || instance.kind === 'uniswap_v4_swap' || isAeroKind(instance.kind) || isAeroBasicKind(instance.kind)}
        <!-- USD ⇆ Amount toggle. For AAVE / Lido the chart shows a single
             series in either mode. For Uniswap (except net_swap_flow which
             is intrinsically directional USD), Amount mode renders TWO
             lines — token0 on the primary axis and token1 on a secondary
             axis — because their magnitudes can be orders apart. -->
        <span class="w-px h-4 bg-zinc-800"></span>
        <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Y axis</span>
        <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
          <button
            type="button"
            onclick={() => (instance.valueMode = 'usd')}
            class={'px-2 py-0.5 text-[11px] ' + ((instance.valueMode ?? 'usd') === 'usd'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Plot sum of value_usd"
          >USD</button>
          <button
            type="button"
            onclick={() => (instance.valueMode = 'amount')}
            class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + (instance.valueMode === 'amount'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title={isUniswapKind(instance.kind)
              ? 'Plot raw token0 + token1 amounts on independent axes'
              : 'Plot sum of raw token amount (units depend on the event)'}
          >Amount</button>
        </div>
      {/if}
      <span class="w-px h-4 bg-zinc-800"></span>
      {#each instance.mas as ma, idx}
        <div class="flex items-center gap-1.5">
          <label class="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={instance.mas[idx].enabled}
              class="accent-zinc-400"
            />
            <span
              class="font-medium"
              style="color: {MA_COLORS[idx]}; opacity: {ma.enabled ? 1 : 0.55}"
            >MA{idx + 1}</span>
          </label>
          <input
            type="number"
            bind:value={instance.mas[idx].length}
            min="2"
            max="500"
            step="1"
            title="Length"
            class="w-14 bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
          />
          <select
            bind:value={instance.mas[idx].type}
            title="Type"
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
          >
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
            <option value="wma">WMA</option>
          </select>
        </div>
      {/each}
      {#if canSum}
        <!-- Cumulative-sum toggle. Plots the running total of the same field
             the main series plots, on a secondary y-axis so it doesn't squash
             the per-bucket curve. Useful for "TVL increase over the window"
             style reads on AAVE / Morpho / Spark / Lido / transfer / Aero. -->
        <label class="flex items-center gap-1.5 cursor-pointer" title="Cumulative sum on secondary axis">
          <input
            type="checkbox"
            bind:checked={instance.showSum}
            class="accent-zinc-400"
          />
          <span
            class="font-medium"
            style="color: #a78bfa; opacity: {instance.showSum ? 1 : 0.55}"
          >Sum</span>
        </label>
      {/if}
    </div>

    {#if instance.kind === 'pc'}
      <div class="px-4 py-3 border-b border-zinc-800 bg-zinc-900/30 text-xs space-y-2">
        <div class="text-[10px] uppercase tracking-widest text-zinc-500">
          Compare
          <span class="text-zinc-600 normal-case">
            — other tokens to overlay against {instance.token} (Y axis is % change from leftmost data)
          </span>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          {#each (instance.overlayTokens ?? []) as tok, idx (tok)}
            <span
              class="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900 px-1.5 py-0.5"
              style="color: {OVERLAY_COLORS[idx % OVERLAY_COLORS.length]}"
            >
              <span class="font-medium">{tok}</span>
              <button
                type="button"
                aria-label="Remove {tok}"
                title="Remove"
                onclick={() => {
                  instance.overlayTokens = (instance.overlayTokens ?? []).filter((t) => t !== tok);
                }}
                class="text-zinc-500 hover:text-red-400 leading-none"
              >×</button>
            </span>
          {/each}
          {#if (instance.overlayTokens ?? []).length < 5}
            {@const taken = new Set([instance.token, ...(instance.overlayTokens ?? [])])}
            {@const available = tokens.filter((t) => !taken.has(t))}
            {#if available.length > 0}
              <select
                value=""
                onchange={(e) => {
                  const v = e.currentTarget.value;
                  if (!v) return;
                  instance.overlayTokens = [...(instance.overlayTokens ?? []), v];
                  e.currentTarget.value = '';
                }}
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              >
                <option value="">+ add token…</option>
                {#each available as t (t)}
                  <option value={t}>{t}</option>
                {/each}
              </select>
            {:else}
              <span class="text-zinc-600 text-[11px]">no more tokens to add</span>
            {/if}
          {:else}
            <span class="text-zinc-600 text-[11px]">max 5</span>
          {/if}
        </div>
      </div>
    {/if}

    {#if instance.kind === 'transfer' && !isTemplate}
      <div class="px-4 py-3 border-b border-zinc-800 bg-zinc-900/30 text-xs space-y-2">
        <div class="text-[10px] uppercase tracking-widest text-zinc-500">
          Wallet filter
          <span class="text-zinc-600 normal-case">— replaces the chart's main series; MAs follow</span>
        </div>
        {#if activeFilterIsAny}
          <div class="text-[11px] text-zinc-300">
            <span class="text-zinc-500">active:</span>
            <span class="font-mono">{activeFilterLabel}</span>
          </div>
        {/if}
        <datalist id="wallet-cats-{instance.id}">
          {#each walletCategories as c (c.name)}
            <option value={c.name}></option>
          {/each}
        </datalist>
        <datalist id="wallet-ents-{instance.id}">
          {#each walletEntities as e (e.name)}
            <option value={e.name}></option>
          {/each}
        </datalist>

        <div class="text-[10px] uppercase tracking-widest text-zinc-500 pt-1">Categories</div>
        {#each [['sender', 'Sender'], ['receiver', 'Receiver'], ['involving', 'Either']] as [side, label]}
          {#if instance.width === 1}
            <div class="space-y-1">
              <div class="text-zinc-400">{label}</div>
              <input
                type="text"
                list="wallet-cats-{instance.id}"
                bind:value={pendingFilter[`${side}_in` as FilterKey]}
                placeholder="✔ include"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
              <input
                type="text"
                list="wallet-cats-{instance.id}"
                bind:value={pendingFilter[`${side}_ex` as FilterKey]}
                placeholder="✘ exclude"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
            </div>
          {:else}
            <div class="grid grid-cols-[60px_1fr_1fr] items-center gap-2">
              <span class="text-zinc-400">{label}</span>
              <input
                type="text"
                list="wallet-cats-{instance.id}"
                bind:value={pendingFilter[`${side}_in` as FilterKey]}
                placeholder="✔ include"
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
              <input
                type="text"
                list="wallet-cats-{instance.id}"
                bind:value={pendingFilter[`${side}_ex` as FilterKey]}
                placeholder="✘ exclude"
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
            </div>
          {/if}
        {/each}

        <div class="text-[10px] uppercase tracking-widest text-zinc-500 pt-2">Entities</div>
        {#each [['sender', 'Sender'], ['receiver', 'Receiver'], ['involving', 'Either']] as [side, label]}
          {#if instance.width === 1}
            <div class="space-y-1">
              <div class="text-zinc-400">{label}</div>
              <input
                type="text"
                list="wallet-ents-{instance.id}"
                bind:value={pendingFilter[`${side}_entity_in` as FilterKey]}
                placeholder="✔ include"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
              <input
                type="text"
                list="wallet-ents-{instance.id}"
                bind:value={pendingFilter[`${side}_entity_ex` as FilterKey]}
                placeholder="✘ exclude"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
            </div>
          {:else}
            <div class="grid grid-cols-[60px_1fr_1fr] items-center gap-2">
              <span class="text-zinc-400">{label}</span>
              <input
                type="text"
                list="wallet-ents-{instance.id}"
                bind:value={pendingFilter[`${side}_entity_in` as FilterKey]}
                placeholder="✔ include"
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
              <input
                type="text"
                list="wallet-ents-{instance.id}"
                bind:value={pendingFilter[`${side}_entity_ex` as FilterKey]}
                placeholder="✘ exclude"
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
              />
            </div>
          {/if}
        {/each}

        <div class="text-[10px] uppercase tracking-widest text-zinc-500 pt-2">
          Addresses
          <span class="text-zinc-600 normal-case">
            — exact match, comma-separated. Case-insensitive for EVM (0x…), case-sensitive for BTC / TRON.
          </span>
        </div>
        {#each [['sender', 'Sender'], ['receiver', 'Receiver'], ['involving', 'Either']] as [side, label]}
          {#if instance.width === 1}
            <div class="space-y-1">
              <div class="text-zinc-400">{label}</div>
              <input
                type="text"
                bind:value={pendingFilter[`${side}_addr_in` as FilterKey]}
                placeholder="✔ include"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100"
              />
              <input
                type="text"
                bind:value={pendingFilter[`${side}_addr_ex` as FilterKey]}
                placeholder="✘ exclude"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100"
              />
            </div>
          {:else}
            <div class="grid grid-cols-[60px_1fr_1fr] items-center gap-2">
              <span class="text-zinc-400">{label}</span>
              <input
                type="text"
                bind:value={pendingFilter[`${side}_addr_in` as FilterKey]}
                placeholder="✔ include"
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100"
              />
              <input
                type="text"
                bind:value={pendingFilter[`${side}_addr_ex` as FilterKey]}
                placeholder="✘ exclude"
                class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100"
              />
            </div>
          {/if}
        {/each}

        <div class="flex items-center gap-2 pt-2">
          <button
            type="button"
            onclick={applyFilter}
            disabled={!pendingDiffers}
            class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-md px-3 py-1 text-xs text-zinc-100"
          >Apply</button>
          <button
            type="button"
            onclick={clearFilter}
            class="text-zinc-500 hover:text-zinc-200 text-xs"
          >Clear</button>
          {#if pendingDiffers}
            <span class="text-amber-400 text-[11px]">unsaved</span>
          {/if}
        </div>
      </div>
    {/if}
    </div>
  {/if}

    <!-- Indeterminate load strip — visible whenever a fetch is in flight (chain/token/interval/filter change). -->
    <div class="loadbar h-0.5 overflow-hidden bg-blue-500/10" aria-hidden="true">
      {#if loading}
        <div class="loadbar-track"></div>
      {/if}
    </div>

    {#if error}
      <div class="p-3 text-xs text-red-300 bg-red-950/30">{error}</div>
    {/if}
    {#if data.length === 0}
      <div class="p-4 text-sm text-zinc-400">No data for {kindLabel}.</div>
    {:else if instance.kind === 'ohlcv'}
      <CandlestickChart
        candles={ohlcvCandles}
        lines={ohlcvLinesD}
        showCandles={instance.showPoint}
        formatVolume={(instance.volumeUnit ?? 'token') === 'usd'
          ? fmtUsdCompact
          : (v: number) => v.toFixed(2)}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
      />
    {:else if instance.kind === 'pc'}
      <LineChart
        data={data as Candle[]}
        lines={pcLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
        formatTooltip={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`}
      />
    {:else if instance.kind === 'oi'}
      <LineChart
        data={data as OpenInterestRow[]}
        lines={oiLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {:else if instance.kind === 'fr'}
      <SignedBarChart
        data={frBpsData}
        valueKey="rate_bps"
        lines={frLinesD}
        showBars={instance.showPoint}
        valueLabel={frIsApr ? 'APR' : 'Rate'}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => v.toFixed(2)}
        formatTooltip={(v) => `${v.toFixed(2)} ${frIsApr ? '%' : 'bps/8h'}`}
        minBarWidthPx={3}
      />
    {:else if instance.kind === 'bs'}
      <StackedBarChart
        data={data as VolumeBucket[]}
        series={bsBars}
        lines={bsLines}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
      />
    {:else if instance.kind === 'sz'}
      <StackedBarChart
        data={data as VolumeBucket[]}
        series={szBars}
        lines={szLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
      />
    {:else if instance.kind === 'tt'}
      <LineChart
        data={data as LongShortRow[]}
        lines={ttLinesD}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => v.toFixed(2)}
        formatTooltip={(v) => v.toFixed(4)}
      />
    {:else if instance.kind === 'ls'}
      <LineChart
        data={data as LongShortRow[]}
        lines={lsLinesD}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => v.toFixed(2)}
        formatTooltip={(v) => v.toFixed(4)}
      />
    {:else if instance.kind === 'transfer'}
      <LineChart
        data={data as TransferBucket[]}
        lines={transferLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={transferUseUsd ? fmtUsdAxis : fmtAmountAxis}
        formatTooltip={transferUseUsd ? fmtUsdTooltip : fmtAmountTooltip}
      />
    {:else if instance.kind === 'hl_top_traders'}
      <TableChart leaders={data.length > 0 ? ((data[0] as unknown as {leaders?: Record<string, unknown>[]}).leaders ?? []) : []} />
    {:else if instance.kind === 'hl_top_positions'}
      <HlTopPositionsChart
        wallets={data.length > 0 ? ((data[0] as unknown as {wallets?: unknown[]}).wallets ?? []) : []}
        selectedWallet={instance.hlSelectedWallet ?? ''}
        onSelectWallet={(w) => (instance.hlSelectedWallet = w)}
      />
    {:else if instance.kind === 'hl_unrealized_pnl'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={hlUnrealizedLinesD}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {:else if instance.kind === 'hl_oi_split'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={hlOiSplitLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {:else if instance.kind === 'hl_transfers'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={hlBridgeFlowsLinesD}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {:else if isAaveKind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind) || isGmxKind(instance.kind) || isHlKind(instance.kind)}
      <LineChart
        data={data as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>}
        lines={aaveLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={valueAxisFn}
        formatTooltip={valueTooltipFn}
      />
    {:else if isUniswapKind(instance.kind) || isUniswapV2Kind(instance.kind) || isUniswapV4Kind(instance.kind) || isAeroKind(instance.kind) || isAeroBasicKind(instance.kind)}
      <LineChart
        data={data as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>}
        lines={uniswapLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={uniswapValueModeEffective === 'amount' ? fmtAmountAxis : fmtUsdAxis}
        formatTooltip={uniswapValueModeEffective === 'amount' ? fmtAmountTooltip : fmtUsdTooltip}
        formatY2={uniswapValueModeEffective === 'amount' ? fmtAmountAxis : undefined}
        formatTooltip2={uniswapValueModeEffective === 'amount' ? fmtAmountTooltip : undefined}
      />
    {:else if isLidoKind(instance.kind)}
      <LineChart
        data={data as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>}
        lines={lidoLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={valueAxisFn}
        formatTooltip={valueTooltipFn}
      />
    {/if}

  </div>
</div>

<style>
  /* Indeterminate progress strip — a coloured segment slides left→right→left
     across a translucent track. Lives in scoped CSS because Tailwind ships no
     ready-made indeterminate keyframes. */
  .loadbar {
    position: relative;
  }
  .loadbar-track {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 35%;
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgb(96 165 250) 50%,
      transparent 100%
    );
    animation: loadbar-slide 1.1s ease-in-out infinite;
  }
  @keyframes loadbar-slide {
    0%   { left: -35%; }
    100% { left: 100%; }
  }
</style>
