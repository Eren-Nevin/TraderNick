<script lang="ts">
  import { onMount } from 'svelte';
  import {
    createChart,
    LineStyle,
    type AutoscaleInfo,
    type HistogramData,
    type IChartApi,
    type ISeriesApi,
    type LineData,
    type UTCTimestamp
  } from 'lightweight-charts';
  import { themeStore } from '$lib/stores/theme.svelte';
  import { fmtUtcTime } from '$lib/components/charts/config';
  import { type View } from '$lib/chart-zoom';
  import { lwcChartOptions } from './theme';
  import { VRefLinesPrimitive } from './primitives/vRefLine';
  import { timeToLogical, logicalToTime } from './logical';

  type Datum = { time: number } & Record<string, number>;
  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Datum, i: number, data: Datum[]) => number;
    dash?: string;
    rawValue?: (d: Datum, i: number, data: Datum[]) => number;
    rawFormat?: (v: number) => string;
    // SignedBarChart never reads `axis` — kept for type parity with the
    // shared Line shape used across LineChart / StackedBarChart consumers.
    axis?: 'primary' | 'secondary';
  };

  type VRefLine = { time: number; color?: string; dash?: string };

  let {
    data = [] as Datum[],
    valueKey,
    lines = [] as Line[],
    vRefLines = [] as VRefLine[],
    showBars = true,
    height = 220,
    title = '',
    xExtent,
    view = null as View,
    onView,
    hoverTime = null,
    onHover,
    posColor = '#22c55e',
    negColor = '#ef4444',
    formatY = (v: number) => v.toFixed(2),
    formatTooltip = (v: number) => v.toFixed(4),
    valueLabel = 'Value'
  }: {
    data: Datum[];
    valueKey: string;
    lines?: Line[];
    vRefLines?: VRefLine[];
    showBars?: boolean;
    height?: number;
    title?: string;
    xExtent?: [number, number];
    view?: View;
    onView?: (v: View) => void;
    hoverTime?: number | null;
    onHover?: (t: number | null) => void;
    posColor?: string;
    negColor?: string;
    formatY?: (v: number) => string;
    formatTooltip?: (v: number) => string;
    // Legacy prop — no analog in Lightweight (histogram bar width follows
    // the time-scale's barSpacing). Accepted and ignored for prop parity.
    minBarWidthPx?: number;
    valueLabel?: string;
  } = $props();

  let wrapper = $state<HTMLDivElement | null>(null);

  let chart: IChartApi | null = null;
  let histogramSeries: ISeriesApi<'Histogram'> | null = null;
  const lineSeries = new Map<string, ISeriesApi<'Line'>>();
  let vRefPrimitive: VRefLinesPrimitive | null = null;
  let suppressViewEmit = false;
  let lastEmittedFrom: number | null = null;
  let lastEmittedTo: number | null = null;

  // Symmetric y range. Mutable closure read by autoscaleInfoProvider on
  // every redraw; updated before any setData() call.
  let maxAbs = 1;

  const hoverIdx = $derived.by<number | null>(() => {
    if (hoverTime === null || !data.length) return null;
    let lo = 0;
    let hi = data.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (data[mid].time < hoverTime) lo = mid + 1;
      else hi = mid;
    }
    if (lo === 0) return 0;
    const a = data[lo - 1].time;
    const b = data[lo].time;
    return Math.abs(b - hoverTime) < Math.abs(hoverTime - a) ? lo : lo - 1;
  });
  const hoverDatum = $derived(hoverIdx !== null ? data[hoverIdx] : null);
  const hoverVal = $derived(hoverDatum ? (hoverDatum[valueKey] ?? 0) : 0);

  function symmetricProvider(): AutoscaleInfo {
    const r = maxAbs * 1.05;
    return { priceRange: { minValue: -r, maxValue: r } };
  }

  function recomputeMaxAbs(): void {
    let m = 0;
    for (let i = 0; i < data.length; i++) {
      const d = data[i];
      if (showBars) {
        const v = Math.abs(d[valueKey] ?? 0);
        if (v > m) m = v;
      }
      for (const ln of lines) {
        const v = ln.compute(d, i, data);
        if (Number.isFinite(v)) {
          const a = Math.abs(v);
          if (a > m) m = a;
        }
      }
    }
    maxAbs = m || 1;
  }

  onMount(() => {
    if (!wrapper) return;
    const c = createChart(wrapper, { ...lwcChartOptions(), height, autoSize: true });
    chart = c;

    histogramSeries = c.addHistogramSeries({
      priceFormat: { type: 'custom', formatter: formatY, minMove: 0.00000001 },
      priceScaleId: 'right',
      priceLineVisible: false,
      lastValueVisible: false,
      visible: showBars,
      base: 0,
      autoscaleInfoProvider: () => symmetricProvider()
    });

    vRefPrimitive = new VRefLinesPrimitive(vRefLines.slice());
    histogramSeries.attachPrimitive(vRefPrimitive);

    c.subscribeCrosshairMove((p) => {
      if (!p.time) onHover?.(null);
      else onHover?.(p.time as unknown as number);
    });

    c.timeScale().subscribeVisibleTimeRangeChange((r) => {
      if (suppressViewEmit || !r) return;
      let from = r.from as unknown as number;
      let to = r.to as unknown as number;
      const lr = c.timeScale().getVisibleLogicalRange();
      if (lr && data.length >= 2) {
        if (lr.from < 0) from = logicalToTime(lr.from, data);
        if (lr.to > data.length - 1) to = logicalToTime(lr.to, data);
      }
      lastEmittedFrom = from;
      lastEmittedTo = to;
      onView?.([from, to]);
    });

    return () => {
      c.remove();
      chart = null;
      histogramSeries = null;
      lineSeries.clear();
      vRefPrimitive = null;
    };
  });

  $effect(() => {
    void themeStore.theme;
    chart?.applyOptions(lwcChartOptions());
  });

  $effect(() => {
    histogramSeries?.applyOptions({ visible: showBars });
  });

  $effect(() => {
    histogramSeries?.applyOptions({
      priceFormat: { type: 'custom', formatter: formatY, minMove: 0.00000001 }
    });
  });

  // Bars + lines + symmetric range update. Recompute maxAbs first so the
  // first redraw triggered by setData() reads the new envelope.
  $effect(() => {
    if (!chart || !histogramSeries) return;

    recomputeMaxAbs();

    const bars: HistogramData[] = data.map((d) => ({
      time: d.time as UTCTimestamp,
      value: d[valueKey] ?? 0,
      color: (d[valueKey] ?? 0) >= 0 ? posColor : negColor
    }));
    histogramSeries.setData(bars);

    const wanted = new Set(lines.map((l) => l.key));
    for (const [k, s] of lineSeries) {
      if (!wanted.has(k)) {
        chart.removeSeries(s);
        lineSeries.delete(k);
      }
    }
    for (const ln of lines) {
      let s = lineSeries.get(ln.key);
      if (!s) {
        s = chart.addLineSeries({
          color: ln.color,
          lineWidth: 1,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid,
          priceScaleId: 'right',
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => symmetricProvider()
        });
        lineSeries.set(ln.key, s);
      } else {
        s.applyOptions({
          color: ln.color,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid
        });
      }
      const points: LineData[] = [];
      for (let i = 0; i < data.length; i++) {
        const v = ln.compute(data[i], i, data);
        if (Number.isFinite(v)) points.push({ time: data[i].time as UTCTimestamp, value: v });
      }
      s.setData(points);
    }
  });

  $effect(() => {
    vRefPrimitive?.setRefs(vRefLines.slice());
  });

  // View sync with echo-skip — same pattern as the other Lwc charts.
  $effect(() => {
    if (!chart || !data.length) return;
    const target = view ?? xExtent;
    if (!target) return;
    if (lastEmittedFrom === target[0] && lastEmittedTo === target[1]) return;
    suppressViewEmit = true;
    const first = data[0].time;
    const last = data[data.length - 1].time;
    if (target[1] > last || target[0] < first) {
      chart.timeScale().setVisibleLogicalRange({
        from: timeToLogical(target[0], data),
        to: timeToLogical(target[1], data)
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

  // Parent → chart crosshair, snapped to nearest datum, drawn at the bar
  // value height so the marker tracks the histogram tip.
  $effect(() => {
    if (!chart || !histogramSeries) return;
    if (hoverDatum === null) {
      chart.clearCrosshairPosition();
    } else {
      chart.setCrosshairPosition(hoverVal, hoverDatum.time as UTCTimestamp, histogramSeries);
    }
  });
</script>

<div bind:this={wrapper} class="relative w-full" style="height: {height}px;">
  {#if title}
    <div
      class="absolute top-2 left-16 text-[10px] uppercase tracking-widest text-zinc-400 z-10 pointer-events-none"
    >
      {title}
    </div>
  {/if}
  {#if hoverDatum}
    <div
      class="absolute top-2 right-20 px-3 py-2 rounded border border-zinc-700/70 bg-zinc-900/70 text-xs font-mono text-zinc-100 pointer-events-none shadow z-10"
    >
      <div class="text-zinc-400">
        {fmtUtcTime(hoverDatum.time)}
      </div>
      <div class="flex items-center gap-2">
        <span
          class="inline-block w-2 h-2 rounded-sm"
          style="background: {hoverVal >= 0 ? posColor : negColor}"
        ></span>
        <span class="text-zinc-400 w-20">{valueLabel}</span>
        <span class="w-24 text-right">{formatTooltip(hoverVal)}</span>
      </div>
      {#if lines.length && hoverIdx !== null}
        <div class="mt-1 pt-1 border-t border-zinc-800"></div>
        {#each lines as ln (ln.key)}
          {@const v = ln.compute(hoverDatum, hoverIdx, data)}
          <div class="flex items-center gap-2">
            <span class="inline-block w-3 h-[2px]" style="background: {ln.color}"></span>
            <span class="text-zinc-400 w-20">{ln.label}</span>
            <span class="w-24 text-right">{formatTooltip(v)}</span>
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>
