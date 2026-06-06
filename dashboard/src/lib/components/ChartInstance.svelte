<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import LineChart from '$lib/components/LineChart.svelte';
  import TableChart from '$lib/components/TableChart.svelte';
  import HlTopPositionsChart from '$lib/components/HlTopPositionsChart.svelte';
  import HlTopVaultsTable from '$lib/components/HlTopVaultsTable.svelte';
  import HlTopVaultLpsTable from '$lib/components/HlTopVaultLpsTable.svelte';
  import HlVaultDetailChart from '$lib/components/HlVaultDetailChart.svelte';
  import WalletLeaderboardTable from '$lib/components/WalletLeaderboardTable.svelte';
  import SignedBarChart from '$lib/components/SignedBarChart.svelte';
  import { onMount, untrack } from 'svelte';
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
    ZERO_REF,
    OI_LINES,
    TOP_TRADERS_LINES,
    defaultView,
    fmtAmountAxis,
    fmtAmountTooltip,
    fmtUsdAxis,
    fmtUsdTooltip,
    fmtUsdCompact,
    fmtRatio,
    lookbackWindow,
    maArray,
    sizeLines,
    sizeSeries,
    unixSec,
    weekBoundariesSec,
    AAVE_V3_CHART_KINDS,
    AAVE_V3_KIND_TO_EVENT,
    AAVE_V3_NET_KIND_TO_EVENTS,
    isAaveV3Kind,
    AAVE_V2_CHART_KINDS,
    AAVE_V2_KIND_TO_EVENT,
    AAVE_V2_NET_KIND_TO_EVENTS,
    isAaveV2Kind,
    AAVE_V4_CHART_KINDS,
    AAVE_V4_KIND_TO_EVENT,
    AAVE_V4_NET_KIND_TO_EVENTS,
    isAaveV4Kind,
    MORPHO_CHART_KINDS,
    MORPHO_KIND_TO_EVENT,
    MORPHO_NET_KIND_TO_EVENTS,
    isMorphoKind,
    chartKindShortLabel,
    chartKindGroup,
    SPARK_CHART_KINDS,
    SPARK_KIND_TO_EVENT,
    SPARK_NET_KIND_TO_EVENTS,
    isSparkKind,
    GMX_V2_CHART_KINDS,
    GMX_V2_KIND_TO_EVENT,
    GMX_V2_NET_KIND_TO_EVENTS,
    GMX_V2_PRIMARY_FIELD,
    isGmxV2Kind,
    HL_KIND_TO_EVENT,
    HL_PRIMARY_FIELD,
    isHlKind,
    UNISWAP_V3_CHART_KINDS,
    UNISWAP_V3_KIND_TO_EVENT,
    UNISWAP_V3_NET_KIND_TO_EVENTS,
    isUniswapV3Kind,
    UNISWAP_V2_CHART_KINDS,
    UNISWAP_V2_KIND_TO_EVENT,
    UNISWAP_V2_NET_KIND_TO_EVENTS,
    isUniswapV2Kind,
    UNISWAP_V4_CHART_KINDS,
    UNISWAP_V4_KIND_TO_EVENT,
    UNISWAP_V4_NET_KIND_TO_EVENTS,
    isUniswapV4Kind,
    AERO_CL_CHART_KINDS,
    AERO_CL_KIND_TO_EVENT,
    AERO_CL_NET_KIND_TO_EVENTS,
    isAeroClKind,
    AERO_BASIC_CHART_KINDS,
    AERO_BASIC_KIND_TO_EVENT,
    AERO_BASIC_NET_KIND_TO_EVENTS,
    isAeroBasicKind,
    fmtUniPool,
    LIDO_CHART_KINDS,
    LIDO_KIND_TO_EVENT,
    LIDO_NET_KIND_TO_EVENTS,
    LIDO_L1_KINDS,
    isLidoKind,
    isLeaderboardKind,
    LEADERBOARD_KIND_CONFIG,
    type LeaderboardMetric,
    overlayChipLabel,
    nextOverlayColor,
    OVERLAY_KIND_SERIES,
    type ChartInstance as ChartInstanceT,
    type ChartOverlay,
    type TransferFilters,
    type UniPool
  } from '$lib/components/charts/config';
  import { fetchOverlayData, type OverlayPoint } from '$lib/components/charts/overlay-fetch';
  import AddOverlayDialog from '$lib/components/AddOverlayDialog.svelte';
  import SmartWalletSelector from '$lib/components/SmartWalletSelector.svelte';
  import SmartWalletsDialog from '$lib/components/SmartWalletsDialog.svelte';
  import {
    defaultSmartSelectorState,
    smartSelectorCacheKey
  } from '$lib/components/charts/smartSelector';
  import Pencil from '@lucide/svelte/icons/pencil';
  import PlusCircle from '@lucide/svelte/icons/plus-circle';
  import type { View } from '$lib/chart-zoom';
  import { queuedFetch } from '$lib/fetch-queue';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';

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

  // ---- effective kind ----
  // The general wrapper kinds (morpho, spark, aave_v2/v3/v4, uniswap_v2/v3/v4)
  // delegate to a concrete *_subkind selected by the in-chart picker. Every
  // protocol routing lookup (KIND_TO_EVENT, NET_KIND_TO_EVENTS, cache key,
  // per-kind inline comparisons like uniswap_v3_net_swap_flow) reads through
  // this so switching subkinds re-fetches the right event. For every other
  // kind, effectiveKind is just instance.kind.
  let effectiveKind = $derived(
    instance.kind === 'morpho'
      ? ((instance.morphoSubkind ?? 'morpho_supply') as ChartInstanceT['kind'])
      : instance.kind === 'spark'
      ? ((instance.sparkSubkind ?? 'spark_deposit') as ChartInstanceT['kind'])
      : instance.kind === 'aave_v3'
      ? ((instance.aaveV3Subkind ?? 'aave_v3_deposit') as ChartInstanceT['kind'])
      : instance.kind === 'aave_v2'
      ? ((instance.aaveV2Subkind ?? 'aave_v2_deposit') as ChartInstanceT['kind'])
      : instance.kind === 'aave_v4'
      ? ((instance.aaveV4Subkind ?? 'aave_v4_deposit') as ChartInstanceT['kind'])
      : instance.kind === 'uniswap_v3'
      ? ((instance.uniswapV3Subkind ?? 'uniswap_v3_swap') as ChartInstanceT['kind'])
      : instance.kind === 'uniswap_v2'
      ? ((instance.uniswapV2Subkind ?? 'uniswap_v2_swap') as ChartInstanceT['kind'])
      : instance.kind === 'uniswap_v4'
      ? ((instance.uniswapV4Subkind ?? 'uniswap_v4_swap') as ChartInstanceT['kind'])
      : instance.kind === 'aero_cl'
      ? ((instance.aeroClSubkind ?? 'aero_cl_swap') as ChartInstanceT['kind'])
      : instance.kind === 'aero_basic'
      ? ((instance.aeroBasicSubkind ?? 'aero_basic_swap') as ChartInstanceT['kind'])
      : instance.kind === 'lido'
      ? ((instance.lidoSubkind ?? 'lido_deposit') as ChartInstanceT['kind'])
      : instance.kind === 'gmx_v2'
      ? ((instance.gmxV2Subkind ?? 'gmx_v2_position_increase') as ChartInstanceT['kind'])
      : instance.kind
  );

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
  // Applies to V3 charts AND the V3 leaderboard kind (both use the same
  // (chain, sym0, sym1, fee) pool selector).
  $effect(() => {
    if (!isUniswapV3Kind(instance.kind) && instance.kind !== 'uniswap_v3_top_wallets') return;
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
    if (LIDO_L1_KINDS.has(effectiveKind)) return ['ETH'];
    // L2 kinds: filter streams to the relevant event(s) for the kind.
    const targetEvents: string[] = (() => {
      const single = LIDO_KIND_TO_EVENT[effectiveKind];
      if (single) return [single];
      const net = LIDO_NET_KIND_TO_EVENTS[effectiveKind];
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
    if (LIDO_L1_KINDS.has(effectiveKind)) {
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
  // Display helper: market_name from DS is `BTC/USD [WBTC-USDC]` — the
  // bracketed pool token-pair is noise for picker UI, hide it. The stored
  // value (instance.gmxMarket) keeps the full string the server needs.
  function gmxMarketShort(m: string): string {
    return m.replace(/\s*\[[^\]]*\]\s*$/, '');
  }
  let gmxMarketsForKind = $derived.by<{ market: string; rows: number }[]>(() => {
    if (!isGmxV2Kind(instance.kind)) return [];
    // Identify which underlying events the kind reads from.
    const evs: string[] = (() => {
      const single = GMX_V2_KIND_TO_EVENT[effectiveKind];
      if (single) return [single];
      const net = GMX_V2_NET_KIND_TO_EVENTS[effectiveKind];
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
    if (instance.kind !== 'transfer' && instance.kind !== 'exchange_flow') return;
    if (activeTokenGroup !== null) return;
    if (tokensForChain.length > 0 && !tokensForChain.includes(instance.token)) {
      // Prefer USDT when available (stable, broadest cross-chain coverage),
      // else fall back to the first listed token on the chain.
      instance.token = tokensForChain.includes('USDT') ? 'USDT' : tokensForChain[0];
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
    if (instance.kind === 'exchange_flow') {
      // The flow type selector (inflow/outflow/netflow/all) doesn't bust
      // the cache — we always fetch both sides and pick at render time.
      // Exchange does bust, since it changes which filters are sent.
      const ex = instance.exchangeFlowExchange ?? 'binance';
      const cPart = activeChainGroup ? `cg:${activeChainGroup.name}` : (instance.chain ?? '');
      const tPart = activeTokenGroup !== null ? `tg:${activeTokenGroup}` : instance.token;
      return `${instance.kind}|${cPart}|${tPart}|${ex}|${instance.interval}`;
    }
    if (instance.kind === 'pc') {
      // Overlay tokens influence the rendered chart, so they belong in the
      // cache key. Sorted so order-of-add doesn't bust the key. Exchange
      // included since switching Binance ↔ HL pulls from a different
      // ohlcv table.
      const ov = [...(instance.overlayTokens ?? [])].sort().join(',');
      const ex = instance.exchange ?? 'binance';
      return `${instance.kind}|${instance.token}|${ex}|${instance.interval}|ov:${ov}`;
    }
    if (isLeaderboardKind(instance.kind)) {
      // Top-wallets leaderboards: AAVE kinds key on (chain, token); Uniswap
      // kinds key on the pool tuple. Metric + top-N always included. No
      // interval — it's a single-shot rollup.
      const cfg = LEADERBOARD_KIND_CONFIG[instance.kind]!;
      const m = instance.leaderboardMetric ?? cfg.defaultMetric;
      const n = instance.leaderboardTopN ?? 10;
      let scope = '';
      if (cfg.paramShape === 'aave') {
        const cPart = activeChainGroup ? `cg:${activeChainGroup.name}` : (instance.chain ?? '');
        const tPart = activeTokenGroup !== null ? `tg:${activeTokenGroup}` : instance.token;
        scope = `${cPart}|${tPart}`;
      } else if (cfg.paramShape === 'uniswap_v3' || cfg.paramShape === 'uniswap_v2') {
        scope = `${instance.chain ?? ''}|${uniPoolKey(instance.uniPool)}`;
      } else if (cfg.paramShape === 'uniswap_v4') {
        const p = instance.uniV4Pool;
        scope = `${instance.chain ?? ''}|${p ? `${p.symbol0}|${p.symbol1}|${p.fee}|${p.tick_spacing}|${p.hooks}` : ''}`;
      }
      return `${instance.kind}|${scope}|m:${m}|n:${n}`;
    }
    if (isAaveV3Kind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind)) {
      // AAVE charts (single-event + net) depend on chain + token (event_type
      // derived from kind). Either axis may be a group name — fold the
      // group flag into the key so toggling busts the cache. Same shape
      // for AAVE V2 (different endpoint, identical key shape). For the
      // general Morpho wrapper we key on effectiveKind so the subkind
      // selector (Supplies / Net Borrow / …) busts the cache and re-fires
      // the right /api/morpho/aggregate?event=… fetch.
      const cPart = activeChainGroup ? `cg:${activeChainGroup.name}` : (instance.chain ?? '');
      const tPart = activeTokenGroup !== null ? `tg:${activeTokenGroup}` : instance.token;
      return `${effectiveKind}|${cPart}|${tPart}|${instance.interval}`;
    }
    if (isGmxV2Kind(instance.kind)) {
      // GMX charts depend on chain (ARB-only for now) + market_name selector.
      // Empty market = "all markets summed" — folded into the key so toggling
      // busts the cache. effectiveKind so the 'gmx_v2' wrapper re-fetches
      // when the in-chart subkind selector flips.
      const cPart = instance.chain ?? 'ARB';
      const mPart = instance.gmxMarket ? `m:${instance.gmxMarket}` : 'all';
      return `${effectiveKind}|${cPart}|${mPart}|${instance.interval}`;
    }
    if (isHlKind(instance.kind)) {
      // HL: per-token + optional wallet OR wallet_category filter (mutually
      // exclusive). Empty wallet filter = aggregate across all traders.
      // hl_top_vaults adds its sort selector to the key so flipping the
      // sort triggers a re-fetch.
      const wPart = instance.hlWallet
        ? `w:${instance.hlWallet.toLowerCase()}`
        : (instance.hlWalletCategory ? `wc:${instance.hlWalletCategory}` : 'all');
      const sortPart = instance.kind === 'hl_top_vaults'
        ? `|sort:${instance.hlVaultSortBy ?? 'net'}` : '';
      // hl_pnl side flips the endpoint (trade_history aggregate vs
      // realized_pnl_split from hl_fills) — must bust the cache.
      const sidePart = instance.kind === 'hl_pnl'
        ? `|side:${instance.hlPnlSide ?? 'total'}` : '';
      return `${instance.kind}|${instance.token}|${wPart}|${instance.interval}${sortPart}${sidePart}`;
    }
    if (isUniswapV3Kind(instance.kind) || isUniswapV2Kind(instance.kind)) {
      // Uniswap V2/V3 charts: pool keyed by (sym0, sym1, fee) — fee=0 marks V2.
      // Key on effectiveKind so the general 'uniswap_v3'/'uniswap_v2'
      // wrappers re-fetch when the in-chart subkind selector flips
      // (Swaps → Deposits → Net Liquidity → …).
      const cPart = instance.chain ?? '';
      const pPart = uniPoolKey(instance.uniPool);
      return `${effectiveKind}|${cPart}|${pPart}|${instance.interval}`;
    }
    if (isUniswapV4Kind(instance.kind)) {
      // V4 pool keyed by 5-tuple (sym0, sym1, fee, tick_spacing, hooks).
      // effectiveKind for the same reason as V2/V3 above.
      const p = instance.uniV4Pool;
      const pPart = p ? `${p.symbol0}|${p.symbol1}|${p.fee}|${p.tick_spacing}|${p.hooks}` : '';
      return `${effectiveKind}|${instance.chain ?? ''}|${pPart}|${instance.interval}`;
    }
    if (isAeroClKind(instance.kind)) {
      // Key on effectiveKind so the 'aero_cl' wrapper re-fetches when the
      // in-chart subkind selector flips (Swaps → Deposits → Net Liquidity).
      const p = instance.aeroPool;
      const pPart = p ? `${p.symbol0}|${p.symbol1}|${p.tick_spacing}` : '';
      return `${effectiveKind}|${instance.chain ?? 'BASE'}|${pPart}|${instance.interval}`;
    }
    if (isAeroBasicKind(instance.kind)) {
      // Same effectiveKind rule as aero_cl above.
      const p = instance.aeroBasicPool;
      const pPart = p ? `${p.symbol0}|${p.symbol1}|${p.stable ? 's' : 'v'}` : '';
      return `${effectiveKind}|${instance.chain ?? 'BASE'}|${pPart}|${instance.interval}`;
    }
    if (isLidoKind(instance.kind)) {
      // Lido charts are keyed by (kind, chain | chain_group, interval). L1
      // kinds are ETH-pinned but we include the axis anyway. The cg: prefix
      // makes "EVM" (group) cache-distinct from a literal "EVM" chain name.
      // effectiveKind so the 'lido' wrapper re-fetches when the subkind
      // selector flips (Deposits → L2 Bridge → Net Stake / …).
      const cPart = activeChainGroup
        ? `cg:${activeChainGroup.name}`
        : (instance.chain ?? '');
      return `${effectiveKind}|${cPart}|${instance.interval}`;
    }
    if (instance.kind === 'ohlcv' || instance.kind === 'fr' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'oi' || instance.kind === 'ls') {
      // Exchange selector busts the cache so flipping Binance ↔ HL re-fetches.
      const ex = instance.exchange ?? 'binance';
      return `${instance.kind}|${instance.token}|${ex}|${instance.interval}`;
    }
    if (instance.kind === 'hl_smart_oi') {
      // The smart-money OI series materially changes when any selector
      // knob moves — fold the full selector state into the cache key.
      const selKey = smartSelectorCacheKey(
        instance.smartSelector ?? defaultSmartSelectorState()
      );
      return `${instance.kind}|${instance.token}|${instance.interval}|${selKey}`;
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
      limit: '200000'
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
      // Transfer + AAVE / HL / DEX / etc. event kinds use a fixed window
      // regardless of interval (they're sparse compared to OHLCV); other
      // kinds use the per-interval lookback window. The window length
      // tracks the ClickHouse table TTL — currently 60 days. (It used to
      // be 30 when TTL=30d; when TTL was bumped to 60 this cap was
      // missed, which silently truncated HL Bridge Flows / etc. to half
      // the available data.)
      let sinceIso: string;
      let untilIso: string;
      const isWideWindowKind =
        instance.kind === 'transfer' ||
        isAaveV3Kind(instance.kind) ||
        isAaveV2Kind(instance.kind) ||
        isAaveV4Kind(instance.kind) ||
        isMorphoKind(instance.kind) ||
        isSparkKind(instance.kind) ||
        isGmxV2Kind(instance.kind) ||
        isHlKind(instance.kind) ||
        isUniswapV3Kind(instance.kind) ||
        isUniswapV2Kind(instance.kind) ||
        isUniswapV4Kind(instance.kind) ||
        isAeroClKind(instance.kind) ||
        isAeroBasicKind(instance.kind) ||
        isLidoKind(instance.kind) ||
        isLeaderboardKind(instance.kind);
      if (isWideWindowKind) {
        const now = new Date();
        const tu = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
        const ts = new Date(tu.getTime() - 60 * 24 * 60 * 60 * 1000);
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
        limit: '200000'
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
            limit: '200000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = MORPHO_NET_KIND_TO_EVENTS[effectiveKind];
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
        const morphoEvent = MORPHO_KIND_TO_EVENT[effectiveKind];
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
            limit: '200000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = SPARK_NET_KIND_TO_EVENTS[effectiveKind];
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
        const sparkEvent = SPARK_KIND_TO_EVENT[effectiveKind];
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
      if (isGmxV2Kind(instance.kind)) {
        const buildGmxQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            chain: instance.chain ?? 'ARB',
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '200000'
          });
          if (instance.gmxMarket && instance.gmxMarket.length > 0) {
            qs.set('market', instance.gmxMarket);
          }
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const primary = GMX_V2_PRIMARY_FIELD[effectiveKind] ?? 'sum_value_usd';
        // Normalise a server bucket onto a single value picked by primary.
        // Returned datum mirrors both fields so downstream code can keep
        // reading sum_amount / sum_value_usd uniformly.
        const pick = (r: Record<string, number>): number => Number(r[primary] ?? 0);
        // For events with an `is_long` column the server emits per-side sums
        // alongside the total — we copy them across so the render layer can
        // pull Long / Short / Net out of the same datum. The "_long_field"
        // here mirrors the chosen `primary` field (sum_amount / sum_value_usd).
        const longField  = primary === 'sum_amount' ? 'sum_amount_long'  : 'sum_value_usd_long';
        const shortField = primary === 'sum_amount' ? 'sum_amount_short' : 'sum_value_usd_short';
        const pickLong  = (r: Record<string, number>): number => Number(r[longField]  ?? 0);
        const pickShort = (r: Record<string, number>): number => Number(r[shortField] ?? 0);

        const netEvs = GMX_V2_NET_KIND_TO_EVENTS[effectiveKind];
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
          const negByTime = new Map<number, { val: number; long: number; short: number; count: number }>();
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            negByTime.set(r.time, { val: pick(r), long: pickLong(r), short: pickShort(r), count: r.count });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (posBody.series ?? []) as Array<Record<string, number>>) {
            const n = negByTime.get(r.time) ?? { val: 0, long: 0, short: 0, count: 0 };
            const diff = pick(r) - n.val;
            out.push({
              time: r.time, sum_amount: diff, sum_value_usd: diff, count: r.count + n.count,
              sum_amount_long: pickLong(r) - n.long,
              sum_amount_short: pickShort(r) - n.short
            });
            seen.add(r.time);
          }
          for (const r of (negBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({
              time: r.time, sum_amount: -pick(r), sum_value_usd: -pick(r), count: r.count,
              sum_amount_long: -pickLong(r),
              sum_amount_short: -pickShort(r)
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
        const gmxEvent = GMX_V2_KIND_TO_EVENT[effectiveKind];
        if (gmxEvent) {
          const res = await queuedFetch(`/api/gmx/aggregate?${buildGmxQs(gmxEvent)}`, { signal });
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          const out: Record<string, number>[] = [];
          for (const r of (body.series ?? []) as Array<Record<string, number>>) {
            const v = pick(r);
            out.push({
              time: r.time, sum_amount: v, sum_value_usd: v, count: r.count,
              sum_amount_long: pickLong(r),
              sum_amount_short: pickShort(r)
            });
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
      // Hyperliquid vault flow: 3-line deposit/withdraw/net per bucket
      // over hl_vaults. Same render shape as bridge_flows. Replaces the
      // old single-sum hl_vault_net path (which was misleadingly summing
      // every action type together regardless of direction).
      if (instance.kind === 'hl_vault_net') {
        const qs = new URLSearchParams({
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '200000'
        });
        const res = await queuedFetch(`/api/hyperliquid/vault_flow?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = (body.series ?? []) as unknown as AnyDatum[];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Hyperliquid top vaults: leaderboard with sort selector. Switching
      // the sort triggers a re-fetch (loadKey includes hlVaultSortBy).
      if (instance.kind === 'hl_top_vaults') {
        const qs = new URLSearchParams({
          since: sinceIso,
          until: untilIso,
          limit: '20',
          order_by: instance.hlVaultSortBy ?? 'net'
        });
        const res = await queuedFetch(`/api/hyperliquid/top_vaults?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ vaults: body.vaults ?? [] } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Hyperliquid top vault LPs.
      if (instance.kind === 'hl_top_vault_lps') {
        const qs = new URLSearchParams({
          since: sinceIso,
          until: untilIso,
          limit: '20'
        });
        const res = await queuedFetch(`/api/hyperliquid/top_vault_lps?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ lps: body.lps ?? [] } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
      // Hyperliquid vault detail: top-N vaults + each vault's recent
      // activity log. One fetch — vault dropdown switches instantly.
      if (instance.kind === 'hl_vault_detail') {
        const qs = new URLSearchParams({
          since: sinceIso,
          until: untilIso,
          limit: '10',
          recent_n: '50'
        });
        const res = await queuedFetch(`/api/hyperliquid/vault_detail?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ vaults: body.vaults ?? [] } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(instance.id, { key: loadedKey, data, since, until, localView });
        return;
      }
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
          limit: '200000'
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
          limit: '200000'
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
      // HL Realized PnL with a per-side selector — switch from the
      // trade_history aggregate (which only carries net per-wallet) to
      // the dedicated split endpoint sourced from hl_fills. 'total' falls
      // through to the existing aggregate path so the default chart
      // remains identical.
      if (instance.kind === 'hl_pnl' && (instance.hlPnlSide ?? 'total') !== 'total') {
        const qs = new URLSearchParams({
          token: instance.token,
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '200000'
        });
        if (instance.hlWallet && instance.hlWallet.length > 0) {
          qs.set('wallet', instance.hlWallet);
        } else if (instance.hlWalletCategory && instance.hlWalletCategory.length > 0) {
          qs.set('wallet_category', instance.hlWalletCategory);
        }
        const res = await queuedFetch(`/api/hyperliquid/realized_pnl_split?${qs}`, { signal });
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
            limit: '200000'
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
      // Top-wallets leaderboard kinds (aave_v*_top_wallets). One JSON fetch
      // per render — server-side GROUP BY wallet across all 5 events. Carries
      // the leader rows as a single AnyDatum payload (same shape trick as
      // hl_top_traders above).
      if (isLeaderboardKind(instance.kind)) {
        const cfg = LEADERBOARD_KIND_CONFIG[instance.kind]!;
        const qs = new URLSearchParams({
          since: sinceIso,
          until: untilIso,
          order_by: instance.leaderboardMetric ?? cfg.defaultMetric,
          limit: String(Math.max(1, Math.min(200, instance.leaderboardTopN ?? 10)))
        });
        if (cfg.paramShape === 'aave') {
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
        } else if (cfg.paramShape === 'uniswap_v3' || cfg.paramShape === 'uniswap_v2') {
          const pool = instance.uniPool;
          if (!pool) throw new Error(`${instance.kind} missing pool selection`);
          qs.set('chain', instance.chain ?? 'ETH');
          qs.set('symbol0', pool.symbol0);
          qs.set('symbol1', pool.symbol1);
          if (cfg.paramShape === 'uniswap_v3') qs.set('fee_tier', String(pool.fee));
        } else if (cfg.paramShape === 'uniswap_v4') {
          const pool = instance.uniV4Pool;
          if (!pool) throw new Error(`${instance.kind} missing pool selection`);
          qs.set('chain', instance.chain ?? 'ETH');
          qs.set('symbol0', pool.symbol0);
          qs.set('symbol1', pool.symbol1);
          qs.set('fee', String(pool.fee));
          qs.set('tick_spacing', String(pool.tick_spacing));
          qs.set('hooks', pool.hooks);
        }
        const res = await queuedFetch(`${cfg.endpoint}?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ leaders: body.leaders ?? [] } as unknown as AnyDatum];
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
            limit: '200000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netV4Events = AAVE_V4_NET_KIND_TO_EVENTS[effectiveKind];
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
        const v4Event = AAVE_V4_KIND_TO_EVENT[effectiveKind];
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
            limit: '200000'
          });
          if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
          else qs.set('chain', instance.chain ?? 'ETH');
          if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
          else qs.set('token', instance.token);
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netV2Events = AAVE_V2_NET_KIND_TO_EVENTS[effectiveKind];
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
        const v2Event = AAVE_V2_KIND_TO_EVENT[effectiveKind];
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
      const aaveNetEvents = AAVE_V3_NET_KIND_TO_EVENTS[effectiveKind];
      if (aaveNetEvents) {
        const [posEvent, negEvent] = aaveNetEvents;
        const buildAaveQs = (event: string) => {
          const qs = new URLSearchParams({
            event,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '200000'
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
            limit: '200000'
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
        const lidoNetEvents = LIDO_NET_KIND_TO_EVENTS[effectiveKind];
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
        const lidoEvent = LIDO_KIND_TO_EVENT[effectiveKind];
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
            limit: '200000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = UNISWAP_V4_NET_KIND_TO_EVENTS[effectiveKind];
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
        const eventForKind = UNISWAP_V4_KIND_TO_EVENT[effectiveKind];
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
            limit: '200000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = AERO_BASIC_NET_KIND_TO_EVENTS[effectiveKind];
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
        const eventForKind = AERO_BASIC_KIND_TO_EVENT[effectiveKind];
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
      if (isAeroClKind(instance.kind)) {
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
            limit: '200000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = AERO_CL_NET_KIND_TO_EVENTS[effectiveKind];
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
        const eventForKind = AERO_CL_KIND_TO_EVENT[effectiveKind];
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
            limit: '200000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const netEvs = UNISWAP_V2_NET_KIND_TO_EVENTS[effectiveKind];
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
        const eventForKind = UNISWAP_V2_KIND_TO_EVENT[effectiveKind];
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
      // once. uniswap_v3_net_liquidity needs two parallel calls (deposit −
      // withdraw, by sum_amount of amount0+amount1). uniswap_v3_net_swap_flow
      // makes a single swap call and uses the server's directional split
      // (sum_value_usd_t0t1 − sum_value_usd_t1t0) — no second fetch.
      if (isUniswapV3Kind(instance.kind)) {
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
            limit: '200000'
          });
          if (forceFresh) qs.set('fresh', '1');
          return qs;
        };
        const uniNetEvents = UNISWAP_V3_NET_KIND_TO_EVENTS[effectiveKind];
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
        // Single-event path (including uniswap_v3_net_swap_flow, which uses the
        // swap endpoint and computes net from its directional t0t1/t1t0 split).
        const eventForKind =
          effectiveKind === 'uniswap_v3_net_swap_flow'
            ? 'swap'
            : UNISWAP_V3_KIND_TO_EVENT[effectiveKind];
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
        if (effectiveKind === 'uniswap_v3_net_swap_flow') {
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
      const aaveEvent = AAVE_V3_KIND_TO_EVENT[effectiveKind];
      if (aaveEvent) {
        const qs = new URLSearchParams({
          event: aaveEvent,
          interval: instance.interval,
          since: sinceIso,
          until: untilIso,
          limit: '200000'
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
            const q = new URLSearchParams({
              ...baseQS,
              token: tok,
              exchange: instance.exchange ?? 'binance'
            });
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
          // HL OI rides on /hyperliquid/oi_split which carries long/short/
          // total in one payload; the long/short/total/all selector picks
          // which line(s) to render without re-fetching. Binance OI keeps
          // its dedicated endpoint.
          if ((instance.exchange ?? 'binance') === 'hl') {
            url = `/api/hyperliquid/oi_split?${new URLSearchParams(baseQS)}`;
            pickArr = (b) => {
              // Keep open_interest_value populated (= total) so the
              // cumulative MA branch — which always reads that field —
              // continues to work without HL-specific branching.
              const rows = (b.series ?? []) as Array<Record<string, number>>;
              return rows.map((r) => ({
                ...r,
                open_interest: r.total_oi ?? 0,
                open_interest_value: r.total_oi_value ?? 0
              })) as unknown as AnyDatum[];
            };
          } else {
            url = `/api/open_interest?${new URLSearchParams(baseQS)}`;
            pickArr = (b) => (b.series ?? []) as AnyDatum[];
          }
          break;
        case 'hl_smart_oi': {
          // Same payload shape as /oi_split (long/short/total in token + USD)
          // but filtered to the rolling-PnL leaderboard. Smart-money params
          // ride as extra query string entries.
          const sQs = new URLSearchParams(baseQS);
          sQs.set('selector', JSON.stringify(
            instance.smartSelector ?? defaultSmartSelectorState()
          ));
          url = `/api/hyperliquid/smart_oi?${sQs}`;
          pickArr = (b) => {
            const rows = (b.series ?? []) as Array<Record<string, number>>;
            return rows.map((r) => ({
              ...r,
              open_interest: r.total_oi ?? 0,
              open_interest_value: r.total_oi_value ?? 0
            })) as unknown as AnyDatum[];
          };
          break;
        }
        case 'fr': {
          // Same Binance / HL exchange selector pattern as the ohlcv kind.
          const frQs = new URLSearchParams(baseQS);
          frQs.set('exchange', instance.exchange ?? 'binance');
          url = `/api/funding_rate?${frQs}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        }
        case 'tt':
          // Top-trader L/S is Binance-only — the "top trader" tier is a
          // Binance Futures product concept with no clean HL analog.
          url = `/api/long_short_ratios?${new URLSearchParams(baseQS)}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        case 'ls':
          // L/S supports the exchange selector. HL backend computes the
          // count ratio from hl_position_history and the taker volume
          // ratio from hl_fills; top_trader_* fields are returned as 0.
          url = `/api/long_short_ratios?${new URLSearchParams({
            ...baseQS,
            exchange: instance.exchange ?? 'binance'
          })}`;
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
        case 'exchange_flow': {
          // Pre-rolled fast path: /exchange_flow/aggregate reads
          // tradernick.exchange_flow_minute (SummingMergeTree fed by
          // mv_exchange_flow). Same shape as /transfers/aggregate but the
          // baked-in MV WHERE means All-chain queries finish in ms
          // instead of ~80s. We still fire two requests — one per
          // direction — so the render-time linesD can pick which series
          // (inflow / outflow / netflow / all) to plot at toggle time
          // without re-fetching.
          const ex = instance.exchangeFlowExchange ?? 'binance';
          const buildQS = (direction: 'in' | 'out') => {
            const qs = new URLSearchParams({
              direction,
              exchange: ex,
              interval: instance.interval,
              since: sinceIso,
              until: untilIso,
              limit: '200000'
            });
            if (activeChainGroup) qs.set('chain_group', activeChainGroup.name);
            else qs.set('chain', instance.chain ?? 'ETH');
            if (activeTokenGroup !== null) qs.set('token_group', activeTokenGroup);
            else qs.set('token', instance.token);
            return qs;
          };
          const [inRes, outRes] = await Promise.all([
            queuedFetch(`/api/exchange_flow/aggregate?${buildQS('in')}`, { signal }),
            queuedFetch(`/api/exchange_flow/aggregate?${buildQS('out')}`, { signal })
          ]);
          if (!inRes.ok)  throw new Error(`exchange_flow inflow ${inRes.status}`);
          if (!outRes.ok) throw new Error(`exchange_flow outflow ${outRes.status}`);
          const inBody  = await inRes.json();
          const outBody = await outRes.json();
          const outByTime = new Map<number, { amount: number; usd: number }>();
          for (const r of (outBody.series ?? []) as Array<Record<string, number>>) {
            outByTime.set(r.time, { amount: r.sum_amount, usd: r.sum_value_usd });
          }
          const out: Record<string, number>[] = [];
          const seen = new Set<number>();
          for (const r of (inBody.series ?? []) as Array<Record<string, number>>) {
            const o = outByTime.get(r.time) ?? { amount: 0, usd: 0 };
            out.push({
              time: r.time,
              sum_amount_in:     r.sum_amount,
              sum_value_usd_in:  r.sum_value_usd,
              sum_amount_out:    o.amount,
              sum_value_usd_out: o.usd,
              net_amount:        r.sum_amount - o.amount,
              net_value_usd:     r.sum_value_usd - o.usd
            });
            seen.add(r.time);
          }
          for (const r of (outBody.series ?? []) as Array<Record<string, number>>) {
            if (seen.has(r.time)) continue;
            out.push({
              time: r.time,
              sum_amount_in: 0, sum_value_usd_in: 0,
              sum_amount_out:    r.sum_amount,
              sum_value_usd_out: r.sum_value_usd,
              net_amount:    -r.sum_amount,
              net_value_usd: -r.sum_value_usd
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
      || isAaveV3Kind(instance.kind)
      || isAaveV2Kind(instance.kind)
      || isAaveV4Kind(instance.kind)
      || isMorphoKind(instance.kind)
      || isSparkKind(instance.kind)
      || isLidoKind(instance.kind)
      || isAeroClKind(instance.kind)
      || isAeroBasicKind(instance.kind)
      // GMX charts already pick a single value field per kind (via
      // GMX_PRIMARY_FIELD); the running sum of that field is meaningful
      // — total position-open USD over the visible window, total swap USD,
      // etc.
      || isGmxV2Kind(instance.kind)
      // Uniswap: only when plotting a single USD line — amount mode is
      // dual-axis (token0 + token1) so the secondary axis is already taken
      // and there's no single "the amount" to sum.
      || ((isUniswapV3Kind(instance.kind) || isUniswapV2Kind(instance.kind) || effectiveKind === 'uniswap_v4_swap')
            && (instance.valueMode ?? 'usd') === 'usd')
      // HL Bridge Flows: net deposits over a window is the canonical
      // "where did HL TVL move" read. Uses the windowed-sum variant
      // (instance.sumWindow) so the user can pick a rolling horizon.
      || instance.kind === 'hl_transfers'
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
        case 'oi':
        case 'hl_smart_oi': {
          // HL single-line modes (long/short/total/net/ratio/pct) get one
          // MA tracking the displayed line. Long+Short mode plots two
          // primary lines, so we emit two MAs — one per side, dashed and
          // colour-matched to its primary. hl_smart_oi is HL-only by
          // construction and uses the same payload shape as /oi_split.
          // The wallet-count overlay line is added separately in oiLinesD
          // on a secondary axis and is intentionally excluded from MA
          // tracking.
          const hlMode = (instance.kind === 'hl_smart_oi'
                          || (instance.exchange ?? 'binance') === 'hl')
            ? (instance.oiHlDisplay ?? 'total') : null;
          const useTok = (instance.oiUnit ?? 'usd') === 'token';
          const rows = data as Array<Record<string, number>>;
          if (hlMode === 'long_short') {
            const longArr = maArray(
              rows.map((d) => (useTok ? (d.long_oi  ?? 0) : (d.long_oi_value  ?? 0))),
              ma.length, ma.type);
            const shortArr = maArray(
              rows.map((d) => (useTok ? (d.short_oi ?? 0) : (d.short_oi_value ?? 0))),
              ma.length, ma.type);
            out.push({
              key: `cum_oi_long_${idx}`,
              label: `Long ${tag}`,
              color: '#22c55e',
              dash: SUB_DASH[0],
              compute: (_d: OpenInterestRow, i: number) => longArr[i]
            });
            out.push({
              key: `cum_oi_short_${idx}`,
              label: `Short ${tag}`,
              color: '#ef4444',
              dash: SUB_DASH[0],
              compute: (_d: OpenInterestRow, i: number) => shortArr[i]
            });
            break;
          }
          const pickField: (d: Record<string, number>) => number =
            hlMode === 'long'          ? (d) => (useTok ? (d.long_oi  ?? 0) : (d.long_oi_value  ?? 0)) :
            hlMode === 'short'         ? (d) => (useTok ? (d.short_oi ?? 0) : (d.short_oi_value ?? 0)) :
            hlMode === 'long_to_short' ? hlLongShortRatio :
            hlMode === 'net_pct'       ? hlNetOiPct :
            hlMode === 'net'           ? hlNetOi :
                                         (d) => (useTok ? (d.open_interest ?? 0) : (d.open_interest_value ?? 0));
          const arr = maArray(rows.map(pickField), ma.length, ma.type);
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
        default: {
          // Generic MA branch for event-summary kinds whose rows expose
          // {sum_value_usd, sum_amount} — transfer, AAVE V2/V3/V4, Morpho,
          // Spark, Lido, Aerodrome (CL + basic), Uniswap (USD mode).
          //
          // Special-cased inline below:
          //   - exchange_flow: one MA per displayed inflow/outflow/netflow line
          //   - gmx:           MA on the per-kind primary field (GMX_PRIMARY_FIELD)
          //   - uniswap (amount mode): per-token MAs on amt0 + amt1
          //
          // Kinds with no usable scalar value field (pc, hl_*) intentionally
          // fall through without producing an MA line.
          const rows = data as unknown as Record<string, number>[];

          if (instance.kind === 'exchange_flow') {
            const useUsd = (instance.valueMode ?? 'usd') === 'usd';
            const t = instance.exchangeFlowType ?? 'netflow';
            const fIn  = useUsd ? 'sum_value_usd_in'  : 'sum_amount_in';
            const fOut = useUsd ? 'sum_value_usd_out' : 'sum_amount_out';
            const fNet = useUsd ? 'net_value_usd'     : 'net_amount';
            const lbl  = useUsd ? 'USD' : 'Amount';
            const pushFlow = (key: string, line: string, field: string, dash: string) => {
              const a = maArray(rows.map((r) => r[field] ?? 0), ma.length, ma.type);
              out.push({
                key: `cum_${key}_${idx}`,
                label: `${line} ${lbl} ${tag}`,
                color, dash,
                compute: (_d: unknown, i: number) => a[i]
              });
            };
            if (t === 'inflow')  pushFlow('in',  'Inflow',  fIn,  SUB_DASH[0]);
            else if (t === 'outflow') pushFlow('out', 'Outflow', fOut, SUB_DASH[0]);
            else if (t === 'netflow') pushFlow('net', 'Netflow', fNet, SUB_DASH[0]);
            else {
              pushFlow('in',  'Inflow',  fIn,  SUB_DASH[0]);
              pushFlow('out', 'Outflow', fOut, SUB_DASH[1]);
              pushFlow('net', 'Netflow', fNet, SUB_DASH[2]);
            }
            break;
          }

          if (instance.kind === 'hl_transfers') {
            // Same shape as the exchange_flow branch but the HL bridge
            // payload uses {deposit, withdrawal, net} field names and is
            // USDC-only (no token/USD toggle needed). Emit one MA per
            // visible series so the legend matches the primary lines.
            const t = instance.exchangeFlowType ?? 'netflow';
            const pushFlow = (key: string, line: string, field: string, dash: string) => {
              const a = maArray(rows.map((r) => r[field] ?? 0), ma.length, ma.type);
              out.push({
                key: `cum_${key}_${idx}`,
                label: `${line} ${tag}`,
                color, dash,
                compute: (_d: unknown, i: number) => a[i]
              });
            };
            if (t === 'inflow')  pushFlow('in',  'Inflow',  'deposit',    SUB_DASH[0]);
            else if (t === 'outflow') pushFlow('out', 'Outflow', 'withdrawal', SUB_DASH[0]);
            else if (t === 'netflow') pushFlow('net', 'Netflow', 'net',        SUB_DASH[0]);
            else {
              pushFlow('in',  'Inflow',  'deposit',    SUB_DASH[0]);
              pushFlow('out', 'Outflow', 'withdrawal', SUB_DASH[1]);
              pushFlow('net', 'Netflow', 'net',        SUB_DASH[2]);
            }
            break;
          }

          if (isGmxV2Kind(instance.kind)) {
            const field = GMX_V2_PRIMARY_FIELD[effectiveKind] ?? 'sum_value_usd';
            const a = maArray(rows.map((r) => r[field] ?? 0), ma.length, ma.type);
            out.push({
              key: `cum_gmx_${idx}`,
              label: `${field === 'sum_value_usd' ? 'USD' : 'Amount'} ${tag}`,
              color, dash: SUB_DASH[0],
              compute: (_d: unknown, i: number) => a[i]
            });
            break;
          }

          const isUniAmtMode =
            (isUniswapV3Kind(instance.kind)
              || isUniswapV2Kind(instance.kind)
              || effectiveKind === 'uniswap_v4_swap')
            && uniswapValueModeEffective === 'amount';
          if (isUniAmtMode) {
            const sym0 = instance.uniV4Pool?.symbol0
              ?? instance.aeroPool?.symbol0
              ?? instance.aeroBasicPool?.symbol0
              ?? instance.uniPool?.symbol0 ?? 't0';
            const sym1 = instance.uniV4Pool?.symbol1
              ?? instance.aeroPool?.symbol1
              ?? instance.aeroBasicPool?.symbol1
              ?? instance.uniPool?.symbol1 ?? 't1';
            const a0 = maArray(rows.map((r) => r.sum_amount0 ?? 0), ma.length, ma.type);
            const a1 = maArray(rows.map((r) => r.sum_amount1 ?? 0), ma.length, ma.type);
            out.push({
              key: `cum_amt0_${idx}`,
              label: `${sym0} ${tag}`,
              color, dash: SUB_DASH[0],
              axis: 'primary' as const,
              compute: (_d: unknown, i: number) => a0[i]
            });
            out.push({
              key: `cum_amt1_${idx}`,
              label: `${sym1} ${tag}`,
              color, dash: SUB_DASH[1],
              axis: 'secondary' as const,
              compute: (_d: unknown, i: number) => a1[i]
            });
            break;
          }

          // Kinds we know have no {sum_value_usd, sum_amount} scalar — skip
          // rather than feed maArray a NaN-filled series.
          if (instance.kind === 'pc' || instance.kind.startsWith('hl_')) break;

          const useUsd = (instance.valueMode ?? 'usd') === 'usd';
          const a = maArray(
            rows.map((r) => (useUsd ? r.sum_value_usd : r.sum_amount) ?? 0),
            ma.length, ma.type
          );
          out.push({
            key: `cum_${idx}`,
            label: `${useUsd ? 'USD' : 'Amount'} ${tag}`,
            color, dash: SUB_DASH[0],
            compute: (_d: unknown, i: number) => a[i]
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
      // HL Bridge Flows special-case: payload uses {deposit, withdrawal,
      // net} fields instead of {sum_value_usd, sum_amount}, and the sum
      // tracks whichever direction the user selected in the flow-type
      // toggle. Also honours a sliding window via instance.sumWindow (0
      // or unset = strict running total from the first loaded bucket).
      if (instance.kind === 'hl_transfers') {
        const t = instance.exchangeFlowType ?? 'netflow';
        const field = t === 'inflow' ? 'deposit'
                    : t === 'outflow' ? 'withdrawal'
                    : 'net'; // 'netflow' and 'all' both anchor on net
        const lineLabel = t === 'inflow' ? 'Inflow'
                       : t === 'outflow' ? 'Outflow'
                       : 'Netflow';
        const win = Math.max(0, Math.floor(instance.sumWindow ?? 0));
        const src = (data as unknown as Record<string, number>[]).map(
          (d) => Number(d[field] ?? 0)
        );
        const running: number[] = new Array(src.length);
        let acc = 0;
        if (win === 0) {
          for (let i = 0; i < src.length; i++) { acc += src[i] || 0; running[i] = acc; }
        } else {
          for (let i = 0; i < src.length; i++) {
            acc += src[i] || 0;
            if (i >= win) acc -= src[i - win] || 0;
            running[i] = acc;
          }
        }
        const winLabel = win > 0 ? ` (last ${win})` : '';
        out.push({
          key: 'cum_sum',
          label: `Σ ${lineLabel}${winLabel}`,
          color: '#a78bfa',
          axis: 'secondary' as const,
          compute: (_d: unknown, i: number) => running[i]
        });
      } else {
        const useUsd = (instance.valueMode ?? 'usd') === 'usd';
        const valLabel = useUsd ? 'USD' : 'Amount';
        // Source array: every applicable kind exposes the same shape
        // ({sum_value_usd, sum_amount}) on each row.
        const src = (data as unknown as Record<string, number>[]).map(
          (d) => (useUsd ? d.sum_value_usd : d.sum_amount) ?? 0
        );
        // Windowed running sum — same sliding-window scheme as the
        // hl_transfers branch above. 0 / unset = strict running total
        // from the first loaded bucket (matches the historical default);
        // positive N = rolling window over the last N buckets at the
        // current interval. Lets the user read e.g. "rolling 24h supply"
        // on an AAVE V3 supply chart at 1h interval with sumWindow=24.
        const win = Math.max(0, Math.floor(instance.sumWindow ?? 0));
        const running: number[] = new Array(src.length);
        let acc = 0;
        if (win === 0) {
          for (let i = 0; i < src.length; i++) { acc += src[i] || 0; running[i] = acc; }
        } else {
          for (let i = 0; i < src.length; i++) {
            acc += src[i] || 0;
            if (i >= win) acc -= src[i - win] || 0;
            running[i] = acc;
          }
        }
        const winLabel = win > 0 ? ` (last ${win})` : '';
        out.push({
          key: 'cum_sum',
          label: `Σ ${valLabel}${winLabel}`,
          color: '#a78bfa',                 // violet-400 — distinct from MA palette
          axis: 'secondary' as const,
          compute: (_d: unknown, i: number) => running[i]
        });
      }
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

  // exchange_flow chart: deposit-umbrella / hot-wallet filters per
  // exchange. Mirrors the filter shapes the deprecated CeX/Perp inflow +
  // outflow templates used to bake at insertion time, but here they're
  // derived live from the (exchange, flowType) selector pair on the
  // instance so the user can flip exchanges/directions in-place.
  //
  // Backend lowercases the filter values before matching against the
  // pre-lowered materialized columns on tradernick.transfers (see
  // routes/transfers.py:482), so casing isn't strictly load-bearing —
  // BUT entity filters DO require the exact stored entity name. The
  // wallets.entity column stores 'OKX' (all caps), not 'Okx'. Use an
  // explicit map so the entity case is always correct.
  type TF = TransferFilters;
  const EXCHANGE_LABEL: Record<string, string> = {
    binance: 'Binance',
    coinbase: 'Coinbase',
    okx: 'OKX',
    bybit: 'Bybit',
    hyperliquid: 'Hyperliquid'
  };
  function exchangeFlowInFilter(ex: string): TF {
    const label = EXCHANGE_LABEL[ex] ?? ex;
    if (ex === 'hyperliquid') {
      // Hyperliquid-Deposit already implies Perp (verified: every wallet
      // tagged Hyperliquid-Deposit also carries 'Perp'). Sender-not-Perp
      // still rules out HL-internal bridge ↔ hot wallet moves.
      return { receiver_all_in: ['Hyperliquid-Deposit'], sender_ex: ['Perp'] };
    }
    // {Exchange}-Deposit already implies CEX (verified across Binance,
    // Coinbase, OKX, Bybit — 0 exceptions). Drop the redundant umbrella
    // tag; sender-not-CEX still excludes CeX-internal moves (those land
    // under the "CeX Internal Flow" template).
    return { receiver_all_in: [`${label}-Deposit`], sender_ex: ['CEX'] };
  }
  function exchangeFlowOutFilter(ex: string): TF {
    const label = EXCHANGE_LABEL[ex] ?? ex;
    if (ex === 'hyperliquid') {
      // sender_entity='Hyperliquid' already implies 'Perp', so the
      // hasAll(['Hot-Wallet','Perp']) check is redundant — keep just
      // 'Hot-Wallet'. Receiver-not-Hyperliquid still rules out HL-internal
      // moves.
      return {
        sender_all_in: ['Hot-Wallet'],
        sender_entity_in: ['Hyperliquid'],
        receiver_entity_ex: ['Hyperliquid']
      };
    }
    // sender_entity=<CEX> already implies 'CEX', so the hasAll(['Hot-Wallet',
    // 'CEX']) check is redundant — narrowing to 'Hot-Wallet' alone is what
    // makes All-chain queries fast enough to clear the Sanic 180s budget.
    return {
      sender_all_in: ['Hot-Wallet'],
      sender_entity_in: [label],
      receiver_ex: ['CEX']
    };
  }

  // Lines for exchange_flow:
  //   inflow   → 1 line, green  (positive bucket sums)
  //   outflow  → 1 line, red    (positive bucket sums of outflow side)
  //   netflow  → 1 line, cyan   (inflow - outflow)
  //   all      → 3 lines (inflow + outflow + netflow), so the operator
  //              can compare absolute flow sizes side-by-side AND see
  //              the directional bias at a glance.
  let exchangeFlowUseUsd = $derived((instance.valueMode ?? 'usd') === 'usd');
  let exchangeFlowLinesD = $derived.by(() => {
    if (!instance.showPoint) return [...cumulativeLines];
    const field = exchangeFlowUseUsd ? 'sum_value_usd_in' : 'sum_amount_in';
    const fieldOut = exchangeFlowUseUsd ? 'sum_value_usd_out' : 'sum_amount_out';
    const fieldNet = exchangeFlowUseUsd ? 'net_value_usd' : 'net_amount';
    const t = instance.exchangeFlowType ?? 'netflow';
    const inLine = { key: 'in', label: 'Inflow', color: '#22c55e',
      compute: (d: Record<string, number>) => d[field] ?? 0 };
    const outLine = { key: 'out', label: 'Outflow', color: '#ef4444',
      compute: (d: Record<string, number>) => d[fieldOut] ?? 0 };
    const netLine = { key: 'net', label: 'Netflow', color: '#06b6d4',
      compute: (d: Record<string, number>) => d[fieldNet] ?? 0 };
    if (t === 'inflow')  return [inLine, ...cumulativeLines];
    if (t === 'outflow') return [outLine, ...cumulativeLines];
    if (t === 'netflow') return [netLine, ...cumulativeLines];
    return [inLine, outLine, netLine, ...cumulativeLines]; // 'all'
  });

  // For event-driven kinds (AAVE / Lido) the user can toggle between
  // sum_value_usd (default) and sum_amount via instance.valueMode. Both
  // fields come back from /aave/aggregate + /lido/aggregate today, so no
  // server change is needed — the toggle just picks which one to plot
  // and swaps the axis/tooltip formatter to match.
  let useUsdValue = $derived((instance.valueMode ?? 'usd') === 'usd');
  let valueField = $derived(useUsdValue ? 'sum_value_usd' : 'sum_amount');
  let valueAxisFn = $derived(useUsdValue ? fmtUsdAxis : fmtAmountAxis);
  let valueTooltipFn = $derived(useUsdValue ? fmtUsdTooltip : fmtAmountTooltip);

  // GMX V2 position/liquidation events: the server emits sum_amount_long
  // and sum_amount_short alongside the existing combined sum_amount, so we
  // expose 4 series (Long, Short, Long + Short, Net) on a single chart.
  // Colors mirror the long/short convention used elsewhere (HL OI / HL PnL):
  // long = green, short = red, total = cyan accent, net = amber.
  let gmxIsPositionLongShortKind = $derived(
    effectiveKind === 'gmx_v2_position_increase' ||
    effectiveKind === 'gmx_v2_position_decrease' ||
    effectiveKind === 'gmx_v2_liquidation' ||
    effectiveKind === 'gmx_v2_net_position'
  );
  let gmxPositionLinesD = $derived.by(() => {
    if (!instance.showPoint) return [];
    const longLine = { key: 'gmx_long',  label: 'Long',  color: '#22c55e',
      compute: (d: Record<string, number>) => d.sum_amount_long ?? 0 };
    const shortLine = { key: 'gmx_short', label: 'Short', color: '#ef4444',
      compute: (d: Record<string, number>) => d.sum_amount_short ?? 0 };
    const totalLine = { key: 'gmx_total', label: 'Long + Short', color: '#06b6d4',
      compute: (d: Record<string, number>) =>
        (d.sum_amount ?? ((d.sum_amount_long ?? 0) + (d.sum_amount_short ?? 0))) };
    const netLine  = { key: 'gmx_net',   label: 'Net Long', color: '#f59e0b',
      compute: (d: Record<string, number>) => (d.sum_amount_long ?? 0) - (d.sum_amount_short ?? 0) };
    const mode = instance.gmxLongShortDisplay ?? 'total';
    if (mode === 'long')  return [longLine];
    if (mode === 'short') return [shortLine];
    if (mode === 'net')   return [netLine];
    if (mode === 'all')   return [longLine, shortLine, totalLine, netLine];
    return [totalLine]; // 'total' (default)
  });

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

  // HL Realized PnL with side != 'total': one or two lines sourced from
  // the {long_pnl, short_pnl, total_pnl} payload of /realized_pnl_split.
  // 'total' falls through to aaveLinesD (sum_value_usd) so the default
  // chart shape is unchanged. Same green/red color convention as
  // hl_unrealized_pnl below.
  let hlPnlSplitLinesD = $derived.by(() => {
    if (!instance.showPoint) return [];
    const side = instance.hlPnlSide ?? 'total';
    const longL = { key: 'long',  label: 'Long Realized PnL',  color: '#22c55e',
      compute: (d: Record<string, number>) => d.long_pnl ?? 0 };
    const shortL = { key: 'short', label: 'Short Realized PnL', color: '#ef4444',
      compute: (d: Record<string, number>) => d.short_pnl ?? 0 };
    if (side === 'long')  return [longL];
    if (side === 'short') return [shortL];
    if (side === 'both')  return [longL, shortL];
    return []; // 'total' handled by aaveLinesD
  });

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

  // HL Vault Flow: same 3-line shape as Bridge Flows but over hl_vaults.
  // deposit / withdraw are positive magnitudes; net = deposit - withdraw.
  let hlVaultFlowLinesD = $derived(
    instance.showPoint
      ? [
          { key: 'deposit',  label: 'Deposit',  color: '#22c55e',
            compute: (d: Record<string, number>) => d.deposit ?? 0 },
          { key: 'withdraw', label: 'Withdraw', color: '#ef4444',
            compute: (d: Record<string, number>) => d.withdraw ?? 0 },
          { key: 'net',      label: 'Net',      color: '#06b6d4',
            compute: (d: Record<string, number>) => d.net ?? 0 }
        ]
      : []
  );

  // HL Bridge Flows: directional USDC bridge view. Inflow (deposits to HL)
  // and outflow (withdrawals from HL) are both rendered as POSITIVE
  // magnitudes so the operator can compare absolute flow sizes side-by-
  // side. Netflow is signed (deposit - withdrawal) and floats through zero
  // to show directional bias. Reuses the same `exchangeFlowType` toggle as
  // the CeX Exchange Flow chart — same semantic (inflow/outflow/netflow/
  // all), no need for a parallel field on ChartInstance.
  let hlBridgeFlowsLinesD = $derived.by(() => {
    if (!instance.showPoint) return [...cumulativeLines];
    const t = instance.exchangeFlowType ?? 'netflow';
    const inLine = { key: 'in', label: 'Inflow', color: '#22c55e',
      compute: (d: Record<string, number>) => d.deposit ?? 0 };
    const outLine = { key: 'out', label: 'Outflow', color: '#ef4444',
      compute: (d: Record<string, number>) => d.withdrawal ?? 0 };
    const netLine = { key: 'net', label: 'Netflow', color: '#06b6d4',
      compute: (d: Record<string, number>) => d.net ?? 0 };
    if (t === 'inflow')  return [inLine, ...cumulativeLines];
    if (t === 'outflow') return [outLine, ...cumulativeLines];
    if (t === 'netflow') return [netLine, ...cumulativeLines];
    return [inLine, outLine, netLine, ...cumulativeLines]; // 'all'
  });

  // Uniswap chart lines. USD mode (default): one cyan sum_value_usd line.
  // Amount mode: two lines (token0 on primary axis, token1 on secondary
  // axis) — each token has its own scale because t0/t1 magnitudes can be
  // off by 4-6 orders (e.g. USDC vs WETH). The toggle is hidden in the
  // settings UI for uniswap_v3_net_swap_flow (which is intrinsically a
  // directional USD chart with no clean per-token amount split).
  // V4 LP events (deposit/withdraw/initialize) and net_liquidity have no
  // per-token amount split — only liquidity_delta. Force USD mode for
  // those even if the toggle is on (the server returns 0 for sum_amount0/1).
  let uniswapValueModeEffective = $derived(
    effectiveKind === 'uniswap_v3_net_swap_flow'
      || effectiveKind === 'uniswap_v4_deposit'
      || effectiveKind === 'uniswap_v4_withdraw'
      || effectiveKind === 'uniswap_v4_net_liquidity'
      || effectiveKind === 'uniswap_v4_initialize'
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
  // OI lines: Binance is always the single total line. HL switches by
  // the oiHlDisplay selector — 'total' matches Binance shape exactly,
  // 'long'/'short' shows just that side, 'long_short' shows two, and
  // 'long_to_short' shows a unitless ratio. The oiUnit selector picks
  // dollar notional (`*_oi_value` on HL, `open_interest_value` on Binance)
  // vs token amount (`*_oi`, `open_interest`).
  let oiIsToken = $derived((instance.oiUnit ?? 'usd') === 'token');
  let oiHlPrimary = $derived.by(() => {
    // hl_smart_oi is HL-only with no exchange field — treat it as HL.
    if (instance.kind !== 'hl_smart_oi'
        && (instance.exchange ?? 'binance') !== 'hl') return null;
    const mode = instance.oiHlDisplay ?? 'total';
    const unitLabel = oiIsToken ? ` (${instance.token ?? ''})` : ' (USD)';
    if (mode === 'long')  return { color: '#22c55e', field: oiIsToken ? 'long_oi'  : 'long_oi_value',  label: 'Long OI'  + unitLabel };
    if (mode === 'short') return { color: '#ef4444', field: oiIsToken ? 'short_oi' : 'short_oi_value', label: 'Short OI' + unitLabel };
    if (mode === 'long_short' || mode === 'long_to_short' || mode === 'net_pct' || mode === 'net') return null;
    return { color: '#06b6d4', field: oiIsToken ? 'total_oi' : 'total_oi_value', label: 'OI' + unitLabel };
  });
  // Long/Short ratio: guard against zero-short buckets (early-history HL
  // markets where one side hadn't traded yet) — we emit 0 there instead of
  // an Infinity that would ruin auto-axis scaling. Unit-independent: the
  // mark price cancels out of the ratio.
  function hlLongShortRatio(d: Record<string, number>): number {
    const s = d.short_oi_value ?? 0;
    if (!isFinite(s) || s <= 0) return 0;
    return (d.long_oi_value ?? 0) / s;
  }
  // Net OI percentage: (long - short) / total. Unitless, bounded to [-1, 1].
  // Positive = long-skewed book, negative = short-skewed, 0 = balanced.
  // Same value whether computed from token amounts or USD — mark price
  // cancels in numerator and denominator.
  function hlNetOiPct(d: Record<string, number>): number {
    const t = d.total_oi_value ?? 0;
    if (!isFinite(t) || t <= 0) return 0;
    return ((d.long_oi_value ?? 0) - (d.short_oi_value ?? 0)) / t;
  }
  // Net OI absolute: long - short in the unit selected by oiUnit. Unlike
  // hlNetOiPct this keeps the unit (USD or token), so two markets at the
  // same skew but different sizes plot differently. Used by the 'net' mode.
  function hlNetOi(d: Record<string, number>): number {
    return oiIsToken
      ? ((d.long_oi       ?? 0) - (d.short_oi       ?? 0))
      : ((d.long_oi_value ?? 0) - (d.short_oi_value ?? 0));
  }
  let oiLinesD = $derived.by(() => {
    if (!instance.showPoint) return [...cumulativeLines];
    // hl_smart_oi is HL-only by construction (no exchange field on the
    // instance), so treat it as `ex === 'hl'` for every mode branch below
    // — otherwise the dropdown silently falls through to the Binance shape.
    const ex = instance.kind === 'hl_smart_oi'
      ? 'hl'
      : (instance.exchange ?? 'binance');
    const mode = instance.oiHlDisplay ?? 'total';
    let base: typeof cumulativeLines;
    if (ex === 'hl' && mode === 'long_short') {
      base = [
        { key: 'oi_long',  label: 'Long OI',  color: '#22c55e',
          compute: (d: Record<string, number>) => (oiIsToken ? (d.long_oi ?? 0) : (d.long_oi_value ?? 0)) },
        { key: 'oi_short', label: 'Short OI', color: '#ef4444',
          compute: (d: Record<string, number>) => (oiIsToken ? (d.short_oi ?? 0) : (d.short_oi_value ?? 0)) },
        ...cumulativeLines
      ];
    } else if (ex === 'hl' && mode === 'long_to_short') {
      base = [
        { key: 'oi_l2s', label: 'Long / Short OI', color: '#a855f7',
          compute: hlLongShortRatio },
        ...cumulativeLines
      ];
    } else if (ex === 'hl' && mode === 'net_pct') {
      base = [
        { key: 'oi_net_pct', label: 'Net OI %', color: '#f59e0b',
          compute: hlNetOiPct },
        ...cumulativeLines
      ];
    } else if (ex === 'hl' && mode === 'net') {
      base = [
        { key: 'oi_net', label: `Net OI${oiIsToken ? ` (${instance.token ?? ''})` : ' (USD)'}`, color: '#f97316',
          compute: hlNetOi },
        ...cumulativeLines
      ];
    } else if (oiHlPrimary) {
      base = [
        { key: 'oi_primary', label: oiHlPrimary.label, color: oiHlPrimary.color,
          compute: (d: Record<string, number>) => (d[oiHlPrimary.field] ?? 0) },
        ...cumulativeLines
      ];
    } else if (ex !== 'hl' && oiIsToken) {
      base = [
        { key: 'oi_token', label: `OI (${instance.token ?? ''})`, color: '#06b6d4',
          compute: (d: Record<string, number>) => d.open_interest ?? 0 },
        ...cumulativeLines
      ];
    } else {
      base = [...OI_LINES, ...cumulativeLines];
    }
    // Smart-money OI: optional "wallets passing filter" line on the
    // secondary (right-side) axis. Independent scale so it doesn't fight
    // the OI line for vertical space. Rendered as a short-dashed amber
    // line so it reads as supplementary context — the OI lines are the
    // primary read; the wallet count is a "is the filter too tight?"
    // sanity check. Integer formatter via formatY2.
    if (instance.kind === 'hl_smart_oi' && (instance.smartShowWalletCount ?? false)) {
      base.push({
        key: 'wallet_count', label: 'Wallets', color: '#fbbf24',
        axis: 'secondary',
        dash: '3,3',
        compute: (d: Record<string, number>) => d.wallet_count ?? 0,
      });
    }
    return base;
  });
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

  // Size is now controlled by edge/corner drag handles on the wrapper in
  // DynamicChartLayout — see startResize there. The chart instance just
  // reads instance.width / instance.height to fit its content.

  // Title-bar label. The general Morpho wrapper always reads as "Morpho"
  // (per spec) — the active subkind is communicated via the in-chart
  // selector rendered alongside the chain/token controls below.
  let kindLabel = $derived(CHART_KIND_LABELS[instance.kind]);
  let isTemplate = $derived(typeof instance.templateName === 'string' && instance.templateName.length > 0);
  let exchangeFlowLabel = $derived.by(() => {
    if (instance.kind !== 'exchange_flow') return '';
    const ex = instance.exchangeFlowExchange ?? 'binance';
    const exLabel = ex.charAt(0).toUpperCase() + ex.slice(1);
    const t = instance.exchangeFlowType ?? 'netflow';
    const tLabel = t === 'all' ? 'All' : t.charAt(0).toUpperCase() + t.slice(1);
    return `${exLabel} ${tLabel}`;
  });
  let displayTitle = $derived(
    isTemplate ? (instance.templateName as string)
    : kindLabel
  );
  let panelTitle = $derived(
    `${displayTitle} — ${isUniswapV3Kind(instance.kind) && instance.uniPool ? fmtUniPool(instance.uniPool) : instance.token} ${instance.interval}` +
      (instance.kind === 'sz' ? ` (< $${instance.under} / > $${instance.over})` : '')
  );

  let settingsOpen = $state(false);

  // ── Compound overlays ───────────────────────────────────────────────
  // Per-overlay fetched series, keyed by overlay.id. Re-fetched whenever
  // the host's interval changes or an overlay is added/edited/removed.
  // The (+) FAB is hidden on `pc` and on every TableView kind (those
  // can't host or be added as overlays — see overlayableKinds()).
  type OverlayLoad = { data: OverlayPoint[]; key: string };
  let overlayLoaded = $state<Map<string, OverlayLoad>>(new Map());
  // Per-overlay loading flag — keyed by overlay.id. The chip itself shows
  // the inline indicator while its id is in this set, so each overlay's
  // load state is visible next to its own chip rather than as one global
  // strip-level "loading…" text.
  let overlayLoadingIds = $state<Set<string>>(new Set());
  let overlayDialogOpen = $state(false);
  let overlayEditing = $state<ChartOverlay | null>(null);

  // Smart-wallets popover (hl_smart_oi only): triggered by clicking the
  // wallet-count line. Loads the wallet addresses for the clicked day
  // and pops up SmartWalletsDialog with copy + Coinglass actions.
  let walletsDialogOpen = $state(false);
  let walletsDialogLoading = $state(false);
  let walletsDialogError = $state<string | null>(null);
  let walletsDialogList = $state<string[]>([]);
  let walletsDialogDay = $state('');
  let walletsFetchCtl: AbortController | null = null;

  async function openSmartWalletsDialog(timeSec: number) {
    if (instance.kind !== 'hl_smart_oi') return;
    // Round to UTC day — matches the selector's `target_days` grain.
    const d = new Date(timeSec * 1000);
    const dayIso = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    walletsDialogDay = dayIso;
    walletsDialogList = [];
    walletsDialogError = null;
    walletsDialogLoading = true;
    walletsDialogOpen = true;
    if (walletsFetchCtl) walletsFetchCtl.abort();
    walletsFetchCtl = new AbortController();
    try {
      const selector = JSON.stringify(instance.smartSelector ?? {});
      const qs = new URLSearchParams({
        token: instance.token ?? '',
        day: dayIso,
        selector,
      });
      const res = await fetch(`/api/hyperliquid/smart_wallets?${qs}`, { signal: walletsFetchCtl.signal });
      if (!res.ok) throw new Error(`smart_wallets ${res.status}`);
      const body = await res.json();
      walletsDialogList = (body.wallets ?? []) as string[];
    } catch (e) {
      if ((e as DOMException)?.name !== 'AbortError') {
        walletsDialogError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      walletsDialogLoading = false;
    }
  }

  let canHaveOverlays = $derived(
    instance.kind !== 'pc'
    && instance.kind !== 'hl_top_traders'
    && instance.kind !== 'hl_top_positions'
    && instance.kind !== 'hl_top_vaults'
    && instance.kind !== 'hl_top_vault_lps'
    && instance.kind !== 'hl_vault_detail'
    && !isLeaderboardKind(instance.kind)
  );

  function overlayLoadKey(o: ChartOverlay, iv: Interval, sinceIso: string, untilIso: string): string {
    // Hash the full config + interval + window so any field change re-fetches.
    return [
      o.kind, o.seriesKey, iv, sinceIso, untilIso,
      o.token ?? '', o.tokenGroup ?? '', o.tokenDenominator ?? '',
      o.chain ?? '', o.chainGroup ?? '',
      o.exchange ?? '', o.valueMode ?? '',
      o.gmxMarket ?? '', o.hlWallet ?? '', o.hlWalletCategory ?? '',
      o.exchangeFlowExchange ?? '',
      o.uniPool ? `${o.uniPool.symbol0}|${o.uniPool.symbol1}|${o.uniPool.fee}` : '',
      o.uniV4Pool ? `${o.uniV4Pool.symbol0}|${o.uniV4Pool.symbol1}|${o.uniV4Pool.fee}|${o.uniV4Pool.tick_spacing}|${o.uniV4Pool.hooks}` : '',
      o.aeroPool ? `${o.aeroPool.symbol0}|${o.aeroPool.symbol1}|${o.aeroPool.tick_spacing}` : '',
      o.aeroBasicPool ? `${o.aeroBasicPool.symbol0}|${o.aeroBasicPool.symbol1}|${o.aeroBasicPool.stable}` : '',
      o.ma ? `${o.ma.type}|${o.ma.length}` : '',
      o.sum ? `sum|${o.sum.length}` : ''
    ].join('#');
  }

  let overlayFetchCtl: AbortController | null = null;
  $effect(() => {
    // Subscribe only to the inputs that should re-trigger overlay loading.
    // Anything we *read but write back to* (overlayLoaded itself) is read
    // through `untrack` so the write at the end doesn't loop the effect.
    const overlays = instance.overlays ?? [];
    const iv = instance.interval;
    const sinceIso = since;
    const untilIso = until;
    if (!canHaveOverlays) {
      untrack(() => { if (overlayLoaded.size > 0) overlayLoaded = new Map(); });
      return;
    }
    if (overlays.length === 0) {
      untrack(() => { if (overlayLoaded.size > 0) overlayLoaded = new Map(); });
      return;
    }
    // Avoid double-fetch storms during the host's own pending load.
    if (sinceIso === new Date(0).toISOString()) return;

    if (overlayFetchCtl) overlayFetchCtl.abort();
    const ctl = new AbortController();
    overlayFetchCtl = ctl;
    const sinceD = new Date(sinceIso);
    const untilD = new Date(untilIso);

    untrack(() => {
      const next = new Map(overlayLoaded);
      let changed = false;
      const tasks: Promise<void>[] = [];
      const loadingNow = new Set<string>(overlayLoadingIds);
      for (const o of overlays) {
        const k = overlayLoadKey(o, iv, sinceIso, untilIso);
        const cached = next.get(o.id);
        if (cached && cached.key === k) continue;
        loadingNow.add(o.id);
        tasks.push((async () => {
          try {
            const points = await fetchOverlayData(o, iv, sinceD, untilD, ctl.signal);
            if (ctl.signal.aborted) return;
            next.set(o.id, { data: points, key: k });
            changed = true;
          } catch {
            if (!ctl.signal.aborted) {
              next.set(o.id, { data: [], key: k });
              changed = true;
            }
          } finally {
            if (!ctl.signal.aborted) {
              // Per-id clear so other in-flight overlays keep showing
              // their own spinner.
              const after = new Set(overlayLoadingIds);
              after.delete(o.id);
              overlayLoadingIds = after;
            }
          }
        })());
      }
      // Drop entries for overlays that no longer exist on the host.
      const currentIds = new Set(overlays.map((o) => o.id));
      for (const id of [...next.keys()]) {
        if (!currentIds.has(id)) { next.delete(id); changed = true; }
      }
      // Clean stale loading flags for removed overlays too.
      for (const id of [...loadingNow]) {
        if (!currentIds.has(id)) loadingNow.delete(id);
      }
      if (tasks.length === 0) {
        // Only publish a new Map when content actually changed — otherwise
        // reassigning the same content would still flip the $state ref and
        // re-trigger any effect that *did* subscribe to overlayLoaded.
        if (changed) overlayLoaded = next;
        if (loadingNow.size !== overlayLoadingIds.size) overlayLoadingIds = loadingNow;
        return;
      }
      overlayLoadingIds = loadingNow;
      Promise.all(tasks).finally(() => {
        if (ctl.signal.aborted) return;
        if (changed) overlayLoaded = next;
      });
    });
  });

  /** Build remapped Line[] for the host chart. Computes the primary Y range
   *  from `primarySource` (OHLCV high/low band, otherwise the host's primary
   *  lines' numeric range over the loaded data), then for each overlay
   *  scales its raw values into the same range. Tooltip shows the raw
   *  (un-remapped) number via `rawValue`. */
  type LineLike = {
    key: string; label: string; color: string;
    compute: (d: Record<string, number>, i: number, arr: Record<string, number>[]) => number;
    rawValue?: (d: Record<string, number>, i: number, arr: Record<string, number>[]) => number;
    rawFormat?: (v: number) => string;
    axis?: 'primary' | 'secondary';
  };
  function computePrimaryRangeFromLines(
    src: Record<string, number>[],
    lines: { compute: (d: Record<string, number>, i: number, arr: Record<string, number>[]) => number; axis?: 'primary' | 'secondary' }[]
  ): [number, number] {
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < src.length; i++) {
      for (const ln of lines) {
        if ((ln.axis ?? 'primary') !== 'primary') continue;
        const v = ln.compute(src[i], i, src);
        if (Number.isFinite(v)) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    return [lo, hi];
  }
  function computePrimaryRangeFromCandles(candles: Candle[]): [number, number] {
    let lo = Infinity, hi = -Infinity;
    for (const c of candles) {
      if (c.low < lo) lo = c.low;
      if (c.high > hi) hi = c.high;
    }
    return [lo, hi];
  }
  /** Y range derived from a single numeric field across `src`. Used by host
   *  kinds where the bars (not lines) define the primary axis range — FR /
   *  BS / SZ — so overlays still have a sensible primary to remap into when
   *  no MA / Sum line is enabled. */
  function computePrimaryRangeFromField(
    src: Record<string, number>[],
    field: string,
    signed: boolean
  ): [number, number] {
    let lo = Infinity, hi = -Infinity;
    for (const d of src) {
      const v = d[field];
      if (Number.isFinite(v)) {
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [lo, hi];
    if (signed) {
      const span = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
      return [-span, span];
    }
    return [lo, hi];
  }

  /** Per-kind tooltip formatter used by overlay lines. Most kinds are USD-
   *  denominated; FR is per-event rate (already converted client-side to
   *  bps/8h or APR — see overlay-fetch); L/S + TT are pure ratios; OHLCV
   *  volume varies with the host. */
  function overlayValueFormatter(o: ChartOverlay): (v: number) => string {
    if (o.kind === 'fr') {
      const isApr = (o.frDisplay ?? 'rate8h') === 'apr';
      return (v: number) => isApr ? `${v.toFixed(3)}% APR` : `${v.toFixed(3)} bps/8h`;
    }
    if (o.kind === 'ls' || o.kind === 'tt') {
      return (v: number) => v.toFixed(4);
    }
    // OI Long/Short ratio is unitless (1.03, not $1.03). Token-amount OI
    // overlays render as the coin count (e.g. 19,154 BTC). USD overlays
    // fall through to fmtUsdTooltip below.
    if ((o.kind === 'oi' || o.kind === 'hl_smart_oi') && o.seriesKey === 'long_to_short_oi') {
      return fmtRatio;
    }
    if ((o.kind === 'oi' || o.kind === 'hl_smart_oi') && o.seriesKey === 'net_oi_pct') {
      return (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;
    }
    if ((o.kind === 'oi' || o.kind === 'hl_smart_oi')
        && (o.seriesKey === 'total_oi' || o.seriesKey === 'long_oi'
         || o.seriesKey === 'short_oi' || o.seriesKey === 'net_oi')) {
      return fmtAmountTooltip;
    }
    if (o.kind === 'vol_oi') {
      // OI / Volume is a unitless ratio (bucket multiples of liquidity).
      // 3-4 sig figs is the right precision — too many digits looks like
      // false accuracy on a ratio that's already noisy.
      return (v: number) => `${v.toFixed(3)}×`;
    }
    if (o.kind === 'ohlcv' || o.kind === 'price') {
      // Volume is USD when the host renders it that way; close/open/high/low
      // are price (USD for fiat-quoted markets). Both formatters round to
      // a similar precision, so the USD compact form is fine for all.
      // `price` is the close-only overlay and uses the same formatter.
      return (v: number) => `$${v.toFixed(2)}`;
    }
    if (o.kind === 'price_ratio') {
      // Unitless ratio between two close prices. toPrecision(4) so
      // BTC/USDC at ~73 000 reads "73020" and SOL/BTC at ~0.0023 reads
      // "0.002301" — same readability across magnitudes.
      return (v: number) => v.toPrecision(4);
    }
    if (o.valueMode === 'amount') {
      return (v: number) => v.toFixed(4);
    }
    return fmtUsdTooltip;
  }

  function buildOverlayLines(pRange: [number, number]): LineLike[] {
    const overlays = instance.overlays ?? [];
    if (overlays.length === 0) return [];
    const [pmin, pmax] = pRange;
    const pspan = pmax - pmin;
    const usable = Number.isFinite(pmin) && Number.isFinite(pmax) && pspan !== 0;
    const out: LineLike[] = [];
    for (const o of overlays) {
      if (o.hidden) continue; // chip kept; line suppressed.
      const load = overlayLoaded.get(o.id);
      const data = load?.data ?? [];
      if (data.length === 0) continue;

      // Sorted copy + range stats. We keep the sorted list because the
      // host's bucket grid often doesn't align 1:1 with the overlay's:
      // a 4h OHLCV chart hosting an 8h-spaced funding-rate overlay would
      // otherwise see NaN at every other bucket and d3.line's .defined()
      // would render nothing (single defined points can't form a path).
      // The forward-fill (binary-search latest overlay point with
      // time <= d.time) is also semantically right for step-style data
      // like funding rate, where the value is constant between updates.
      const sorted = data.filter((p) => Number.isFinite(p.value)).slice().sort((a, b) => a.time - b.time);
      if (sorted.length === 0) continue;
      let omin = Infinity, omax = -Infinity;
      for (const p of sorted) {
        if (p.value < omin) omin = p.value;
        if (p.value > omax) omax = p.value;
      }
      const oRange = omax - omin;
      const remap = (raw: number): number => {
        if (!usable) return NaN;
        if (oRange === 0) return (pmin + pmax) / 2;
        return pmin + ((raw - omin) * pspan) / oRange;
      };
      // Binary search: largest index where sorted[i].time <= t. -1 = no
      // such point (host timestamps before the overlay's earliest sample,
      // line stays undefined there — d3 correctly omits leading gap).
      function findLE(t: number): number {
        let lo = 0, hi = sorted.length - 1, found = -1;
        while (lo <= hi) {
          const mid = (lo + hi) >> 1;
          if (sorted[mid].time <= t) { found = mid; lo = mid + 1; }
          else hi = mid - 1;
        }
        return found;
      }
      out.push({
        key: 'ovl-' + o.id,
        label: overlayChipLabel(o),
        color: o.color,
        axis: 'primary',
        compute: (d) => {
          const i = findLE(d.time);
          if (i < 0) return NaN;
          return remap(sorted[i].value);
        },
        rawValue: (d) => {
          const i = findLE(d.time);
          if (i < 0) return NaN;
          return sorted[i].value;
        },
        rawFormat: overlayValueFormatter(o)
      });
    }
    return out;
  }

  // Per-host overlay lines. The primary range source depends on the chart
  // kind — OHLCV uses high/low across all candles; everything else derives
  // from the host's already-computed primary lines (axis='primary' only).
  let overlayLinesD = $derived.by((): LineLike[] => {
    if (!canHaveOverlays) return [];
    if ((instance.overlays ?? []).length === 0) return [];
    if (instance.kind === 'ohlcv') {
      const range = computePrimaryRangeFromCandles(ohlcvCandles);
      return buildOverlayLines(range);
    }
    // FR is rendered by SignedBarChart — its `lines` slot is empty unless
    // the user enabled a cumulative MA, so we'd otherwise have no primary
    // range to remap into. Use the bar values' symmetric range instead.
    if (instance.kind === 'fr') {
      const range = computePrimaryRangeFromField(
        frBpsData as unknown as Record<string, number>[],
        'rate_bps', true
      );
      return buildOverlayLines(range);
    }
    // Pick the right per-kind primary-lines array. Falls through to an empty
    // range when no primary lines exist (overlay will render flat-centered).
    let primaryLines: typeof aaveLinesD = [];
    if (instance.kind === 'oi' || instance.kind === 'hl_smart_oi') primaryLines = oiLinesD;
    else if (instance.kind === 'fr') primaryLines = frLinesD;
    else if (instance.kind === 'tt') primaryLines = ttLinesD;
    else if (instance.kind === 'ls') primaryLines = lsLinesD;
    else if (instance.kind === 'bs') primaryLines = bsLines;
    else if (instance.kind === 'sz') primaryLines = szLinesD;
    else if (instance.kind === 'transfer') primaryLines = transferLinesD;
    else if (instance.kind === 'exchange_flow') primaryLines = exchangeFlowLinesD;
    else if (instance.kind === 'hl_unrealized_pnl') primaryLines = hlUnrealizedLinesD;
    else if (instance.kind === 'hl_pnl' && (instance.hlPnlSide ?? 'total') !== 'total') primaryLines = hlPnlSplitLinesD as typeof aaveLinesD;
    else if (instance.kind === 'hl_vault_net') primaryLines = hlVaultFlowLinesD;
    else if (instance.kind === 'hl_transfers') {
      // Special-case range derivation: when host's primary lines exist
      // (Point checked, in/out/net mode active), use them so the overlay
      // remaps into the same range the user actually sees — e.g. in
      // Netflow mode the y-axis is tight around net's range and the
      // overlay should sit inside that. When primary lines are hidden
      // (Point unchecked, no MA/Sum), fall back to the union range of
      // all three bridge-flow fields so the overlay still has a stable
      // target instead of vanishing.
      const rows = data as unknown as Record<string, number>[];
      const primaryRange = computePrimaryRangeFromLines(
        rows,
        hlBridgeFlowsLinesD as unknown as { compute: (d: Record<string, number>, i: number, arr: Record<string, number>[]) => number; axis?: 'primary' | 'secondary' }[]
      );
      const havePrimary = Number.isFinite(primaryRange[0]) && Number.isFinite(primaryRange[1]) && primaryRange[1] !== primaryRange[0];
      if (havePrimary) return buildOverlayLines(primaryRange);
      // Fallback: union of all three flow fields.
      let lo = Infinity, hi = -Infinity;
      for (const r of rows) {
        for (const f of ['deposit','withdrawal','net']) {
          const v = r[f];
          if (Number.isFinite(v)) {
            if (v < lo) lo = v;
            if (v > hi) hi = v;
          }
        }
      }
      return buildOverlayLines([lo, hi]);
    }
    else if (isUniswapV3Kind(instance.kind) || isUniswapV2Kind(instance.kind)
             || isUniswapV4Kind(instance.kind) || isAeroClKind(instance.kind)
             || isAeroBasicKind(instance.kind)) primaryLines = uniswapLinesD;
    else if (isLidoKind(instance.kind)) primaryLines = lidoLinesD;
    else if (isGmxV2Kind(instance.kind) && gmxIsPositionLongShortKind) primaryLines = [...gmxPositionLinesD, ...cumulativeLines] as typeof aaveLinesD;
    else primaryLines = aaveLinesD;
    const range = computePrimaryRangeFromLines(
      data as unknown as Record<string, number>[],
      primaryLines as unknown as { compute: (d: Record<string, number>, i: number, arr: Record<string, number>[]) => number; axis?: 'primary' | 'secondary' }[]
    );
    return buildOverlayLines(range);
  });

  function addOverlay(o: ChartOverlay) {
    const list = (instance.overlays ?? []).slice();
    const existingIdx = list.findIndex((x) => x.id === o.id);
    if (existingIdx >= 0) list[existingIdx] = o;
    else list.push(o);
    instance.overlays = list;
    overlayDialogOpen = false;
    overlayEditing = null;
  }
  function removeOverlay(id: string) {
    instance.overlays = (instance.overlays ?? []).filter((x) => x.id !== id);
    const next = new Map(overlayLoaded);
    next.delete(id);
    overlayLoaded = next;
  }
  function toggleOverlayHidden(id: string) {
    const list = (instance.overlays ?? []).slice();
    const i = list.findIndex((x) => x.id === id);
    if (i < 0) return;
    list[i] = { ...list[i], hidden: !list[i].hidden };
    instance.overlays = list;
  }
  function openOverlayAdd() {
    overlayEditing = null;
    overlayDialogOpen = true;
  }
  function openOverlayEdit(o: ChartOverlay) {
    overlayEditing = o;
    overlayDialogOpen = true;
  }
  let usedOverlayColors = $derived((instance.overlays ?? []).map((o) => o.color));
  // Colours the HOST chart's primary lines are using right now. We pass
  // these to nextOverlayColor (via AddOverlayDialog's usedColors prop)
  // so a new overlay isn't auto-assigned a hue indistinguishable from
  // what the user is already looking at. Mode-sensitive for OI because
  // the rendered colours depend on the long/short/total/ratio/pct/net
  // selector. Returns a small hex list — empty is fine.
  let hostPrimaryColors = $derived.by((): string[] => {
    const k = instance.kind;
    if (k === 'oi' || k === 'hl_smart_oi') {
      const ex = k === 'hl_smart_oi' ? 'hl' : (instance.exchange ?? 'binance');
      const mode = instance.oiHlDisplay ?? 'total';
      if (ex === 'hl' && mode === 'long_short') return ['#22c55e', '#ef4444'];
      if (ex === 'hl' && mode === 'long')         return ['#22c55e'];
      if (ex === 'hl' && mode === 'short')        return ['#ef4444'];
      if (ex === 'hl' && mode === 'long_to_short') return ['#a855f7'];
      if (ex === 'hl' && mode === 'net_pct')      return ['#f59e0b'];
      if (ex === 'hl' && mode === 'net')          return ['#f97316'];
      if (ex === 'hl')                            return ['#06b6d4']; // total
      return ['#fbbf24']; // Binance total OI default
    }
    if (k === 'bs')  return ['#22c55e', '#ef4444'];                  // buyer / seller
    if (k === 'sz')  return ['#3f3f46', '#3b82f6', '#a855f7'];        // small / mid / large
    if (k === 'fr')  return ['#fbbf24'];                              // funding bar default
    if (k === 'tt')  return ['#84cc16', '#a855f7'];                   // top-trader L/S
    if (k === 'ls')  return ['#fbbf24', '#06b6d4'];                   // all / taker L/S
    if (k === 'pc')  return ['#22c55e'];                              // price-comparison primary
    // ohlcv uses candle bodies (green/red); volume bars borrow the
    // buyer/seller colours. Treat as bs to keep overlays clear of them.
    if (k === 'ohlcv') return ['#22c55e', '#ef4444'];
    return [];
  });
  let dialogUsedColors = $derived([...usedOverlayColors, ...hostPrimaryColors]);

  // ── Stable merged-lines per chart kind ──────────────────────────────
  // We can't do `lines={[...primary, ...overlayLinesD]}` inline at each
  // call site — the array literal would be a new reference on every parent
  // render, refiring LineChart's $effect (which calls zoom.transform) and
  // killing any in-progress pan/zoom. Wrapping each merge in a $derived
  // makes the result a stable reference whenever neither input changes,
  // so pan/zoom stops fighting the redraw cycle.
  let ohlcvLinesM        = $derived(overlayLinesD.length === 0 ? ohlcvLinesD : [...ohlcvLinesD, ...overlayLinesD]);
  let oiLinesM           = $derived(overlayLinesD.length === 0 ? oiLinesD : [...oiLinesD, ...overlayLinesD]);
  let frLinesM           = $derived(overlayLinesD.length === 0 ? frLinesD : [...frLinesD, ...overlayLinesD]);
  let bsLinesM           = $derived(overlayLinesD.length === 0 ? bsLines : [...bsLines, ...overlayLinesD]);
  let szLinesM           = $derived(overlayLinesD.length === 0 ? szLinesD : [...szLinesD, ...overlayLinesD]);
  let ttLinesM           = $derived(overlayLinesD.length === 0 ? ttLinesD : [...ttLinesD, ...overlayLinesD]);
  let lsLinesM           = $derived(overlayLinesD.length === 0 ? lsLinesD : [...lsLinesD, ...overlayLinesD]);
  let transferLinesM     = $derived(overlayLinesD.length === 0 ? transferLinesD : [...transferLinesD, ...overlayLinesD]);
  let exchangeFlowLinesM = $derived(overlayLinesD.length === 0 ? exchangeFlowLinesD : [...exchangeFlowLinesD, ...overlayLinesD]);
  let hlUnrealizedLinesM = $derived(overlayLinesD.length === 0 ? hlUnrealizedLinesD : [...hlUnrealizedLinesD, ...overlayLinesD]);
  let hlPnlSplitLinesM   = $derived(overlayLinesD.length === 0 ? hlPnlSplitLinesD : [...hlPnlSplitLinesD, ...overlayLinesD]);
  let hlVaultFlowLinesM  = $derived(overlayLinesD.length === 0 ? hlVaultFlowLinesD : [...hlVaultFlowLinesD, ...overlayLinesD]);
  let hlBridgeFlowsLinesM= $derived(overlayLinesD.length === 0 ? hlBridgeFlowsLinesD : [...hlBridgeFlowsLinesD, ...overlayLinesD]);
  let gmxPositionLinesM  = $derived(overlayLinesD.length === 0 ? [...gmxPositionLinesD, ...cumulativeLines] : [...gmxPositionLinesD, ...cumulativeLines, ...overlayLinesD]);
  let aaveLinesM         = $derived(overlayLinesD.length === 0 ? aaveLinesD : [...aaveLinesD, ...overlayLinesD]);
  let uniswapLinesM      = $derived(overlayLinesD.length === 0 ? uniswapLinesD : [...uniswapLinesD, ...overlayLinesD]);
  let lidoLinesM         = $derived(overlayLinesD.length === 0 ? lidoLinesD : [...lidoLinesD, ...overlayLinesD]);
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
             `chain IN (...)` server-side. The general wrapper
             (instance.kind === 'lido') also surfaces an event sub-kind
             selector; switching between an L1 and an L2 subkind also flips
             the chain dropdown's disabled / options state via the L1/L2
             auto-snap effect. -->
        {#if instance.kind === 'lido'}
          <select
            bind:value={instance.lidoSubkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Lido event to display"
          >
            {#each LIDO_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
        <select
          bind:value={instance.chain}
          disabled={LIDO_L1_KINDS.has(effectiveKind)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {#if !LIDO_L1_KINDS.has(effectiveKind) && chainGroups.length > 0}
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
      {:else if isUniswapV3Kind(instance.kind)}
        <!-- Uniswap V3 kinds: chain dropdown (only chains that have
             ingested pools) + a pool dropdown filtered to that chain.
             Pools are sorted by total rows desc so the most-traded pool
             floats to the top. The general wrapper (instance.kind ===
             'uniswap_v3') also surfaces an event sub-kind selector. -->
        {#if instance.kind === 'uniswap_v3'}
          <select
            bind:value={instance.uniswapV3Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Uniswap V3 event to display"
          >
            {#each UNISWAP_V3_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
             dimension in V1. The general wrapper (instance.kind === 'morpho')
             also surfaces an event sub-kind selector — picks which Morpho
             event the chart is currently showing. -->
        {#if instance.kind === 'morpho'}
          <select
            bind:value={instance.morphoSubkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Morpho event to display"
          >
            {#each MORPHO_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
             + token selector identical to V3. The general wrapper
             (instance.kind === 'spark') also surfaces an event sub-kind
             selector that picks which Spark event the chart is showing. -->
        {#if instance.kind === 'spark'}
          <select
            bind:value={instance.sparkSubkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Spark event to display"
          >
            {#each SPARK_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
      {:else if isGmxV2Kind(instance.kind)}
        <!-- GMX V2: ARB-only (server-side AVAX is "not configured" in 2.18).
             Static chain chip + market dropdown populated from /gmx/streams,
             sorted by row count so the busiest perp floats to the top.
             First option = "All markets" (empty string), meaning the
             aggregate endpoint sums across every market. The general
             wrapper (instance.kind === 'gmx_v2') also surfaces an event
             sub-kind selector. -->
        {#if instance.kind === 'gmx_v2'}
          <select
            bind:value={instance.gmxV2Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="GMX V2 event to display"
          >
            {#each GMX_V2_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
            <option value={instance.gmxMarket}>{gmxMarketShort(instance.gmxMarket)}</option>
          {/if}
          {#each gmxMarketsForKind as m (m.market)}
            <option value={m.market}>{gmxMarketShort(m.market)}</option>
          {/each}
        </select>
        {#if gmxIsPositionLongShortKind}
          <!-- Long/Short series selector. The chart shows the chosen side
               only (one line) by default, or all four when 'All' is picked.
               Default 'total' = the original "Long + Short" single line. -->
          <select
            value={instance.gmxLongShortDisplay ?? 'total'}
            onchange={(e) =>
              (instance.gmxLongShortDisplay = e.currentTarget.value as
                'long' | 'short' | 'total' | 'net' | 'all')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Which side(s) of the position to plot"
          >
            <option value="long">Long</option>
            <option value="short">Short</option>
            <option value="total">Long + Short</option>
            <option value="net">Net Long</option>
            <option value="all">All</option>
          </select>
        {/if}
      {:else if isHlKind(instance.kind) && instance.kind !== 'hl_smart_oi'}
        <!-- Hyperliquid: static HL chip + token dropdown from the binance
             roster + optional wallet filter (free-text EVM address OR
             wallet-label category dropdown — mutually exclusive). The
             top_traders kind hides the wallet filter since it ranks ALL
             wallets by definition. The top_positions kind adds an
             "All tokens" option (empty string) since its leaderboard
             can rank wallets either per-token or by aggregate exposure.
             hl_transfers (bridge flows) is USDC-only — no token select.
             hl_smart_oi falls through to the generic OI branch below so
             it can reuse the long/short/total/long_to_short/net_pct +
             USD/token selectors that the `oi` kind already exposes. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">HL</span>
        {#if instance.kind === 'hl_transfers' || instance.kind === 'hl_vault_net' || instance.kind === 'hl_top_vaults' || instance.kind === 'hl_top_vault_lps' || instance.kind === 'hl_vault_detail'}
          <!-- These kinds have no token dimension — show a static USDC chip. -->
          <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">USDC</span>
          {#if instance.kind === 'hl_transfers'}
            <!-- Inflow/Outflow/Netflow/All selector for HL Bridge Flows.
                 Mirrors the CeX Exchange Flow chart's selector — same
                 `exchangeFlowType` field so saved layouts round-trip. -->
            <select
              value={instance.exchangeFlowType ?? 'netflow'}
              onchange={(e) => (instance.exchangeFlowType = e.currentTarget.value as 'inflow' | 'outflow' | 'netflow' | 'all')}
              class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
              title="Which direction(s) of HL bridge flow to plot"
            >
              <option value="inflow">Inflow</option>
              <option value="outflow">Outflow</option>
              <option value="netflow">Netflow</option>
              <option value="all">All</option>
            </select>
          {/if}
        {:else}
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
        {/if}
        {#if instance.kind !== 'hl_top_traders' && instance.kind !== 'hl_top_positions' && instance.kind !== 'hl_transfers' && instance.kind !== 'hl_vault_net' && instance.kind !== 'hl_top_vaults' && instance.kind !== 'hl_top_vault_lps' && instance.kind !== 'hl_vault_detail'}
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
        {#if instance.kind === 'hl_pnl'}
          <!-- Realized PnL side selector. 'Total' (default) keeps the
               original single net line sourced from hl_trade_history.
               The other modes flip to the /realized_pnl_split endpoint
               that bins hl_fills by Close Long / Close Short. -->
          <select
            value={instance.hlPnlSide ?? 'total'}
            onchange={(e) =>
              (instance.hlPnlSide = e.currentTarget.value as
                'total' | 'long' | 'short' | 'both')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Which side(s) of realized PnL to plot"
          >
            <option value="total">Net</option>
            <option value="long">Long</option>
            <option value="short">Short</option>
            <option value="both">Long + Short</option>
          </select>
        {/if}
      {:else if isLeaderboardKind(instance.kind)}
        <!-- Top-wallets leaderboards: filter selectors depend on paramShape
             (AAVE → chain+token with groups; Uniswap → pool tuple). The
             metric selector + Top N input live inside the table toolbar —
             see WalletLeaderboardTable. No interval selector (single-shot
             rollup, not a bucketed series). -->
        {#if LEADERBOARD_KIND_CONFIG[instance.kind]!.paramShape === 'aave'}
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
        {:else if LEADERBOARD_KIND_CONFIG[instance.kind]!.paramShape === 'uniswap_v3'}
          <!-- Pool selector reuses the V3 chart's dropdown — chain narrows
               the pool list, pool dropdown picks (sym0/sym1/fee). -->
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
        {:else if LEADERBOARD_KIND_CONFIG[instance.kind]!.paramShape === 'uniswap_v2'}
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
        {:else if LEADERBOARD_KIND_CONFIG[instance.kind]!.paramShape === 'uniswap_v4'}
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
        {/if}
      {:else if isAaveV4Kind(instance.kind)}
        <!-- AAVE V4: ETH-only (V4 is mainnet-only currently). Static
             chain chip + the same token selector as V2/V3. The general
             wrapper (instance.kind === 'aave_v4') also surfaces an event
             sub-kind selector. -->
        {#if instance.kind === 'aave_v4'}
          <select
            bind:value={instance.aaveV4Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="AAVE V4 event to display"
          >
            {#each AAVE_V4_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
             as V3 since the data shape is identical. The general wrapper
             (instance.kind === 'aave_v2') also surfaces an event sub-kind
             selector. -->
        {#if instance.kind === 'aave_v2'}
          <select
            bind:value={instance.aaveV2Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="AAVE V2 event to display"
          >
            {#each AAVE_V2_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
             V2 below. The general wrapper (instance.kind === 'uniswap_v2')
             also surfaces an event sub-kind selector. -->
        {#if instance.kind === 'uniswap_v2'}
          <select
            bind:value={instance.uniswapV2Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Uniswap V2 event to display"
          >
            {#each UNISWAP_V2_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
             carry the rest). The general wrapper (instance.kind ===
             'uniswap_v4') also surfaces an event sub-kind selector. -->
        {#if instance.kind === 'uniswap_v4'}
          <select
            bind:value={instance.uniswapV4Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Uniswap V4 event to display"
          >
            {#each UNISWAP_V4_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
      {:else if isAeroClKind(instance.kind)}
        <!-- Aerodrome CL: chain is BASE-only; show as static label.
             Pool = (sym0, sym1, tick_spacing). The general wrapper
             (instance.kind === 'aero_cl') also surfaces an event
             sub-kind selector. -->
        {#if instance.kind === 'aero_cl'}
          <select
            bind:value={instance.aeroClSubkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Aerodrome CL event to display"
          >
            {#each AERO_CL_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
             sAMM (stableswap curve). The general wrapper (instance.kind
             === 'aero_basic') also surfaces an event sub-kind selector. -->
        {#if instance.kind === 'aero_basic'}
          <select
            bind:value={instance.aeroBasicSubkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Aerodrome Basic event to display"
          >
            {#each AERO_BASIC_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">BASE</span>
        {#if instance.aeroBasicPool}
          <span class="text-zinc-100 text-xs font-medium px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">
            {instance.aeroBasicPool.symbol0}/{instance.aeroBasicPool.symbol1}
            <span class="text-zinc-500 ml-1">{instance.aeroBasicPool.stable ? 'sAMM' : 'vAMM'}</span>
          </span>
        {/if}
      {:else if isAaveV3Kind(instance.kind)}
        <!-- AAVE kinds: chain dropdown (5 EVMs + chain groups) + token
             <select> with a "Token group" optgroup so the user can pick
             e.g. "USDC+USDT" or "Stables" and the chart sums across the
             group's members. The general wrapper (instance.kind === 'aave_v3')
             also surfaces an event sub-kind selector. -->
        {#if instance.kind === 'aave_v3'}
          <select
            bind:value={instance.aaveV3Subkind}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="AAVE V3 event to display"
          >
            {#each AAVE_V3_CHART_KINDS as k (k)}
              <option value={k}>{chartKindShortLabel(k)}</option>
            {/each}
          </select>
        {/if}
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
      {:else if instance.kind === 'exchange_flow'}
        <!-- Exchange selector + Flow type selector + chain (locked to ARB
             for Hyperliquid, freely chosen for CeXes). Token selector is
             the same as transfer's. -->
        {#if (instance.exchangeFlowExchange ?? 'binance') === 'hyperliquid'}
          <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700" title="Hyperliquid is ARB-only">ARB</span>
        {:else}
          <select
            bind:value={instance.chain}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          >
            {#if chainGroups.length > 0}
              <optgroup label="Chain">
                {#each chains as c (c)}<option value={c}>{c}</option>{/each}
              </optgroup>
              <optgroup label="Chain group">
                {#each chainGroups as g (g.name)}
                  <option value={g.name} title={g.description}>Σ {g.label}</option>
                {/each}
              </optgroup>
            {:else}
              {#each chains as c (c)}<option value={c}>{c}</option>{/each}
            {/if}
          </select>
        {/if}
        <select
          value={instance.exchangeFlowExchange ?? 'binance'}
          onchange={(e) => {
            const v = e.currentTarget.value as 'binance' | 'coinbase' | 'okx' | 'bybit' | 'hyperliquid';
            instance.exchangeFlowExchange = v;
            if (v === 'hyperliquid') {
              // HL bridge is ARB + USDC only; auto-correct both so the
              // user doesn't have to reset them after the swap.
              instance.chain = 'ARB';
              instance.token = 'USDC';
            }
          }}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          title="Which exchange's deposit/hot-wallet wallets to filter on"
        >
          <option value="binance">Binance</option>
          <option value="coinbase">Coinbase</option>
          <option value="okx">OKX</option>
          <option value="bybit">Bybit</option>
          <option value="hyperliquid">Hyperliquid</option>
        </select>
        <select
          value={instance.exchangeFlowType ?? 'netflow'}
          onchange={(e) => (instance.exchangeFlowType = e.currentTarget.value as 'inflow' | 'outflow' | 'netflow' | 'all')}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          title="Which direction(s) of flow to plot"
        >
          <option value="inflow">Inflow</option>
          <option value="outflow">Outflow</option>
          <option value="netflow">Netflow</option>
          <option value="all">All</option>
        </select>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#if tokenGroups.length > 0}
            <optgroup label={activeChainGroup ? `Tokens on Σ ${activeChainGroup.label}` : `Tokens on ${instance.chain ?? ''}`}>
              {#each tokensForChain as t (t)}<option value={t}>{t}</option>{/each}
            </optgroup>
            <optgroup label="Token group">
              {#each tokenGroups as g (g.name)}
                <option value={g.name} title={g.description}>Σ {g.label}</option>
              {/each}
            </optgroup>
          {:else}
            {#each tokensForChain as t (t)}<option value={t}>{t}</option>{/each}
          {/if}
          {#if instance.token && !tokensForChain.includes(instance.token) && !tokenGroups.some((g) => g.name === instance.token)}
            <option value={instance.token}>{instance.token}</option>
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
        {#if instance.kind === 'ohlcv' || instance.kind === 'fr' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'oi' || instance.kind === 'pc' || instance.kind === 'ls'}
          <!-- Exchange selector picks the data source. ohlcv → *_ohlcv_1m,
               fr → binance_funding_rate / hl_funding, bs/sz → *_raw_trades /
               hl_trades, pc → *_ohlcv_1m close, ls → binance_long_short_ratios /
               (hl_position_history + hl_fills). Same render path either way.
               tt (top-trader L/S) stays Binance-only — see derivatives.py. -->
          <select
            value={instance.exchange ?? 'binance'}
            onchange={(e) => (instance.exchange = e.currentTarget.value as 'binance' | 'hl')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          >
            <option value="binance">Binance</option>
            <option value="hl">Hyperliquid</option>
          </select>
        {/if}
        {#if (instance.kind === 'oi' && (instance.exchange ?? 'binance') === 'hl') || instance.kind === 'hl_smart_oi'}
          <!-- HL-only display selector. position_history carries per-wallet
               sides so we can split OI into long/short or show all three on
               one chart. Same selector for hl_smart_oi which is HL-only by
               construction. -->
          <select
            value={instance.oiHlDisplay ?? 'total'}
            onchange={(e) => (instance.oiHlDisplay = e.currentTarget.value as 'long' | 'short' | 'total' | 'long_short' | 'long_to_short' | 'net_pct' | 'net')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Which side(s) of HL OI to plot"
          >
            <option value="total">Total</option>
            <option value="long">Long</option>
            <option value="short">Short</option>
            <option value="long_short">Long + Short</option>
            <option value="net">Net OI (L − S)</option>
            <option value="long_to_short">Long / Short</option>
            <option value="net_pct">Net OI %</option>
          </select>
        {/if}
        {#if (instance.kind === 'oi' || instance.kind === 'hl_smart_oi') && !((instance.kind === 'hl_smart_oi' || (instance.exchange ?? 'binance') === 'hl') && ((instance.oiHlDisplay ?? 'total') === 'long_to_short' || (instance.oiHlDisplay ?? 'total') === 'net_pct'))}
          <!-- USD vs token-amount unit selector for OI. Hidden in the Long/Short
               ratio mode where the unit cancels out. -->
          <select
            value={instance.oiUnit ?? 'usd'}
            onchange={(e) => (instance.oiUnit = e.currentTarget.value as 'usd' | 'token')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Display OI as dollar notional or token amount"
          >
            <option value="usd">USD</option>
            <option value="token">{instance.token ?? 'Token'}</option>
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
      {#if !isLeaderboardKind(instance.kind)}
        <select
          bind:value={instance.interval}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
      {/if}
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
      <button
        type="button"
        onclick={() => onRemove(instance.id)}
        title="Remove chart"
        class="w-7 h-7 rounded-md text-zinc-400 hover:text-red-400 hover:bg-zinc-800 border border-transparent text-sm leading-none flex items-center justify-center"
      >✕</button>
    </div>
  </div>

  {#if canHaveOverlays && (instance.overlays ?? []).length > 0}
    <!-- use:stopDragEvents — without it the chips strip below the titlebar
         counts as drag-grab area for svelte-dnd-action, so click-to-edit /
         click-to-toggle on a chip starts a reorder drag instead.
         cursor-default overrides the `cursor: grab` that svelte-dnd-action
         applies to the whole card — chips aren't a drag handle. -->
    <div
      use:stopDragEvents
      class="px-3 py-1.5 border-b border-zinc-800/60 bg-zinc-950 flex flex-wrap items-center gap-1.5 text-[11px] cursor-default">
      {#each (instance.overlays ?? []) as o (o.id)}
        <div
          class={'overlay-chip inline-flex items-center gap-1.5 rounded-md border bg-zinc-900 px-1.5 py-0.5 max-w-[18rem] ' +
                 (o.hidden ? 'border-zinc-800 opacity-50' : 'border-zinc-700')}
        >
          <button
            type="button"
            onclick={() => toggleOverlayHidden(o.id)}
            aria-pressed={!o.hidden}
            aria-label={o.hidden ? 'Show series' : 'Hide series'}
            title={o.hidden ? 'Show series' : 'Hide series'}
            class={'inline-block w-2.5 h-2.5 rounded-full leading-none p-0 cursor-pointer ' + (o.hidden ? 'border border-zinc-500 bg-transparent' : '')}
            style={o.hidden ? `border-color: ${o.color}` : `background: ${o.color}`}
          ></button>
          <span class={'truncate ' + (o.hidden ? 'text-zinc-500 line-through decoration-zinc-700' : 'text-zinc-200')}>{overlayChipLabel(o)}</span>
          {#if overlayLoadingIds.has(o.id)}
            <span class="text-zinc-500 text-[10px] leading-none">loading…</span>
          {/if}
          <button
            type="button"
            onclick={() => openOverlayEdit(o)}
            aria-label="Edit overlay"
            title="Edit overlay"
            class="text-zinc-500 hover:text-zinc-200 leading-none cursor-pointer flex items-center"
          ><Pencil size={11} strokeWidth={1.75} /></button>
          <button
            type="button"
            onclick={() => removeOverlay(o.id)}
            aria-label="Remove overlay"
            title="Remove overlay"
            class="text-zinc-500 hover:text-rose-400 leading-none cursor-pointer"
          >×</button>
        </div>
      {/each}
      <!-- Inline add-overlay shortcut after the last chip. Same dialog as
           the chart-area FAB — just saves a hover-to-bottom-right when
           the user is already looking at the chip row. -->
      <button
        type="button"
        onclick={openOverlayAdd}
        aria-label="Add overlay series"
        title="Add overlay series"
        class="text-zinc-400 hover:text-zinc-100 leading-none cursor-pointer flex items-center"
      ><PlusCircle size={14} strokeWidth={1.75} /></button>
    </div>
  {/if}

  <div class="flex-1 relative min-h-0 cursor-default group/chart" bind:clientHeight={chartAreaHeight} use:stopDragEvents>

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
      {#if instance.kind === 'hl_smart_oi'}
        <!-- Wallet-selection knobs live in their own widget so any future
             smart-wallet chart can drop the same component in. The Apply
             button explicitly triggers a refetch so the user can compose
             a multi-criterion query without hitting the server on every
             keystroke (number inputs that lose focus already cost a
             rebuild — Apply gates the actual chart update). -->
        <div class="basis-full flex items-start gap-2">
          <div class="flex-1">
            <SmartWalletSelector
              value={instance.smartSelector ?? defaultSmartSelectorState()}
              onChange={(v) => (instance.smartSelector = v)}
              tokenLabel={instance.token ?? ''}
            />
          </div>
          <button
            type="button"
            onclick={() => reload()}
            class="self-start mt-1 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-md px-3 py-1 text-xs text-zinc-100"
          >Apply</button>
        </div>
        <label
          class="flex items-center gap-1.5 text-zinc-300 cursor-pointer"
          title="Overlay a secondary-axis line showing how many wallets pass the criteria each day — spot over-filtering before it surprises you."
        >
          <input type="checkbox" bind:checked={instance.smartShowWalletCount} class="accent-zinc-400" />
          Show wallet count
        </label>
        <!-- Same instance.oiUnit field as the toolbar dropdown — duplicated
             here so the display-unit choice sits with the other smart-OI
             chart controls. Hidden in long_to_short / net_pct where the
             unit is mathematically meaningless. -->
        {#if (instance.oiHlDisplay ?? 'total') !== 'long_to_short' && (instance.oiHlDisplay ?? 'total') !== 'net_pct'}
          <span class="w-px h-4 bg-zinc-800"></span>
          <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Unit</span>
          <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
            <button
              type="button"
              onclick={() => (instance.oiUnit = 'usd')}
              class={'px-2 py-0.5 text-[11px] ' + ((instance.oiUnit ?? 'usd') === 'usd'
                ? 'bg-zinc-800 text-zinc-100'
                : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
              title="Plot OI as dollar notional"
            >USD</button>
            <button
              type="button"
              onclick={() => (instance.oiUnit = 'token')}
              class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + ((instance.oiUnit) === 'token'
                ? 'bg-zinc-800 text-zinc-100'
                : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
              title="Plot OI as raw token amount (e.g. BTC count)"
            >{instance.token ?? 'Token'}</button>
          </div>
        {/if}
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
      {#if instance.kind === 'transfer' || isAaveV3Kind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind) || isLidoKind(instance.kind) || (isUniswapV3Kind(instance.kind) && effectiveKind !== 'uniswap_v3_net_swap_flow') || isUniswapV2Kind(instance.kind) || effectiveKind === 'uniswap_v4_swap' || isAeroClKind(instance.kind) || isAeroBasicKind(instance.kind)}
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
            title={isUniswapV3Kind(instance.kind)
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
        {#if instance.showSum}
          <!-- Sliding-window length for the sum line, applies to every
               canSum kind. Empty / 0 = strict running total from the
               first loaded bucket (legacy behaviour); positive N = last
               N buckets at the current interval. -->
          <label class="flex items-center gap-1 text-zinc-400" title="Rolling window in current-interval buckets (0 = full running total)">
            <span class="text-[10px] uppercase tracking-widest">win</span>
            <input
              type="number"
              min="0"
              step="1"
              placeholder="all"
              value={instance.sumWindow ?? ''}
              onchange={(e) => {
                const n = Number(e.currentTarget.value);
                instance.sumWindow = !e.currentTarget.value ? undefined
                                   : Number.isFinite(n) && n > 0 ? Math.floor(n)
                                   : undefined;
              }}
              class="w-14 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 text-right"
            />
            <span class="text-zinc-500 text-[10px]">bars</span>
          </label>
        {/if}
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
    {#if data.length === 0 && loading}
      <div class="p-4 text-sm text-zinc-400 flex items-center gap-2">
        <svg class="animate-spin h-4 w-4 text-zinc-500" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        </svg>
        Loading {chartKindGroup(effectiveKind) ? `${chartKindGroup(effectiveKind)} ${chartKindShortLabel(effectiveKind)}` : kindLabel}…
      </div>
    {:else if data.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        {#if instance.token && instance.chain && (instance.kind === 'transfer' || instance.kind === 'exchange_flow')}
          No data available for {activeTokenGroup ? `Σ ${tokenGroups.find((g) => g.name === activeTokenGroup)?.label ?? activeTokenGroup}` : instance.token} on {activeChainGroup ? `Σ ${activeChainGroup.label}` : instance.chain}{instance.kind === 'exchange_flow' ? ` for ${EXCHANGE_LABEL[instance.exchangeFlowExchange ?? 'binance'] ?? (instance.exchangeFlowExchange ?? 'binance')}` : ''}.
        {:else if chartKindGroup(effectiveKind)}
          No data for {chartKindGroup(effectiveKind)} {chartKindShortLabel(effectiveKind)}.
        {:else}
          No data for {kindLabel}.
        {/if}
      </div>
    {:else if instance.kind === 'ohlcv'}
      <CandlestickChart
        candles={ohlcvCandles}
        lines={ohlcvLinesM}
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
    {:else if instance.kind === 'oi' || instance.kind === 'hl_smart_oi'}
      <!-- HL Long/Short ratio is unitless (1.03, not $1.03). Otherwise USD
           or token amount based on the oiUnit selector. hl_smart_oi reuses
           the same rendering — its payload shape matches /oi_split. -->
      {@const oiHlMode = (instance.kind === 'hl_smart_oi'
                          || (instance.exchange ?? 'binance') === 'hl')
                          ? (instance.oiHlDisplay ?? 'total') : null}
      {@const oiIsRatio = oiHlMode === 'long_to_short'}
      {@const oiIsPct = oiHlMode === 'net_pct'}
      {@const oiIsNet = oiHlMode === 'net'}
      {@const oiUseToken = (instance.oiUnit ?? 'usd') === 'token' && !oiIsRatio && !oiIsPct}
      {@const showWalletCount = instance.kind === 'hl_smart_oi' && (instance.smartShowWalletCount ?? false)}
      <LineChart
        data={data as OpenInterestRow[]}
        lines={oiLinesM}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        refLines={(oiIsNet || oiIsPct) ? ZERO_REF : []}
        formatY={oiIsRatio ? fmtRatio
                 : oiIsPct ? ((v: number) => `${(v >= 0 ? '+' : '')}${(v * 100).toFixed(1)}%`)
                 : (oiUseToken ? fmtAmountAxis : fmtUsdAxis)}
        formatTooltip={oiIsRatio ? fmtRatio
                 : oiIsPct ? ((v: number) => `${(v >= 0 ? '+' : '')}${(v * 100).toFixed(2)}%`)
                 : (oiUseToken ? fmtAmountTooltip : fmtUsdTooltip)}
        formatY2={showWalletCount ? ((v: number) => Math.round(v).toString()) : undefined}
        formatTooltip2={showWalletCount ? ((v: number) => `${Math.round(v)} wallets`) : undefined}
        onClick={showWalletCount ? ((t: number) => openSmartWalletsDialog(t)) : undefined}
      />
    {:else if instance.kind === 'fr'}
      <SignedBarChart
        data={frBpsData}
        valueKey="rate_bps"
        lines={frLinesM}
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
        lines={bsLinesM}
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
        lines={szLinesM}
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
        lines={ttLinesM}
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
        lines={lsLinesM}
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
        lines={transferLinesM}
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
    {:else if instance.kind === 'exchange_flow'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={exchangeFlowLinesM}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={exchangeFlowUseUsd ? fmtUsdAxis : fmtAmountAxis}
        formatTooltip={exchangeFlowUseUsd ? fmtUsdTooltip : fmtAmountTooltip}
      />
    {:else if instance.kind === 'hl_top_traders'}
      <TableChart leaders={data.length > 0 ? ((data[0] as unknown as {leaders?: Record<string, unknown>[]}).leaders ?? []) : []} />
    {:else if isLeaderboardKind(instance.kind)}
      <WalletLeaderboardTable
        rows={data.length > 0 ? ((data[0] as unknown as {leaders?: Record<string, unknown>[]}).leaders ?? []) as unknown as import('$lib/components/WalletLeaderboardTable.svelte').LeaderboardRow[] : []}
        columns={LEADERBOARD_KIND_CONFIG[instance.kind]!.metrics}
        orderBy={(instance.leaderboardMetric ?? 'deposit') as LeaderboardMetric}
        topN={instance.leaderboardTopN ?? 10}
        onChangeOrderBy={(m) => (instance.leaderboardMetric = m)}
        onChangeTopN={(n) => (instance.leaderboardTopN = n)}
        loading={loading}
        error={error}
        protocolLabel={LEADERBOARD_KIND_CONFIG[instance.kind]!.protocolLabel}
      />
    {:else if instance.kind === 'hl_top_positions'}
      <HlTopPositionsChart
        wallets={data.length > 0 ? ((data[0] as unknown as {wallets?: unknown[]}).wallets ?? []) : []}
        selectedWallet={instance.hlSelectedWallet ?? ''}
        onSelectWallet={(w) => (instance.hlSelectedWallet = w)}
      />
    {:else if instance.kind === 'hl_unrealized_pnl'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={hlUnrealizedLinesM}
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
    {:else if instance.kind === 'hl_vault_net'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={hlVaultFlowLinesM}
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
    {:else if instance.kind === 'hl_top_vaults'}
      <HlTopVaultsTable
        vaults={data.length > 0 ? ((data[0] as unknown as {vaults?: unknown[]}).vaults ?? []) : []}
        orderBy={instance.hlVaultSortBy ?? 'net'}
        onChangeOrderBy={(v) => (instance.hlVaultSortBy = v)}
      />
    {:else if instance.kind === 'hl_top_vault_lps'}
      <HlTopVaultLpsTable
        lps={data.length > 0 ? ((data[0] as unknown as {lps?: unknown[]}).lps ?? []) : []}
      />
    {:else if instance.kind === 'hl_vault_detail'}
      <HlVaultDetailChart
        vaults={data.length > 0 ? ((data[0] as unknown as {vaults?: unknown[]}).vaults ?? []) : []}
        selectedVault={instance.hlSelectedVault ?? ''}
        onSelectVault={(v) => (instance.hlSelectedVault = v)}
      />
    {:else if instance.kind === 'hl_transfers'}
      <!-- Zero ref line only when Netflow is on screen (it crosses zero);
           hidden in pure Inflow / Outflow modes where the line is bounded
           below by zero by construction. Sum line, when enabled, lives on
           the secondary axis and shares the USD formatter. -->
      {@const hlFlowT = instance.exchangeFlowType ?? 'netflow'}
      <LineChart
        data={data as Array<Record<string, number>>}
        lines={hlBridgeFlowsLinesM}
        refLines={(hlFlowT === 'netflow' || hlFlowT === 'all') ? ZERO_REF : []}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
        formatY2={instance.showSum ? fmtUsdAxis : undefined}
        formatTooltip2={instance.showSum ? fmtUsdTooltip : undefined}
      />
    {:else if isGmxV2Kind(instance.kind) && gmxIsPositionLongShortKind}
      <LineChart
        data={data as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>}
        lines={gmxPositionLinesM}
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
    {:else if instance.kind === 'hl_pnl' && (instance.hlPnlSide ?? 'total') !== 'total'}
      <LineChart
        data={data as Array<{ time: number; long_pnl: number; short_pnl: number; total_pnl: number; count: number }>}
        lines={hlPnlSplitLinesM}
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
    {:else if isAaveV3Kind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind) || isGmxV2Kind(instance.kind) || isHlKind(instance.kind)}
      <LineChart
        data={data as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>}
        lines={aaveLinesM}
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
    {:else if isUniswapV3Kind(instance.kind) || isUniswapV2Kind(instance.kind) || isUniswapV4Kind(instance.kind) || isAeroClKind(instance.kind) || isAeroBasicKind(instance.kind)}
      <LineChart
        data={data as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>}
        lines={uniswapLinesM}
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
        lines={lidoLinesM}
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

  {#if canHaveOverlays}
    <!-- Add-overlay FAB. Only visible while the chart card is hovered (so it
         doesn't sit on top of the chart at rest). Click opens the two-step
         overlay dialog. -->
    <button
      type="button"
      onclick={openOverlayAdd}
      title="Add overlay series"
      aria-label="Add overlay series"
      class="overlay-fab"
    >
      <PlusCircle size={22} strokeWidth={1.75} />
    </button>
  {/if}
  </div>
</div>

{#if canHaveOverlays}
  <AddOverlayDialog
    open={overlayDialogOpen}
    initial={overlayEditing}
    primaryToken={instance.token ?? ''}
    usedColors={dialogUsedColors}
    {tokens}
    {tokenGroups}
    {chainGroups}
    transferStreams={streams}
    {uniPools}
    {lidoChains}
    {gmxMarkets}
    onSubmit={addOverlay}
    onClose={() => { overlayDialogOpen = false; overlayEditing = null; }}
  />
{/if}

<SmartWalletsDialog
  open={walletsDialogOpen}
  wallets={walletsDialogList}
  loading={walletsDialogLoading}
  error={walletsDialogError}
  day={walletsDialogDay}
  token={instance.token ?? ''}
  onClose={() => { walletsDialogOpen = false; if (walletsFetchCtl) walletsFetchCtl.abort(); }}
/>

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

  /* Compound-chart "+" floating action button. Sits in the bottom-right of
     the chart canvas and only fades in while the chart card is hovered, so
     it stays out of the way at rest. Click opens the overlay dialog. */
  .overlay-fab {
    /* The PlusCircle icon already paints its own circle, so the button
       itself is transparent — no doubled-ring or off-centre glyph. */
    position: absolute;
    bottom: 0.5rem;
    right: 0.5rem;
    padding: 0;
    border: 0;
    background: transparent;
    color: rgb(212 212 216);                       /* zinc-300 */
    line-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    /* Don't intercept pointer events when invisible — otherwise the FAB
       silently swallows clicks in the bottom-right corner of the chart,
       which would block d3.zoom from seeing pan/zoom gestures that
       start there. */
    pointer-events: none;
    transition: opacity 120ms ease-out, color 120ms ease-out, transform 120ms ease;
    z-index: 15;
    cursor: pointer;
  }
  .group\/chart:hover .overlay-fab {
    opacity: 0.85;
    pointer-events: auto;
  }
  .overlay-fab:hover {
    opacity: 1 !important;
    color: rgb(244 244 245);                       /* zinc-100 */
    transform: translateY(-1px);
  }
  .overlay-fab:active {
    transform: translateY(0);
  }
</style>
