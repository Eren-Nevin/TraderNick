<script lang="ts">
  import CandlestickChart from '$lib/components/charts/lwc/LwcCandlestickChart.svelte';
  import StackedBarChart from '$lib/components/charts/lwc/LwcStackedBarChart.svelte';
  import LineChart from '$lib/components/charts/lwc/LwcLineChart.svelte';
  import TableChart from '$lib/components/TableChart.svelte';
  import TokenLeaderboardTable from '$lib/components/TokenLeaderboardTable.svelte';
  import SpotCvdTable from '$lib/components/SpotCvdTable.svelte';
  import SmartWalletMetricsTable from '$lib/components/SmartWalletMetricsTable.svelte';
  import SmartWalletTokenListTable from '$lib/components/SmartWalletTokenListTable.svelte';
  import HlTopPositionsChart from '$lib/components/HlTopPositionsChart.svelte';
  import HlTopVaultsTable from '$lib/components/HlTopVaultsTable.svelte';
  import HlTopVaultLpsTable from '$lib/components/HlTopVaultLpsTable.svelte';
  import HlVaultDetailChart from '$lib/components/HlVaultDetailChart.svelte';
  import WalletLeaderboardTable from '$lib/components/WalletLeaderboardTable.svelte';
  import SignedBarChart from '$lib/components/charts/lwc/LwcSignedBarChart.svelte';
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
    BUYER_SELLER_PCT_LINES,
    BUYER_SELLER_IMBALANCE_LINES,
    BUYER_SELLER_RATIO_LINES,
    BUYER_SELLER_SERIES,
    buyerSellerSeries,
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
    fmtPriceAxis,
    fmtPriceTooltip,
    fmtRatio,
    lookbackWindow,
    maArray,
    sizeLineSeries,
    takerSplitLines,
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
    isDualViewKind,
    DUAL_VIEW_KINDS,
    SMART_WALLET_LOOKBACKS,
    SMART_WALLET_DYNAMIC_LOOKBACKS,
    SMART_WALLET_CUTOFF_LOOKBACKS,
    type LeaderboardMetric,
    type SmartWalletMetric,
    type SmartWalletLookback,
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
  import SmartWalletsDialog from '$lib/components/SmartWalletsDialog.svelte';
  import { filtersStore } from '$lib/stores/filters.svelte';
  import { walletPinsStore } from '$lib/stores/walletPins.svelte';
  import { expandFilter, filterWireKey, type FilterWire } from '$lib/components/charts/filters';
  import Pencil from '@lucide/svelte/icons/pencil';
  import PlusCircle from '@lucide/svelte/icons/plus-circle';
  import type { View } from '$lib/chart-zoom';
  import { queuedFetch } from '$lib/fetch-queue';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { SHADOW_ITEM_MARKER_PROPERTY_NAME } from 'svelte-dnd-action';

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
  // Cache slot id. Dual-view widgets (table ⇄ chart) keep a SEPARATE entry per
  // view so switching modes restores the other view's data instantly instead of
  // refetching — `${id}:table` and `${id}:chart`. Single-view kinds key on id.
  function cacheId(): string {
    return isDualViewKind(instance.kind)
      ? `${instance.id}:${instance.viewMode ?? 'table'}`
      : instance.id;
  }

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
      // Dual-view widgets in 'chart' mode borrow their mapped chart kind's
      // rendering + controls (e.g. smart_wallets_table → hl_smart_oi).
      : (isDualViewKind(instance.kind) && instance.viewMode === 'chart')
      ? (DUAL_VIEW_KINDS[instance.kind]!.chartKind as ChartInstanceT['kind'])
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
    // Group widget: load the wallet groups so the group dropdown is populated.
    if (isGroup) walletPinsStore.hydrate();
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

  // ---- dynamic ("chunked") loading ----
  // Some kinds are expensive to resolve over the full window — hl_smart_oi
  // (per-day wallet leaderboard + OI rollup), HL open interest (oi_split), and
  // exchange flow. Instead of fetching the whole window up front we load the
  // most recent CHUNK and backfill older CHUNK-day windows on demand as the
  // user pans / zooms toward the loaded floor. Absolute-time views mean a
  // prepend never shifts the chart; backend caches make the re-pans cheap.
  // The oldest reachable day is the kind's normal full-window floor, captured
  // per-load in `dynFloor` (so per-interval caps like 1m→30d still hold).
  // hl_smart_oi uses a SMALLER initial/backfill chunk: a global-Sharpe selector
  // cold-fills its per-day leaderboard over the whole chunk in one request, and
  // 60 days can take ~90-100s — close to the 120s browser fetch cap. 30 days
  // halves the cold-fill cost (~45-55s) for guaranteed headroom; older windows
  // backfill on pan (each its own request, on a separate slot), and the
  // smart_wallets_cache makes re-pans cheap. The cheaper kinds (HL OI, exchange
  // flow) keep 60.
  // smart_wallets_dynamic is the heaviest per-day cost (each plotted day reruns
  // the rolling selection over its trailing lookback), so it uses small chunks.
  const DYN_CHUNK_DAYS = instance.kind === 'hl_smart_oi'
    ? 30
    : instance.kind === 'smart_wallets_dynamic'
      ? 14
      : 60;
  // Dynamic FIRST fetch window (days). Kept tiny so the initial paint is cheap;
  // ≤ DEFAULT_VIEW_DAYS (14) so defaultView() fits the view to exactly this
  // loaded range (no unloaded gap on the left). Older history backfills on pan.
  const DYN_SW_FIRST_DAYS = 3;
  // Dynamic chart: initial visible window (days). Smaller than DYN_SW_FIRST_DAYS
  // so the loaded 3-day window has headroom to the LEFT of the view — otherwise
  // the view's left edge sits on the loaded floor and the prefetch below fires
  // immediately (the reported "needs more than one fetch").
  const DYN_SW_VIEW_DAYS = 1;
  // Start backfilling once the view's left edge comes within this many days of
  // the loaded floor, so data is already there by the time the user reaches it.
  // Dynamic uses a tiny buffer (its loaded window is only a few days), so the
  // initial small viewport leaves headroom and doesn't trigger an immediate
  // backfill; other chunked kinds keep the generous 12-day prefetch.
  const DYN_PREFETCH_DAYS = instance.kind === 'smart_wallets_dynamic' ? 1 : 12;

  /** True when this instance should load in chunks. hl_smart_oi always;
   *  open interest only on Hyperliquid (Binance OI is a fast dedicated
   *  endpoint); exchange flow always. */
  function isDynamicChunkKind(): boolean {
    if (instance.kind === 'hl_smart_oi') return true;
    // Dual-view chart mode (smart_wallets_table → OI of found wallets) backfills
    // on pan like hl_smart_oi; the table view is single-shot (not chunked).
    if (isDualViewKind(instance.kind) && instance.viewMode === 'chart') return true;
    if (instance.kind === 'oi') return (instance.exchange ?? 'binance') === 'hl';
    if (instance.kind === 'exchange_flow') return true;
    return false;
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
  // hl_smart_oi backfill: a second in-flight slot independent of the main
  // load(), so panning-triggered older-chunk fetches don't cancel (or get
  // cancelled by) the primary load. `loadingMore` guards against overlap.
  let currentChunkLoad: AbortController | null = null;
  let loadingMore = $state(false);
  // Oldest day (unix-sec) the current dynamic load may backfill to — the kind's
  // full-window floor, captured when the primary load runs. null = not a
  // chunked load (don't backfill).
  let dynFloor: number | null = null;
  let localView = $state<View>(null);
  let localHoverTime = $state<number | null>(null);
  let error = $state<string | null>(null);
  // smart_wallets_table: the FULL count of wallets passing the filters, captured
  // from the table view's fetch (always loaded first) so the chart view can
  // display the same number without re-running the costly selection. Tagged with
  // the selection key it was computed for, so the chart badge hides rather than
  // shows a stale count if the filters change before the table reloads.
  let swFoundTotal = $state(0);
  let swFoundTotalKey = $state('');
  // Which dual-view view the current `data` holds ('table' = wallet rows,
  // 'chart' = OI series). The two shapes are incompatible, so on a view switch
  // we must drop `data` before the new load — otherwise the chart branch would
  // render table rows (no valid points → a black chart) for the whole load.
  let dualDataView = $state<'table' | 'chart' | 'token_list' | ''>('');

  // ---- effective view + hoverTime ----
  // A chart follows the shared zoom/pan only when global sync is on AND it
  // hasn't opted out via its own `noSync` setting. Excluded charts use their
  // local view/hover so they can be navigated without touching the others.
  let synced = $derived(syncZoom && !instance.noSync);
  let effectiveView = $derived(synced ? sharedView : localView);
  let effectiveHoverTime = $derived(synced ? sharedHoverTime : localHoverTime);

  function handleView(v: View) {
    if (synced) onSharedView(v);
    else localView = v;
  }
  function handleHover(t: number | null) {
    if (synced) onSharedHover(t);
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

  // Dual-view chart: mark where the lookback window ENDS (the snapshot day).
  // Data to its right is post-selection; panning left reveals the in-sample
  // lookback period. Amber + label so it reads as an annotation. Appended to
  // the week lines; for non-dual / table mode this is just weekVRefLines.
  // Dynamic has NO fixed snapshot (the lookback rolls per bucket), so the
  // "Lookback end" marker is meaningless there — omit it (the rolling lookback
  // is surfaced as a text chip in the chart toolbar instead).
  let chartVRefLines = $derived.by(() => {
    if (!(isDualViewKind(instance.kind) && instance.viewMode === 'chart')) return weekVRefLines;
    if (instance.kind === 'smart_wallets_dynamic') return weekVRefLines;
    const t = Math.floor(Date.parse(swSnapshotIso() + 'T00:00:00Z') / 1000);
    return [
      ...weekVRefLines,
      { time: t, color: '#fbbf24', width: 1, dash: '4,3', label: 'Lookback end' }
    ];
  });

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

  // smart_wallets_table: resolved snapshot ISO date — instance.swSnapshot when
  // set, else the default for a freshly-created widget: the FIRST day of the
  // current UTC month (e.g. 2026-06-01 in June). The slider writes back into
  // instance.swSnapshot; this is the single source of truth for the fetch +
  // the table header.
  function swSnapshotIso(): string {
    // Group: stats are "as of now" → the latest day.
    if (instance.kind === 'smart_wallets_group') return swTodayIso();
    // Cutoff: the user-selectable cutoff day (default = latest/today).
    if (instance.kind === 'smart_wallets_cutoff') return instance.swCutoffDate || swTodayIso();
    if (instance.swSnapshot) return instance.swSnapshot;
    // Dynamic has no snapshot slider — its table view shows the LATEST day, and
    // its rolling chart ignores snapshot entirely (the proxy strips it). So the
    // default is today; Fixed defaults to the first of the current month.
    if (instance.kind === 'smart_wallets_dynamic') return swTodayIso();
    return swDefaultSnapshotIso();
  }
  function swDefaultSnapshotIso(): string {
    const n = new Date();
    return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), 1))
      .toISOString().slice(0, 10);
  }
  function swTodayIso(): string {
    const n = new Date();
    return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()))
      .toISOString().slice(0, 10);
  }
  // Both smart-wallet widgets share criteria, gear panel, and refresh-gating.
  const isSwKind = (k: string) => k === 'smart_wallets_table' || k === 'smart_wallets_dynamic' || k === 'smart_wallets_cutoff' || k === 'smart_wallets_group';
  const isCutoff = $derived(instance.kind === 'smart_wallets_cutoff');
  const isGroup = $derived(instance.kind === 'smart_wallets_group');

  // ── Deferred smart-wallet FILTERS ─────────────────────────────────────
  // The gear inputs edit instance.sw* live (so they persist), but the table's
  // load key + fetch read these COMMITTED values instead — so tweaking guards
  // doesn't fire a reload per change. The user applies them with the refresh
  // button (which commits + reloads). Metric / lookback / token / snapshot stay
  // immediate (they're header selectors, not gear "settings").
  const SW_FILTER_FIELDS = [
    'swMinDays', 'swMinVolume', 'swMinRealized', 'swMinOi', 'swMinAvgTradeSize',
    'swMinTakerPct', 'swMaxFeePct', 'swMaxFundingPct', 'swMinAccountDuration',
    'swMinTokens', 'swMinWinRate', 'swMinTradesPerDay', 'swMaxTradesPerDay',
    'swMinAnnualizedSharpe',
    'swMinAvgOiShare', 'swMaxAvgOiShare', 'swMinVolumeShare', 'swMaxVolumeShare',
  ] as const;
  type SwFilterField = (typeof SW_FILTER_FIELDS)[number];
  function swFilterSnap(): Record<SwFilterField, number | null | undefined> {
    const o = {} as Record<SwFilterField, number | null | undefined>;
    for (const k of SW_FILTER_FIELDS) o[k] = instance[k] as number | null | undefined;
    return o;
  }
  let committedFilters = $state(swFilterSnap());
  function swF(field: SwFilterField, dflt: number): number {
    return (committedFilters[field] ?? dflt) as number;
  }
  function commitSwFilters() {
    committedFilters = swFilterSnap();
  }
  let swFiltersDirty = $derived(
    isSwKind(instance.kind) &&
      SW_FILTER_FIELDS.some((k) => (committedFilters[k] ?? null) !== (instance[k] ?? null))
  );
  // Refresh-only gating for smart_wallets_table: the finder NEVER auto-fetches
  // (not on mount, not on token / lookback / metric / snapshot / filter change).
  // It runs ONLY when the user clicks refresh (reload), which arms a single load
  // via the $effect. `swArmed` = a refresh has run; `swArmedSelectionKey` = the
  // selection key that was loaded — a later view toggle keeps the same selection
  // key so it may still load the other view, but any selection change is blocked
  // until the next explicit refresh. `swForceFreshNext` carries the cache-bust
  // through to the effect's single load.
  let swArmed = $state(false);
  let swArmedSelectionKey = $state('');
  let swForceFreshNext = $state(false);
  // True when the displayed finder data is stale vs the current inputs (never
  // run, selection changed, or gear filters edited) — drives the refresh
  // button's "needs refresh" highlight.
  let swNeedsRefresh = $derived(
    isSwKind(instance.kind) &&
      (!swArmed || swFiltersDirty || swTableKey() !== swArmedSelectionKey
        // Dynamic never auto-loads, so the loaded view can also be stale vs the
        // current oi_token / interval — flag those too (loadKey includes them).
        || (instance.kind === 'smart_wallets_dynamic' && loadedKey !== '' && loadKey() !== loadedKey))
  );

  // The smart_wallets_table cache/load key for the TABLE view (the found-wallet
  // set). Every selector busts it: metric+order change the ranking,
  // lookback/token/snapshot change the window, and the min-/max- guards change
  // the candidate set. The guards come from the COMMITTED snapshot so editing
  // them doesn't reload until refresh. No interval — single-shot rollup. Chart
  // mode reuses this (its wallet set is the table's) and appends its own OI
  // params; see loadKey.
  function swTableKey(): string {
    return [
      instance.kind, instance.swMetric ?? 'sharpe', instance.swLookback ?? 7,
      instance.swToken ?? '__all__', swSnapshotIso(),
      swF('swMinDays', 3), swF('swMinVolume', 0),
      swF('swMinRealized', 0), swF('swMinOi', 0),
      swF('swMinAvgTradeSize', 0), swF('swMinTakerPct', 0),
      committedFilters.swMaxFeePct ?? '', committedFilters.swMaxFundingPct ?? '',
      swF('swMinAccountDuration', 0), swF('swMinTokens', 0),
      swF('swMinWinRate', 0),
      swF('swMinTradesPerDay', 0), committedFilters.swMaxTradesPerDay ?? '',
      committedFilters.swMinAnnualizedSharpe ?? '',
      swF('swMinAvgOiShare', 0), committedFilters.swMaxAvgOiShare ?? '',
      swF('swMinVolumeShare', 0), committedFilters.swMaxVolumeShare ?? '',
      (instance.swCutoffLookbacks ?? []).join(','), instance.swRowLimit ?? 100,
      instance.swGroupId ?? '', instance.swCutoffCombine ?? 'union'
    ].join('|');
  }

  function loadKey(): string {
    if (isSwKind(instance.kind)) {
      // Chart mode plots the OI of the found wallets (Fixed: one set; Dynamic:
      // per-day rolling set), so its key extends the table key with the chart's
      // own OI-fetch params (token + interval). The differing tag makes a
      // Table⇄Chart toggle re-run the load effect into the other slot.
      if (instance.viewMode === 'chart') {
        // swShowClose is NOT in the key: close is ALWAYS fetched + merged into
        // the data (cheap OHLCV call), so toggling the overlay is a pure
        // client-side line show/hide — no refetch (important for the expensive
        // dynamic rolling fetch, and it keeps the refresh-only contract).
        return `${swTableKey()}|chart|${instance.token}|${instance.interval}`;
      }
      // Token List: per-token OI over the SAME selection. Selection-driven only
      // (unit is display-only → not in the key).
      if (instance.viewMode === 'token_list') {
        return `${swTableKey()}|token_list`;
      }
      return swTableKey();
    }
    if (instance.kind === 'token_leaderboard') {
      // Single global snapshot — the endpoint computes everything relative to
      // now() server-side, so there are no per-instance params. Constant key:
      // one chart's fetch serves every token_leaderboard on the page.
      return 'token_leaderboard';
    }
    if (instance.kind === 'spot_cvd_table') {
      // lookback varies the per-token aggregate → in the key. unit is
      // display-only (server returns both $ and token), so it's NOT here.
      return `spot_cvd_table|lb:${instance.cvdtLookback ?? 'all'}`;
    }
    if (instance.kind === 'sz') {
      const ex = instance.exchange ?? 'binance';
      return `${instance.kind}|${instance.token}|${ex}|${instance.interval}|${instance.under ?? 0}|${instance.over ?? 0}|${instance.szSide ?? 'all'}`;
    }
    if (instance.kind === 'realized_price') {
      // rpLookback changes the VWAP calculation → in the key; rpMode is
      // display-only (both fields ride each row), so it's NOT.
      return `${instance.kind}|${instance.token}|${instance.interval}|lb:${instance.rpLookback ?? 'all'}`;
    }
    if (instance.kind === 'spot_cvd') {
      // mode / unit / lookback all change the query → all in the key.
      return `${instance.kind}|${instance.token}|${instance.interval}|m:${instance.cvdMode ?? 'cumulative'}|u:${instance.cvdUnit ?? 'usd'}|lb:${instance.cvdLookback ?? 'all'}`;
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
    if (isHlKind(instance.kind) && instance.kind !== 'hl_smart_oi') {
      // HL: per-token + optional wallet OR wallet_category filter (mutually
      // exclusive). Empty wallet filter = aggregate across all traders.
      // hl_smart_oi is excluded here — it has its own filter-aware key below
      // (isHlKind matches it since it startsWith 'hl_', so without this guard
      // this branch would shadow the dedicated one and drop the filter hash).
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
    if (instance.kind === 'ohlcv' || instance.kind === 'fr' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'oi' || instance.kind === 'volume' || instance.kind === 'ls' || instance.kind === 'book_depth') {
      // Exchange selector busts the cache so flipping Binance ↔ HL re-fetches.
      // volumeUnit (usd/token) is display-only — both come in the /ohlcv
      // response — so it's intentionally NOT in the key.
      const ex = instance.exchange ?? 'binance';
      return `${instance.kind}|${instance.token}|${ex}|${instance.interval}`;
    }
    if (instance.kind === 'hl_smart_oi') {
      // One series per referenced filter — fold each filter's fully-expanded
      // wire hash into the key so editing a filter (or anything it
      // transitively references) busts the cache and re-fetches.
      const key = (instance.filterIds ?? [])
        .map((id) => {
          const w = filtersStore.getById(id) ? expandFilter(id, filtersStore.getById) : null;
          return w ? filterWireKey(w) : `x:${id}`;
        })
        .join(',');
      return `${instance.kind}|${instance.token}|${instance.interval}|${key}`;
    }
    return `${instance.kind}|${instance.token}|${instance.interval}`;
  }

  $effect(() => {
    // Drag-reorder guard: svelte-dnd-action briefly replaces the dragged
    // item with a "shadow" placeholder that lacks the chart's config
    // fields. Without this guard, loadKey() returns a different value
    // (e.g. instance.filterIds becomes undefined → defaults kick
    // in) and the effect re-fetches mid-drag, plus once more after the
    // drop when the real instance returns. The shadow object has the
    // SHADOW_ITEM_MARKER_PROPERTY_NAME flag; skip the effect while it's
    // present and the cached data + loadedKey survive the drag intact.
    if ((instance as unknown as Record<string, unknown>)[SHADOW_ITEM_MARKER_PROPERTY_NAME]) return;
    const key = loadKey();
    if (key === loadedKey) return;
    // Remount fast-path: if we previously loaded the exact same key for this
    // chart id (e.g. the user just drag-reordered and svelte-dnd-action
    // recreated the component), restore from cache and skip the fetch.
    const cached = loadCache.get(cacheId());
    if (cached && cached.key === key) {
      data = cached.data;
      overlayData = cached.overlayData ?? {};
      since = cached.since;
      until = cached.until;
      localView = cached.localView;
      loadedKey = key;
      // Cached data already matches the active view's shape.
      if (isDualViewKind(instance.kind)) dualDataView = instance.viewMode ?? 'table';
      // Recover the found count when restoring the table slot from cache.
      if (isSwKind(instance.kind) && instance.viewMode !== 'chart') {
        const t = (cached.data[0] as unknown as { total?: number })?.total;
        if (typeof t === 'number') { swFoundTotal = t; swFoundTotalKey = swTableKey(); }
      }
      // A cache hit means this selection has valid data — treat it as armed so
      // a subsequent view toggle (after a drag-reorder remount) can still load.
      if (isSwKind(instance.kind)) {
        swArmed = true; swArmedSelectionKey = swTableKey();
      }
      return;
    }
    // Refresh-only finder: skip auto-fetch unless an explicit refresh armed it
    // and the SELECTION key is unchanged (a view toggle keeps that key, so the
    // other view may still load; a token/lookback/filter change does not).
    if (isSwKind(instance.kind)) {
      if (!swArmed || swTableKey() !== swArmedSelectionKey) return;
      // Dynamic: NEVER auto-fetch on a view toggle or oi_token/interval change —
      // only an explicit refresh (which sets swForceFreshNext) loads. Cached
      // data for the new key is already restored above; otherwise the view
      // stays as-is until the user picks a token and clicks refresh. (Fixed
      // still auto-loads view toggles, which are cheap.)
      if (instance.kind === 'smart_wallets_dynamic' && !swForceFreshNext) return;
      const ff = swForceFreshNext;
      swForceFreshNext = false;
      void load(ff);
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
    // A fresh primary load (token / interval / filter change) invalidates any
    // in-flight smart-OI backfill — abort it and reset so it can't prepend
    // stale history onto the new window.
    if (currentChunkLoad) { currentChunkLoad.abort(); currentChunkLoad = null; }
    loadingMore = false;
    const controller = new AbortController();
    currentLoad = controller;
    const signal = controller.signal;
    error = null;
    loading = true;
    // Dual-view: the table and chart data shapes differ, so a view switch must
    // drop the old data (else the chart renders table rows as a black canvas
    // for the whole load). A same-view reload (e.g. token change) keeps its data
    // so the old line stays up while the new one loads.
    if (isDualViewKind(instance.kind) && dualDataView !== (instance.viewMode ?? 'table')) {
      data = [];
    }
    try {
      // Transfer + AAVE / HL / DEX / etc. event kinds use a fixed window
      // regardless of interval (they're sparse compared to OHLCV); other
      // kinds use the per-interval lookback window. The window length
      // tracks the ClickHouse table TTL — currently 180 days. (It used to
      // be 30 / 60; bumped here in lockstep with the schema TTL bump.)
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
        const ts = new Date(tu.getTime() - 180 * 24 * 60 * 60 * 1000);
        sinceIso = ts.toISOString();
        untilIso = tu.toISOString();
      } else {
        const w = lookbackWindow(instance.interval);
        sinceIso = w.since.toISOString();
        untilIso = w.until.toISOString();
      }
      // Dynamic kinds: keep the full window as the backfill floor, but only
      // fetch the most recent chunk up front; older history is backfilled on
      // demand (see loadOlderChunk / the pan-trigger effect). The chunk never
      // expands past the natural window (e.g. 1m intervals stay at 30d).
      if (isDynamicChunkKind()) {
        const untilU = unixSec(untilIso);
        const fullFloorU = unixSec(sinceIso);
        dynFloor = fullFloorU;
        let chunkU = Math.max(fullFloorU, untilU - DYN_CHUNK_DAYS * 86_400);
        // Dual-view chart: anchor the FIRST fetch to the lookback window's END
        // (the snapshot) → now, so the post-selection period shows immediately;
        // older history (incl. the in-sample lookback) backfills on pan/zoom
        // down to dynFloor. If the snapshot is ~now there's no forward period,
        // so fall back to showing the lookback window through now.
        if (instance.kind === 'smart_wallets_dynamic') {
          // Dynamic: tiny first window (DYN_SW_FIRST_DAYS) → cheap first paint;
          // the view fits exactly this range. Pan backfills older history.
          chunkU = Math.max(fullFloorU, untilU - DYN_SW_FIRST_DAYS * 86_400);
        } else if (isDualViewKind(instance.kind) && instance.viewMode === 'chart') {
          const snapU = Math.floor(Date.parse(swSnapshotIso() + 'T00:00:00Z') / 1000);
          const lookbackSec = (instance.swLookback ?? 7) * 86_400;
          const startU = snapU < untilU - 86_400 ? snapU : untilU - lookbackSec;
          chunkU = Math.max(fullFloorU, Math.min(startU, untilU - 3_600));
        }
        sinceIso = new Date(chunkU * 1000).toISOString();
      } else {
        dynFloor = null;
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
          return;
        }
      }
      // Smart Wallets finder: one JSON fetch returns the ranked wallet rows for
      // the selected metric / lookback / token / snapshot window. The endpoint
      // sorts + top-N caps server-side; the table re-sorts the returned set
      // client-side. Carried as a single AnyDatum payload (same shape trick as
      // the leaderboard kinds). since/until are ignored (the window is derived
      // from lookback + snapshot server-side).
      if (isSwKind(instance.kind)) {
        if (instance.viewMode === 'chart') {
          // Chart view: OI aggregated over EVERY found wallet (computed
          // server-side from the same filters — the address list never crosses
          // the wire, so it scales to thousands of wallets). Independent of the
          // table fetch; each view caches its own slot.
          data = instance.kind === 'smart_wallets_dynamic'
            ? await fetchSmartWalletOiRollingWindow(sinceIso, untilIso, signal)
            : await fetchSmartWalletOiWindow(sinceIso, untilIso, signal);
        } else if (instance.viewMode === 'token_list') {
          // Token List view: per-token long/short OI summed over the SAME found
          // wallet set at now / 24h-ago / 7d-ago. Server returns every held
          // token; the table sorts client-side.
          const res = await queuedFetch(
            `/api/hyperliquid/smart_wallet_token_list?${swSelectionParams()}`,
            { signal }
          );
          if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
          const body = await res.json();
          data = [{ tokens: body.tokens ?? [] } as unknown as AnyDatum];
        } else {
          // Table view: the ranked top-N rows + the FULL found count (`total`).
          const body = await fetchSmartWalletMetrics(signal);
          data = [{ wallets: body.wallets, total: body.total } as unknown as AnyDatum];
          // Remember the found count on the widget so the chart view can show it
          // too without re-running the (expensive) selection — the table always
          // loads first.
          swFoundTotal = body.total ?? 0;
          swFoundTotalKey = swTableKey();
        }
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        // Dynamic chart: pin the initial viewport to a SMALL recent window
        // (DYN_SW_VIEW_DAYS) WITHIN the loaded 3-day window, leaving loaded
        // headroom to the left so the auto-prefetch doesn't fire on first paint
        // (which made it take >1 fetch). Other sw cases keep defaultView.
        if (instance.kind === 'smart_wallets_dynamic' && instance.viewMode === 'chart') {
          const untilU = unixSec(untilIso);
          localView = [untilU - DYN_SW_VIEW_DAYS * 86_400, untilU];
        } else {
          localView = defaultView(sinceIso, untilIso);
        }
        dualDataView = instance.viewMode ?? 'table';
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
        return;
      }
      // Token Leaderboard: one JSON fetch returns a per-token snapshot row set
      // (price, 24h volume, avg 24h OI, 24h/7d change). The endpoint computes
      // everything relative to now() server-side, so since/until are ignored.
      // Carried as a single AnyDatum payload (same shape trick as the
      // leaderboard kinds); the table sorts client-side.
      if (instance.kind === 'token_leaderboard') {
        const res = await queuedFetch('/api/token_leaderboard', { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ tokens: body.tokens ?? [] } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
        return;
      }
      // Spot CVD tableview: per-token cumulative CVD over the lookback. Server
      // returns every token; the table sorts/limits client-side.
      if (instance.kind === 'spot_cvd_table') {
        // 'all' = multi-period comparison (1d/7d/14d pairs in one response);
        // a specific period = single-period columns for that lookback.
        const lb = instance.cvdtLookback ?? 'all';
        const qs = new URLSearchParams({ exchange: 'binance_spot' });
        if (lb === 'all') qs.set('multi', '1');
        else qs.set('lookback', lb);
        const res = await queuedFetch(`/api/spot_cvd_leaderboard?${qs}`, { signal });
        if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
        const body = await res.json();
        data = [{ tokens: body.tokens ?? [] } as unknown as AnyDatum];
        since = sinceIso; until = untilIso;
        loadedKey = loadKey();
        localView = defaultView(sinceIso, untilIso);
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
        return;
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
        return;
      }
      // Aerodrome (concentrated pools, BASE only).
      if (isAeroClKind(instance.kind)) {
        const pool = instance.aeroPool;
        if (!pool) {
          data = []; since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
        loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
        return;
      }
      switch (instance.kind) {
        case 'ohlcv':
        case 'volume': {
          // OHLCV + Volume charts both read the per-bucket candle series
          // (which carries volume + volume_usd) from whichever exchange the
          // instance is pinned to. Default 'binance' for back-compat; 'hl'
          // reads from tradernick.hl_ohlcv_1m server-side. The volume chart
          // just plots the volume / volume_usd field per the unit toggle.
          const ohlcvQs = new URLSearchParams(baseQS);
          ohlcvQs.set('exchange', instance.exchange ?? 'binance');
          url = `/api/ohlcv?${ohlcvQs}`;
          pickArr = (b) => (b.candles ?? []) as AnyDatum[];
          break;
        }
        case 'realized_price': {
          // Cumulative VWAP (realized/avg-entry price) of Binance spot from
          // inception + the bucket's current price. Spot-only.
          const rpQs = new URLSearchParams(baseQS);
          rpQs.set('exchange', 'binance_spot');
          rpQs.set('lookback', instance.rpLookback ?? 'all');
          url = `/api/realized_price?${rpQs}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        }
        case 'spot_cvd': {
          // Spot CVD — cumulative (line) or per-bucket (bar) taker buy−sell
          // volume delta of Binance spot, in USD or token units.
          const cvdQs = new URLSearchParams(baseQS);
          cvdQs.set('exchange', 'binance_spot');
          cvdQs.set('mode', instance.cvdMode ?? 'cumulative');
          cvdQs.set('unit', instance.cvdUnit ?? 'usd');
          cvdQs.set('lookback', instance.cvdLookback ?? 'all');
          url = `/api/spot_cvd?${cvdQs}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
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
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView, overlayData });
          return;
        }
        case 'ps': {
          // Perp vs Spot (Binance): fetch perp + spot OHLCV in parallel and
          // merge per bucket into { basis_pp, vol_ratio_pct }.
          //   basis_pp      = (perp_close − spot_close) / spot_close × 10000  (pp)
          //   vol_ratio_pct = spot_volume_usd / perp_volume_usd × 100        (%)
          // Both legs are required per bucket; spot books are sparser, so a
          // bucket without a spot candle is dropped.
          const perpQs = new URLSearchParams({ ...baseQS, exchange: 'binance' });
          const spotQs = new URLSearchParams({ ...baseQS, exchange: 'binance_spot' });
          if (forceFresh) { perpQs.set('fresh', '1'); spotQs.set('fresh', '1'); }
          const [perpRes, spotRes] = await Promise.all([
            queuedFetch(`/api/ohlcv?${perpQs}`, { signal }),
            queuedFetch(`/api/ohlcv?${spotQs}`, { signal })
          ]);
          if (!perpRes.ok) throw new Error(`ps perp ${perpRes.status}`);
          if (!spotRes.ok) throw new Error(`ps spot ${spotRes.status}`);
          const perpBody = await perpRes.json();
          const spotBody = await spotRes.json();
          const spotByTime = new Map<number, Candle>();
          for (const c of ((spotBody.candles ?? []) as Candle[])) spotByTime.set(c.time, c);
          const merged: AnyDatum[] = [];
          for (const p of ((perpBody.candles ?? []) as Candle[])) {
            const s = spotByTime.get(p.time);
            if (!s) continue;
            const basis_pp = s.close ? ((p.close - s.close) / s.close) * 10000 : NaN;
            const pv = p.volume_usd ?? 0;
            const vol_ratio_pct = pv ? ((s.volume_usd ?? 0) / pv) * 100 : NaN;
            merged.push({ time: p.time, basis_pp, vol_ratio_pct } as unknown as AnyDatum);
          }
          data = merged;
          since = sinceIso;
          until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
          return;
        }
        case 'oi':
          // HL OI rides on /hyperliquid/oi_split which carries long/short/
          // total in one payload; the long/short/total/all selector picks
          // which line(s) to render without re-fetching. It's the slow side,
          // so it loads in chunks (see fetchHlOiWindow). Binance OI keeps its
          // fast dedicated endpoint and the full-window path.
          if ((instance.exchange ?? 'binance') === 'hl') {
            data = await fetchHlOiWindow(sinceIso, untilIso, signal);
            since = sinceIso; until = untilIso;
            loadedKey = loadKey();
            localView = defaultView(sinceIso, untilIso);
            loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
            return;
          } else {
            url = `/api/open_interest?${new URLSearchParams(baseQS)}`;
            pickArr = (b) => (b.series ?? []) as AnyDatum[];
          }
          break;
        case 'hl_smart_oi': {
          // ONE request for ONE effective wallet set. Multiple selected filters
          // are AND-combined client-side into a single composite wire
          // (smartCombinedWire); the backend intersects them per day and
          // returns the same /oi_split-shaped payload as a single filter.
          // Only the most recent chunk is fetched here — see loadOlderSmartOi
          // for the pan-driven backfill of older history.
          const wire = smartCombinedWire;
          if (wire === null || wire === 'broken') {
            // Nothing selected, or a selected filter is missing/broken — render
            // the empty placeholder rather than a stale or partial series.
            data = [] as unknown as AnyDatum[];
            since = sinceIso; until = untilIso;
            loadedKey = loadKey();
            localView = defaultView(sinceIso, untilIso);
            loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
            return;
          }
          data = await fetchSmartOiWindow(wire, sinceIso, untilIso, signal);
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
          return;
        }
        case 'fr': {
          // Same Binance / HL exchange selector pattern as the ohlcv kind.
          const frQs = new URLSearchParams(baseQS);
          frQs.set('exchange', instance.exchange ?? 'binance');
          url = `/api/funding_rate?${frQs}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        }
        case 'book_depth': {
          // Binance-only — HL doesn't publish an equivalent depth feed.
          // The server returns 24 numeric columns per bucket (d_*/v_* per
          // percentage level); the chart pivots into one of four modes
          // (totals / per_level_imbalance / imbalance / stacked) client-side.
          url = `/api/book_depth?${new URLSearchParams(baseQS)}`;
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
            over: String(instance.over ?? 100000),
            side: instance.szSide ?? 'all'
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
          loadCache.set(cacheId(), {
            key: loadedKey,
            data,
            since,
            until,
            localView
          });
          return;
        }
        case 'exchange_flow': {
          // Two requests (one per direction) merged by time so the render-time
          // linesD can pick inflow / outflow / netflow / all without
          // re-fetching. Loads in chunks (see fetchExchangeFlowWindow).
          data = await fetchExchangeFlowWindow(sinceIso, untilIso, signal);
          since = sinceIso; until = untilIso;
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
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
      loadCache.set(cacheId(), {
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

  // ---- dynamic ("chunked") loading ----

  /** Fetch one smart-OI window for the given composite wire and map the rows
   *  into the chart's datum shape. Shared by the initial load and the backfill
   *  so both produce byte-identical rows. */
  async function fetchSmartOiWindow(
    wire: FilterWire,
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal
  ): Promise<AnyDatum[]> {
    const qs = new URLSearchParams({
      token: instance.token,
      interval: instance.interval,
      since: sinceIso,
      until: untilIso,
      limit: '200000'
    });
    qs.set('filter', JSON.stringify(wire));
    const res = await queuedFetch(`/api/hyperliquid/smart_oi?${qs}`, { signal });
    if (!res.ok) throw new Error(`hl_smart_oi ${res.status}`);
    const b = await res.json();
    const rows = (b.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({
      ...r,
      open_interest: r.total_oi ?? 0,
      open_interest_value: r.total_oi_value ?? 0
    })) as unknown as AnyDatum[];
  }

  /** smart_wallets_table: the selection filter params shared by the Table
   *  fetch (smart_wallet_metrics) and the Chart fetch (smart_wallet_oi). These
   *  define WHICH wallets qualify; `token` here is the table's scope (swToken),
   *  not the chart's OI token. */
  function swSelectionParams(): URLSearchParams {
    const qs = new URLSearchParams({
      lookback: String(instance.swLookback ?? 7),
      metric: instance.swMetric ?? 'sharpe',
      snapshot: swSnapshotIso(),
      // Guards come from the COMMITTED snapshot (applied on refresh), not the
      // live gear inputs — so the table doesn't reload while the user edits.
      min_days: String(Math.max(1, swF('swMinDays', 3))),
      min_volume: String(Math.max(0, swF('swMinVolume', 0))),
      min_realized: String(swF('swMinRealized', 0)),
      min_oi: String(Math.max(0, swF('swMinOi', 0))),
      min_avg_trade_size: String(Math.max(0, swF('swMinAvgTradeSize', 0))),
      min_taker_pct: String(Math.max(0, swF('swMinTakerPct', 0))),
      min_account_duration: String(Math.max(0, swF('swMinAccountDuration', 0))),
      min_tokens: String(Math.max(0, swF('swMinTokens', 0))),
      min_win_rate: String(Math.max(0, swF('swMinWinRate', 0))),
      min_trades_per_day: String(Math.max(0, swF('swMinTradesPerDay', 0))),
      min_avg_oi_share: String(Math.max(0, swF('swMinAvgOiShare', 0))),
      min_volume_share: String(Math.max(0, swF('swMinVolumeShare', 0)))
    });
    if (committedFilters.swMaxTradesPerDay != null) qs.set('max_trades_per_day', String(committedFilters.swMaxTradesPerDay));
    if (committedFilters.swMinAnnualizedSharpe != null) qs.set('min_annualized_sharpe', String(committedFilters.swMinAnnualizedSharpe));
    if (committedFilters.swMaxFeePct != null) qs.set('max_fee_pct', String(committedFilters.swMaxFeePct));
    if (committedFilters.swMaxFundingPct != null) qs.set('max_funding_pct', String(committedFilters.swMaxFundingPct));
    if (committedFilters.swMaxAvgOiShare != null) qs.set('max_avg_oi_share', String(committedFilters.swMaxAvgOiShare));
    if (committedFilters.swMaxVolumeShare != null) qs.set('max_volume_share', String(committedFilters.swMaxVolumeShare));
    if (instance.swToken && instance.swToken.length > 0) qs.set('token', instance.swToken);
    // Group: the wallet set IS a pinned group (no criteria) — backend resolves
    // membership from wallet_pins; lookback is the stats window (table only).
    if (instance.kind === 'smart_wallets_group') {
      qs.set('group', instance.swGroupId || 'default');
    }
    // Cutoff: union over multiple lookbacks at the cutoff (snapshot) → static set.
    if (instance.kind === 'smart_wallets_cutoff') {
      qs.set('cutoff', '1');
      const lbs = (instance.swCutoffLookbacks && instance.swCutoffLookbacks.length > 0)
        ? instance.swCutoffLookbacks
        : [...SMART_WALLET_CUTOFF_LOOKBACKS];
      qs.set('lookbacks', lbs.join(','));
      qs.set('combine', instance.swCutoffCombine ?? 'union');
    }
    return qs;
  }

  /** smart_wallets_table (Table view): fetch the ranked top-N rows + `total`
   *  (the FULL count of wallets passing the filters, which may far exceed the
   *  returned rows). */
  async function fetchSmartWalletMetrics(
    signal?: AbortSignal
  ): Promise<{ wallets: Array<{ wallet: string }>; total: number }> {
    const qs = swSelectionParams();
    qs.set('limit', String(instance.swRowLimit ?? 100));
    const res = await queuedFetch(`/api/hyperliquid/smart_wallet_metrics?${qs}`, { signal });
    if (!res.ok) throw new Error(`smart_wallets_table ${res.status}`);
    const body = await res.json();
    return { wallets: (body.wallets ?? []) as Array<{ wallet: string }>, total: body.total ?? 0 };
  }

  /** smart_wallets_table (Chart view): fetch the aggregate OI of ALL found
   *  wallets for the chart token. The wallet set is resolved server-side from
   *  the same selection filters, so an arbitrarily large set never crosses the
   *  wire. Mapped into the same datum shape as fetchSmartOiWindow. */
  async function fetchSmartWalletOiWindow(
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal
  ): Promise<AnyDatum[]> {
    // No token → nothing to plot (the endpoint requires oi_token). Return empty
    // so the chart shows the "Select a token" note instead of erroring/spinning.
    if (!instance.token) return [] as unknown as AnyDatum[];
    const qs = swSelectionParams();
    qs.set('oi_token', instance.token);
    qs.set('interval', instance.interval);
    qs.set('since', sinceIso);
    qs.set('until', untilIso);
    qs.set('limit', '200000');
    const res = await queuedFetch(`/api/hyperliquid/smart_wallet_oi?${qs}`, { signal });
    if (!res.ok) throw new Error(`smart_wallet_oi ${res.status}`);
    const b = await res.json();
    const rows = (b.series ?? []) as Array<Record<string, number>>;
    const mapped = rows.map((r) => ({
      ...r,
      open_interest: r.total_oi ?? 0,
      open_interest_value: r.total_oi_value ?? 0
    })) as Array<Record<string, number>>;
    // Optional HL close-price overlay: merge the token's close (same interval)
    // into each bucket by time, so oiLinesD can plot it as a secondary-axis
    // line. Fetched per-window so it backfills alongside the OI on pan.
    if (mapped.length) {
      const closeByTime = await fetchHlCloseMap(sinceIso, untilIso, signal);
      for (const d of mapped) {
        const c = closeByTime.get(d.time);
        if (c !== undefined) d.close = c;
      }
    }
    return mapped as unknown as AnyDatum[];
  }

  /** smart_wallets_dynamic (Chart view): aggregate OI of the PER-DAY ROLLING
   *  wallet set for the chart token, plus the per-day qualifying-wallet count
   *  (carried on each bucket as `wallet_count`, plotted by the smartShowWalletCount
   *  overlay). The set is recomputed server-side per day over the trailing
   *  lookback — no snapshot. Same datum shape as fetchSmartWalletOiWindow. */
  async function fetchSmartWalletOiRollingWindow(
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal
  ): Promise<AnyDatum[]> {
    if (!instance.token) return [] as unknown as AnyDatum[];
    const qs = swSelectionParams(); // snapshot is ignored by the rolling proxy
    qs.set('oi_token', instance.token);
    qs.set('interval', instance.interval);
    qs.set('since', sinceIso);
    qs.set('until', untilIso);
    qs.set('limit', '200000');
    const res = await queuedFetch(`/api/hyperliquid/smart_wallet_oi_rolling?${qs}`, { signal });
    if (!res.ok) throw new Error(`smart_wallet_oi_rolling ${res.status}`);
    const b = await res.json();
    const rows = (b.buckets ?? []) as Array<Record<string, number>>;
    const mapped = rows.map((r) => ({
      ...r,
      open_interest: r.total_oi ?? 0,
      open_interest_value: r.total_oi_value ?? 0
    })) as Array<Record<string, number>>;
    if (mapped.length) {
      const closeByTime = await fetchHlCloseMap(sinceIso, untilIso, signal);
      for (const d of mapped) {
        const c = closeByTime.get(d.time);
        if (c !== undefined) d.close = c;
      }
    }
    return mapped as unknown as AnyDatum[];
  }

  /** HL OHLCV close per bucket (time → close) for the chart token over a
   *  window, keyed by unix-second bucket time to merge into the OI series. */
  async function fetchHlCloseMap(
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal
  ): Promise<Map<number, number>> {
    const out = new Map<number, number>();
    try {
      const qs = new URLSearchParams({
        token: instance.token,
        interval: instance.interval,
        since: sinceIso,
        until: untilIso,
        limit: '200000',
        exchange: 'hl'
      });
      const res = await queuedFetch(`/api/ohlcv?${qs}`, { signal });
      if (!res.ok) return out;
      const b = await res.json();
      for (const c of (b.candles ?? []) as Array<Record<string, number>>) {
        if (typeof c.time === 'number' && typeof c.close === 'number') out.set(c.time, c.close);
      }
    } catch {
      /* close overlay is best-effort — OI still renders without it */
    }
    return out;
  }

  /** Fetch one HL open-interest window (oi_split) and map the rows. Keeps
   *  open_interest_value populated (= total) so the cumulative MA branch works
   *  without HL-specific branching. */
  async function fetchHlOiWindow(
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal
  ): Promise<AnyDatum[]> {
    const qs = new URLSearchParams({
      token: instance.token,
      interval: instance.interval,
      since: sinceIso,
      until: untilIso,
      limit: '200000'
    });
    const res = await queuedFetch(`/api/hyperliquid/oi_split?${qs}`, { signal });
    if (!res.ok) throw new Error(`oi ${res.status}`);
    const b = await res.json();
    const rows = (b.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({
      ...r,
      open_interest: r.total_oi ?? 0,
      open_interest_value: r.total_oi_value ?? 0
    })) as unknown as AnyDatum[];
  }

  /** Fetch one exchange-flow window: two requests (in / out) merged by time
   *  into the {sum_*_in, sum_*_out, net_*} shape the render-time linesD reads. */
  // Every concrete exchange the 'combined' mode fans out over.
  const EXCHANGE_FLOW_SOURCES = ['binance', 'coinbase', 'okx', 'bybit', 'hyperliquid'] as const;

  async function fetchExchangeFlowWindow(
    sinceIso: string,
    untilIso: string,
    signal?: AbortSignal
  ): Promise<AnyDatum[]> {
    const ex = instance.exchangeFlowExchange ?? 'binance';
    const combined = ex === 'combined';
    // Combined sums every exchange; a single selection fetches just that one.
    const sources: readonly string[] = combined ? EXCHANGE_FLOW_SOURCES : [ex];

    const buildQS = (exchange: string, direction: 'in' | 'out') => {
      const qs = new URLSearchParams({
        direction,
        exchange,
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

    // Fetch one exchange's in + out series. In combined mode a failed or
    // unsupported (e.g. BTC on Hyperliquid) request contributes nothing
    // rather than failing the whole chart; a single selection still throws.
    type Series = Array<Record<string, number>>;
    const fetchOne = async (exchange: string): Promise<{ inS: Series; outS: Series }> => {
      try {
        const [inRes, outRes] = await Promise.all([
          queuedFetch(`/api/exchange_flow/aggregate?${buildQS(exchange, 'in')}`, { signal }),
          queuedFetch(`/api/exchange_flow/aggregate?${buildQS(exchange, 'out')}`, { signal })
        ]);
        if (!inRes.ok || !outRes.ok) {
          if (combined) return { inS: [], outS: [] };
          throw new Error(
            `exchange_flow ${!inRes.ok ? `inflow ${inRes.status}` : `outflow ${outRes.status}`}`
          );
        }
        const inBody = await inRes.json();
        const outBody = await outRes.json();
        return { inS: inBody.series ?? [], outS: outBody.series ?? [] };
      } catch (e) {
        // Preserve abort semantics; otherwise swallow per-exchange errors in
        // combined mode (treat as 0) but surface them for a single selection.
        if (combined && !signal?.aborted) return { inS: [], outS: [] };
        throw e;
      }
    };

    const results = await Promise.all(sources.map(fetchOne));

    // Accumulate inflow / outflow per time bucket across all fetched exchanges.
    const inByTime = new Map<number, { amount: number; usd: number }>();
    const outByTime = new Map<number, { amount: number; usd: number }>();
    const add = (m: Map<number, { amount: number; usd: number }>, s: Series) => {
      for (const r of s) {
        const p = m.get(r.time) ?? { amount: 0, usd: 0 };
        p.amount += r.sum_amount ?? 0;
        p.usd += r.sum_value_usd ?? 0;
        m.set(r.time, p);
      }
    };
    for (const { inS, outS } of results) { add(inByTime, inS); add(outByTime, outS); }

    const times = new Set<number>([...inByTime.keys(), ...outByTime.keys()]);
    const out: Record<string, number>[] = [];
    for (const t of times) {
      const i = inByTime.get(t) ?? { amount: 0, usd: 0 };
      const o = outByTime.get(t) ?? { amount: 0, usd: 0 };
      out.push({
        time: t,
        sum_amount_in:     i.amount,
        sum_value_usd_in:  i.usd,
        sum_amount_out:    o.amount,
        sum_value_usd_out: o.usd,
        net_amount:        i.amount - o.amount,
        net_value_usd:     i.usd - o.usd
      });
    }
    out.sort((a, b) => a.time - b.time);
    return out as unknown as AnyDatum[];
  }

  /** Resolve the window-fetcher for the current dynamic kind, or null when the
   *  instance isn't a chunked kind / isn't ready (e.g. smart-OI with no valid
   *  filter). The returned fn fetches one [since, until) window of rows. */
  function dynamicFetcher():
    | ((sinceIso: string, untilIso: string, signal?: AbortSignal) => Promise<AnyDatum[]>)
    | null {
    if (instance.kind === 'hl_smart_oi') {
      const wire = smartCombinedWire;
      if (wire === null || wire === 'broken') return null;
      return (s, u, sig) => fetchSmartOiWindow(wire, s, u, sig);
    }
    if (isDualViewKind(instance.kind) && instance.viewMode === 'chart') {
      // OI of the found-wallet set, resolved server-side from the selection
      // filters each window — no client-side wallet list needed.
      return instance.kind === 'smart_wallets_dynamic'
        ? (s, u, sig) => fetchSmartWalletOiRollingWindow(s, u, sig)
        : (s, u, sig) => fetchSmartWalletOiWindow(s, u, sig);
    }
    if (instance.kind === 'oi' && (instance.exchange ?? 'binance') === 'hl') {
      return (s, u, sig) => fetchHlOiWindow(s, u, sig);
    }
    if (instance.kind === 'exchange_flow') {
      return (s, u, sig) => fetchExchangeFlowWindow(s, u, sig);
    }
    return null;
  }

  /** Decide whether the current view warrants backfilling older history, and
   *  if so kick off a single fetch sized to cover the view (so a big jump
   *  needs one request, not a chain of chunk hops). Reads state directly —
   *  call it from `untrack` so it doesn't register the heavy state as effect
   *  dependencies. */
  function maybeBackfillDynamic(viewLeftSec: number) {
    if (!isDynamicChunkKind()) return;
    if (loading || loadingMore) return;
    if (!data.length || !loadedKey || dynFloor === null) return;
    const fetchFn = dynamicFetcher();
    if (!fetchFn) return;

    const sinceU = unixSec(since);
    if (sinceU <= dynFloor) return;                   // already at full-window floor
    if (viewLeftSec >= sinceU + DYN_PREFETCH_DAYS * 86_400) return; // not near floor yet

    // Cover the view's left edge (plus a buffer), at least one chunk older,
    // capped at the full-window floor.
    const desiredFloor = Math.min(
      sinceU - DYN_CHUNK_DAYS * 86_400,
      viewLeftSec - DYN_PREFETCH_DAYS * 86_400
    );
    const newFloorU = Math.max(desiredFloor, dynFloor);
    if (newFloorU >= sinceU) return;                  // nothing older to fetch
    void loadOlderChunk(newFloorU, sinceU, fetchFn);
  }

  /** Fetch [newFloorU, oldFloorU) and prepend it to `data`, pinning the view.
   *  Failures are non-fatal — we keep whatever is already loaded. */
  async function loadOlderChunk(
    newFloorU: number,
    oldFloorU: number,
    fetchFn: (sinceIso: string, untilIso: string, signal?: AbortSignal) => Promise<AnyDatum[]>
  ) {
    if (currentChunkLoad) currentChunkLoad.abort();
    const controller = new AbortController();
    currentChunkLoad = controller;
    const signal = controller.signal;
    loadingMore = true;
    const keyAtStart = loadedKey;
    try {
      const newSinceIso = new Date(newFloorU * 1000).toISOString();
      const oldFloorIso = new Date(oldFloorU * 1000).toISOString();
      const older = await fetchFn(newSinceIso, oldFloorIso, signal);
      // Bail if the chart reloaded under us (token / interval / filter change).
      if (signal.aborted || loadedKey !== keyAtStart) return;
      // Prepend + dedup by time — the boundary bucket can appear in both
      // windows, and we never want to double-count a bucket.
      const seen = new Set(data.map((d) => (d as { time: number }).time));
      const merged = older.filter((d) => !seen.has((d as { time: number }).time)).concat(data);
      merged.sort((a, b) => (a as { time: number }).time - (b as { time: number }).time);
      data = merged as AnyDatum[];
      since = newSinceIso;
      loadCache.set(cacheId(), { key: loadedKey, data, since, until, localView });
    } catch (e) {
      if (signal.aborted) return;
      // Swallow — backfill is best-effort; the chart keeps its current data.
    } finally {
      if (currentChunkLoad === controller) {
        currentChunkLoad = null;
        loadingMore = false;
      }
    }
    // The user may have panned further while we were fetching; re-check against
    // the live view. Guards in maybeBackfillDynamic stop this at the floor.
    if (currentChunkLoad === null) {
      const v = effectiveView;
      if (v) maybeBackfillDynamic(v[0]);
    }
  }

  // Pan / zoom trigger: whenever the effective view's left edge moves, see if
  // we need to backfill older history. effectiveView is the only tracked dep so
  // a backfill's own state writes can't re-enter this effect.
  $effect(() => {
    const v = effectiveView;
    if (!v) return;
    const left = v[0];
    untrack(() => maybeBackfillDynamic(left));
  });

  /** Force a fresh fetch — bypasses the `loadedKey === key` short-circuit,
   *  evicts this chart's entry from the remount cache, and sends `?fresh=1`
   *  so the data_server's response cache also recomputes. Wired to the
   *  header refresh button. Allowed mid-load: load() will abort the prior
   *  in-flight fetch via currentLoad so a stuck request can be replaced. */
  async function reload() {
    // smart_wallets_table is refresh-only: commit pending gear-filter edits,
    // ARM a single fetch, then let the $effect run it (routing through the
    // effect keeps it the sole loader, so there's no double-load race). Capture
    // the armed selection key AFTER committing so it matches what the effect
    // compares against. swForceFreshNext carries the cache-bust into that load.
    if (isSwKind(instance.kind)) {
      // Group widget: refresh also reloads the groups + memberships from CH so
      // the dropdown and the resolved set reflect the latest pins.
      if (isGroup) walletPinsStore.reload();
      commitSwFilters();
      swArmed = true;
      swArmedSelectionKey = swTableKey();
      swForceFreshNext = true;
      loadCache.delete(cacheId());
      loadedKey = '';
      return;
    }
    loadedKey = '';
    loadCache.delete(cacheId());
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
    // Default to USD notional; token (raw coin volume) is the opt-in.
    if ((instance.volumeUnit ?? 'usd') !== 'usd') return src;
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
      // CeX Exchange Flow: running sum of the selected direction (net by
      // default) — cumulative net deposits/withdrawals over the window.
      || instance.kind === 'exchange_flow'
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
      switch (effectiveKind) {
        case 'book_depth':
          // book_depth MAs (share modes only) are handled by the dedicated
          // per-band stacked-MA path (bdShareMaData) — each band's share is
          // smoothed then re-stacked. Never emit a generic overlay-MA line
          // here (book_depth rows have no scalar value field, so the default
          // branch would otherwise push a flat-zero line).
          break;
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
          const hlMode = (effectiveKind === 'hl_smart_oi'
                          || (instance.exchange ?? 'binance') === 'hl')
            ? (instance.oiHlDisplay ?? 'total') : null;
          const useTok = (instance.oiUnit ?? 'usd') === 'token';
          const rows = data as Array<Record<string, number>>;
          if (hlMode === 'count') {
            const longArr = maArray(rows.map((d) => d.long_count ?? 0), ma.length, ma.type);
            const shortArr = maArray(rows.map((d) => d.short_count ?? 0), ma.length, ma.type);
            out.push({ key: `cum_oi_long_${idx}`, label: `# Long ${tag}`, color: '#22c55e',
              dash: SUB_DASH[0], compute: (_d: OpenInterestRow, i: number) => longArr[i] });
            out.push({ key: `cum_oi_short_${idx}`, label: `# Short ${tag}`, color: '#ef4444',
              dash: SUB_DASH[0], compute: (_d: OpenInterestRow, i: number) => shortArr[i] });
            break;
          }
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
          // MA tracks whatever the current display mode plots, so it lands on
          // the right axis: absolute buyer/seller $ in stacked/both, the two
          // %-shares in pct mode, the buyer/seller ratio in ratio mode. (The
          // old version always emitted a share-% MA, which was invisible on the
          // USD axis of the stacked chart.) `_d` is unused — values come from
          // the precomputed arrays — so the param is typed Datum-loose to suit
          // both the LineChart and StackedBarChart line slots.
          const arr = data as VolumeBucket[];
          const mode = instance.bsDisplay ?? 'stacked';
          const tot = arr.map((b) => b.buyer_taker_usd + b.seller_taker_usd);
          if (mode === 'ratio') {
            const ratioMA = maArray(
              arr.map((b) => (b.seller_taker_usd > 0 ? b.buyer_taker_usd / b.seller_taker_usd : 0)),
              ma.length, ma.type);
            out.push({
              key: `cum_bs_ratio_${idx}`, label: `Buyer/Seller ${tag}`, color,
              dash: SUB_DASH[0], compute: (_d: { time: number }, i: number) => ratioMA[i]
            });
          } else if (mode === 'imbalance') {
            const imbMA = maArray(
              arr.map((b, i) => (tot[i] > 0 ? ((b.buyer_taker_usd - b.seller_taker_usd) / tot[i]) * 100 : 0)),
              ma.length, ma.type);
            out.push({
              key: `cum_bs_imbalance_${idx}`, label: `Imbalance ${tag}`, color,
              dash: SUB_DASH[0], compute: (_d: { time: number }, i: number) => imbMA[i]
            });
          } else if (mode === 'pct') {
            const buyerPctMA = maArray(
              arr.map((b, i) => (tot[i] > 0 ? (b.buyer_taker_usd / tot[i]) * 100 : 0)), ma.length, ma.type);
            const sellerPctMA = maArray(
              arr.map((b, i) => (tot[i] > 0 ? (b.seller_taker_usd / tot[i]) * 100 : 0)), ma.length, ma.type);
            out.push({
              key: `cum_bs_buyer_pct_${idx}`, label: `% Buyer ${tag}`, color: '#22c55e',
              dash: SUB_DASH[0], compute: (_d: { time: number }, i: number) => buyerPctMA[i]
            });
            out.push({
              key: `cum_bs_seller_pct_${idx}`, label: `% Seller ${tag}`, color: '#ef4444',
              dash: SUB_DASH[1], compute: (_d: { time: number }, i: number) => sellerPctMA[i]
            });
          } else {
            const buyerMA = maArray(arr.map((b) => b.buyer_taker_usd), ma.length, ma.type);
            const sellerMA = maArray(arr.map((b) => b.seller_taker_usd), ma.length, ma.type);
            out.push({
              key: `cum_bs_buyer_${idx}`, label: `Buyer ${tag}`, color: '#22c55e',
              dash: SUB_DASH[0], compute: (_d: { time: number }, i: number) => buyerMA[i]
            });
            out.push({
              key: `cum_bs_seller_${idx}`, label: `Seller ${tag}`, color: '#ef4444',
              dash: SUB_DASH[1], compute: (_d: { time: number }, i: number) => sellerMA[i]
            });
          }
          break;
        }
        case 'sz': {
          // sz now renders absolute-USD bucket LINES, so its MAs are the
          // absolute small/mid/large $ MAs (not share-%), keyed cum_<bucket>_<idx>
          // and colour-matched to their bucket so the series selector's MA filter
          // (pickMA `^cum_<key>_\d+$`) lines up.
          const arr = data as VolumeBucket[];
          const u = instance.under ?? 10000;
          const o = instance.over ?? 100000;
          const smallMA = maArray(arr.map((b) => b.small_usd), ma.length, ma.type);
          const midMA   = maArray(arr.map((b) => b.mid_usd),   ma.length, ma.type);
          const largeMA = maArray(arr.map((b) => b.large_usd), ma.length, ma.type);
          out.push({
            key: `cum_small_usd_${idx}`,
            label: `< $${u} ${tag}`,
            color: '#3f3f46',
            dash: SUB_DASH[0],
            compute: (_d: VolumeBucket, i: number) => smallMA[i]
          });
          out.push({
            key: `cum_mid_usd_${idx}`,
            label: `$${u}–$${o} ${tag}`,
            color: '#3b82f6',
            dash: SUB_DASH[1],
            compute: (_d: VolumeBucket, i: number) => midMA[i]
          });
          out.push({
            key: `cum_large_usd_${idx}`,
            label: `> $${o} ${tag}`,
            color: '#a855f7',
            dash: SUB_DASH[2] ?? SUB_DASH[0],
            compute: (_d: VolumeBucket, i: number) => largeMA[i]
          });
          break;
        }
        case 'tt': {
          const arr = data as LongShortRow[];
          const countMA = maArray(arr.map((d) => d.top_trader_count_ratio), ma.length, ma.type);
          const volMA = maArray(arr.map((d) => d.top_trader_vol_ratio), ma.length, ma.type);
          const avgVolMA = maArray(
            arr.map((d) => (d.top_trader_count_ratio ? d.top_trader_vol_ratio / d.top_trader_count_ratio : 0)),
            ma.length, ma.type
          );
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
          out.push({
            key: `cum_top_avg_vol_${idx}`,
            label: `Top avg vol ${tag}`,
            color,
            dash: SUB_DASH[2] ?? SUB_DASH[1],
            compute: (_d: LongShortRow, i: number) => avgVolMA[i]
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
            else if (t === 'in_out') {
              pushFlow('in',  'Inflow',  fIn,  SUB_DASH[0]);
              pushFlow('out', 'Outflow', fOut, SUB_DASH[1]);
            } else {
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
            else if (t === 'in_out') {
              pushFlow('in',  'Inflow',  'deposit',    SUB_DASH[0]);
              pushFlow('out', 'Outflow', 'withdrawal', SUB_DASH[1]);
            } else {
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
      if (instance.kind === 'exchange_flow') {
        // CeX flow rows expose {sum_value_usd_in/out, sum_amount_in/out,
        // net_value_usd, net_amount}. The running sum tracks whichever
        // direction the flow-type toggle selects; multi-line modes
        // (netflow / in_out / all) anchor on the signed net so the Σ line
        // reads as cumulative net flow. Honours the USD/amount toggle and
        // the optional sliding window (sumWindow).
        const useUsd = (instance.valueMode ?? 'usd') === 'usd';
        const t = instance.exchangeFlowType ?? 'netflow';
        const field = t === 'inflow' ? (useUsd ? 'sum_value_usd_in' : 'sum_amount_in')
                    : t === 'outflow' ? (useUsd ? 'sum_value_usd_out' : 'sum_amount_out')
                    : (useUsd ? 'net_value_usd' : 'net_amount');
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
      } else if (instance.kind === 'hl_transfers') {
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
    hyperliquid: 'Hyperliquid',
    combined: 'Combined'
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
    if (t === 'in_out')  return [inLine, outLine, ...cumulativeLines];
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
    if (t === 'in_out')  return [inLine, outLine, ...cumulativeLines];
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

  // bs: stacked bar series (Point toggle controls visibility).
  // bs display mode: 'stacked' (bars), 'ratio' (Buyer/Seller line), 'both'
  // (bars + ratio line on a secondary axis), or 'pct' (two %-share lines).
  let bsMode = $derived(instance.bsDisplay ?? 'stacked');
  // bs taker-volume denomination — USD (default) or token amount. Reuses the
  // shared volumeUnit field; display-only (both values ride each bucket).
  let bsUnit = $derived<'usd' | 'token'>((instance.volumeUnit ?? 'usd') === 'token' ? 'token' : 'usd');
  // Bars only in stacked/both modes (ratio + pct are pure line charts).
  let bsBars = $derived(
    ((bsMode === 'stacked' || bsMode === 'both') && instance.showPoint) ? buyerSellerSeries(bsUnit) : []
  );
  // StackedBarChart overlay lines: the ratio line (secondary axis) in 'both'
  // mode, plus the MA / %-buyer overlays when an MA is enabled.
  let bsLines = $derived([
    ...(bsMode === 'both' ? BUYER_SELLER_RATIO_LINES : []),
    ...(anyMaEnabled ? [...BUYER_SELLER_LINES, ...cumulativeLines] : [])
  ]);
  // Ratio-mode line set (LineChart): the Buyer/Seller line + its MA. The
  // primary line is gated by the Point toggle (like the sz buckets / bs bars),
  // so deselecting Point hides it; the MA stays (Point and MA are independent).
  // cumulativeLines is the shared unknown[] MA bag — cast to the line shape so
  // the merge stays typed.
  let bsRatioLinesD = $derived([
    ...(instance.showPoint ? BUYER_SELLER_RATIO_LINES : []),
    ...(cumulativeLines as typeof BUYER_SELLER_RATIO_LINES)
  ]);
  // Pct-mode line set (LineChart): % Buyer + % Seller (Point-gated) + their MAs.
  let bsPctLinesD = $derived([
    ...(instance.showPoint ? BUYER_SELLER_PCT_LINES : []),
    ...(cumulativeLines as typeof BUYER_SELLER_PCT_LINES)
  ]);
  // Imbalance-mode line set (LineChart): the single Buyer−Seller % line
  // (Point-gated) + its MA.
  let bsImbalanceLinesD = $derived([
    ...(instance.showPoint ? BUYER_SELLER_IMBALANCE_LINES : []),
    ...(cumulativeLines as typeof BUYER_SELLER_IMBALANCE_LINES)
  ]);

  // sz (Volume by Size) renders as LINES — three absolute-USD bucket lines
  // (small / mid / large), not a stack. showPoint toggles the bucket lines;
  // MA overlays (absolute small/large/total from cumulativeLines) ride along.
  let szLinesD = $derived([
    ...(instance.showPoint
      ? pickSeries(sizeLineSeries(instance.under ?? 10000, instance.over ?? 100000), instance.seriesFilter)
      : []),
    ...pickMA(cumulativeLines, instance.seriesFilter)
  ]);
  // sz "Buyer vs Seller (taker)" mode: two lines (buyer-taker, seller-taker) for
  // the chosen bracket. Display-only (the split fields are always in the
  // response). Point-gated like the bucket lines.
  let szTakerSplitLinesD = $derived(
    instance.showPoint
      ? takerSplitLines(instance.seriesFilter, instance.under ?? 10000, instance.over ?? 100000)
      : []
  );
  // OI lines: Binance is always the single total line. HL switches by
  // the oiHlDisplay selector — 'total' matches Binance shape exactly,
  // 'long'/'short' shows just that side, 'long_short' shows two, and
  // 'long_to_short' shows a unitless ratio. The oiUnit selector picks
  // dollar notional (`*_oi_value` on HL, `open_interest_value` on Binance)
  // vs token amount (`*_oi`, `open_interest`).
  let oiIsToken = $derived((instance.oiUnit ?? 'usd') === 'token');

  // hl_smart_oi: the chart shows ONE OI series for ONE effective wallet set.
  // When several saved filters are selected, they're AND-combined on the fly
  // into a single composite (empty-criteria node intersecting each filter's
  // wire) so the user can test combinations without first saving a combined
  // filter. Returns null if nothing is selected, or 'broken' if any selected
  // filter is missing / unresolvable (we never silently drop a constraint).
  let smartCombinedWire = $derived.by((): FilterWire | null | 'broken' => {
    if (instance.kind !== 'hl_smart_oi') return null;
    const ids = instance.filterIds ?? [];
    if (ids.length === 0) return null;
    const wires = ids.map((id) =>
      filtersStore.getById(id) ? expandFilter(id, filtersStore.getById) : null,
    );
    if (wires.some((w) => w === null)) return 'broken';
    const valid = wires as FilterWire[];
    if (valid.length === 1) return valid[0];
    // Combine: an empty-criteria composite whose refs are the selected
    // filters — the backend intersects them per day (mirrors a hand-built
    // AND filter). lookback/top_n are unused for an empty-criteria node.
    return { lookback: 1, top_n: 1, scope: 'token', sort_by: 'realized_pnl', criteria: [], refs: valid };
  });
  let smartHasValidFilter = $derived(
    smartCombinedWire !== null && smartCombinedWire !== 'broken',
  );

  let oiHlPrimary = $derived.by(() => {
    // hl_smart_oi (incl. dual-view chart mode) is HL-only with no exchange
    // field — treat it as HL. effectiveKind so the dual chart resolves too.
    if (effectiveKind !== 'hl_smart_oi'
        && (instance.exchange ?? 'binance') !== 'hl') return null;
    const mode = instance.oiHlDisplay ?? 'total';
    const unitLabel = oiIsToken ? ` (${instance.token ?? ''})` : ' (USD)';
    if (mode === 'long')  return { color: '#22c55e', field: oiIsToken ? 'long_oi'  : 'long_oi_value',  label: 'Long OI'  + unitLabel };
    if (mode === 'short') return { color: '#ef4444', field: oiIsToken ? 'short_oi' : 'short_oi_value', label: 'Short OI' + unitLabel };
    if (mode === 'long_short' || mode === 'long_to_short' || mode === 'net_pct' || mode === 'net' || mode === 'count') return null;
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
    // — it renders one OI series for the (possibly combined) wallet set.
    const ex = effectiveKind === 'hl_smart_oi'
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
    } else if (effectiveKind === 'hl_smart_oi' && mode === 'count') {
      // Wallet-count mode: # wallets long vs # short (equal-weighting small
      // wallets, vs the OI-weighted sums). Plain counts, primary axis.
      base = [
        { key: 'oi_long_n',  label: '# Long wallets',  color: '#22c55e',
          compute: (d: Record<string, number>) => (d.long_count ?? 0) },
        { key: 'oi_short_n', label: '# Short wallets', color: '#ef4444',
          compute: (d: Record<string, number>) => (d.short_count ?? 0) },
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
    // secondary (right-side) axis — the count of wallets in the (combined)
    // set each day. Short-dashed blue so it reads as supplementary context.
    if (effectiveKind === 'hl_smart_oi' && (instance.smartShowWalletCount ?? false)) {
      base.push({
        key: 'wallet_count', label: 'Wallets', color: '#3b82f6',
        axis: 'secondary',
        dash: '3,3',
        compute: (d: Record<string, number>) => d.wallet_count ?? 0,
        // Wallets is a plain count — force its own legend formatter so it never
        // inherits the secondary axis's USD/price formatter (e.g. when the
        // close-price overlay is also on). rawValue == compute (legend-only).
        rawValue: (d: Record<string, number>) => d.wallet_count ?? 0,
        rawFormat: (v: number) => Math.round(v).toLocaleString(),
      });
    }
    // Optional HL close-price overlay (smart-wallets chart mode), on the
    // secondary axis since price and OI are different magnitudes. `close` is
    // merged into the data by fetchSmartWalletOiWindow.
    if (effectiveKind === 'hl_smart_oi' && (instance.swShowClose ?? false)) {
      base.push({
        key: 'close', label: `${instance.token ?? ''} close`, color: '#e5e7eb',
        axis: 'secondary',
        compute: (d: Record<string, number>) => (d.close ?? NaN),
      });
    }
    return base;
  });
  // ls / tt series selector: 'all' (or unset) shows every series; a specific
  // series key narrows to that one line. The MA overlays (keyed
  // `cum_<seriesKey>_<idx>`) are filtered to match so picking one series hides
  // the others' moving averages too.
  function pickSeries<T extends { key: string }>(lines: T[], sel: string | undefined): T[] {
    return !sel || sel === 'all' ? lines : lines.filter((l) => l.key === sel);
  }
  function pickMA(maLines: unknown[], sel: string | undefined): unknown[] {
    if (!sel || sel === 'all') return maLines;
    const re = new RegExp(`^cum_${sel}_\\d+$`);
    return maLines.filter((l) => re.test((l as { key: string }).key));
  }
  let ttLinesD = $derived([
    ...(instance.showPoint ? pickSeries(TOP_TRADERS_LINES, instance.seriesFilter) : []),
    ...pickMA(cumulativeLines, instance.seriesFilter)
  ]);
  let lsLinesD = $derived([
    ...(instance.showPoint ? pickSeries(LS_LINES, instance.seriesFilter) : []),
    ...pickMA(cumulativeLines, instance.seriesFilter)
  ]);
  // Rebase an array of {close} (Candle-shaped) rows so the first non-null
  // close is 0%, every subsequent value is `(close - base) / base * 100`.
  // ── Relative Price (pc) ──────────────────────────────────────────────
  // The chart token's price expressed RELATIVE to each base token: one line
  // per base, value = close(chart token) / close(base token) per bucket. Base
  // tokens live in instance.overlayTokens (default ['BTC']). Candle grids are
  // assumed index-aligned across tokens (same interval/window), matching the
  // multi-token fetch above.
  let pcMainCloses = $derived(
    instance.kind === 'pc'
      ? (data as unknown as { close?: number }[]).map((r) =>
          r && typeof r.close === 'number' ? r.close : NaN)
      : []
  );
  let pcRatioByToken = $derived.by<Record<string, number[]>>(() => {
    const out: Record<string, number[]> = {};
    if (instance.kind !== 'pc') return out;
    for (const tok of instance.overlayTokens ?? []) {
      const rows = overlayData[tok];
      out[tok] = pcMainCloses.map((mc, i) => {
        const b = rows?.[i]?.close;
        return typeof b === 'number' && b !== 0 && Number.isFinite(mc) ? mc / b : NaN;
      });
    }
    return out;
  });
  // Distinct palette for the pc chart's ratio lines.
  const OVERLAY_COLORS = ['#06b6d4', '#fbbf24', '#a855f7', '#22c55e', '#ef4444', '#ec4899'] as const;
  // OHLCV chart is back to its original behaviour: candles + MAs only.
  let ohlcvLinesD = $derived(cumulativeLines);
  // One price-ratio line per base token: chart token / base token.
  let pcLinesD = $derived(
    (instance.overlayTokens ?? []).map((tok, idx) => ({
      key: `pc_ratio_${tok}`,
      label: `${instance.token} / ${tok}`,
      color: OVERLAY_COLORS[idx % OVERLAY_COLORS.length],
      compute: (_d: Candle, i: number) => (pcRatioByToken[tok] ?? [])[i] ?? NaN
    }))
  );
  let frLinesD = $derived(cumulativeLines);

  // ---- book_depth ----------------------------------------------------------
  // Bid side = negative percentage levels (m500 .. m020 bps off mid).
  // Ask side = positive percentage levels (p020 .. p500 bps off mid).
  // Order each list from deepest -> tightest; the stacked mode reads from
  // the bottom up, so bids deepest-first puts the tightest bid against the
  // ask wall in the middle and the deepest bid at the band's bottom.
  const BID_LEVELS = ['m500','m400','m300','m200','m100','m020'] as const;
  const ASK_LEVELS = ['p020','p100','p200','p300','p400','p500'] as const;
  // Diverging palette: deep red for far asks → orange near asks → pale near bids
  // → deep green for far bids. Read off Tailwind 500/600/700 emerald + red ramps.
  const BID_COLORS = ['#14532d', '#166534', '#15803d', '#16a34a', '#22c55e', '#4ade80'] as const;
  const ASK_COLORS = ['#fca5a5', '#f87171', '#ef4444', '#dc2626', '#b91c1c', '#7f1d1d'] as const;
  // Pretty label for a level suffix — used in tooltips/legends.
  function bdLevelLabel(sfx: string): string {
    const pct = (sfx.startsWith('m') ? -1 : 1) * Number(sfx.slice(1));
    const sign = pct > 0 ? '+' : '';
    return `${sign}${pct}bps`;
  }
  // Sum of bid (or ask) `value` columns at a single bucket.
  function bdSumSide(d: Record<string, number>, levels: readonly string[]): number {
    let s = 0;
    for (const l of levels) s += d['v_' + l] ?? 0;
    return s;
  }
  // Imbalance series — recomputed only for that mode.
  let bdImbalanceData = $derived(
    instance.kind === 'book_depth' && (instance.bookDepthMode ?? 'totals') === 'imbalance'
      ? (data as unknown as Record<string, number>[]).map((d) => {
          const bid = bdSumSide(d, BID_LEVELS);
          const ask = bdSumSide(d, ASK_LEVELS);
          const total = bid + ask;
          return { time: d.time, imb: total > 0 ? ((bid - ask) / total) * 100 : 0 };
        })
      : []
  );
  // Totals mode — two lines summing each side's notional. Both on the primary axis.
  let bdTotalsLines = $derived([
    {
      key: 'bd_bid', label: 'Bid', color: '#22c55e',
      compute: (d: Record<string, number>) => bdSumSide(d, BID_LEVELS)
    },
    {
      key: 'bd_ask', label: 'Ask', color: '#ef4444',
      compute: (d: Record<string, number>) => bdSumSide(d, ASK_LEVELS)
    },
    ...cumulativeLines
  ]);
  // Per-level imbalance mode — one (bid - ask) / (bid + ask) ratio line per
  // matching percentage band (6 bands: ±20bps, ±1%, ±2%, ±3%, ±4%, ±5%),
  // expressed as a signed percentage (×100, range [-100%, +100%]). bid = the
  // negative band's notional (v_m*), ask = the positive band's (v_p*);
  // positive ⇒ bid-heavy at that band. Bounded, so it rides its own axis —
  // no USD overlays merged in. Colours are the six evenly-spaced hue-wheel
  // primaries (red/yellow/green/cyan/blue/magenta) so all six series stay
  // maximally distinguishable against each other and the dark canvas.
  const BD_BANDS = [
    { bid: 'm020', ask: 'p020', label: '±20bps' },
    { bid: 'm100', ask: 'p100', label: '±1%' },
    { bid: 'm200', ask: 'p200', label: '±2%' },
    { bid: 'm300', ask: 'p300', label: '±3%' },
    { bid: 'm400', ask: 'p400', label: '±4%' },
    { bid: 'm500', ask: 'p500', label: '±5%' }
  ] as const;
  const BD_BAND_COLORS = ['#ef4444', '#eab308', '#22c55e', '#06b6d4', '#3b82f6', '#d946ef'] as const;
  // Per-band visibility. undefined ⇒ all six on (default); the settings panel
  // edits instance.bookDepthBands to select / deselect individual series.
  function bdBandOn(bid: string): boolean {
    return !instance.bookDepthBands || instance.bookDepthBands.includes(bid);
  }
  function bdToggleBand(bid: string): void {
    const cur = instance.bookDepthBands ?? BD_BANDS.map((b) => b.bid);
    const next = cur.includes(bid) ? cur.filter((x) => x !== bid) : [...cur, bid];
    // Never let the user blank the chart — re-show all if they clear the last.
    instance.bookDepthBands = next.length ? next : BD_BANDS.map((b) => b.bid);
  }
  // flatMap keeps each band's colour index stable as others are toggled off.
  let bdPerLevelImbalanceLines = $derived(
    BD_BANDS.flatMap((b, i) =>
      bdBandOn(b.bid)
        ? [{
            key: 'bd_imb_' + b.bid,
            label: b.label,
            color: BD_BAND_COLORS[i],
            compute: (d: Record<string, number>) => {
              const bid = d['v_' + b.bid] ?? 0;
              const ask = d['v_' + b.ask] ?? 0;
              const t = bid + ask;
              return t > 0 ? ((bid - ask) / t) * 100 : 0;
            }
          }]
        : []
    )
  );
  // Stacked mode — same 12 fields but rendered by StackedBarChart, which takes
  // a `series` list (keyed against the data record) rather than `lines`.
  let bdStackedSeries = $derived([
    ...BID_LEVELS.map((sfx, i) => ({
      key: 'v_' + sfx,
      label: 'Bid ' + bdLevelLabel(sfx),
      color: BID_COLORS[i]
    })),
    ...ASK_LEVELS.map((sfx, i) => ({
      key: 'v_' + sfx,
      label: 'Ask ' + bdLevelLabel(sfx),
      color: ASK_COLORS[i]
    }))
  ]);
  // 100%-stacked "share" modes — rendered by StackedBarChart on pre-normalized
  // data so the visible series sum to 100 at every bucket. 'asks_share' = each
  // ask band's notional as a % of all asks; 'bids_share' = each bid band as a %
  // of all bids; 'total_share' = each band's (bid + ask) notional as a % of the
  // whole book (both sides). Series keyed 's_<bid-suffix>' against the
  // normalized records below.
  const BD_SHARE_MODES = ['asks_share', 'bids_share', 'total_share', 'asks_bids_share'];
  let bdAsksShareSeries = $derived(
    ASK_LEVELS.map((sfx, i) => ({ key: 's_' + sfx, label: 'Ask ' + bdLevelLabel(sfx), color: ASK_COLORS[i] }))
  );
  let bdBidsShareSeries = $derived(
    BID_LEVELS.map((sfx, i) => ({ key: 's_' + sfx, label: 'Bid ' + bdLevelLabel(sfx), color: BID_COLORS[i] }))
  );
  let bdTotalShareSeries = $derived(
    BD_BANDS.map((b, i) => ({ key: 's_' + b.bid, label: b.label, color: BD_BAND_COLORS[i] }))
  );
  let bdShareSeries = $derived.by(() => {
    const mode = instance.bookDepthMode ?? 'totals';
    if (mode === 'asks_share') return bdAsksShareSeries;
    if (mode === 'bids_share') return bdBidsShareSeries;
    if (mode === 'total_share') return bdTotalShareSeries;
    // asks_bids_share — bids form the bottom 0–100% stack, asks the top
    // 100–200% stack. Bids deepest→tightest (m500…m020) then asks
    // tightest→deepest (p020…p500) puts the two tightest bands either side of
    // the 100% mid line.
    if (mode === 'asks_bids_share') return [...bdBidsShareSeries, ...bdAsksShareSeries];
    return [];
  });
  // Dashed divider at the 100% mid line for the combined asks+bids view.
  const bdMidLine = [
    { key: 'bd_mid', label: 'Bids ╱ Asks', color: '#71717a', dash: '4,3', compute: () => 100 }
  ];
  // The /book_depth `value` columns are CUMULATIVE depth within ±X% of mid —
  // each deeper band already contains every tighter one (e.g. v_p500 = total
  // notional out to +5%, and includes v_p020). Summing them would double-count
  // wildly, so to build an honest 100%-stack we un-cumulate into non-overlapping
  // rings: ring at distance i = cumulative(i) − cumulative(i−1). The rings
  // partition the ±5% book exactly, so their shares sum to 100. `levels` must
  // run tight → deep; Math.max guards against tiny non-monotonic avg noise.
  function bdRings(d: Record<string, number>, levels: readonly string[]): number[] {
    const rings: number[] = [];
    let prev = 0;
    for (const sfx of levels) {
      const cum = d['v_' + sfx] ?? 0;
      rings.push(Math.max(0, cum - prev));
      prev = cum;
    }
    return rings;
  }
  let bdShareData = $derived.by(() => {
    const mode = instance.bookDepthMode ?? 'totals';
    if (instance.kind !== 'book_depth' || !BD_SHARE_MODES.includes(mode)) return [];
    const rows = data as unknown as Record<string, number>[];
    const askT = BD_BANDS.map((b) => b.ask); // tight → deep: p020 … p500
    const bidT = BD_BANDS.map((b) => b.bid); // tight → deep: m020 … m500
    const sum = (a: number[]) => a.reduce((s, x) => s + x, 0);
    if (mode === 'asks_share') {
      return rows.map((d) => {
        const r = bdRings(d, askT);
        const denom = sum(r);
        const out: Record<string, number> = { time: d.time };
        askT.forEach((sfx, i) => (out['s_' + sfx] = denom > 0 ? (r[i] / denom) * 100 : 0));
        return out;
      });
    }
    if (mode === 'bids_share') {
      return rows.map((d) => {
        const r = bdRings(d, bidT);
        const denom = sum(r);
        const out: Record<string, number> = { time: d.time };
        bidT.forEach((sfx, i) => (out['s_' + sfx] = denom > 0 ? (r[i] / denom) * 100 : 0));
        return out;
      });
    }
    if (mode === 'asks_bids_share') {
      // Both sides at once: each side normalized to its own 100%. Bids occupy
      // 0–100%, asks stack on top to 100–200%.
      return rows.map((d) => {
        const ar = bdRings(d, askT);
        const aDen = sum(ar);
        const br = bdRings(d, bidT);
        const bDen = sum(br);
        const out: Record<string, number> = { time: d.time };
        bidT.forEach((sfx, i) => (out['s_' + sfx] = bDen > 0 ? (br[i] / bDen) * 100 : 0));
        askT.forEach((sfx, i) => (out['s_' + sfx] = aDen > 0 ? (ar[i] / aDen) * 100 : 0));
        return out;
      });
    }
    // total_share — each band's combined (bid ring + ask ring) over the whole book.
    return rows.map((d) => {
      const ar = bdRings(d, askT);
      const br = bdRings(d, bidT);
      const denom = sum(ar) + sum(br);
      const out: Record<string, number> = { time: d.time };
      BD_BANDS.forEach((b, i) => (out['s_' + b.bid] = denom > 0 ? ((ar[i] + br[i]) / denom) * 100 : 0));
      return out;
    });
  });
  // Share-mode moving average. When MA1 is enabled the share modes smooth each
  // band's share *first*, then re-stack — i.e. MA is applied per bracket to the
  // raw share series, not to the rendered stack. A moving average is linear so
  // the smoothed bands still very nearly sum to 100, but the MA's leading-edge
  // ramp can nudge the per-bucket total slightly off; we re-normalize each
  // bucket so the stack stays a clean 100%. Only MA1 (instance.mas[0]) is used
  // — share modes expose a single MA in the settings panel.
  let bdShareMaData = $derived.by(() => {
    const mode = instance.bookDepthMode ?? 'totals';
    if (instance.kind !== 'book_depth' || !BD_SHARE_MODES.includes(mode)) return [];
    const ma = instance.mas[0];
    if (!ma?.enabled) return [];
    const src = bdShareData;
    if (!src.length) return [];
    const keys = bdShareSeries.map((s) => s.key);
    const smoothed: Record<string, number[]> = {};
    for (const k of keys) smoothed[k] = maArray(src.map((d) => d[k] ?? 0), ma.length, ma.type);
    // Re-normalize per side independently. Single-side modes have one group
    // (→ 100%); the combined mode normalizes bids and asks separately so each
    // keeps its own 100% (total 200%) after the MA's edge ramp.
    const groups =
      mode === 'asks_bids_share'
        ? [BD_BANDS.map((b) => 's_' + b.bid), BD_BANDS.map((b) => 's_' + b.ask)]
        : [keys];
    return src.map((d, i) => {
      const out: Record<string, number> = { time: d.time };
      for (const grp of groups) {
        let tot = 0;
        for (const k of grp) tot += smoothed[k][i] ?? 0;
        for (const k of grp) out[k] = tot > 0 ? ((smoothed[k][i] ?? 0) / tot) * 100 : 0;
      }
      return out;
    });
  });
  // Is the share-mode MA currently driving the stack? (used by the render branch
  // and the Point/MA mutual-exclusion in the settings panel)
  let bdShareMaActive = $derived(
    instance.kind === 'book_depth'
      && BD_SHARE_MODES.includes(instance.bookDepthMode ?? '')
      && !!instance.mas[0]?.enabled
  );
  // Imbalance mode: the same single MA, applied to the whole-book imbalance
  // series. When active it replaces the bars with the MA line (Point/MA are
  // mutually exclusive, as in the share modes).
  let bdImbalanceMaActive = $derived(
    instance.kind === 'book_depth'
      && (instance.bookDepthMode ?? 'totals') === 'imbalance'
      && !!instance.mas[0]?.enabled
  );
  let bdImbalanceMaLine = $derived.by(() => {
    type ImbLine = {
      key: string;
      label: string;
      color: string;
      compute: (d: Record<string, number>, i: number) => number;
    };
    if (!bdImbalanceMaActive) return [] as ImbLine[];
    const ma = instance.mas[0];
    const arr = maArray(bdImbalanceData.map((d) => d.imb ?? 0), ma.length, ma.type);
    return [
      {
        key: 'bd_imb_ma',
        label: `Imbalance ${ma.type.toUpperCase()}(${ma.length})`,
        color: MA_COLORS[0],
        compute: (_d: Record<string, number>, i: number) => arr[i]
      }
    ] as ImbLine[];
  });
  // Mode shows the single-MA control (share stacks + imbalance), and whether
  // that MA is currently active (drives Point/MA mutual exclusion).
  let bdSingleMaMode = $derived(
    instance.kind === 'book_depth'
      && (BD_SHARE_MODES.includes(instance.bookDepthMode ?? '')
        || (instance.bookDepthMode ?? 'totals') === 'imbalance')
  );
  let bdMaActive = $derived(bdShareMaActive || bdImbalanceMaActive);
  // Active line set for the per-mode primary-range fallback (overlays).
  let bdLinesD = $derived.by(() => {
    const mode = instance.bookDepthMode ?? 'totals';
    if (mode === 'totals') return bdTotalsLines;
    if (mode === 'per_level_imbalance') return bdPerLevelImbalanceLines;
    return cumulativeLines;
  });

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
    const tLabel = t === 'all' ? 'All'
      : t === 'in_out' ? 'Outflow + Inflow'
      : t.charAt(0).toUpperCase() + t.slice(1);
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
  // The token the dialog is about (chart uses instance.token; the Token pane can
  // open it for a different token row).
  let walletsDialogToken = $state('');
  // As-of-day selector metrics: the values that admitted each wallet on the
  // clicked day (e.g. Sharpe annualized), shown in the dialog's stats area.
  let walletsDialogAsOf = $state<Array<{ key: string; label: string; scope: string; lookback: number }>>([]);
  let walletsDialogMetrics = $state<Record<string, Record<string, number | null>>>({});
  // Per-wallet position in the chart token at the filter day (long/short/none).
  let walletsDialogPositions = $state<Record<string, { side: string; amount: number; size_usd: number; unrealized: number }>>({});
  let walletsFetchCtl: AbortController | null = null;

  async function openSmartWalletsDialog(timeSec: number) {
    if (instance.kind !== 'hl_smart_oi') return;
    // Round to UTC day — matches the selector's `target_days` grain.
    const d = new Date(timeSec * 1000);
    const dayIso = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    walletsDialogDay = dayIso;
    walletsDialogList = [];
    walletsDialogAsOf = [];
    walletsDialogMetrics = {};
    walletsDialogPositions = {};
    walletsDialogError = null;
    walletsDialogLoading = true;
    walletsDialogOpen = true;
    if (walletsFetchCtl) walletsFetchCtl.abort();
    walletsFetchCtl = new AbortController();
    try {
      // Wallet-count click → the day's wallets for the chart's effective
      // (combined) filter set.
      const wire = smartCombinedWire;
      if (wire === null || wire === 'broken') throw new Error('no resolvable filter for this chart');
      const qs = new URLSearchParams({
        token: instance.token ?? '',
        day: dayIso,
        filter: JSON.stringify(wire),
      });
      const res = await fetch(`/api/hyperliquid/smart_wallets?${qs}`, { signal: walletsFetchCtl.signal });
      if (!res.ok) throw new Error(`smart_wallets ${res.status}`);
      const body = await res.json();
      walletsDialogList = (body.wallets ?? []) as string[];
      walletsDialogAsOf = (body.as_of_metrics ?? []) as Array<{ key: string; label: string; scope: string; lookback: number }>;
      walletsDialogMetrics = (body.wallet_metrics ?? {}) as Record<string, Record<string, number | null>>;
      walletsDialogPositions = (body.wallet_positions ?? {}) as Record<string, { side: string; amount: number; size_usd: number; unrealized: number }>;
    } catch (e) {
      if ((e as DOMException)?.name !== 'AbortError') {
        walletsDialogError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      walletsDialogLoading = false;
    }
  }

  // Top-OI dialog (all Smart Wallets widgets): the top-N wallets by OI for a
  // token at a snapshot, among the widget's filtered set. Triggered by clicking
  // a chart point (Chart pane) or a token row (Token pane). Reuses the
  // SmartWalletsDialog (copy / Coinglass / per-wallet PnL).
  async function openTopOiDialog(timeSec: number | null, tokenOverride?: string) {
    if (!isSwKind(instance.kind)) return;
    const token = tokenOverride || instance.token;
    if (!token) return;
    const d = timeSec ? new Date(timeSec * 1000) : new Date();
    walletsDialogToken = token;
    walletsDialogDay = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    walletsDialogList = [];
    walletsDialogAsOf = [];
    walletsDialogMetrics = {};
    walletsDialogPositions = {};
    walletsDialogError = null;
    walletsDialogLoading = true;
    walletsDialogOpen = true;
    if (walletsFetchCtl) walletsFetchCtl.abort();
    walletsFetchCtl = new AbortController();
    try {
      const qs = swSelectionParams();
      qs.set('oi_token', token);
      qs.set('n', '10');
      if (timeSec) qs.set('time', String(Math.floor(timeSec)));
      if (instance.kind === 'smart_wallets_dynamic') qs.set('rolling', '1');
      const res = await fetch(`/api/hyperliquid/smart_wallet_top_oi?${qs}`, { signal: walletsFetchCtl.signal });
      if (!res.ok) throw new Error(`smart_wallet_top_oi ${res.status}`);
      const body = await res.json();
      walletsDialogList = (body.wallets ?? []) as string[];
      walletsDialogPositions = (body.positions ?? {}) as Record<string, { side: string; amount: number; size_usd: number; unrealized: number }>;
      if (typeof body.day === 'string') walletsDialogDay = body.day;
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
    && instance.kind !== 'token_leaderboard'
    && instance.kind !== 'spot_cvd_table'
    && instance.kind !== 'smart_wallets_table'
    && !isLeaderboardKind(instance.kind)
  );

  // TableView kinds render a bespoke table instead of a LineChart, so the
  // generic chart-only settings (Point, Week lines, zoom-sync exclude, MA) are
  // meaningless for them — the settings panel hides that whole block.
  let isTableviewKind = $derived(
    instance.kind === 'token_leaderboard'
    || instance.kind === 'spot_cvd_table'
    // Dual-view chart mode renders a real LineChart, so the chart-only settings
    // (Point / Week lines / MA / zoom-sync) DO apply there — only the table view
    // counts as a tableview kind.
    || (isSwKind(instance.kind) && instance.viewMode !== 'chart')
    || instance.kind === 'hl_top_traders'
    || instance.kind === 'hl_top_positions'
    || instance.kind === 'hl_top_vaults'
    || instance.kind === 'hl_top_vault_lps'
    || instance.kind === 'hl_vault_detail'
    || isLeaderboardKind(instance.kind)
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
    // Drag-reorder guard — same reasoning as the main load effect:
    // during a svelte-dnd-action drag the bound instance briefly
    // becomes a shadow placeholder and any read of instance.overlays
    // would return undefined, causing all overlays to be cleared and
    // refetched on drop.
    if ((instance as unknown as Record<string, unknown>)[SHADOW_ITEM_MARKER_PROPERTY_NAME]) return;
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
      // Bucket volume as a percent of OI — "20% of OI turned over this bucket".
      // 1 decimal is enough; the underlying ratio is noisy.
      return (v: number) => `${(v * 100).toFixed(1)}%`;
    }
    if (o.kind === 'volume') {
      // 'volume_usd' = dollar notional; 'volume' = token amount (coin count).
      return o.seriesKey === 'volume' ? fmtAmountTooltip : fmtUsdTooltip;
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
    // Spot CVD periodic mode is bars (SignedBarChart) — derive the overlay
    // range from the signed bar values like FR. Cumulative mode falls through
    // to the primary-lines path below (primaryLines = cvdLinesD).
    if (instance.kind === 'spot_cvd' && (instance.cvdMode ?? 'cumulative') === 'periodic') {
      const range = computePrimaryRangeFromField(
        data as unknown as Record<string, number>[], 'value', true
      );
      return buildOverlayLines(range);
    }
    // Pick the right per-kind primary-lines array. Falls through to an empty
    // range when no primary lines exist (overlay will render flat-centered).
    let primaryLines: typeof aaveLinesD = [];
    if (effectiveKind === 'oi' || effectiveKind === 'hl_smart_oi') primaryLines = oiLinesD;
    else if (instance.kind === 'fr') primaryLines = frLinesD;
    else if (instance.kind === 'book_depth') primaryLines = bdLinesD;
    else if (instance.kind === 'tt') primaryLines = ttLinesD;
    else if (instance.kind === 'ls') primaryLines = lsLinesD;
    else if (instance.kind === 'bs') primaryLines = bsMode === 'ratio' ? bsRatioLinesD : bsMode === 'pct' ? bsPctLinesD : bsMode === 'imbalance' ? bsImbalanceLinesD : bsLines;
    else if (instance.kind === 'sz') primaryLines = (instance.szMode === 'taker_split' ? szTakerSplitLinesD : szLinesD);
    else if (instance.kind === 'transfer') primaryLines = transferLinesD;
    else if (instance.kind === 'exchange_flow') primaryLines = exchangeFlowLinesD;
    else if (instance.kind === 'spot_cvd') primaryLines = cvdLinesD;
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
    const k = effectiveKind;
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
  let bdTotalsLinesM     = $derived(overlayLinesD.length === 0 ? bdTotalsLines : [...bdTotalsLines, ...overlayLinesD]);
  // Per-level imbalance is a bounded-ratio axis — USD price/MA overlays don't
  // belong on it, so it's passed through unmerged (already a stable $derived).
  let bsLinesM           = $derived(overlayLinesD.length === 0 ? bsLines : [...bsLines, ...overlayLinesD]);
  let bsRatioLinesM      = $derived(overlayLinesD.length === 0 ? bsRatioLinesD : [...bsRatioLinesD, ...overlayLinesD]);
  let bsPctLinesM        = $derived(overlayLinesD.length === 0 ? bsPctLinesD : [...bsPctLinesD, ...overlayLinesD]);
  let bsImbalanceLinesM  = $derived(overlayLinesD.length === 0 ? bsImbalanceLinesD : [...bsImbalanceLinesD, ...overlayLinesD]);
  let szLinesM           = $derived(overlayLinesD.length === 0 ? szLinesD : [...szLinesD, ...overlayLinesD]);
  let szTakerSplitLinesM = $derived(overlayLinesD.length === 0 ? szTakerSplitLinesD : [...szTakerSplitLinesD, ...overlayLinesD]);
  let ttLinesM           = $derived(overlayLinesD.length === 0 ? ttLinesD : [...ttLinesD, ...overlayLinesD]);
  let lsLinesM           = $derived(overlayLinesD.length === 0 ? lsLinesD : [...lsLinesD, ...overlayLinesD]);
  let transferLinesM     = $derived(overlayLinesD.length === 0 ? transferLinesD : [...transferLinesD, ...overlayLinesD]);
  let exchangeFlowLinesM = $derived(overlayLinesD.length === 0 ? exchangeFlowLinesD : [...exchangeFlowLinesD, ...overlayLinesD]);
  // Volume chart: a single line off the /ohlcv candle series — volume_usd
  // (dollar notional) or volume (token amount) per the unit toggle.
  let volumeLinesD = $derived([
    {
      key: 'volume',
      label: (instance.volumeUnit ?? 'usd') === 'token'
        ? `Volume (${instance.token})` : 'Volume ($)',
      color: '#06b6d4',
      // Param typed as a Datum-compatible shape (extra fields optional) so the
      // array satisfies LineChart's Line[] — a bare `Candle` has required
      // OHLC fields and isn't a supertype of Datum.
      compute: (d: { time: number; volume?: number; volume_usd?: number }) =>
        (instance.volumeUnit ?? 'usd') === 'token' ? (d.volume ?? 0) : (d.volume_usd ?? 0)
    }
  ]);
  // realized_price modes (display-only — both fields ride each row):
  //  'price'     = realized + current price (both USD, primary axis)
  //  'diff'      = realized price (primary) + % of current vs realized (secondary)
  //  'diff_only' = just the % difference, alone, on the primary axis
  let rpLinesD = $derived.by(() => {
    type RPRow = { time: number; realized_price?: number; current_price?: number };
    const realized = { key: 'realized_price', label: 'Realized price', color: '#fbbf24',
      compute: (d: RPRow) => d.realized_price ?? NaN };
    const pct = (d: RPRow) => {
      const r = d.realized_price ?? 0;
      return r ? (((d.current_price ?? 0) - r) / r) * 100 : NaN;
    };
    if (instance.rpMode === 'diff_only') {
      return [{ key: 'pct_diff', label: '% vs realized', color: '#06b6d4', compute: pct }];
    }
    if (instance.rpMode === 'diff') {
      return [realized, { key: 'pct_diff', label: '% vs realized', color: '#06b6d4', axis: 'secondary' as const, compute: pct }];
    }
    return [realized, { key: 'current_price', label: 'Current price', color: '#06b6d4',
      compute: (d: RPRow) => d.current_price ?? NaN }];
  });
  // spot_cvd cumulative mode: single running-CVD line (USD or token units).
  let cvdLinesD = $derived.by(() => {
    type CvdRow = { time: number; value?: number };
    return [{ key: 'value', label: instance.cvdUnit === 'token' ? 'CVD (token)' : 'CVD ($)',
      color: '#22c55e', compute: (d: CvdRow) => d.value ?? NaN }];
  });
  // Merged variant so an enabled overlay (e.g. OHLCV close) renders on the CVD
  // line. Defined here (after cvdLinesD) rather than with the other *LinesM.
  let cvdLinesM = $derived(overlayLinesD.length === 0 ? cvdLinesD : [...cvdLinesD, ...overlayLinesD]);
  // ps (Perp vs Spot): the spot/perp volume-ratio line. Two variants — on the
  // SECONDARY (left) axis when shown alongside the basis bars ('all'), or on the
  // primary axis when it's the only series ('volume').
  // Concretely-typed view of the merged ps rows so the bar/line components
  // accept it without the loose `Record<string,number>[]` → Datum cast.
  let psData = $derived(
    data as unknown as Array<{ time: number; basis_pp: number; vol_ratio_pct: number }>
  );
  const psVolCompute = (d: { time: number; vol_ratio_pct?: number }) => d.vol_ratio_pct ?? NaN;
  let psVolLineSecondary = $derived([
    { key: 'vol_ratio_pct', label: 'Spot/Perp Vol %', color: '#06b6d4', axis: 'secondary' as const, compute: psVolCompute }
  ]);
  let psVolLinePrimary = $derived([
    { key: 'vol_ratio_pct', label: 'Spot/Perp Vol %', color: '#06b6d4', compute: psVolCompute }
  ]);
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
      {#if isDualViewKind(instance.kind)}
        <!-- Dual-view ("hyper") widget: toggle the native table vs the linked
             chart (same widget, two views over the same data). Persisted on the
             instance; each view's data is cached so toggling doesn't refetch. -->
        <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
          <button
            type="button"
            onclick={() => {
              // Drop the other view's data synchronously so the table branch
              // never renders OI rows / token rows during the switch.
              if ((instance.viewMode ?? 'table') !== 'table') data = [];
              instance.viewMode = 'table';
            }}
            class={'px-2 py-1 text-xs ' + ((instance.viewMode ?? 'table') === 'table'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Table view"
          >Table</button>
          <button
            type="button"
            onclick={() => {
              // Ensure the chart has a token + interval before switching in.
              if (!instance.token) instance.token = instance.swToken || 'BTC';
              if (!instance.interval) instance.interval = '1h';
              // Drop the table-shaped data synchronously: the OI LineChart must
              // never receive wallet rows (no `time` field → lightweight-charts
              // setData throws on undefined time → black chart).
              if ((instance.viewMode ?? 'table') !== 'chart') data = [];
              instance.viewMode = 'chart';
            }}
            class={'px-2 py-1 text-xs border-l border-zinc-700 ' + ((instance.viewMode ?? 'table') === 'chart'
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Chart view — OI of the found wallets"
          >Chart</button>
          {#if instance.kind === 'smart_wallets_dynamic' || isCutoff || isGroup}
            <button
              type="button"
              onclick={() => {
                if (instance.viewMode !== 'token_list') data = [];
                instance.viewMode = 'token_list';
              }}
              class={'px-2 py-1 text-xs border-l border-zinc-700 ' + (instance.viewMode === 'token_list'
                ? 'bg-zinc-800 text-zinc-100'
                : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
              title="Token List — long/short OI per token across the found wallets"
            >Tokens</button>
          {/if}
        </div>
      {/if}
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
              onchange={(e) => (instance.exchangeFlowType = e.currentTarget.value as 'inflow' | 'outflow' | 'netflow' | 'in_out' | 'all')}
              class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
              title="Which direction(s) of HL bridge flow to plot"
            >
              <option value="inflow">Inflow</option>
              <option value="outflow">Outflow</option>
              <option value="netflow">Netflow</option>
              <option value="in_out">Outflow + Inflow</option>
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
            const v = e.currentTarget.value as 'binance' | 'coinbase' | 'okx' | 'bybit' | 'hyperliquid' | 'combined';
            instance.exchangeFlowExchange = v;
            if (v === 'hyperliquid') {
              // HL bridge is ARB + USDC only; auto-correct both so the
              // user doesn't have to reset them after the swap.
              instance.chain = 'ARB';
              instance.token = 'USDC';
            }
          }}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          title="Which exchange's deposit/hot-wallet wallets to filter on. Combined sums every exchange (unsupported token/chain per exchange counts as 0)."
        >
          <option value="combined">Combined</option>
          <option value="binance">Binance</option>
          <option value="coinbase">Coinbase</option>
          <option value="okx">OKX</option>
          <option value="bybit">Bybit</option>
          <option value="hyperliquid">Hyperliquid</option>
        </select>
        <select
          value={instance.exchangeFlowType ?? 'netflow'}
          onchange={(e) => (instance.exchangeFlowType = e.currentTarget.value as 'inflow' | 'outflow' | 'netflow' | 'in_out' | 'all')}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          title="Which direction(s) of flow to plot"
        >
          <option value="inflow">Inflow</option>
          <option value="outflow">Outflow</option>
          <option value="netflow">Netflow</option>
          <option value="in_out">Outflow + Inflow</option>
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
      {:else if instance.kind === 'token_leaderboard'}
        <!-- Token Leaderboard: a global per-token table with no token/exchange
             dimension (Binance-sourced, all tokens). Sorting lives in the table
             header, so the toolbar just carries a static source chip. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">Binance · all tokens</span>
      {:else if instance.kind === 'spot_cvd_table'}
        <!-- Spot CVD table: global per-token, no token dimension. Lookback +
             units live here; sort-criteria/direction + limit live in the table. -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">Binance spot · all tokens</span>
        <select
          value={instance.cvdtLookback ?? 'all'}
          onchange={(e) => (instance.cvdtLookback = e.currentTarget.value as 'all' | '1' | '7' | '14')}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          title="All = 1d/7d/14d comparison; or a single period"
        >
          <option value="all">All (1d/7d/14d)</option>
          <option value="1">1d</option>
          <option value="7">7d</option>
          <option value="14">14d</option>
        </select>
        <select
          value={instance.cvdtUnit ?? 'usd'}
          onchange={(e) => (instance.cvdtUnit = e.currentTarget.value as 'usd' | 'token')}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          title="Show Avg-Vol + CVD-Vol columns in USD or token volume"
        >
          <option value="usd">$</option>
          <option value="token">Token</option>
        </select>
      {:else if isSwKind(instance.kind) && instance.viewMode !== 'chart'}
        <!-- Smart Wallets finder (TABLE view): HL-only. Every control (metric /
             lookback / token selectors + the snapshot slider) lives inside the
             table's own header; the min-days/min-volume guards live in the gear
             panel. The toolbar just carries a static source chip. (Chart view
             falls through to the generic OI controls below.) -->
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">HL · smart wallets{instance.kind === 'smart_wallets_dynamic' ? ' · dynamic' : ''}</span>
        {#if instance.viewMode === 'token_list'}
          <!-- Token List view: OI columns in USD or token units (display-only). -->
          <select
            value={instance.swtUnit ?? 'usd'}
            onchange={(e) => (instance.swtUnit = e.currentTarget.value as 'usd' | 'token')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Show OI + OI-change columns in USD or token units"
          >
            <option value="usd">$</option>
            <option value="token">Token</option>
          </select>
        {/if}
      {:else}
        {#if instance.kind === 'smart_wallets_dynamic'}
          <!-- Dynamic chart: the set is re-selected per bucket over a ROLLING
               trailing window, so there's no fixed snapshot/end. Surface the
               rolling lookback as a text chip (changed via the table view's
               lookback selector). -->
          <span class="text-zinc-400 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700" title="Each point selects wallets over a rolling trailing window of this length (set in the table view).">Rolling {instance.swLookback ?? 7}d lookback</span>
        {/if}
        {#if instance.kind === 'ohlcv' || instance.kind === 'fr' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'oi' || instance.kind === 'volume' || instance.kind === 'pc' || instance.kind === 'ls'}
          <!-- Exchange selector picks the data source. ohlcv → *_ohlcv_1m,
               fr → binance_funding_rate / hl_funding, bs/sz → *_raw_trades /
               hl_trades, pc → *_ohlcv_1m close, ls → binance_long_short_ratios /
               (hl_position_history + hl_fills). Same render path either way.
               tt (top-trader L/S) stays Binance-only — see derivatives.py. -->
          <!-- Venue selector. 'binance_spot' is the internal exchange value for
               the Binance spot dataset, but it's surfaced via the separate
               MARKET selector below (perp/spot) rather than as a third venue —
               so here Binance covers both perp and spot and we map accordingly.
               Picking HL drops spot (HL has no spot market); picking Binance
               defaults to perp and unlocks the market selector. -->
          <select
            value={(instance.exchange ?? 'binance') === 'hl' ? 'hl' : 'binance'}
            onchange={(e) => (instance.exchange = e.currentTarget.value === 'hl' ? 'hl' : 'binance')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
          >
            <option value="binance">Binance</option>
            <option value="hl">Hyperliquid</option>
          </select>
          {#if instance.kind === 'ohlcv' || instance.kind === 'volume' || instance.kind === 'bs' || instance.kind === 'sz' || instance.kind === 'pc'}
            <!-- Market selector — only the candle / trade-flow / relative-price
                 kinds have spot tables. Spot is unlocked only for Binance; HL is
                 perp-only, so the selector is disabled (and pinned to Perp) for
                 HL. Maps to the internal exchange value: Binance+Spot →
                 'binance_spot'. (bs/sz read raw spot trades; ohlcv/volume/pc
                 read spot OHLCV.) -->
            {@const isHlVenue = (instance.exchange ?? 'binance') === 'hl'}
            <select
              value={instance.exchange === 'binance_spot' ? 'spot' : 'perp'}
              disabled={isHlVenue}
              onchange={(e) => (instance.exchange = e.currentTarget.value === 'spot' ? 'binance_spot' : 'binance')}
              title={isHlVenue ? 'Spot is only available for Binance' : 'Perp (futures) or Spot market'}
              class={'bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 focus:outline-none focus:border-zinc-500 ' + (isHlVenue ? 'opacity-50 cursor-not-allowed text-zinc-500' : 'text-zinc-100 hover:border-zinc-600')}
            >
              <option value="perp">Perp</option>
              <option value="spot">Spot</option>
            </select>
          {/if}
        {/if}
        {#if instance.kind === 'ls' || instance.kind === 'tt'}
          <!-- Series selector for the L/S ratio charts. 'All' shows every line
               (current behaviour); picking a single series isolates it. Options
               come straight from the line catalogue so they stay in sync. -->
          <select
            value={instance.seriesFilter ?? 'all'}
            onchange={(e) => (instance.seriesFilter = e.currentTarget.value)}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Which ratio series to display"
          >
            <option value="all">All</option>
            {#each (instance.kind === 'tt' ? TOP_TRADERS_LINES : LS_LINES) as s (s.key)}
              <option value={s.key}>{s.label}</option>
            {/each}
          </select>
        {/if}
        {#if instance.kind === 'bs'}
          <!-- Taker Buyer vs Seller display: stacked $ bars, the Buyer/Seller
               ratio line, both (bars + ratio on a secondary axis), or the
               buyer/seller % shares as two lines. -->
          <select
            value={instance.bsDisplay ?? 'stacked'}
            onchange={(e) => (instance.bsDisplay = e.currentTarget.value as 'stacked' | 'ratio' | 'both' | 'pct' | 'imbalance')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="How to display taker buyer vs seller"
          >
            <option value="stacked">Stacked</option>
            <option value="ratio">Buyer / Seller</option>
            <option value="both">Both</option>
            <option value="pct">% Buyer / Seller</option>
            <option value="imbalance">Imbalance (Buyer − Seller %)</option>
          </select>
          {#if (instance.bsDisplay ?? 'stacked') === 'stacked' || instance.bsDisplay === 'both'}
            <!-- Taker-volume denomination — USD notional vs token amount. Only
                 meaningful for the bar modes (ratio / % are unit-independent).
                 Reuses volumeUnit; both values are in each bucket so it's a
                 pure display toggle. -->
            <select
              value={instance.volumeUnit ?? 'usd'}
              onchange={(e) => (instance.volumeUnit = e.currentTarget.value as 'usd' | 'token')}
              class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
              title="Show taker volume in USD notional or token amount"
            >
              <option value="usd">USD</option>
              <option value="token">{instance.token ?? 'Token'}</option>
            </select>
          {/if}
        {/if}
        {#if instance.kind === 'ps'}
          <!-- Perp vs Spot series: 'All' shows the basis bars + the volume-ratio
               line (on a secondary axis); 'Basis' isolates the perp−spot close
               basis bars (pp); 'Volume %' isolates the spot/perp volume ratio. -->
          <select
            value={instance.psSeries ?? 'all'}
            onchange={(e) => (instance.psSeries = e.currentTarget.value as 'all' | 'basis' | 'volume')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Which series to display"
          >
            <option value="all">All</option>
            <option value="basis">Basis (pp)</option>
            <option value="volume">Volume %</option>
          </select>
        {/if}
        {#if instance.kind === 'realized_price'}
          <!-- Realized Price view: realized + current price, or realized + the
               % difference of current vs realized. Display-only (no refetch). -->
          <select
            value={instance.rpMode ?? 'price'}
            onchange={(e) => (instance.rpMode = e.currentTarget.value as 'price' | 'diff' | 'diff_only')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Show realized + current price, realized + the % difference, or just the % difference"
          >
            <option value="price">Realized + Price</option>
            <option value="diff">Realized + % Diff</option>
            <option value="diff_only">% Diff only</option>
          </select>
          <!-- Lookback for the VWAP: from the first record, or a trailing window.
               Changes the calculation → refetches. -->
          <select
            value={instance.rpLookback ?? 'all'}
            onchange={(e) => (instance.rpLookback = e.currentTarget.value as 'all' | '1' | '7' | '14' | '30' | '90')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Lookback window for the realized-price VWAP"
          >
            <option value="all">All (from start)</option>
            <option value="1">1d</option>
            <option value="7">7d</option>
            <option value="14">14d</option>
            <option value="30">30d</option>
            <option value="90">90d</option>
          </select>
        {/if}
        {#if instance.kind === 'spot_cvd'}
          <!-- Mode: cumulative running sum (line) vs per-bucket delta (bars).
               Changes the query → refetches. -->
          <select
            value={instance.cvdMode ?? 'cumulative'}
            onchange={(e) => (instance.cvdMode = e.currentTarget.value as 'cumulative' | 'periodic')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Cumulative running CVD (line) or per-bucket delta (bars)"
          >
            <option value="cumulative">Cumulative</option>
            <option value="periodic">Periodic</option>
          </select>
          <!-- Units: USD vs token volume. Changes the query → refetches. -->
          <select
            value={instance.cvdUnit ?? 'usd'}
            onchange={(e) => (instance.cvdUnit = e.currentTarget.value as 'usd' | 'token')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Denominate the delta in USD or token volume"
          >
            <option value="usd">$</option>
            <option value="token">Token</option>
          </select>
          {#if (instance.cvdMode ?? 'cumulative') === 'cumulative'}
            <!-- Accumulation window (cumulative only). Changes the query. -->
            <select
              value={instance.cvdLookback ?? 'all'}
              onchange={(e) => (instance.cvdLookback = e.currentTarget.value as 'all' | '1' | '7' | '14' | '30' | '90')}
              class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
              title="How far back the cumulative CVD accumulates"
            >
              <option value="all">All (from start)</option>
              <option value="1">1d</option>
              <option value="7">7d</option>
              <option value="14">14d</option>
              <option value="30">30d</option>
              <option value="90">90d</option>
            </select>
          {/if}
        {/if}
        {#if instance.kind === 'sz'}
          <!-- Mode: bucket totals vs the chosen bracket split into buyer-taker
               vs seller-taker. Display-only (the split fields ride each bucket). -->
          <select
            value={instance.szMode ?? 'total'}
            onchange={(e) => (instance.szMode = e.currentTarget.value as 'total' | 'taker_split')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Show bucket totals, or split the chosen bracket into buyer-taker vs seller-taker"
          >
            <option value="total">Size totals</option>
            <option value="taker_split">Buyer vs Seller (taker)</option>
          </select>
          <!-- Volume-by-Size series selector: 'All' shows every bucket line;
               picking one isolates that bucket (and its MA). Options come from
               the same bucket catalogue so labels track the $ thresholds. In
               taker-split mode it picks WHICH bracket to split. -->
          <select
            value={instance.seriesFilter ?? 'all'}
            onchange={(e) => (instance.seriesFilter = e.currentTarget.value)}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Which size bucket to display"
          >
            <option value="all">All</option>
            {#each sizeLineSeries(instance.under ?? 10000, instance.over ?? 100000) as s (s.key)}
              <option value={s.key}>{s.label}</option>
            {/each}
          </select>
          {#if (instance.szMode ?? 'total') === 'total'}
            <!-- Taker-side filter (totals mode only): restrict the size buckets to
                 buyer-taker or seller-taker trades (or all). Changes the data →
                 refetches. (The split mode shows both sides already.) -->
            <select
              value={instance.szSide ?? 'all'}
              onchange={(e) => (instance.szSide = e.currentTarget.value as 'all' | 'buy' | 'sell')}
              class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
              title="Restrict size buckets to buyer-taker, seller-taker, or all trades"
            >
              <option value="all">All trades</option>
              <option value="buy">Buyer taker</option>
              <option value="sell">Seller taker</option>
            </select>
          {/if}
        {/if}
        {#if (instance.kind === 'oi' && (instance.exchange ?? 'binance') === 'hl') || effectiveKind === 'hl_smart_oi'}
          <!-- HL-only display selector. position_history carries per-wallet
               sides so we can split OI into long/short or show all three on
               one chart. Same selector for hl_smart_oi which is HL-only by
               construction. -->
          <select
            value={instance.oiHlDisplay ?? 'total'}
            onchange={(e) => (instance.oiHlDisplay = e.currentTarget.value as 'long' | 'short' | 'total' | 'long_short' | 'long_to_short' | 'net_pct' | 'net' | 'count')}
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
            {#if effectiveKind === 'hl_smart_oi'}
              <option value="count">Long + Short num</option>
            {/if}
          </select>
        {/if}
        {#if instance.kind === 'book_depth'}
          <!-- Book-depth mode selector. The /book_depth response carries
               24 numeric columns per bucket (d_*/v_* per percentage level);
               this picks the visualization the chart pivots into. -->
          <select
            value={instance.bookDepthMode ?? 'totals'}
            onchange={(e) => (instance.bookDepthMode = e.currentTarget.value as NonNullable<typeof instance.bookDepthMode>)}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="How to render the book depth time series"
          >
            <option value="totals">Totals</option>
            <option value="per_level_imbalance">Per-level imbalance</option>
            <option value="imbalance">Imbalance</option>
            <option value="stacked">Stacked</option>
            <option value="asks_share">Asks share (100%)</option>
            <option value="bids_share">Bids share (100%)</option>
            <option value="total_share">Total share (100%)</option>
            <option value="asks_bids_share">Asks + Bids share</option>
          </select>
        {/if}
        {#if (instance.kind === 'oi' || effectiveKind === 'hl_smart_oi') && !((effectiveKind === 'hl_smart_oi' || (instance.exchange ?? 'binance') === 'hl') && ((instance.oiHlDisplay ?? 'total') === 'long_to_short' || (instance.oiHlDisplay ?? 'total') === 'net_pct' || (instance.oiHlDisplay ?? 'total') === 'count'))}
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
        {#if instance.kind === 'volume' || instance.kind === 'ohlcv'}
          <!-- Volume unit: dollar notional vs token amount. For ohlcv it
               denominates the candle chart's volume sub-pane; for the volume
               kind it's the plotted line. Reuses the shared volumeUnit field;
               display-only (both ride each candle). -->
          <select
            value={instance.volumeUnit ?? 'usd'}
            onchange={(e) => (instance.volumeUnit = e.currentTarget.value as 'usd' | 'token')}
            class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
            title="Display volume as dollar notional or token amount"
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
      {#if !isLeaderboardKind(instance.kind) && instance.kind !== 'token_leaderboard' && instance.kind !== 'spot_cvd_table' && (instance.kind !== 'smart_wallets_table' || instance.viewMode === 'chart')}
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
        title={swNeedsRefresh ? (swArmed ? 'Inputs changed — click to run the finder' : 'Click to run the finder') : (loading ? 'Loading — click to cancel and retry' : 'Refresh')}
        class={'w-7 h-7 rounded-md text-sm leading-none flex items-center justify-center ' + (loading ? 'animate-spin ' : '') + (swNeedsRefresh
          ? 'text-amber-300 border border-amber-500 bg-amber-600/20 hover:bg-amber-600/40'
          : 'text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 border border-transparent')}
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
      {#if isSwKind(instance.kind) && !isGroup && (instance.kind === 'smart_wallets_dynamic' || isCutoff || instance.viewMode !== 'chart')}
        <!-- Smart-wallet finder filters. For Fixed they show in TABLE view only
             (chart mode shows chart-appearance settings); for Dynamic they
             ALWAYS show (the criteria define the per-day rolling set the chart
             plots). Grouped into labelled sections so each guard is easy to
             find. All commit together on refresh. -->
        {@const swCell = 'w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500'}
        {@const swLabel = 'text-zinc-500 text-[10px] uppercase tracking-wide leading-tight'}
        {@const swGroup = 'border border-zinc-800 rounded-md px-3 pt-1 pb-2 min-w-[170px]'}
        {@const swLegend = 'text-zinc-400 text-[10px] font-semibold uppercase tracking-widest px-1'}
        {@const swGrid = 'grid grid-cols-2 gap-x-3 gap-y-2'}
        <div class="w-full flex flex-wrap gap-3 items-start">

          <fieldset class={swGroup}>
            <legend class={swLegend}>Activity</legend>
            <div class={swGrid}>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min active days</span>
                <input
                  type="number" min="1" max="90" step="1"
                  value={instance.swMinDays ?? 3}
                  onchange={(e) => (instance.swMinDays = Math.max(1, parseInt(e.currentTarget.value, 10) || 1))}
                  title="Minimum active (trade) days in the window for a wallet to be ranked"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min account age (d)</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMinAccountDuration ?? 0}
                  onchange={(e) => (instance.swMinAccountDuration = Math.max(0, parseInt(e.currentTarget.value, 10) || 0))}
                  title="Minimum days since the wallet's first recorded trade"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min trades/day</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMinTradesPerDay ?? 0}
                  onchange={(e) => (instance.swMinTradesPerDay = Math.max(0, parseFloat(e.currentTarget.value) || 0))}
                  title="Minimum trades per active day (window trades ÷ active days)"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Max trades/day</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMaxTradesPerDay ?? ''}
                  placeholder="∞"
                  onchange={(e) => { const v = parseFloat(e.currentTarget.value); instance.swMaxTradesPerDay = Number.isFinite(v) ? v : null; }}
                  title="Maximum trades per active day (blank = no limit). Low caps find discretionary, non-HFT wallets."
                  class={swCell}
                />
              </label>
              {#if !instance.swToken}
                <!-- Min tokens is meaningless in token scope (every wallet's
                     count is 1 there), so it's hidden when a token is selected. -->
                <label class="flex flex-col gap-1">
                  <span class={swLabel}>Min tokens</span>
                  <input
                    type="number" min="0" step="1"
                    value={instance.swMinTokens ?? 0}
                    onchange={(e) => (instance.swMinTokens = Math.max(0, parseInt(e.currentTarget.value, 10) || 0))}
                    title="Minimum number of distinct tokens traded in the window (tight vs wide scope)"
                    class={swCell}
                  />
                </label>
              {/if}
            </div>
          </fieldset>

          <fieldset class={swGroup}>
            <legend class={swLegend}>Size ($)</legend>
            <div class={swGrid}>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min volume ($)</span>
                <input
                  type="number" min="0" step="10000"
                  value={instance.swMinVolume ?? 0}
                  onchange={(e) => (instance.swMinVolume = Math.max(0, parseFloat(e.currentTarget.value) || 0))}
                  title="Minimum window volume (USD) for a wallet to be ranked"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min avg trade ($)</span>
                <input
                  type="number" min="0" step="50"
                  value={instance.swMinAvgTradeSize ?? 0}
                  onchange={(e) => (instance.swMinAvgTradeSize = Math.max(0, parseFloat(e.currentTarget.value) || 0))}
                  title="Minimum average trade size (volume ÷ trades, USD)"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min OI ($)</span>
                <input
                  type="number" min="0" step="10000"
                  value={instance.swMinOi ?? 0}
                  onchange={(e) => (instance.swMinOi = Math.max(0, parseFloat(e.currentTarget.value) || 0))}
                  title="Minimum open interest (USD), as of the snapshot, for a wallet to be ranked"
                  class={swCell}
                />
              </label>
            </div>
          </fieldset>

          <fieldset class={swGroup}>
            <legend class={swLegend}>Performance</legend>
            <div class={swGrid}>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min realized ($)</span>
                <input
                  type="number" step="1000"
                  value={instance.swMinRealized ?? 0}
                  onchange={(e) => (instance.swMinRealized = parseFloat(e.currentTarget.value) || 0)}
                  title="Minimum window realized PnL (USD) for a wallet to be ranked. 0 = profitable only; set negative to include losers."
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min win rate %</span>
                <input
                  type="number" min="0" max="100" step="5"
                  value={instance.swMinWinRate ?? 0}
                  onchange={(e) => (instance.swMinWinRate = Math.min(100, Math.max(0, parseFloat(e.currentTarget.value) || 0)))}
                  title="Minimum win rate — % of active trade days with positive total PnL"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min taker %</span>
                <input
                  type="number" min="0" max="100" step="5"
                  value={instance.swMinTakerPct ?? 0}
                  onchange={(e) => (instance.swMinTakerPct = Math.min(100, Math.max(0, parseFloat(e.currentTarget.value) || 0)))}
                  title="Minimum taker fill percentage (taker volume ÷ total fill volume)"
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Max fee/PnL %</span>
                <input
                  type="number" step="5"
                  value={instance.swMaxFeePct ?? ''}
                  placeholder="∞"
                  onchange={(e) => { const v = parseFloat(e.currentTarget.value); instance.swMaxFeePct = Number.isFinite(v) ? v : null; }}
                  title="Maximum fees as a % of realized PnL (blank = no limit). Only applies to profitable wallets."
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Max funding/PnL %</span>
                <input
                  type="number" step="5"
                  value={instance.swMaxFundingPct ?? ''}
                  placeholder="∞"
                  onchange={(e) => { const v = parseFloat(e.currentTarget.value); instance.swMaxFundingPct = Number.isFinite(v) ? v : null; }}
                  title="Maximum funding PnL as a % of realized PnL (blank = no limit). Filters out carry-dominated wallets."
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min Sharpe</span>
                <input
                  type="number" step="0.5"
                  value={instance.swMinAnnualizedSharpe ?? ''}
                  placeholder="−∞"
                  onchange={(e) => { const v = parseFloat(e.currentTarget.value); instance.swMinAnnualizedSharpe = Number.isFinite(v) ? v : null; }}
                  title="Minimum annualized Sharpe (×√365, OI-un-normalized — the table's ranking metric). Blank = no floor; Sharpe can be negative."
                  class={swCell}
                />
              </label>
            </div>
          </fieldset>

          <fieldset class={swGroup}>
            <!-- Market-share guards, in 0.01% units: 30 ⇒ 0.30% share. OI share
                 uses the wallet's window-AVERAGE OI; volume share its window
                 volume. Denominators are scoped to the selected token, or global. -->
            <legend class={swLegend} title="Units of 0.01% — 30 means 0.30% of the market">Market share <span class="text-zinc-600 normal-case tracking-normal font-normal">· 30 = 0.30%</span></legend>
            <div class={swGrid}>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min OI share</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMinAvgOiShare ?? 0}
                  onchange={(e) => (instance.swMinAvgOiShare = Math.max(0, parseFloat(e.currentTarget.value) || 0))}
                  title="Minimum average OI as a share of total OI, in 0.01% units (30 = 0.30%). Avg over the lookback window."
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Max OI share</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMaxAvgOiShare ?? ''}
                  placeholder="∞"
                  onchange={(e) => { const v = parseFloat(e.currentTarget.value); instance.swMaxAvgOiShare = Number.isFinite(v) ? v : null; }}
                  title="Maximum average OI share, in 0.01% units (blank = no limit). Excludes whales that dominate the book."
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Min vol share</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMinVolumeShare ?? 0}
                  onchange={(e) => (instance.swMinVolumeShare = Math.max(0, parseFloat(e.currentTarget.value) || 0))}
                  title="Minimum window volume as a share of total volume, in 0.01% units (30 = 0.30%)."
                  class={swCell}
                />
              </label>
              <label class="flex flex-col gap-1">
                <span class={swLabel}>Max vol share</span>
                <input
                  type="number" min="0" step="1"
                  value={instance.swMaxVolumeShare ?? ''}
                  placeholder="∞"
                  onchange={(e) => { const v = parseFloat(e.currentTarget.value); instance.swMaxVolumeShare = Number.isFinite(v) ? v : null; }}
                  title="Maximum window volume share, in 0.01% units (blank = no limit)."
                  class={swCell}
                />
              </label>
            </div>
          </fieldset>

        </div>
      {/if}
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
      {#if effectiveKind === 'hl_smart_oi'}
        {#if instance.kind === 'hl_smart_oi'}
        <!-- Saved-filter picker: selecting several AND-combines them into one
             effective wallet set (per-day intersection) and the chart shows a
             single OI series for it. Filters are created / edited on the
             Filters page; editing one re-fetches this chart automatically.
             (Dual-view chart mode gets its wallet set from the table's found
             rows, so the picker is hidden there.) -->
        <div class="basis-full flex flex-col gap-1.5 rounded-md border border-zinc-700 bg-zinc-900/40 p-2.5 text-xs">
          <div class="flex items-center gap-2">
            <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Filters</span>
            <span class="w-px h-4 bg-zinc-800"></span>
            <span class="text-zinc-500">multiple = AND (intersection)</span>
            <span class="flex-1"></span>
            <a
              href="/filters"
              class="text-zinc-400 hover:text-zinc-100 underline decoration-dotted"
              title="Create or edit filters"
            >Manage filters →</a>
          </div>
          {#if filtersStore.filters.length === 0}
            <div class="text-zinc-500">
              No saved filters yet. <a href="/filters" class="underline decoration-dotted hover:text-zinc-200">Create one</a> to plot smart-money OI.
            </div>
          {:else}
            <div class="flex flex-wrap gap-1.5">
              {#each filtersStore.filters as f (f.id)}
                {@const on = (instance.filterIds ?? []).includes(f.id)}
                <button
                  type="button"
                  onclick={() => {
                    const cur = instance.filterIds ?? [];
                    instance.filterIds = on ? cur.filter((x) => x !== f.id) : [...cur, f.id];
                  }}
                  class="rounded border px-2 py-1 transition-colors {on
                    ? 'border-emerald-600 bg-emerald-700/30 text-emerald-200'
                    : 'border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800'}"
                >{on ? '✓ ' : ''}{f.name}</button>
              {/each}
            </div>
          {/if}
        </div>
        {/if}
        <label
          class="flex items-center gap-1.5 text-zinc-300 cursor-pointer"
          title="Overlay a secondary-axis line showing how many wallets pass the criteria each day — spot over-filtering before it surprises you."
        >
          <input type="checkbox" bind:checked={instance.smartShowWalletCount} class="accent-zinc-400" />
          Show wallet count
        </label>
        {#if isDualViewKind(instance.kind)}
          <!-- Overlay the token's HL close price (secondary axis) so OI can be
               read against price moves. From hl_ohlcv at the chart interval. -->
          <label
            class="flex items-center gap-1.5 text-zinc-300 cursor-pointer"
            title="Overlay the token's HL close price on the secondary axis"
          >
            <input type="checkbox" bind:checked={instance.swShowClose} class="accent-zinc-400" />
            Show close price
          </label>
        {/if}
        <!-- Same instance.oiUnit field as the toolbar dropdown — duplicated
             here so the display-unit choice sits with the other smart-OI
             chart controls. Hidden in long_to_short / net_pct where the
             unit is mathematically meaningless. -->
        {#if (instance.oiHlDisplay ?? 'total') !== 'long_to_short' && (instance.oiHlDisplay ?? 'total') !== 'net_pct' && (instance.oiHlDisplay ?? 'total') !== 'count'}
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
      {#if instance.kind === 'book_depth' && instance.bookDepthMode === 'per_level_imbalance'}
        <!-- Per-band series toggles. Each band's imbalance line can be shown /
             hidden independently; colour swatch matches the plotted line.
             Clearing the last one re-shows all (see bdToggleBand). -->
        <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Bands</span>
        {#each BD_BANDS as b, i (b.bid)}
          <label class="flex items-center gap-1.5 text-zinc-300 cursor-pointer" title="Toggle the {b.label} imbalance series">
            <input
              type="checkbox"
              checked={bdBandOn(b.bid)}
              onchange={() => bdToggleBand(b.bid)}
              class="accent-zinc-400"
            />
            <span class="inline-block w-2.5 h-2.5 rounded-sm" style="background: {BD_BAND_COLORS[i]}; opacity: {bdBandOn(b.bid) ? 1 : 0.4}"></span>
            {b.label}
          </label>
        {/each}
        <span class="w-px h-4 bg-zinc-800"></span>
      {/if}
      {#if !isTableviewKind}
      <label class="flex items-center gap-1.5 text-zinc-300 cursor-pointer">
        <input
          type="checkbox"
          bind:checked={instance.showPoint}
          onchange={() => {
            // book_depth single-MA modes (share stacks + imbalance): Point and
            // MA are mutually exclusive — turning Point back on drops the MA.
            if (instance.showPoint && bdMaActive) instance.mas[0].enabled = false;
          }}
          class="accent-zinc-400"
        />
        Point
      </label>
      <label
        class="flex items-center gap-1.5 text-zinc-300 cursor-pointer"
        title="Dotted vertical lines at the start of each Saturday and Monday (UTC)"
      >
        <input type="checkbox" bind:checked={instance.showWeekLines} class="accent-zinc-400" />
        Week lines
      </label>
      {#if syncZoom}
        <span class="w-px h-4 bg-zinc-800"></span>
        <label
          class="flex items-center gap-1.5 text-zinc-300 cursor-pointer"
          title="Zoom/pan this chart on its own — it won't follow or drive the shared zoom of the other charts"
        >
          <input
            type="checkbox"
            checked={instance.noSync ?? false}
            onchange={(e) => {
              instance.noSync = e.currentTarget.checked;
              // Seed the local view from the current shared one so excluding
              // the chart doesn't snap it back to its full-window default.
              if (instance.noSync && sharedView) localView = sharedView;
            }}
            class="accent-zinc-400"
          />
          Exclude from zoom sync
        </label>
      {/if}
      {#if instance.kind === 'transfer' || instance.kind === 'exchange_flow' || isAaveV3Kind(instance.kind) || isAaveV2Kind(instance.kind) || isAaveV4Kind(instance.kind) || isMorphoKind(instance.kind) || isSparkKind(instance.kind) || isLidoKind(instance.kind) || (isUniswapV3Kind(instance.kind) && effectiveKind !== 'uniswap_v3_net_swap_flow') || isUniswapV2Kind(instance.kind) || effectiveKind === 'uniswap_v4_swap' || isAeroClKind(instance.kind) || isAeroBasicKind(instance.kind)}
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
      {#if instance.kind === 'book_depth'}
        {#if bdSingleMaMode}
          <!-- Single moving average for the share stacks and the imbalance bar.
               Share modes: applied per band to each band's share series, then
               re-stacked (bdShareMaData) so the chart stays a 100% stack.
               Imbalance: applied to the whole-book imbalance series, replacing
               the bars with the MA line. Either way, enabling it deselects
               Point (Point/MA are mutually exclusive). No Sum option. -->
          <span class="w-px h-4 bg-zinc-800"></span>
          <div class="flex items-center gap-1.5">
            <label class="flex items-center gap-1.5 cursor-pointer" title="Smooth with a moving average; replaces the points/bars (mutually exclusive with Point)">
              <input
                type="checkbox"
                checked={instance.mas[0].enabled}
                onchange={(e) => {
                  instance.mas[0].enabled = e.currentTarget.checked;
                  instance.showPoint = !e.currentTarget.checked;
                }}
                class="accent-zinc-400"
              />
              <span
                class="font-medium"
                style="color: {MA_COLORS[0]}; opacity: {instance.mas[0].enabled ? 1 : 0.55}"
              >MA</span>
            </label>
            <input
              type="number"
              bind:value={instance.mas[0].length}
              min="2"
              max="500"
              step="1"
              title="Length"
              class="w-14 bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
            />
            <select
              bind:value={instance.mas[0].type}
              title="Type"
              class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
            >
              <option value="sma">SMA</option>
              <option value="ema">EMA</option>
              <option value="wma">WMA</option>
            </select>
          </div>
        {/if}
      {:else}
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
      {/if}
      {/if}
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
          Base tokens
          <span class="text-zinc-600 normal-case">
            — {instance.token} is shown relative to each (one {instance.token} / base price-ratio line per base)
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
                <option value="">+ add base token…</option>
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

    <!-- Indeterminate load strip — visible whenever a fetch is in flight: a
         primary load (chain/token/interval/filter change) or an hl_smart_oi
         backfill of older history (loadingMore). -->
    <div class="loadbar h-0.5 overflow-hidden bg-blue-500/10" aria-hidden="true">
      {#if loading || loadingMore}
        <div class="loadbar-track"></div>
      {/if}
    </div>

    {#if error}
      <div class="p-3 text-xs text-red-300 bg-red-950/30">{error}</div>
    {/if}
    {#if data.length === 0 && loading && !(isSwKind(instance.kind) && instance.viewMode !== 'chart')}
      <div class="p-4 text-sm text-zinc-400 flex items-center gap-2">
        <svg class="animate-spin h-4 w-4 text-zinc-500" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        </svg>
        Loading {chartKindGroup(effectiveKind) ? `${chartKindGroup(effectiveKind)} ${chartKindShortLabel(effectiveKind)}` : kindLabel}…
      </div>
    {:else if data.length === 0 && !(isSwKind(instance.kind) && instance.viewMode !== 'chart')}
      <!-- smart_wallets_table (table view) is excluded here so it ALWAYS renders
           its own component even with no rows yet — the finder is refresh-only,
           and the user needs its metric / lookback / token / snapshot chrome to
           configure a run before clicking refresh. Its component shows the
           "click ↻ to run" hint in the body. -->
      <div class="p-4 text-sm text-zinc-400">
        {#if instance.kind === 'hl_smart_oi' && !smartHasValidFilter}
          {#if (instance.filterIds ?? []).length === 0}
            No filter selected. Pick one or more saved filters in this chart's
            settings, or <a href="/filters" class="underline decoration-dotted hover:text-zinc-200">create a filter</a>.
          {:else}
            The selected filter{(instance.filterIds ?? []).length > 1 ? 's are' : ' is'} missing or broken
            (a referenced filter was deleted). Fix it on the <a href="/filters" class="underline decoration-dotted hover:text-zinc-200">Filters page</a>.
          {/if}
        {:else if instance.token && instance.chain && (instance.kind === 'transfer' || instance.kind === 'exchange_flow')}
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
        formatVolume={(instance.volumeUnit ?? 'usd') === 'usd'
          ? fmtUsdCompact
          : fmtAmountTooltip}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
      />
    {:else if instance.kind === 'pc'}
      <!-- Relative price: chart token / base token ratios (one line per base). -->
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
        formatY={fmtRatio}
        formatTooltip={(v) => v.toPrecision(5)}
      />
    {:else if effectiveKind === 'oi' || effectiveKind === 'hl_smart_oi'}
      <!-- HL Long/Short ratio is unitless (1.03, not $1.03). Otherwise USD
           or token amount based on the oiUnit selector. hl_smart_oi (incl.
           smart_wallets_table chart mode) reuses the same rendering — its
           payload shape matches /oi_split. -->
      {@const oiHlMode = (effectiveKind === 'hl_smart_oi'
                          || (instance.exchange ?? 'binance') === 'hl')
                          ? (instance.oiHlDisplay ?? 'total') : null}
      {@const oiIsRatio = oiHlMode === 'long_to_short'}
      {@const oiIsPct = oiHlMode === 'net_pct'}
      {@const oiIsNet = oiHlMode === 'net'}
      {@const oiIsCount = oiHlMode === 'count'}
      {@const oiUseToken = (instance.oiUnit ?? 'usd') === 'token' && !oiIsRatio && !oiIsPct && !oiIsCount}
      {@const showWalletCount = effectiveKind === 'hl_smart_oi' && (instance.smartShowWalletCount ?? false)}
      {@const showClose = effectiveKind === 'hl_smart_oi' && (instance.swShowClose ?? false)}
      <LineChart
        data={data as OpenInterestRow[]}
        lines={oiLinesM}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={chartVRefLines}
        refLines={(oiIsNet || oiIsPct) ? ZERO_REF : []}
        formatY={oiIsRatio ? fmtRatio
                 : oiIsPct ? ((v: number) => `${(v >= 0 ? '+' : '')}${(v * 100).toFixed(1)}%`)
                 : oiIsCount ? ((v: number) => Math.round(v).toLocaleString())
                 : (oiUseToken ? fmtAmountAxis : fmtUsdAxis)}
        formatTooltip={oiIsRatio ? fmtRatio
                 : oiIsPct ? ((v: number) => `${(v >= 0 ? '+' : '')}${(v * 100).toFixed(2)}%`)
                 : oiIsCount ? ((v: number) => Math.round(v).toLocaleString() + ' wallets')
                 : (oiUseToken ? fmtAmountTooltip : fmtUsdTooltip)}
        formatY2={showClose ? fmtPriceAxis
                 : showWalletCount ? ((v: number) => Math.round(v).toString()) : undefined}
        formatTooltip2={showClose ? fmtPriceTooltip
                 : showWalletCount ? ((v: number) => `${Math.round(v)} wallets`) : undefined}
        onClick={effectiveKind === 'hl_smart_oi'
          ? ((t: number) => openTopOiDialog(t))
          : undefined}
      />
    {:else if instance.kind === 'volume'}
      <!-- Traded volume per bucket. USD notional or token amount per the
           volumeUnit toggle, on the same line-chart path as OI. -->
      {@const volUseToken = (instance.volumeUnit ?? 'usd') === 'token'}
      <LineChart
        data={data as Candle[]}
        lines={volumeLinesD}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={volUseToken ? fmtAmountAxis : fmtUsdAxis}
        formatTooltip={volUseToken ? fmtAmountTooltip : fmtUsdTooltip}
      />
    {:else if instance.kind === 'realized_price'}
      <!-- Realized (cumulative-VWAP / avg-entry) price of Binance spot. 'price'
           mode: realized + current price on the USD axis. 'diff' mode: realized
           price (USD) + the % of current vs realized on a secondary axis. -->
      {@const rpDiffOnly = instance.rpMode === 'diff_only'}
      <LineChart
        data={data as unknown as Array<{ time: number; realized_price?: number; current_price?: number }>}
        lines={rpLinesD}
        refLines={instance.rpMode === 'diff'
          ? [{ value: 0, axis: 'secondary', color: '#52525b', label: '0%' }]
          : rpDiffOnly ? [{ value: 0, color: '#52525b', label: '0%' }] : []}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={rpDiffOnly ? ((v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`) : fmtPriceAxis}
        formatTooltip={rpDiffOnly ? ((v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`) : fmtPriceTooltip}
        formatY2={instance.rpMode === 'diff' ? ((v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`) : undefined}
        formatTooltip2={instance.rpMode === 'diff' ? ((v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`) : undefined}
      />
    {:else if instance.kind === 'spot_cvd'}
      <!-- Spot CVD (Binance spot taker buy − sell). Cumulative = running-sum
           line; Periodic = per-bucket signed bars (green = net buy, red = net
           sell). USD or token units. -->
      {@const cvdUseToken = instance.cvdUnit === 'token'}
      {#if (instance.cvdMode ?? 'cumulative') === 'periodic'}
        <SignedBarChart
          data={data as unknown as Array<{ time: number; value: number }>}
          valueKey="value"
          lines={overlayLinesD}
          showBars={true}
          valueLabel="CVD"
          height={chartCanvasHeight}
          {xExtent}
          view={effectiveView}
          onView={handleView}
          hoverTime={effectiveHoverTime}
          onHover={handleHover}
          vRefLines={weekVRefLines}
          formatY={cvdUseToken ? fmtAmountAxis : fmtUsdAxis}
          formatTooltip={cvdUseToken ? fmtAmountTooltip : fmtUsdTooltip}
          minBarWidthPx={3}
        />
      {:else}
        <LineChart
          data={data as unknown as Array<{ time: number; value?: number }>}
          lines={cvdLinesM}
          refLines={[{ value: 0, color: '#52525b', label: '0' }]}
          height={chartCanvasHeight}
          {xExtent}
          view={effectiveView}
          onView={handleView}
          hoverTime={effectiveHoverTime}
          onHover={handleHover}
          vRefLines={weekVRefLines}
          formatY={cvdUseToken ? fmtAmountAxis : fmtUsdAxis}
          formatTooltip={cvdUseToken ? fmtAmountTooltip : fmtUsdTooltip}
        />
      {/if}
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
    {:else if instance.kind === 'ps'}
      <!-- Perp vs Spot (Binance). 'volume' shows just the spot/perp volume-ratio
           line; 'basis' / 'all' show the perp−spot close basis as signed bars
           (pp), with the volume-ratio line on a secondary axis when 'all'. -->
      {@const psMode = instance.psSeries ?? 'all'}
      {#if psMode === 'volume'}
        <LineChart
          data={psData}
          lines={psVolLinePrimary}
          height={chartCanvasHeight}
          {xExtent}
          view={effectiveView}
          onView={handleView}
          hoverTime={effectiveHoverTime}
          onHover={handleHover}
          vRefLines={weekVRefLines}
          formatY={(v) => `${v.toFixed(0)}%`}
          formatTooltip={(v) => `${v.toFixed(1)}%`}
        />
      {:else}
        <SignedBarChart
          data={psData}
          valueKey="basis_pp"
          lines={psMode === 'all' ? psVolLineSecondary : []}
          showBars={true}
          valueLabel="Basis"
          height={chartCanvasHeight}
          {xExtent}
          view={effectiveView}
          onView={handleView}
          hoverTime={effectiveHoverTime}
          onHover={handleHover}
          vRefLines={weekVRefLines}
          formatY={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}pp`}
          formatY2={(v) => `${v.toFixed(0)}%`}
          formatTooltip={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(3)} pp`}
        />
      {/if}
    {:else if instance.kind === 'book_depth' && (instance.bookDepthMode ?? 'totals') === 'totals'}
      <LineChart
        data={data as Record<string, number>[]}
        lines={bdTotalsLinesM}
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
    {:else if instance.kind === 'book_depth' && instance.bookDepthMode === 'per_level_imbalance'}
      <LineChart
        data={data as Record<string, number>[]}
        lines={bdPerLevelImbalanceLines}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => v.toFixed(0) + '%'}
        formatTooltip={(v) => v.toFixed(2) + '%'}
      />
    {:else if instance.kind === 'book_depth' && instance.bookDepthMode === 'imbalance'}
      <SignedBarChart
        data={bdImbalanceData}
        valueKey="imb"
        showBars={bdImbalanceMaActive ? false : instance.showPoint}
        lines={bdImbalanceMaActive ? bdImbalanceMaLine : []}
        valueLabel="Imbalance"
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => v.toFixed(0) + '%'}
        formatTooltip={(v) => v.toFixed(2) + '%'}
      />
    {:else if instance.kind === 'book_depth' && instance.bookDepthMode === 'stacked'}
      <StackedBarChart
        data={data as Record<string, number>[]}
        series={bdStackedSeries}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
      />
    {:else if instance.kind === 'book_depth' && BD_SHARE_MODES.includes(instance.bookDepthMode ?? '')}
      <StackedBarChart
        data={bdShareMaActive ? bdShareMaData : bdShareData}
        series={bdShareSeries}
        valueFormat="pct"
        pctMax={instance.bookDepthMode === 'asks_bids_share' ? 200 : 100}
        lines={instance.bookDepthMode === 'asks_bids_share' ? bdMidLine : []}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
      />
    {:else if instance.kind === 'bs' && bsMode === 'pct'}
      <!-- Taker buyer/seller share: two lines (% Buyer, % Seller), summing to
           100; 50 = balanced. -->
      <LineChart
        data={data as VolumeBucket[]}
        lines={bsPctLinesM}
        refLines={[{ value: 50 }]}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => `${v.toFixed(0)}%`}
        formatTooltip={(v) => `${v.toFixed(1)}%`}
      />
    {:else if instance.kind === 'bs' && bsMode === 'imbalance'}
      <!-- Taker imbalance: % Buyer − % Seller, one line in [−100, +100];
           0 = balanced (dashed ref). MA overlays via bsImbalanceLinesM. -->
      <LineChart
        data={data as VolumeBucket[]}
        lines={bsImbalanceLinesM}
        refLines={[{ value: 0, color: '#52525b' }]}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`}
        formatTooltip={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
      />
    {:else if instance.kind === 'bs' && bsMode === 'ratio'}
      <!-- Taker Buyer/Seller ratio only: a single line, 1 = balanced. -->
      <LineChart
        data={data as VolumeBucket[]}
        lines={bsRatioLinesM}
        refLines={NEUTRAL_REF}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
        formatY={(v) => v.toFixed(2)}
        formatTooltip={(v) => v.toFixed(3)}
      />
    {:else if instance.kind === 'bs'}
      <!-- stacked / both: opaque buyer+seller $ bars; 'both' adds the ratio
           line on the secondary axis (via bsLinesM). -->
      <StackedBarChart
        data={data as VolumeBucket[]}
        series={bsBars}
        lines={bsLinesM}
        valueFormat={bsUnit === 'token' ? 'token' : 'usd'}
        bars
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        vRefLines={weekVRefLines}
      />
    {:else if instance.kind === 'sz'}
      <!-- Volume by Size. 'total': independent bucket lines (small / mid / large
           $). 'taker_split': the chosen bracket split into buyer-taker vs
           seller-taker $ for comparison. -->
      <LineChart
        data={data as VolumeBucket[]}
        lines={instance.szMode === 'taker_split' ? szTakerSplitLinesM : szLinesM}
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
        formatY2={instance.showSum ? (exchangeFlowUseUsd ? fmtUsdAxis : fmtAmountAxis) : undefined}
        formatTooltip2={instance.showSum ? (exchangeFlowUseUsd ? fmtUsdTooltip : fmtAmountTooltip) : undefined}
      />
    {:else if isSwKind(instance.kind) && instance.viewMode === 'token_list'}
      <SmartWalletTokenListTable
        rows={data.length > 0 ? ((data[0] as unknown as {tokens?: Record<string, unknown>[]}).tokens ?? []) : []}
        unit={instance.swtUnit ?? 'usd'}
        loading={loading}
        error={error}
        onSelectToken={(tok) => openTopOiDialog(null, tok)}
      />
    {:else if isSwKind(instance.kind)}
      <SmartWalletMetricsTable
        rows={data.length > 0 ? ((data[0] as unknown as {wallets?: import('$lib/components/SmartWalletMetricsTable.svelte').SmartWalletRow[]}).wallets ?? []) : []}
        total={data.length > 0 ? ((data[0] as unknown as {total?: number}).total ?? 0) : 0}
        {tokens}
        metric={(instance.swMetric ?? 'sharpe') as SmartWalletMetric}
        lookback={(instance.swLookback ?? 7) as SmartWalletLookback}
        lookbacks={instance.kind === 'smart_wallets_dynamic' ? SMART_WALLET_DYNAMIC_LOOKBACKS : SMART_WALLET_LOOKBACKS}
        token={instance.swToken ?? null}
        snapshot={swSnapshotIso()}
        dynamic={instance.kind === 'smart_wallets_dynamic'}
        cutoff={isCutoff}
        cutoffOptions={SMART_WALLET_CUTOFF_LOOKBACKS}
        cutoffLookbacks={instance.swCutoffLookbacks ?? [...SMART_WALLET_CUTOFF_LOOKBACKS]}
        cutoffCombine={instance.swCutoffCombine ?? 'union'}
        onChangeCutoffCombine={(m) => (instance.swCutoffCombine = m)}
        rowLimit={instance.swRowLimit ?? 100}
        groupMode={isGroup}
        groups={walletPinsStore.groups.map((g) => ({ id: g.id, name: g.name }))}
        selectedGroup={instance.swGroupId ?? 'default'}
        onChangeGroup={(id) => (instance.swGroupId = id)}
        onChangeMetric={(m) => (instance.swMetric = m)}
        onChangeLookback={(l) => (instance.swLookback = l)}
        onToggleCutoffLookback={(l) => {
          const cur = instance.swCutoffLookbacks ?? [...SMART_WALLET_CUTOFF_LOOKBACKS];
          const next = cur.includes(l) ? cur.filter((x) => x !== l) : [...cur, l];
          if (next.length > 0) instance.swCutoffLookbacks = next.sort((a, b) => a - b);
        }}
        onChangeRowLimit={(n) => (instance.swRowLimit = n)}
        onChangeToken={(t) => {
          instance.swToken = t;
          // Keep the chart's OI token in lockstep with the table's selection
          // scope: when a specific token is picked (not ALL), the chart should
          // plot that same token. ALL (t === null) leaves the chart token as-is
          // since the OI chart needs one concrete token to plot.
          if (t) instance.token = t;
        }}
        onChangeSnapshot={(iso) => {
          if (isCutoff) instance.swCutoffDate = iso;
          else instance.swSnapshot = iso;
        }}
        loading={loading}
        error={error}
        notRun={!swArmed}
      />
    {:else if instance.kind === 'token_leaderboard'}
      <TokenLeaderboardTable
        rows={data.length > 0 ? ((data[0] as unknown as {tokens?: Record<string, unknown>[]}).tokens ?? []) : []}
        loading={loading}
        error={error}
      />
    {:else if instance.kind === 'spot_cvd_table'}
      <SpotCvdTable
        rows={data.length > 0 ? ((data[0] as unknown as {tokens?: Record<string, unknown>[]}).tokens ?? []) : []}
        loading={loading}
        error={error}
        unit={instance.cvdtUnit ?? 'usd'}
        multi={(instance.cvdtLookback ?? 'all') === 'all'}
        lookbackLabel={(instance.cvdtLookback ?? 'all') === 'all' ? 'All' : `${instance.cvdtLookback}d`}
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

  {#if isDualViewKind(instance.kind) && instance.viewMode === 'chart' && swFoundTotal > 0 && swFoundTotalKey === swTableKey()}
    <!-- Found-wallet count for the chart view: the size of the selected set the
         OI is aggregated over (carried from the table view's fetch). Tagged with
         the selection key so it hides instead of showing a stale value if the
         filters change before the table reloads. Top-left so it doesn't collide
         with the loading badge (top-right) or backfill pill (bottom-left). -->
    <div class="pointer-events-none absolute top-1 left-1 z-10 rounded border border-zinc-700 bg-zinc-900/90 px-2 py-1 text-sm font-semibold text-zinc-200 shadow">
      {swFoundTotal.toLocaleString()} wallets
    </div>
  {/if}

  {#if isDualViewKind(instance.kind) && instance.viewMode === 'chart' && !loading && data.length === 0}
    <!-- No chart yet — say why instead of leaving a black (empty) canvas:
         either no token is picked, or the found wallets hold no OI for the
         chosen token/window. -->
    <div class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center p-4 text-center">
      <div class="rounded-lg border border-zinc-700 bg-zinc-900/90 px-4 py-3 text-sm text-zinc-300 shadow-lg">
        {#if !instance.token}
          Select a token to plot the found wallets' open interest.
        {:else}
          No <span class="font-semibold text-zinc-100">{instance.token}</span> open interest for the
          {swFoundTotal > 0 ? swFoundTotal.toLocaleString() + ' found wallets' : 'found wallets'}
          over this window.<br />
          <span class="text-zinc-500">Try another token, a wider lookback, or a different snapshot.</span>
        {/if}
      </div>
    </div>
  {/if}

  {#if loadingMore}
    <!-- hl_smart_oi backfill indicator. Sits bottom-left (the FAB owns
         bottom-right) so the user knows older history is streaming in while
         they pan/zoom; the chart stays interactive underneath. -->
    <div class="backfill-pill" aria-live="polite">
      <svg class="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-opacity="0.25"/>
        <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
      </svg>
      Loading older history…
    </div>
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
  asOfMetrics={walletsDialogAsOf}
  walletMetrics={walletsDialogMetrics}
  walletPositions={walletsDialogPositions}
  loading={walletsDialogLoading}
  error={walletsDialogError}
  day={walletsDialogDay}
  token={walletsDialogToken || instance.token || ''}
  onClose={() => { walletsDialogOpen = false; if (walletsFetchCtl) walletsFetchCtl.abort(); }}
/>

<style>
  /* hl_smart_oi backfill pill — a small translucent badge in the bottom-left
     of the chart canvas, shown while older history is being fetched. Bottom-
     left so it clears the overlay FAB (bottom-right). */
  .backfill-pill {
    position: absolute;
    bottom: 0.5rem;
    left: 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.2rem 0.5rem;
    font-size: 0.6875rem;                            /* text-[11px] */
    line-height: 1;
    color: rgb(212 212 216);                         /* zinc-300 */
    background: rgb(24 24 27 / 0.85);                /* zinc-900/85 */
    border: 1px solid rgb(63 63 70);                 /* zinc-700 */
    border-radius: 9999px;
    pointer-events: none;                            /* never block pan/zoom */
    z-index: 15;
    backdrop-filter: blur(2px);
  }

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
