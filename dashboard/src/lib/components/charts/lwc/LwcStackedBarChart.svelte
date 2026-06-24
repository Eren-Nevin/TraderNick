<script lang="ts">
  import { onMount } from 'svelte';
  import {
    createChart,
    LineStyle,
    type AreaData,
    type IChartApi,
    type ISeriesApi,
    type LineData,
    type UTCTimestamp
  } from 'lightweight-charts';
  import { themeStore } from '$lib/stores/theme.svelte';
  import { fmtUtcTime } from '$lib/components/charts/config';
  import { type View } from '$lib/chart-zoom';
  import { lwcChartOptions } from './theme';
  import { timeToLogical, logicalToTime } from './logical';

  type Datum = { time: number } & Record<string, number>;
  type Series = { key: string; label: string; color: string };
  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Datum, i: number, data: Datum[]) => number;
    dash?: string;
    /** Percent lines (scale: 'pct') and ratio lines (scale: 'ratio') go on the
     *  left (secondary) axis — pct formatted 0..100%, ratio as a plain number
     *  (autoscaled). Value lines (scale: 'value' or omitted) use the right USD
     *  axis. */
    scale?: 'pct' | 'value' | 'ratio';
    rawValue?: (d: Datum, i: number, data: Datum[]) => number;
    rawFormat?: (v: number) => string;
    /** Unused by StackedBarChart — kept for shared Line type parity. */
    axis?: 'primary' | 'secondary';
  };

  let {
    data = [] as Datum[],
    series = [] as Series[],
    lines = [] as Line[],
    height = 220,
    title = '',
    xExtent,
    view = null as View,
    onView,
    hoverTime = null,
    onHover,
    valueFormat = 'usd' as 'usd' | 'pct' | 'token',
    pctMax = 100,
    bars = false
  }: {
    data: Datum[];
    series: Series[];
    lines?: Line[];
    height?: number;
    title?: string;
    xExtent?: [number, number];
    view?: View;
    onView?: (v: View) => void;
    hoverTime?: number | null;
    onHover?: (t: number | null) => void;
    /** Render each stacked layer as an opaque histogram bar instead of a
     *  filled area. Areas overdraw with a gradient fill that reads as
     *  semi-transparent when layers overlap ("one behind the other"); opaque
     *  bars give a clean, unambiguous stack. Used by the bs chart. */
    bars?: boolean;
    /** How the stacked series values are denominated. 'usd' (default) formats
     *  the axis/tooltip as dollars and shows a separate %-of-total column.
     *  'token' formats as a plain K/M/B amount (no $). 'pct' means the values
     *  ARE percentages (e.g. a 100%-stack), so the axis
     *  shows % and the tooltip drops the redundant dollar column. */
    valueFormat?: 'usd' | 'pct' | 'token';
    /** pct-mode only: the fixed top of the pinned axis. 100 for a single
     *  100%-stack; 200 for two stacks combined (e.g. asks-on-bids). */
    pctMax?: number;
  } = $props();

  let wrapper = $state<HTMLDivElement | null>(null);

  let chart: IChartApi | null = null;
  // Stack layers, keyed by series.key. Each layer's value is the cumulative
  // top up to (and including) that series.
  const areaSeries = new Map<string, ISeriesApi<'Area'> | ISeriesApi<'Histogram'>>();
  const lineSeries = new Map<string, ISeriesApi<'Line'>>();
  let suppressViewEmit = false;
  let lastEmittedFrom: number | null = null;
  let lastEmittedTo: number | null = null;
  // First/last bar time at the last view application. A data-extent change
  // (dynamic-loading prepends older history) would otherwise let Lightweight
  // hold the logical range and shift the window; detecting it lets us
  // re-assert the absolute-time view to pin the visible range.
  let lastAppliedFirst: number | null = null;
  let lastAppliedLast: number | null = null;

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
  const hoverTotal = $derived(
    hoverDatum ? series.reduce((s, ser) => s + (hoverDatum![ser.key] || 0), 0) : 0
  );

  function fmtUsd(v: number): string {
    const abs = Math.abs(v);
    if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  }

  function fmtAmt(v: number): string {
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    return `${v.toFixed(2)}`;
  }
  function fmtPct(v: number): string {
    return `${v.toFixed(0)}%`;
  }
  function fmtRatio(v: number): string {
    return v.toFixed(2);
  }
  // Stacked-value formatter for the active denomination.
  function fmtVal(v: number): string {
    return valueFormat === 'pct' ? fmtPct(v) : valueFormat === 'token' ? fmtAmt(v) : fmtUsd(v);
  }

  onMount(() => {
    if (!wrapper) return;
    const c = createChart(wrapper, { ...lwcChartOptions(), height, autoSize: true });
    chart = c;

    // In pct ('100%-stack') mode, pin the right axis floor to 0 so the stack
    // sits on a true 0% base; autoscaleInfoProvider below pins the top to 100.
    c.priceScale('right').applyOptions(
      valueFormat === 'pct'
        ? { visible: true, scaleMargins: { top: 0.06, bottom: 0 } }
        : { visible: true }
    );
    c.priceScale('left').applyOptions({
      visible: false // shown only if a pct line opts in
    });

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
      areaSeries.clear();
      lineSeries.clear();
    };
  });

  $effect(() => {
    void themeStore.theme;
    chart?.applyOptions(lwcChartOptions());
  });

  // Stack layer rebuild. We rebuild the area-series set from scratch any
  // time the series array changes so the add-order — and therefore the
  // visual paint order — is deterministic. Layers must be added in reverse
  // cumulative order so the topmost stack (full total) paints first and
  // gets overdrawn by smaller-cumulative layers below it, producing the
  // band effect of a true stacked chart.
  $effect(() => {
    if (!chart) return;

    // Tear down existing area layers — cheap and avoids ordering bugs when
    // the consumer changes the series list.
    for (const s of areaSeries.values()) chart.removeSeries(s);
    areaSeries.clear();

    const n = series.length;
    if (n === 0 || !data.length) return;

    // Precompute the cumulative-top per layer per timestamp.
    // tops[layer][i] = sum of series[0..layer] at data[i]
    const tops: number[][] = Array.from({ length: n }, () => new Array<number>(data.length));
    for (let i = 0; i < data.length; i++) {
      let acc = 0;
      for (let k = 0; k < n; k++) {
        acc += data[i][series[k].key] || 0;
        tops[k][i] = acc;
      }
    }

    // Add layers in REVERSE so the top-most cumulative paints first.
    for (let k = n - 1; k >= 0; k--) {
      const ser = series[k];
      const common = {
        priceScaleId: 'right',
        priceFormat: {
          type: 'custom' as const,
          formatter: fmtVal,
          minMove: 0.01
        },
        // Pin the axis FLOOR to 0 so band thicknesses are honest proportions.
        // An area series fills from its line down to the price scale's visible
        // minimum, not to zero — so without this, autoscale floats the floor up
        // to the smallest stacked value and the bottom band gets its base
        // chopped off (a true 50/50 buyer/seller split rendered as ~95/05).
        // pct mode additionally pins the top to pctMax (100% / 200%); USD mode
        // keeps the autoscaled top but forces the bottom to 0.
        autoscaleInfoProvider:
          valueFormat === 'pct'
            ? () => ({ priceRange: { minValue: 0, maxValue: pctMax } })
            : (base: () => { priceRange: { minValue: number; maxValue: number } | null } | null) => {
                const r = base();
                if (!r || !r.priceRange) return r;
                return {
                  ...r,
                  priceRange: {
                    minValue: Math.min(0, r.priceRange.minValue),
                    maxValue: r.priceRange.maxValue
                  }
                };
              },
        priceLineVisible: false,
        lastValueVisible: false
      };
      // Opaque histogram bars (bs) vs gradient-filled area (book_depth). The
      // cumulative overdraw is identical either way; histograms just paint a
      // solid bar so overlapping layers read as a clean stack.
      const a = bars
        ? chart.addHistogramSeries({ ...common, color: ser.color })
        : chart.addAreaSeries({
            ...common,
            topColor: ser.color,
            bottomColor: ser.color,
            lineColor: ser.color,
            lineWidth: 1,
            crosshairMarkerVisible: false
          });
      const points = data.map((d, i) => ({
        time: d.time as UTCTimestamp,
        value: tops[k][i]
      }));
      a.setData(points as AreaData[]);
      areaSeries.set(ser.key, a);
    }
  });

  // Overlay lines — same diff-by-key pattern as the other charts.
  $effect(() => {
    if (!chart) return;
    const wanted = new Set(lines.map((l) => l.key));
    for (const [k, s] of lineSeries) {
      if (!wanted.has(k)) {
        chart.removeSeries(s);
        lineSeries.delete(k);
      }
    }

    let hasLeft = false;

    for (const ln of lines) {
      // pct + ratio share the left (secondary) axis; pct formats as %, ratio
      // as a plain number, everything else is right-axis USD.
      const onLeft = ln.scale === 'pct' || ln.scale === 'ratio';
      if (onLeft) hasLeft = true;
      const scaleId = onLeft ? 'left' : 'right';
      let s = lineSeries.get(ln.key);
      if (!s) {
        s = chart.addLineSeries({
          color: ln.color,
          lineWidth: 1,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid,
          priceScaleId: scaleId,
          priceFormat:
            ln.scale === 'pct'
              ? { type: 'custom', formatter: fmtPct, minMove: 0.01 }
              : ln.scale === 'ratio'
                ? { type: 'custom', formatter: fmtRatio, minMove: 0.01 }
                : { type: 'custom', formatter: fmtVal, minMove: 0.01 },
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
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

    // Show the left (secondary) axis when any pct/ratio line is present.
    chart.priceScale('left').applyOptions({ visible: hasLeft });
  });

  // View sync with echo-skip — same pattern as the other Lwc charts.
  $effect(() => {
    if (!chart || !data.length) return;
    const target = view ?? xExtent;
    if (!target) return;
    const first = data[0].time;
    const last = data[data.length - 1].time;
    // Re-apply when the view changed OR when the data extent shifted under us
    // (prepend/append) — the latter keeps a dynamic-loading backfill from
    // jumping the visible window.
    const extentChanged = first !== lastAppliedFirst || last !== lastAppliedLast;
    if (!extentChanged && lastEmittedFrom === target[0] && lastEmittedTo === target[1]) return;
    suppressViewEmit = true;
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
    lastAppliedFirst = first;
    lastAppliedLast = last;
    queueMicrotask(() => {
      suppressViewEmit = false;
    });
  });

  // Parent → chart crosshair. Pin to the full stack total on the topmost
  // area layer so the marker appears at the band the user is reading.
  $effect(() => {
    if (!chart) return;
    if (hoverDatum === null) {
      chart.clearCrosshairPosition();
      return;
    }
    // Topmost layer in the visual stack is the FIRST series we added, which
    // is series[n-1] (because we add in reverse). Its area holds the full
    // cumulative total.
    const top = series.length ? areaSeries.get(series[series.length - 1].key) : null;
    if (!top) {
      chart.clearCrosshairPosition();
      return;
    }
    chart.setCrosshairPosition(hoverTotal, hoverDatum.time as UTCTimestamp, top);
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
      {#if series.length}
        {#each series as ser (ser.key)}
          {@const v = hoverDatum[ser.key] || 0}
          {@const pct = hoverTotal > 0 ? (v / hoverTotal) * 100 : 0}
          <div class="flex items-center gap-2">
            <span class="inline-block w-2 h-2 rounded-sm" style="background: {ser.color}"></span>
            <span class="text-zinc-400 w-20">{ser.label}</span>
            {#if valueFormat === 'pct'}
              <span class="w-20 text-right">{v.toFixed(1)}%</span>
            {:else}
              <span class="w-20 text-right">{fmtVal(v)}</span>
              <span class="w-14 text-right text-zinc-500">{pct.toFixed(1)}%</span>
            {/if}
          </div>
        {/each}
        <div class="mt-1 pt-1 border-t border-zinc-800 flex items-center gap-2">
          <span class="inline-block w-2 h-2"></span>
          <span class="text-zinc-400 w-20">Total</span>
          {#if valueFormat === 'pct'}
            <span class="w-20 text-right">{hoverTotal.toFixed(1)}%</span>
          {:else}
            <span class="w-20 text-right">{fmtVal(hoverTotal)}</span>
            <span class="w-14 text-right text-zinc-500">100.0%</span>
          {/if}
        </div>
      {/if}
      {#if lines.length && hoverIdx !== null}
        <div class="mt-1 pt-1 border-t border-zinc-800"></div>
        {#each lines as ln (ln.key)}
          {@const v = ln.compute(hoverDatum, hoverIdx, data)}
          <div class="flex items-center gap-2">
            <span
              class="inline-block w-3 h-[2px] rounded-sm"
              style="background: {ln.color}; {ln.dash
                ? 'background: repeating-linear-gradient(90deg, ' +
                  ln.color +
                  ' 0 4px, transparent 4px 7px)'
                : ''}"
            ></span>
            <span class="text-zinc-400 w-28">{ln.label}</span>
            <span class="w-12 text-right"></span>
            <span class="w-14 text-right text-zinc-500">{v.toFixed(1)}%</span>
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>
