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
    EXTRA_SERIES_COLORS,
    LS_LINES,
    MA_COLORS,
    MAX_EXTRA_SERIES,
    NEUTRAL_REF,
    OI_LINES,
    TOP_TRADERS_LINES,
    defaultView,
    fmtUsdAxis,
    fmtUsdTooltip,
    lookbackWindow,
    maArray,
    sizeLines,
    sizeSeries,
    unixSec,
    type ChartInstance as ChartInstanceT,
    type FilteredSeries,
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
  let tokensForChain = $derived(
    Array.from(
      new Set(streams.filter((s) => s.chain === instance.chain).map((s) => s.token))
    ).sort()
  );
  let transferKind = $derived(
    streams.find((s) => s.chain === instance.chain && s.token === instance.token)?.kind ?? 'erc20'
  );
  // Auto-snap token when chain changes and current token isn't on the new chain.
  $effect(() => {
    if (instance.kind !== 'transfer') return;
    if (tokensForChain.length > 0 && !tokensForChain.includes(instance.token)) {
      instance.token = tokensForChain[0];
    }
  });

  // Wallet-category catalogue (for filter input <datalist> suggestions).
  let walletCategories = $state<WalletCategory[]>([]);
  onMount(async () => {
    if (instance.kind !== 'transfer') return;
    try {
      const res = await fetch('/api/transfers/categories');
      if (res.ok) {
        const body = await res.json();
        walletCategories = body.categories ?? [];
      }
    } catch {
      // ignore — UI degrades to plain text input
    }
  });

  // ---- transfer "extra series" form state ----
  type FilterKey = 'sender_in' | 'sender_ex' | 'receiver_in' | 'receiver_ex' | 'involving_in' | 'involving_ex';
  const FILTER_KEYS: FilterKey[] = [
    'sender_in',
    'sender_ex',
    'receiver_in',
    'receiver_ex',
    'involving_in',
    'involving_ex'
  ];
  const EMPTY_PENDING: Record<FilterKey, string> = {
    sender_in: '',
    sender_ex: '',
    receiver_in: '',
    receiver_ex: '',
    involving_in: '',
    involving_ex: ''
  };
  let pendingName = $state('');
  let pendingFilters = $state<Record<FilterKey, string>>({ ...EMPTY_PENDING });

  function parseFilterCsv(s: string): string[] {
    return s
      .split(',')
      .map((v) => v.trim())
      .filter((v) => v.length > 0);
  }
  function buildPendingFilterSet(): TransferFilters {
    const next: TransferFilters = {};
    for (const k of FILTER_KEYS) {
      const arr = parseFilterCsv(pendingFilters[k as FilterKey]);
      if (arr.length) next[k] = arr;
    }
    return next;
  }
  function autoNameFromFilters(f: TransferFilters): string {
    const parts: string[] = [];
    if (f.sender_in?.length) parts.push(`${f.sender_in.join('+')} →`);
    if (f.receiver_in?.length) parts.push(`→ ${f.receiver_in.join('+')}`);
    if (f.involving_in?.length) parts.push(`⇄ ${f.involving_in.join('+')}`);
    if (f.sender_ex?.length) parts.push(`¬${f.sender_ex.join('+')} →`);
    if (f.receiver_ex?.length) parts.push(`→ ¬${f.receiver_ex.join('+')}`);
    if (f.involving_ex?.length) parts.push(`¬⇄${f.involving_ex.join('+')}`);
    return parts.join(' · ');
  }

  let pendingFilterSet = $derived.by(() => buildPendingFilterSet());
  let pendingHasAny = $derived(
    FILTER_KEYS.some((k) => parseFilterCsv(pendingFilters[k as FilterKey]).length > 0)
  );
  let extraSeriesCount = $derived((instance.extraSeries ?? []).length);
  let canAddSeries = $derived(pendingHasAny && extraSeriesCount < MAX_EXTRA_SERIES);

  function addSeries() {
    if (!canAddSeries) return;
    const f = pendingFilterSet;
    const newId =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const newSeries: FilteredSeries = {
      id: newId,
      name: pendingName.trim() || autoNameFromFilters(f),
      filters: f
    };
    instance.extraSeries = [...(instance.extraSeries ?? []), newSeries];
    pendingName = '';
    pendingFilters = { ...EMPTY_PENDING };
  }
  function removeSeries(id: string) {
    instance.extraSeries = (instance.extraSeries ?? []).filter((s) => s.id !== id);
  }
  function clearPending() {
    pendingName = '';
    pendingFilters = { ...EMPTY_PENDING };
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

  function extraSeriesKey(): string {
    return (instance.extraSeries ?? [])
      .map((s) => `${s.id}=${JSON.stringify(s.filters)}`)
      .join('|');
  }

  function loadKey(): string {
    if (instance.kind === 'sz') {
      return `${instance.kind}|${instance.token}|${instance.interval}|${instance.under ?? 0}|${instance.over ?? 0}`;
    }
    if (instance.kind === 'transfer') {
      return `${instance.kind}|${instance.chain ?? ''}|${instance.token}|${instance.interval}|${extraSeriesKey()}`;
    }
    return `${instance.kind}|${instance.token}|${instance.interval}`;
  }

  $effect(() => {
    const key = loadKey();
    if (key === loadedKey) return;
    void load();
  });

  /** Fetch the unfiltered "main" series plus each `instance.extraSeries` in
   *  parallel against /api/transfers/aggregate, then merge them into one
   *  array keyed by `time` so a LineChart can render N+1 lines at once. */
  async function loadTransferMerged(sinceIso: string, untilIso: string) {
    const baseQS = {
      chain: instance.chain ?? 'ETH',
      kind: transferKind,
      token: instance.token,
      interval: instance.interval,
      since: sinceIso,
      until: untilIso,
      limit: '10000'
    };
    function urlFor(filters: TransferFilters | null): string {
      const qs: Record<string, string> = { ...baseQS };
      if (filters) {
        for (const k of FILTER_KEYS) {
          const arr = filters[k] ?? [];
          if (arr.length) qs[k] = arr.join(',');
        }
      }
      return `/api/transfers/aggregate?${new URLSearchParams(qs)}`;
    }

    const extras = instance.extraSeries ?? [];
    const urls = [urlFor(null), ...extras.map((s) => urlFor(s.filters))];
    const results = await Promise.all(
      urls.map(async (u) => {
        const r = await fetch(u);
        if (!r.ok) throw new Error(`transfers ${r.status}`);
        const body = await r.json();
        return (body.series ?? []) as Array<{ time: number; sum_amount: number; sum_value_usd: number; count: number }>;
      })
    );

    const merged = new Map<number, Record<string, number>>();
    function ensure(t: number): Record<string, number> {
      let row = merged.get(t);
      if (!row) {
        row = { time: t, main: 0, sum_amount: 0, sum_value_usd: 0, count: 0 };
        for (let i = 0; i < extras.length; i++) row[`extra_${i}`] = 0;
        merged.set(t, row);
      }
      return row;
    }
    for (const b of results[0]) {
      const row = ensure(b.time);
      row.main = b.sum_amount;
      row.sum_amount = b.sum_amount;
      row.sum_value_usd = b.sum_value_usd;
      row.count = b.count;
    }
    for (let i = 0; i < extras.length; i++) {
      for (const b of results[i + 1] ?? []) {
        const row = ensure(b.time);
        row[`extra_${i}`] = b.sum_amount;
      }
    }
    const out = Array.from(merged.values()).sort((a, b) => (a.time as number) - (b.time as number));
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

  // Transfer-kind lines: main (cyan) + one per extraSeries.
  const TRANSFER_MAIN_LINE = {
    key: 'main',
    label: 'Total',
    color: '#06b6d4',
    compute: (d: TransferBucket & Record<string, number>) => d.main ?? d.sum_amount ?? 0
  };
  let transferExtraLines = $derived(
    (instance.extraSeries ?? []).map((s, idx) => ({
      key: `extra_${idx}`,
      label: s.name || `Filter ${idx + 1}`,
      color: EXTRA_SERIES_COLORS[idx] ?? '#fbbf24',
      compute: (d: Record<string, number>) => d[`extra_${idx}`] ?? 0
    }))
  );
  let transferLinesD = $derived([
    ...(instance.showPoint ? [TRANSFER_MAIN_LINE] : []),
    ...transferExtraLines,
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
  function toggleWidth() {
    instance.width = instance.width === 1 ? 2 : 1;
  }

  let kindLabel = $derived(CHART_KIND_LABELS[instance.kind]);
  let panelTitle = $derived(
    `${kindLabel} — ${instance.token} ${instance.interval}` +
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
  <div class="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-zinc-800 bg-gradient-to-b from-zinc-900/40 to-transparent">
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
        {kindLabel}
      </span>
      <span
        class="hidden sm:inline-flex items-center gap-1 ml-1 px-2 py-0.5 rounded-md bg-zinc-800/70 border border-zinc-700/70 text-[10px] uppercase tracking-wider text-zinc-300"
      >
        {#if instance.kind === 'transfer'}
          <span class="text-zinc-300">{instance.chain}</span>
          <span class="text-zinc-500">·</span>
        {/if}
        <span class="text-zinc-100 font-medium">{instance.token}</span>
        <span class="text-zinc-500">·</span>
        <span>{instance.interval}</span>
      </span>
    </button>

    <!-- Primary controls (always visible) -->
    <div class="flex items-center gap-1.5">
      {#if instance.kind === 'transfer'}
        <select
          bind:value={instance.chain}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        >
          {#each chains as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
        <select
          value={instance.token}
          onchange={(e) => (instance.token = e.currentTarget.value)}
          disabled={tokensForChain.length <= 1}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {#each tokensForChain as t (t)}
            <option value={t}>{t}</option>
          {/each}
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
      <button
        type="button"
        onclick={toggleWidth}
        title={instance.width === 1 ? 'Expand to 2 columns' : 'Shrink to 1 column'}
        class="w-7 h-7 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 border border-transparent text-sm leading-none flex items-center justify-center"
      >{instance.width === 1 ? '⇔' : '⇒'}</button>
      <button
        type="button"
        onclick={() => onRemove(instance.id)}
        title="Remove chart"
        class="w-7 h-7 rounded-md text-zinc-400 hover:text-red-400 hover:bg-zinc-800 border border-transparent text-sm leading-none flex items-center justify-center"
      >✕</button>
    </div>
  </div>

  {#if settingsOpen && !collapsed}
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

    {#if instance.kind === 'transfer'}
      <div class="px-4 py-3 border-b border-zinc-800 bg-zinc-900/30 text-xs space-y-3">
        <div class="text-[10px] uppercase tracking-widest text-zinc-500">
          Filtered series
          <span class="text-zinc-600 normal-case">— overlay up to {MAX_EXTRA_SERIES} colored lines, each its own wallet filter</span>
        </div>

        <!-- Existing series chips -->
        {#if (instance.extraSeries ?? []).length > 0}
          <div class="flex flex-wrap gap-2">
            {#each instance.extraSeries ?? [] as s, idx (s.id)}
              <span
                class="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-zinc-700 bg-zinc-900 text-zinc-100"
                title={autoNameFromFilters(s.filters)}
              >
                <span
                  class="w-3 h-3 rounded-sm inline-block"
                  style="background: {EXTRA_SERIES_COLORS[idx] ?? '#fbbf24'}"
                ></span>
                <span class="truncate max-w-[200px]">{s.name}</span>
                <button
                  type="button"
                  onclick={() => removeSeries(s.id)}
                  title="Remove series"
                  class="text-zinc-400 hover:text-red-400 leading-none"
                >✕</button>
              </span>
            {/each}
          </div>
        {/if}

        <!-- Add-new form -->
        {#if (instance.extraSeries ?? []).length < MAX_EXTRA_SERIES}
          <datalist id="wallet-cats-{instance.id}">
            {#each walletCategories as c (c.name)}
              <option value={c.name}></option>
            {/each}
          </datalist>
          <div class="space-y-2 p-2 border border-zinc-800 rounded-md">
            <input
              type="text"
              bind:value={pendingName}
              placeholder="Series name (optional)"
              class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
            />
            {#each [['sender', 'Sender'], ['receiver', 'Receiver'], ['involving', 'Either']] as [side, label]}
              <div class="grid grid-cols-[60px_1fr_1fr] items-center gap-2">
                <span class="text-zinc-400">{label}</span>
                <input
                  type="text"
                  list="wallet-cats-{instance.id}"
                  bind:value={pendingFilters[`${side}_in` as FilterKey]}
                  placeholder="✔ include"
                  class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
                />
                <input
                  type="text"
                  list="wallet-cats-{instance.id}"
                  bind:value={pendingFilters[`${side}_ex` as FilterKey]}
                  placeholder="✘ exclude"
                  class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
                />
              </div>
            {/each}
            <div class="flex items-center gap-2">
              <button
                type="button"
                onclick={addSeries}
                disabled={!canAddSeries}
                class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-md px-3 py-1 text-xs text-zinc-100"
              >+ Add series</button>
              <button
                type="button"
                onclick={clearPending}
                class="text-zinc-500 hover:text-zinc-200 text-xs"
              >Clear</button>
              {#if !pendingHasAny}
                <span class="text-zinc-600 text-[11px]">Set at least one filter</span>
              {/if}
            </div>
          </div>
        {:else}
          <div class="text-zinc-500 text-[11px]">
            Max {MAX_EXTRA_SERIES} series reached — remove one to add another.
          </div>
        {/if}
      </div>
    {/if}
  {/if}

  {#if !collapsed}
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
        height={540}
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
        height={540}
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
        height={540}
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
        height={540}
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
        height={540}
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
        height={540}
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
        height={540}
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
        height={540}
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        formatY={fmtUsdAxis}
        formatTooltip={fmtUsdTooltip}
      />
    {/if}
  {/if}
</div>
