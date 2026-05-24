<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import LineChart from '$lib/components/LineChart.svelte';
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
    fmtUsdAxis,
    fmtUsdTooltip,
    lookbackWindow,
    maArray,
    sizeLines,
    sizeSeries,
    unixSec,
    type ChartHeight,
    type ChartInstance as ChartInstanceT,
    type ChartWidth,
    type TransferFilters
  } from '$lib/components/charts/config';
  import type { View } from '$lib/chart-zoom';

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
    tokenGroups = [],
    chainGroups = [],
    syncZoom,
    sharedView,
    sharedHoverTime,
    onSharedView,
    onSharedHover,
    onTokenChange,
    onRemove
  }: {
    instance: ChartInstanceT;
    tokens: string[];
    streams?: TransferStream[];
    tokenGroups?: import('$lib/api').TokenGroup[];
    chainGroups?: import('$lib/api').ChainGroup[];
    syncZoom: boolean;
    sharedView: View;
    sharedHoverTime: number | null;
    onSharedView: (v: View) => void;
    onSharedHover: (t: number | null) => void;
    onTokenChange: (id: string, token: string) => void;
    onRemove: (id: string) => void;
  } = $props();

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
        fetch('/api/transfers/categories'),
        fetch('/api/transfers/entities')
      ]);
      if (catsRes.ok) walletCategories = (await catsRes.json()).categories ?? [];
      if (entsRes.ok) walletEntities = (await entsRes.json()).entities ?? [];
    } catch {
      // ignore — inputs still work without autocomplete
    }
  });

  // ---- transfer "extra series" form state ----
  type FilterKey =
    | 'sender_in' | 'sender_ex'
    | 'receiver_in' | 'receiver_ex'
    | 'involving_in' | 'involving_ex'
    | 'sender_entity_in' | 'sender_entity_ex'
    | 'receiver_entity_in' | 'receiver_entity_ex'
    | 'involving_entity_in' | 'involving_entity_ex';
  const CAT_FILTER_KEYS: FilterKey[] = [
    'sender_in', 'sender_ex',
    'receiver_in', 'receiver_ex',
    'involving_in', 'involving_ex'
  ];
  const ENT_FILTER_KEYS: FilterKey[] = [
    'sender_entity_in', 'sender_entity_ex',
    'receiver_entity_in', 'receiver_entity_ex',
    'involving_entity_in', 'involving_entity_ex'
  ];
  const FILTER_KEYS: FilterKey[] = [...CAT_FILTER_KEYS, ...ENT_FILTER_KEYS];
  const EMPTY_PENDING: Record<FilterKey, string> = {
    sender_in: '',
    sender_ex: '',
    receiver_in: '',
    receiver_ex: '',
    involving_in: '',
    involving_ex: '',
    sender_entity_in: '',
    sender_entity_ex: '',
    receiver_entity_in: '',
    receiver_entity_ex: '',
    involving_entity_in: '',
    involving_entity_ex: ''
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
    if (f.sender_in?.length) parts.push(`from_${f.sender_in.join('+')}`);
    if (f.sender_ex?.length) parts.push(`from_not_${f.sender_ex.join('+')}`);
    if (f.receiver_in?.length) parts.push(`to_${f.receiver_in.join('+')}`);
    if (f.receiver_ex?.length) parts.push(`to_not_${f.receiver_ex.join('+')}`);
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
  let since = $state<string>(new Date(0).toISOString());
  let until = $state<string>(new Date(0).toISOString());
  let loadedKey = $state<string>('');
  let localView = $state<View>(null);
  let localHoverTime = $state<number | null>(null);
  let collapsed = $state(false);
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

  // ---- loader: dispatch on kind ----

  function transferFilterKey(): string {
    const f = instance.filter ?? {};
    return FILTER_KEYS.map((k) => (f[k as FilterKey] ?? []).join(',')).join('|');
  }

  function loadKey(): string {
    if (instance.kind === 'sz') {
      return `${instance.kind}|${instance.token}|${instance.interval}|${instance.under ?? 0}|${instance.over ?? 0}`;
    }
    if (instance.kind === 'transfer') {
      // Key encodes whether each axis is singleton or group so cache busts
      // when the user toggles between e.g. ETH-USDC and EVM-USDC.
      const cPart = activeChainGroup ? `cg:${activeChainGroup.name}` : (instance.chain ?? '');
      const tPart = activeTokenGroup !== null ? `tg:${activeTokenGroup}` : instance.token;
      return `${instance.kind}|${cPart}|${tPart}|${instance.interval}|${transferFilterKey()}`;
    }
    return `${instance.kind}|${instance.token}|${instance.interval}`;
  }

  $effect(() => {
    const key = loadKey();
    if (key === loadedKey) return;
    void load();
  });

  /** Single fetch — the main line IS the (possibly filtered) series. The six
   *  legacy single-filter params on /transfers/aggregate apply pre-aggregation
   *  in CH, so the returned `sum_amount` is the filtered sum. MAs then compute
   *  from those filtered values automatically. */
  async function loadTransferMerged(sinceIso: string, untilIso: string) {
    const qs = new URLSearchParams({
      interval: instance.interval,
      since: sinceIso,
      until: untilIso,
      limit: '10000'
    });
    // Each axis is either a singleton (chain= / token=) or a group
    // (chain_group= / token_group=). Both can be set independently; the
    // backend cross-products them against the streams catalogue.
    if (activeChainGroup) {
      qs.set('chain_group', activeChainGroup.name);
    } else {
      qs.set('chain', instance.chain ?? 'ETH');
    }
    if (activeTokenGroup !== null) {
      qs.set('token_group', activeTokenGroup);
    } else {
      qs.set('token', instance.token);
      // `kind` only matters in the all-singleton path — it disambiguates
      // (TRON, trc20, USDT) vs (TRON, tron_native, TRX). When either axis
      // is a group the backend resolves `kind` per-pair itself.
      if (!activeChainGroup) qs.set('kind', transferKind);
    }
    const f = instance.filter ?? {};
    for (const k of FILTER_KEYS) {
      const arr = f[k as FilterKey] ?? [];
      if (arr.length) qs.set(k, arr.join(','));
    }
    const res = await fetch(`/api/transfers/aggregate?${qs}`);
    if (!res.ok) throw new Error(`transfers ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    const out: Record<string, number>[] = rows.map((b) => ({
      time: b.time,
      main: b.sum_amount,
      sum_amount: b.sum_amount,
      sum_value_usd: b.sum_value_usd,
      count: b.count
    }));
    data = out as unknown as AnyDatum[];
  }

  async function load() {
    error = null;
    try {
      // Transfer kind uses a fixed 30-day window regardless of interval; other kinds use
      // the per-interval lookback window.
      let sinceIso: string;
      let untilIso: string;
      if (instance.kind === 'transfer') {
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
      switch (instance.kind) {
        case 'ohlcv':
          url = `/api/ohlcv?${new URLSearchParams(baseQS)}`;
          pickArr = (b) => (b.candles ?? []) as AnyDatum[];
          break;
        case 'oi':
          url = `/api/open_interest?${new URLSearchParams(baseQS)}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        case 'fr':
          url = `/api/funding_rate?${new URLSearchParams(baseQS)}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        case 'tt':
        case 'ls':
          url = `/api/long_short_ratios?${new URLSearchParams(baseQS)}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
        case 'bs':
          url = `/api/trade_volume?${new URLSearchParams({
            ...baseQS,
            under: '10000',
            over: '100000'
          })}`;
          pickArr = (b) => (b.buckets ?? []) as AnyDatum[];
          break;
        case 'sz':
          url = `/api/trade_volume?${new URLSearchParams({
            ...baseQS,
            under: String(instance.under ?? 10000),
            over: String(instance.over ?? 100000)
          })}`;
          pickArr = (b) => (b.buckets ?? []) as AnyDatum[];
          break;
        case 'transfer': {
          // Transfer kind does its own multi-fetch + merge so the chart can show
          // a main (unfiltered) line alongside up to MAX_EXTRA_SERIES filtered
          // overlays. Skip the single-URL pickArr path below.
          await loadTransferMerged(sinceIso, untilIso);
          loadedKey = loadKey();
          localView = defaultView(sinceIso, untilIso);
          since = sinceIso;
          until = untilIso;
          return;
        }
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
      const body = await res.json();
      data = pickArr(body);
      since = sinceIso;
      until = untilIso;
      loadedKey = loadKey();
      localView = defaultView(sinceIso, untilIso);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  // ---- derived series / lines / extra computed data ----
  let frBpsData = $derived(
    instance.kind === 'fr'
      ? (data as FundingRateRow[]).map((d) => ({ ...d, rate_bps: d.rate * 10000 }))
      : []
  );

  // Per-MA sub-line dash patterns (for kinds where one MA config emits multiple lines).
  const SUB_DASH = ['5,3', '2,2', '6,2,2,2'];

  let anyMaEnabled = $derived(instance.mas.some((m) => m.enabled));

  let cumulativeLines = $derived.by(() => {
    if (data.length === 0 || !anyMaEnabled) return [] as unknown[];
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
          const arrMa = maArray(arr.map((b) => b.sum_amount), ma.length, ma.type);
          out.push({
            key: `cum_transfer_${idx}`,
            label: `Amount ${tag}`,
            color,
            dash: SUB_DASH[0],
            compute: (_d: TransferBucket, i: number) => arrMa[i]
          });
          break;
        }
      }
    }
    return out;
  });

  // Transfer-kind single line: whatever the (optionally filtered) sum is.
  let transferMainLabel = $derived(activeFilterIsAny ? activeFilterLabel : 'Total');
  let transferLinesD = $derived([
    ...(instance.showPoint
      ? [{
          key: 'main',
          label: transferMainLabel,
          color: '#06b6d4',
          compute: (d: TransferBucket & Record<string, number>) =>
            d.main ?? d.sum_amount ?? 0
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
  let ohlcvLinesD = $derived(cumulativeLines);
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

  // ---- width toggle ----
  // Chart canvas height in px — driven by instance.height (1 row vs 2 rows)
  // and instance.width (because a 1-col panel uses a taller two-row header so
  // less vertical space is left for the chart canvas).
  let chartCanvasHeight = $derived(
    instance.height === 1
      ? instance.width === 1
        ? 240
        : 270
      : instance.width === 1
        ? 510
        : 540
  );

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
    `${displayTitle} — ${instance.token} ${instance.interval}` +
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
      'px-4 py-2 border-b border-zinc-800 bg-gradient-to-b from-zinc-900/40 to-transparent',
      // 1×1 stacks title above controls; bigger sizes keep them side-by-side.
      instance.width === 1
        ? 'flex flex-col items-stretch gap-1.5'
        : 'flex items-center justify-between gap-3'
    ].join(' ')}
  >
    <!-- Title block -->
    <button
      type="button"
      onclick={() => (collapsed = !collapsed)}
      title="Drag to reorder · Click to collapse"
      class="cursor-grab active:cursor-grabbing flex items-center gap-2 min-w-0 text-left"
    >
      <span class="text-zinc-500 text-base leading-none select-none">⠿</span>
      <span class="text-zinc-500 text-[10px] w-3 inline-block text-center leading-none">
        {collapsed ? '▶' : '▼'}
      </span>
      <span class="text-zinc-100 text-sm font-semibold tracking-tight truncate">
        {displayTitle}
      </span>
      {#if isTemplate}
        <span
          class="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-900/40 border border-amber-700/40 text-[9px] uppercase tracking-widest text-amber-300"
          title="Template — filter locked"
        >tpl</span>
      {/if}
      {#if instance.width !== 1}
        <span
          class="hidden sm:inline-flex items-center gap-1 ml-1 px-2 py-0.5 rounded-md bg-zinc-800/70 border border-zinc-700/70 text-[10px] uppercase tracking-wider text-zinc-300"
        >
          {#if instance.kind === 'transfer'}
            {#if activeChainGroup}
              <span class="text-amber-300" title={activeChainGroup.description}>Σ</span>
              <span class="text-zinc-300">{activeChainGroup.label}</span>
              <span class="text-zinc-500">·</span>
            {:else}
              <span class="text-zinc-300">{instance.chain}</span>
              <span class="text-zinc-500">·</span>
            {/if}
          {/if}
          {#if activeTokenGroup !== null}
            <span class="text-amber-300" title="Token group">Σ</span>
            <span class="text-zinc-100 font-medium">{instance.token}</span>
          {:else}
            <span class="text-zinc-100 font-medium">{instance.token}</span>
          {/if}
          <span class="text-zinc-500">·</span>
          <span>{instance.interval}</span>
        </span>
      {/if}
    </button>

    <!-- Primary controls (always visible) -->
    <div
      class={[
        'flex items-center gap-1.5',
        instance.width === 1 ? 'flex-wrap' : ''
      ].join(' ')}
    >
      {#if instance.kind === 'transfer'}
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

  {#if !collapsed}
  <div class="flex-1 relative min-h-0">

  {#if settingsOpen}
    <div class="absolute inset-0 z-20 bg-zinc-950/95 overflow-y-auto">
    <div class="px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/30 flex items-center gap-3 flex-wrap text-xs">
      {#if instance.kind === 'ohlcv'}
        <label class="flex items-center gap-1.5 text-zinc-300 cursor-pointer">
          <input type="checkbox" bind:checked={instance.pin} class="accent-zinc-400" />
          Pin
        </label>
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
    </div>

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

    {#if error}
      <div class="p-3 text-xs text-red-300 bg-red-950/30">{error}</div>
    {/if}
    {#if data.length === 0}
      <div class="p-4 text-sm text-zinc-400">No data for {kindLabel}.</div>
    {:else if instance.kind === 'ohlcv'}
      <CandlestickChart
        candles={data as Candle[]}
        lines={ohlcvLinesD}
        showCandles={instance.showPoint}
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
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
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {:else if instance.kind === 'fr'}
      <SignedBarChart
        data={frBpsData}
        valueKey="rate_bps"
        lines={frLinesD}
        showBars={instance.showPoint}
        valueLabel="Rate"
        height={chartCanvasHeight}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        formatY={(v) => v.toFixed(2)}
        formatTooltip={(v) => `${v.toFixed(2)} bps`}
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
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {/if}

  </div>
  {/if}
</div>
