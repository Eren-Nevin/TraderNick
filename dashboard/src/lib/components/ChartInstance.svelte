<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import LineChart from '$lib/components/LineChart.svelte';
  import SignedBarChart from '$lib/components/SignedBarChart.svelte';
  import { INTERVALS, type Candle, type FundingRateRow, type Interval, type LongShortRow, type OpenInterestRow, type VolumeBucket } from '$lib/api';
  import {
    BUYER_SELLER_LINES,
    BUYER_SELLER_SERIES,
    CHART_KIND_LABELS,
    LS_LINES,
    NEUTRAL_REF,
    OI_LINES,
    TOP_TRADERS_LINES,
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

  type AnyDatum = Candle | OpenInterestRow | FundingRateRow | LongShortRow | VolumeBucket;

  let {
    instance = $bindable(),
    tokens,
    syncZoom,
    sharedView,
    sharedHoverTime,
    onSharedView,
    onSharedHover,
    onRemove,
    onDragStart,
    onDragOver,
    onDrop
  }: {
    instance: ChartInstanceT;
    tokens: string[];
    syncZoom: boolean;
    sharedView: View;
    sharedHoverTime: number | null;
    onSharedView: (v: View) => void;
    onSharedHover: (t: number | null) => void;
    onRemove: (id: string) => void;
    onDragStart: (e: DragEvent, id: string) => void;
    onDragOver: (e: DragEvent) => void;
    onDrop: (e: DragEvent, id: string) => void;
  } = $props();

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
      const w = lookbackWindow(instance.interval);
      const sinceIso = w.since.toISOString();
      const untilIso = w.until.toISOString();
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
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${instance.kind} ${res.status}`);
      const body = await res.json();
      data = pickArr(body);
      since = sinceIso;
      until = untilIso;
      loadedKey = loadKey();
      localView = null;
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

  let cumulativeLines = $derived.by(() => {
    if (!instance.showCumulative || data.length === 0) return [] as unknown[];
    const tag = `${instance.maType.toUpperCase()}(${instance.maLength})`;
    switch (instance.kind) {
      case 'ohlcv': {
        const ma = maArray(
          (data as Candle[]).map((c) => c.close),
          instance.maLength,
          instance.maType
        );
        return [
          {
            key: 'cum_close',
            label: `Close ${tag}`,
            color: '#fbbf24',
            compute: (_d: Candle, i: number) => ma[i]
          }
        ];
      }
      case 'oi': {
        const ma = maArray(
          (data as OpenInterestRow[]).map((d) => d.open_interest_value),
          instance.maLength,
          instance.maType
        );
        return [
          {
            key: 'cum_oi',
            label: `OI ${tag}`,
            color: '#06b6d4',
            dash: '5,3',
            compute: (_d: OpenInterestRow, i: number) => ma[i]
          }
        ];
      }
      case 'fr': {
        const ma = maArray(
          frBpsData.map((d) => d.rate_bps),
          instance.maLength,
          instance.maType
        );
        return [
          {
            key: 'cum_fr',
            label: `Rate ${tag}`,
            color: '#fbbf24',
            dash: '5,3',
            compute: (_d: FundingRateRow, i: number) => ma[i]
          }
        ];
      }
      case 'bs': {
        const arr = data as VolumeBucket[];
        const buyerMA = maArray(arr.map((b) => b.buyer_taker_usd), instance.maLength, instance.maType);
        const totalMA = maArray(
          arr.map((b) => b.buyer_taker_usd + b.seller_taker_usd),
          instance.maLength,
          instance.maType
        );
        return [
          {
            key: 'cum_buyer',
            label: `% Buyer ${tag}`,
            color: '#fbbf24',
            dash: '5,3',
            compute: (_d: VolumeBucket, i: number) =>
              totalMA[i] > 0 ? (buyerMA[i] / totalMA[i]) * 100 : 0
          }
        ];
      }
      case 'sz': {
        const arr = data as VolumeBucket[];
        const u = instance.under ?? 10000;
        const o = instance.over ?? 100000;
        const smallMA = maArray(arr.map((b) => b.small_usd), instance.maLength, instance.maType);
        const largeMA = maArray(arr.map((b) => b.large_usd), instance.maLength, instance.maType);
        const totalMA = maArray(
          arr.map((b) => b.small_usd + b.mid_usd + b.large_usd),
          instance.maLength,
          instance.maType
        );
        return [
          {
            key: 'cum_small',
            label: `% < $${u} ${tag}`,
            color: '#fbbf24',
            dash: '5,3',
            compute: (_d: VolumeBucket, i: number) =>
              totalMA[i] > 0 ? (smallMA[i] / totalMA[i]) * 100 : 0
          },
          {
            key: 'cum_large',
            label: `% > $${o} ${tag}`,
            color: '#06b6d4',
            dash: '5,3',
            compute: (_d: VolumeBucket, i: number) =>
              totalMA[i] > 0 ? (largeMA[i] / totalMA[i]) * 100 : 0
          }
        ];
      }
      case 'tt': {
        const arr = data as LongShortRow[];
        const countMA = maArray(
          arr.map((d) => d.top_trader_count_ratio),
          instance.maLength,
          instance.maType
        );
        const volMA = maArray(
          arr.map((d) => d.top_trader_vol_ratio),
          instance.maLength,
          instance.maType
        );
        return [
          { key: 'cum_top_ct', label: `Top count ${tag}`, color: '#fbbf24', dash: '5,3', compute: (_d: LongShortRow, i: number) => countMA[i] },
          { key: 'cum_top_vol', label: `Top vol ${tag}`, color: '#06b6d4', dash: '5,3', compute: (_d: LongShortRow, i: number) => volMA[i] }
        ];
      }
      case 'ls': {
        const arr = data as LongShortRow[];
        const allCountMA = maArray(
          arr.map((d) => d.long_short_count_ratio),
          instance.maLength,
          instance.maType
        );
        const takerVolMA = maArray(
          arr.map((d) => d.taker_long_short_vol_ratio),
          instance.maLength,
          instance.maType
        );
        return [
          { key: 'cum_all_ct', label: `All L/S ct ${tag}`, color: '#84cc16', dash: '5,3', compute: (_d: LongShortRow, i: number) => allCountMA[i] },
          { key: 'cum_taker_vol', label: `Taker vol ${tag}`, color: '#a855f7', dash: '5,3', compute: (_d: LongShortRow, i: number) => takerVolMA[i] }
        ];
      }
    }
    return [];
  });

  // bs / sz: bar series (Point toggle controls visibility)
  let bsBars = $derived(instance.showPoint ? BUYER_SELLER_SERIES : []);
  let szBars = $derived(
    instance.showPoint ? sizeSeries(instance.under ?? 10000, instance.over ?? 100000) : []
  );

  let bsLines = $derived(
    instance.showCumulative ? [...BUYER_SELLER_LINES, ...cumulativeLines] : []
  );
  let szLinesD = $derived(
    instance.showCumulative
      ? [...sizeLines(instance.under ?? 10000, instance.over ?? 100000), ...cumulativeLines]
      : []
  );
  let oiLinesD = $derived(
    [...(instance.showPoint ? OI_LINES : []), ...(instance.showCumulative ? cumulativeLines : [])]
  );
  let ttLinesD = $derived(
    [...(instance.showPoint ? TOP_TRADERS_LINES : []), ...(instance.showCumulative ? cumulativeLines : [])]
  );
  let lsLinesD = $derived(
    [...(instance.showPoint ? LS_LINES : []), ...(instance.showCumulative ? cumulativeLines : [])]
  );
  let ohlcvLinesD = $derived(instance.showCumulative ? cumulativeLines : []);
  let frLinesD = $derived(instance.showCumulative ? cumulativeLines : []);

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
</script>

<div
  class={'rounded border border-zinc-800 bg-zinc-950 overflow-hidden flex flex-col ' +
    (instance.pin && instance.kind === 'ohlcv' ? 'sticky top-0 z-20 shadow-xl shadow-black/60 ' : '')}
  style="grid-column: span {instance.width}"
  ondragover={(e) => onDragOver(e)}
  ondrop={(e) => onDrop(e, instance.id)}
  role="region"
  aria-label={panelTitle}
>
  <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-zinc-900">
    <div class="flex items-center gap-2 min-w-0">
      <div
        draggable="true"
        ondragstart={(e) => onDragStart(e, instance.id)}
        title="Drag to reorder"
        class="cursor-grab active:cursor-grabbing flex items-center gap-2 text-zinc-400 hover:text-zinc-100 select-none"
      >
        <span class="text-zinc-500 text-xs leading-none">⠿</span>
        <button
          type="button"
          onclick={() => (collapsed = !collapsed)}
          class="flex items-center gap-2 text-zinc-400 hover:text-zinc-100"
        >
          <span class="text-[10px] w-3 inline-block text-center leading-none"
            >{collapsed ? '▶' : '▼'}</span
          >
          <span class="text-[10px] uppercase tracking-widest truncate">{panelTitle}</span>
        </button>
      </div>
    </div>
    <div class="flex items-center gap-2 flex-wrap">
      <select
        bind:value={instance.token}
        class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
      >
        {#each tokens as t (t)}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <select
        bind:value={instance.interval}
        class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
      >
        {#each INTERVALS as iv (iv)}
          <option value={iv}>{iv}</option>
        {/each}
      </select>
      {#if instance.kind === 'ohlcv'}
        <label class="text-xs text-zinc-400 flex items-center gap-1">
          <input type="checkbox" bind:checked={instance.pin} class="accent-zinc-400" />
          Pin
        </label>
      {/if}
      {#if instance.kind === 'sz'}
        <input
          bind:value={instance.underInput}
          type="number"
          step="100"
          min="0"
          title="Under threshold (USD)"
          class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <input
          bind:value={instance.overInput}
          type="number"
          step="100"
          min="0"
          title="Over threshold (USD)"
          class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <button
          type="button"
          onclick={applySzThresholds}
          class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >Apply</button>
      {/if}
      <label class="text-xs text-zinc-400 flex items-center gap-1">
        <input type="checkbox" bind:checked={instance.showPoint} class="accent-zinc-400" />
        Point
      </label>
      <label class="text-xs text-zinc-400 flex items-center gap-1">
        <input type="checkbox" bind:checked={instance.showCumulative} class="accent-zinc-400" />
        MA
      </label>
      <input
        type="number"
        bind:value={instance.maLength}
        min="2"
        max="500"
        step="1"
        class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
      />
      <select
        bind:value={instance.maType}
        class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
      >
        <option value="sma">SMA</option>
        <option value="ema">EMA</option>
        <option value="wma">WMA</option>
      </select>
      <button
        type="button"
        onclick={toggleWidth}
        title={instance.width === 1 ? 'Expand to 2 columns' : 'Shrink to 1 column'}
        class="w-7 h-6 rounded text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 text-xs"
      >{instance.width === 1 ? '⇔' : '⇒'}</button>
      <button
        type="button"
        onclick={() => onRemove(instance.id)}
        title="Remove chart"
        class="w-6 h-6 rounded text-zinc-400 hover:text-red-300 hover:bg-zinc-800 text-sm leading-none"
      >✕</button>
    </div>
  </div>

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
        {xExtent}
        view={effectiveView}
        onView={handleView}
        hoverTime={effectiveHoverTime}
        onHover={handleHover}
        formatY={(v) => v.toFixed(2)}
        formatTooltip={(v) => v.toFixed(4)}
      />
    {/if}
  {/if}
</div>
