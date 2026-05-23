<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import LineChart from '$lib/components/LineChart.svelte';
  import SignedBarChart from '$lib/components/SignedBarChart.svelte';
  import {
    INTERVALS,
    type Candle,
    type FundingRateRow,
    type Interval,
    type LongShortRow,
    type OpenInterestRow,
    type TransferBucket,
    type TransferStream,
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
    TOP_TRADERS_LINES,
    defaultView,
    fmtUsdAxis,
    fmtUsdTooltip,
    lookbackWindow,
    maArray,
    sizeLines,
    sizeSeries,
    unixSec,
    type ChartInstance as ChartInstanceT
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
  function loadKey(): string {
    if (instance.kind === 'sz') {
      return `${instance.kind}|${instance.token}|${instance.interval}|${instance.under ?? 0}|${instance.over ?? 0}`;
    }
    if (instance.kind === 'transfer') {
      return `${instance.kind}|${instance.chain ?? ''}|${instance.token}|${instance.interval}`;
    }
    return `${instance.kind}|${instance.token}|${instance.interval}`;
  }

  $effect(() => {
    const key = loadKey();
    if (key === loadedKey) return;
    void load();
  });

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
        case 'transfer':
          url = `/api/transfers/aggregate?${new URLSearchParams({
            chain: instance.chain ?? 'ETH',
            kind: transferKind,
            token: instance.token,
            interval: instance.interval,
            since: sinceIso,
            until: untilIso,
            limit: '10000'
          })}`;
          pickArr = (b) => (b.series ?? []) as AnyDatum[];
          break;
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

  // Transfer-kind primary line (Point toggle controls visibility).
  const TRANSFER_LINES = [
    {
      key: 'sum_amount',
      label: 'Amount',
      color: '#06b6d4',
      compute: (d: TransferBucket) => d.sum_amount
    }
  ];
  let transferLinesD = $derived([
    ...(instance.showPoint ? TRANSFER_LINES : []),
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
