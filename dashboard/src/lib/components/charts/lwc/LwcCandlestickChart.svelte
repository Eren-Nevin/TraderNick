<script lang="ts">
  import { onMount } from 'svelte';
  import {
    createChart,
    LineStyle,
    type CandlestickData,
    type HistogramData,
    type IChartApi,
    type ISeriesApi,
    type LineData,
    type UTCTimestamp
  } from 'lightweight-charts';
  import { themeStore } from '$lib/stores/theme.svelte';
  import { fmtUtcTime } from '$lib/components/charts/config';
  import type { Candle } from '$lib/api';
  import { type View } from '$lib/chart-zoom';
  import { lwcChartOptions } from './theme';
  import { VRefLinesPrimitive } from './primitives/vRefLine';
  import { timeToLogical, logicalToTime } from './logical';

  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Candle, i: number, data: Candle[]) => number;
    dash?: string;
    /** Put this line on its own price scale (e.g. 'left') so a different-unit
     *  overlay (a $ PnL curve) doesn't squash the candles' price scale. */
    priceScaleId?: string;
    lineWidth?: number;
    /** Custom axis/label formatter for this line's own price scale (e.g. compact
     *  $ K/M for a PnL line). Only meaningful with a dedicated priceScaleId. */
    priceFmt?: (v: number) => string;
    /** Compound overlay lines call `compute` with a *remapped* value so the
     *  drawn path fits the primary chart's Y range. When set, the tooltip
     *  shows `rawValue` instead so the user sees the line's native unit. */
    rawValue?: (d: Candle, i: number, data: Candle[]) => number;
    rawFormat?: (v: number) => string;
  };

  /** Vertical reference line at a specific Unix-second timestamp. */
  type VRefLine = { time: number; color?: string; dash?: string };

  let {
    candles = [] as Candle[],
    lines = [] as Line[],
    vRefLines = [] as VRefLine[],
    showCandles = true,
    formatVolume = (v: number) => v.toFixed(2),
    height = 540,
    xExtent,
    view = null as View,
    onView,
    hoverTime = null,
    onHover,
    onClick,
    markers = [],
    fontSize,
    fontFamily
  }: {
    candles: Candle[];
    lines?: Line[];
    vRefLines?: VRefLine[];
    showCandles?: boolean;
    formatVolume?: (v: number) => string;
    height?: number;
    xExtent?: [number, number];
    view?: View;
    onView?: (v: View) => void;
    hoverTime?: number | null;
    onHover?: (t: number | null) => void;
    /** Click a bar → the bar's time (unix seconds). Read at click time so it
     *  binds even when the handler is enabled after mount (see LwcLineChart). */
    onClick?: (t: number, evt: MouseEvent) => void;
    /** Series markers (must be sorted by time ascending) — e.g. per-bar buy/sell
     *  pressure. Applied to the candle series via setMarkers. */
    markers?: Array<{ time: number; position: 'aboveBar' | 'belowBar' | 'inBar'; color: string; shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'; text?: string; size?: number }>;
    /** Override the chart layout font size (px). Also enlarges series-marker text,
     *  which LWC ties to layout.fontSize. Undefined = theme default. */
    fontSize?: number;
    /** Override the chart layout font family. LWC has no bold-weight option for
     *  markers (font is `${size}px ${family}`), so a heavy family (e.g. Arial
     *  Black) is the way to make marker text bolder. Undefined = theme default. */
    fontFamily?: string;
  } = $props();

  let wrapper = $state<HTMLDivElement | null>(null);

  // Lightweight Charts instances — held outside reactive state so $effects
  // don't trigger on identity changes.
  let chart: IChartApi | null = null;
  let candleSeries: ISeriesApi<'Candlestick'> | null = null;
  let volumeSeries: ISeriesApi<'Histogram'> | null = null;
  const lineSeries = new Map<string, ISeriesApi<'Line'>>();
  let vRefPrimitive: VRefLinesPrimitive | null = null;
  // Prevents the parent → chart `setVisibleRange` from re-emitting an
  // identical view back upstream and ping-ponging.
  let suppressViewEmit = false;
  // Last range we emitted via subscribeVisibleTimeRangeChange. When the
  // parent's `view` propagates back identical to this, we skip re-applying
  // it. Without this check, every reactive re-render (theme toggle, hover,
  // etc.) would re-issue setVisibleRange on our own pan state, fighting
  // the user's current scroll position.
  let lastEmittedFrom: number | null = null;
  let lastEmittedTo: number | null = null;

  // Snap-to-nearest hover index — same binary-search shape as the old
  // CandlestickChart's hot path. Drives the top-left tooltip card and the
  // synced crosshair we push back into Lightweight when the parent steers
  // hover via `hoverTime`.
  const hoverIdx = $derived.by<number | null>(() => {
    if (hoverTime === null || !candles.length) return null;
    let lo = 0;
    let hi = candles.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (candles[mid].time < hoverTime) lo = mid + 1;
      else hi = mid;
    }
    if (lo === 0) return 0;
    const a = candles[lo - 1].time;
    const b = candles[lo].time;
    return Math.abs(b - hoverTime) < Math.abs(hoverTime - a) ? lo : lo - 1;
  });
  const hoverCandle = $derived(hoverIdx !== null ? candles[hoverIdx] : null);

  onMount(() => {
    if (!wrapper) return;
    const c = createChart(wrapper, { ...lwcChartOptions(), height, autoSize: true });
    chart = c;
    if (fontSize || fontFamily) c.applyOptions({ layout: { ...(fontSize ? { fontSize } : {}), ...(fontFamily ? { fontFamily } : {}) } });
    // Bar clicks → onClick(barTime). Read the prop at click time (not gated in
    // onMount) so it works even if the handler is added after mount.
    c.subscribeClick((p) => {
      if (!p.time || !onClick) return;
      const evt = (p.sourceEvent as unknown as MouseEvent) ?? new MouseEvent('click');
      onClick(p.time as unknown as number, evt);
    });

    candleSeries = c.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      visible: showCandles
    });
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.25 } });

    volumeSeries = c.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume_scale',
      priceLineVisible: false,
      lastValueVisible: false
    });
    c.priceScale('volume_scale').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    vRefPrimitive = new VRefLinesPrimitive(vRefLines.slice());
    candleSeries.attachPrimitive(vRefPrimitive);

    c.subscribeCrosshairMove((p) => {
      if (!p.time) {
        onHover?.(null);
      } else {
        onHover?.(p.time as unknown as number);
      }
    });

    c.timeScale().subscribeVisibleTimeRangeChange((r) => {
      if (suppressViewEmit || !r) return;
      let from = r.from as unknown as number;
      let to = r.to as unknown as number;
      // subscribeVisibleTimeRangeChange clamps the emitted range to data
      // ("cannot extrapolate time"). Synthesize the past-data portion from
      // getVisibleLogicalRange so cross-chart sync follows the pan into
      // the right-edge whitespace.
      const lr = c.timeScale().getVisibleLogicalRange();
      if (lr && candles.length >= 2) {
        if (lr.from < 0) from = logicalToTime(lr.from, candles);
        if (lr.to > candles.length - 1) to = logicalToTime(lr.to, candles);
      }
      lastEmittedFrom = from;
      lastEmittedTo = to;
      onView?.([from, to]);
    });

    return () => {
      c.remove();
      chart = null;
      candleSeries = null;
      volumeSeries = null;
      lineSeries.clear();
      vRefPrimitive = null;
    };
  });

  // Theme reactive — themeStore.theme is the rune subscription; the cached
  // CSS vars inside `lwcChartOptions()` are cleared by themeStore.set().
  $effect(() => {
    void themeStore.theme;
    chart?.applyOptions(lwcChartOptions());
    if (fontSize || fontFamily) chart?.applyOptions({ layout: { ...(fontSize ? { fontSize } : {}), ...(fontFamily ? { fontFamily } : {}) } });
  });

  $effect(() => {
    candleSeries?.applyOptions({ visible: showCandles });
  });

  // Per-bar markers (e.g. group buy/sell pressure). Caller supplies them sorted
  // by time ascending. When markers are present, widen the price-scale margins so
  // above/below markers — including two stacked on one side (a flow arrow + a
  // spot-VD square, or when both a buy and sell land on the same bar) — stay
  // within the chart bounds vertically. Reverts to the tight default when none.
  $effect(() => {
    if (!candleSeries) return;
    candleSeries.setMarkers(markers as unknown as Parameters<typeof candleSeries.setMarkers>[0]);
    candleSeries.priceScale().applyOptions({
      scaleMargins: markers.length > 0 ? { top: 0.22, bottom: 0.28 } : { top: 0.05, bottom: 0.25 }
    });
  });

  $effect(() => {
    if (!candleSeries || !volumeSeries) return;
    const candleData: CandlestickData[] = candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close
    }));
    candleSeries.setData(candleData);
    const volumeData: HistogramData[] = candles.map((c) => ({
      time: c.time as UTCTimestamp,
      value: c.volume,
      color: c.close >= c.open ? '#22c55e' : '#ef4444'
    }));
    volumeSeries.setData(volumeData);
  });

  // Indicator line series — diff against `lines` prop. Remove gone keys,
  // upsert kept keys, recompute and setData() each frame.
  $effect(() => {
    if (!chart) return;
    const wanted = new Set(lines.map((l) => l.key));
    for (const [k, s] of lineSeries) {
      if (!wanted.has(k)) {
        chart.removeSeries(s);
        lineSeries.delete(k);
      }
    }
    // Show the left price scale iff some line is drawn against it (a PnL curve).
    chart.priceScale('left').applyOptions({
      visible: lines.some((l) => l.priceScaleId === 'left'),
      scaleMargins: { top: 0.1, bottom: 0.2 }
    });
    for (const ln of lines) {
      let s = lineSeries.get(ln.key);
      if (!s) {
        s = chart.addLineSeries({
          color: ln.color,
          lineWidth: (ln.lineWidth ?? 1) as 1 | 2 | 3 | 4,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          ...(ln.priceScaleId ? { priceScaleId: ln.priceScaleId } : {}),
          ...(ln.priceFmt ? { priceFormat: { type: 'custom', formatter: ln.priceFmt, minMove: 1 } } : {})
        });
        lineSeries.set(ln.key, s);
      } else {
        s.applyOptions({
          color: ln.color,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid
        });
      }
      const data: LineData[] = [];
      for (let i = 0; i < candles.length; i++) {
        const v = ln.compute(candles[i], i, candles);
        if (Number.isFinite(v)) data.push({ time: candles[i].time as UTCTimestamp, value: v });
      }
      s.setData(data);
    }
  });

  $effect(() => {
    vRefPrimitive?.setRefs(vRefLines.slice());
  });

  // View sync with echo-skip. When the user pans, Lightweight emits the
  // new range; the parent stores it; the parent re-renders; we receive our
  // own view back. Without the lastEmitted check we'd re-issue
  // setVisibleRange on our own pan state — which clamps and stomps on the
  // user's mid-pan scroll position (the original "aggressive zoom on
  // pan-into-recent-past" bug).
  //
  // setVisibleRange can't extrapolate past data, so when the target range
  // falls outside the data extent (cross-chart sync from a chart that
  // panned into whitespace) we apply via setVisibleLogicalRange instead.
  $effect(() => {
    if (!chart || !candles.length) return;
    const target = view ?? xExtent;
    if (!target) return;
    if (lastEmittedFrom === target[0] && lastEmittedTo === target[1]) return;
    suppressViewEmit = true;
    const first = candles[0].time;
    const last = candles[candles.length - 1].time;
    if (target[1] > last || target[0] < first) {
      chart.timeScale().setVisibleLogicalRange({
        from: timeToLogical(target[0], candles),
        to: timeToLogical(target[1], candles)
      });
    } else {
      chart.timeScale().setVisibleRange({
        from: target[0] as UTCTimestamp,
        to: target[1] as UTCTimestamp
      });
    }
    lastEmittedFrom = target[0];
    lastEmittedTo = target[1];
    queueMicrotask(() => {
      suppressViewEmit = false;
    });
  });

  // Parent → chart crosshair. `hoverCandle` is the snap-to-nearest result;
  // we draw the crosshair on the candle's close price.
  $effect(() => {
    if (!chart || !candleSeries) return;
    if (hoverCandle === null) {
      chart.clearCrosshairPosition();
    } else {
      chart.setCrosshairPosition(
        hoverCandle.close,
        hoverCandle.time as UTCTimestamp,
        candleSeries
      );
    }
  });
</script>

<div bind:this={wrapper} class="relative w-full" style="height: {height}px;">
  {#if hoverCandle}
    <div
      class="absolute top-2 left-2 px-3 py-2 rounded border border-zinc-700/70 bg-zinc-900/70 text-xs font-mono text-zinc-100 pointer-events-none shadow z-10"
    >
      <div class="text-zinc-400">
        {fmtUtcTime(hoverCandle.time)}
      </div>
      <div>
        <span class="text-zinc-400">O</span>
        {hoverCandle.open.toFixed(4)}
        <span class="text-zinc-400 ml-2">H</span>
        {hoverCandle.high.toFixed(4)}
      </div>
      <div>
        <span class="text-zinc-400">L</span>
        {hoverCandle.low.toFixed(4)}
        <span class="text-zinc-400 ml-2">C</span>
        {hoverCandle.close.toFixed(4)}
      </div>
      <div><span class="text-zinc-400">V</span> {formatVolume(hoverCandle.volume)}</div>
      {#if lines.length && hoverIdx !== null}
        <div class="mt-1 pt-1 border-t border-zinc-800"></div>
        {#each lines as ln (ln.key)}
          {@const v = ln.rawValue
            ? ln.rawValue(hoverCandle, hoverIdx, candles)
            : ln.compute(hoverCandle, hoverIdx, candles)}
          <div class="flex items-center gap-2">
            <span class="inline-block w-3 h-[2px]" style="background: {ln.color}"></span>
            <span class="text-zinc-400 w-24">{ln.label}</span>
            <span class="w-20 text-right">{ln.rawFormat ? ln.rawFormat(v) : v.toFixed(4)}</span>
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>
