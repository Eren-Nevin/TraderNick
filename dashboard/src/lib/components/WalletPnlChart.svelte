<script lang="ts">
  // Small self-contained PnL equity-curve chart for the Smart Wallets
  // dialog's collapsible per-wallet view. Plots a single "total PnL" line
  // as a baseline series (green above 0, red below). Deliberately minimal —
  // no zoom/view sync, no overlays — it just fits its data and shows a
  // crosshair tooltip.
  import { onMount } from 'svelte';
  import {
    createChart,
    LineStyle,
    type IChartApi,
    type IPriceLine,
    type ISeriesApi,
    type UTCTimestamp
  } from 'lightweight-charts';
  import { themeStore } from '$lib/stores/theme.svelte';
  import { lwcChartOptions, lwcTooltipColors } from '$lib/components/charts/lwc/theme';
  import { fmtUsdAxis, fmtUsdTooltip, fmtUtcTime } from '$lib/components/charts/config';
  import { VRefLinesPrimitive } from '$lib/components/charts/lwc/primitives/vRefLine';

  type Point = { time: number; value: number };

  let {
    data = [] as Point[],
    height = 200,
    // Unix seconds at UTC midnight; draws a dashed vertical "cutoff" line
    // (the day the smart-wallets dialog was opened for). null → no line.
    cutoff = null as number | null,
    // Unix seconds; when set, draws a second, thinner dashed line in a
    // different colour marking the start of a metric's lookback window
    // (e.g. the Sharpe lookback) relative to `cutoff`. null → no line.
    lookbackStart = null as number | null,
    // Optional close-price overlay (token), drawn as a blue line on a separate
    // left price scale. Empty → not shown. Must be timeframe-aligned with `data`.
    closeData = [] as Point[],
    // Tooltip label for the plotted value (e.g. "Realized", "Total").
    label = 'PnL',
    // Fixed visible time range (unix seconds). When both are set the time
    // scale is pinned to exactly [rangeFrom, rangeTo] instead of fitting the
    // data — so the plot edges map to known times (lets callers align an
    // external day-slider to the plot area). null/null → fitContent().
    rangeFrom = null as number | null,
    rangeTo = null as number | null,
    // Reports the right price-axis width (px) on mount / resize / data change.
    // Callers use it to pad a slider so its track lines up with the plot area.
    onAxisWidth = undefined as ((w: number) => void) | undefined,
    // Selected token's entry price → horizontal line on the close (left) axis.
    entryPrice = null as number | null,
    // Selected position's open time (unix s) → vertical marker. null → skipped.
    // Only set when the open date is inside the visible window; for earlier
    // opens the caller passes null and supplies `entryNote` instead.
    entryTime = null as number | null,
    // Note shown in-chart when the selected position opened before the visible
    // window (so there's no on-axis marker to draw). null → no note.
    entryNote = null as string | null,
    // Colour for the entry-price line (green in profit, red underwater).
    entryColor = '#34d399',
    // Click → pick a single day (unix s). Drag → pick a [start, end] range.
    // When either is set the chart becomes interactive (cursor + selection).
    onPickDay = undefined as ((unix: number) => void) | undefined,
    onPickRange = undefined as ((startUnix: number, endUnix: number) => void) | undefined
  }: { data?: Point[]; height?: number; cutoff?: number | null; lookbackStart?: number | null; closeData?: Point[]; label?: string; rangeFrom?: number | null; rangeTo?: number | null; onAxisWidth?: (w: number) => void; entryPrice?: number | null; entryTime?: number | null; entryNote?: string | null; entryColor?: string; onPickDay?: (unix: number) => void; onPickRange?: (startUnix: number, endUnix: number) => void } = $props();

  // Cutoff = amber (as-of day); lookback start = thinner sky-blue; entry =
  // emerald (the day the selected position was first opened).
  function buildRefs() {
    const refs: {
      time: number;
      color?: string;
      dash?: string;
      width?: number;
      clamp?: 'left' | 'right';
      label?: string;
    }[] = [];
    if (lookbackStart != null) refs.push({ time: lookbackStart, color: '#38bdf8', dash: '2,3', width: 0.6 });
    // Entry-date line is only drawn when the open date is inside the visible
    // window (the caller passes entryTime=null otherwise and supplies
    // `entryNote` instead). Keep it subtle — a thin emerald dash.
    if (entryTime != null) refs.push({ time: entryTime, color: '#34d399', dash: '4,3', width: 0.7, label: 'entry' });
    if (cutoff != null) refs.push({ time: cutoff, color: '#fbbf24', dash: '4,3' });
    return refs;
  }

  // Fully locked view: no zoom/pan from any input. Reasserted after every
  // applyOptions(lwcChartOptions()) because lwcChartOptions() turns these
  // back ON — without re-merging, the theme effect re-enables interaction.
  const LOCK_INTERACTION = {
    handleScale: {
      mouseWheel: false,
      pinch: false,
      axisPressedMouseMove: false,
      axisDoubleClickReset: false
    },
    handleScroll: {
      mouseWheel: false,
      pressedMouseMove: false,
      horzTouchDrag: false,
      vertTouchDrag: false
    }
  };

  let container = $state<HTMLDivElement | null>(null);
  let chart: IChartApi | null = null;
  let series: ISeriesApi<'Area'> | null = null;
  let closeSeries: ISeriesApi<'Line'> | null = null;
  let vref: VRefLinesPrimitive | null = null;
  let ro: ResizeObserver | null = null;

  let tip = $state<{ x: number; time: number; value: number } | null>(null);

  // Click-to-pick-day / drag-to-pick-range. Pixel x → bar time via the time
  // scale. A tiny move counts as a click; a real drag is a range selection.
  const interactive = $derived(!!onPickDay || !!onPickRange);
  let dragStartX: number | null = null;
  let dragSel = $state<{ left: number; width: number } | null>(null);

  function xInContainer(clientX: number): number {
    const rect = container!.getBoundingClientRect();
    return clientX - rect.left;
  }
  function timeAtX(x: number): number | null {
    if (!chart) return null;
    const t = chart.timeScale().coordinateToTime(x);
    return t == null ? null : (t as unknown as number);
  }
  function onPointerDown(e: PointerEvent) {
    if (!interactive || !container) return;
    dragStartX = xInContainer(e.clientX);
  }
  function onPointerMove(e: PointerEvent) {
    if (dragStartX == null || !container) return;
    const x = xInContainer(e.clientX);
    dragSel = Math.abs(x - dragStartX) > 3
      ? { left: Math.min(x, dragStartX), width: Math.abs(x - dragStartX) }
      : null;
  }
  function onPointerUp(e: PointerEvent) {
    if (dragStartX == null || !container) return;
    const x = xInContainer(e.clientX);
    const moved = Math.abs(x - dragStartX);
    if (moved < 4) {
      const t = timeAtX(dragStartX);
      if (t != null) onPickDay?.(t);
    } else {
      const a = timeAtX(Math.min(x, dragStartX));
      const b = timeAtX(Math.max(x, dragStartX));
      if (a != null && b != null) onPickRange?.(a, b);
    }
    dragStartX = null;
    dragSel = null;
  }

  onMount(() => {
    if (!container) return;
    const base = lwcChartOptions();
    chart = createChart(container, {
      ...base,
      // Larger axis labels than the default 11px.
      layout: { ...base.layout, fontSize: 13 },
      height,
      ...LOCK_INTERACTION
    });
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } });
    // Area (not baseline) so the fill is always UNDER the line — from the line
    // down to the pane bottom — regardless of the curve's sign. A baseline
    // series fills toward price 0, which paints ABOVE the line when PnL is
    // negative. Green line; fill fades green → beige.
    series = chart.addAreaSeries({
      lineColor: '#22c55e',
      topColor: 'rgba(34,197,94,0.35)',
      bottomColor: 'rgba(214,201,168,0.06)',
      lineWidth: 3,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'custom', formatter: fmtUsdAxis, minMove: 0.01 }
    });
    // Zero baseline reference line.
    series.createPriceLine({
      price: 0,
      color: lwcTooltipColors().muted,
      lineStyle: LineStyle.Dotted,
      lineWidth: 1,
      axisLabelVisible: false,
      title: ''
    });

    // Close-price overlay: a blue line on its own LEFT price scale (price units
    // differ from PnL $). Data pushed by the effect below; hidden when empty.
    closeSeries = chart.addLineSeries({
      color: '#3b82f6',
      lineWidth: 1,
      priceScaleId: 'left',
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });
    chart.priceScale('left').applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 }, visible: false });

    // Dashed vertical markers: amber cutoff ("as-of" day) + optional
    // thinner sky-blue lookback-start line.
    vref = new VRefLinesPrimitive(buildRefs(), '#fbbf24');
    series.attachPrimitive(vref);

    chart.subscribeCrosshairMove((p) => {
      if (!p.time || !series || !p.point) { tip = null; return; }
      const v = p.seriesData.get(series) as { value?: number } | undefined;
      if (v?.value === undefined) { tip = null; return; }
      tip = { x: p.point.x, time: p.time as unknown as number, value: v.value };
    });

    ro = new ResizeObserver(() => {
      if (chart && container) {
        chart.applyOptions({ width: container.clientWidth });
        onAxisWidth?.(chart.priceScale('right').width());
      }
    });
    ro.observe(container);

    // Click/drag picking: pointerdown on the chart, move/up on the window so a
    // drag that leaves the chart still resolves.
    container.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);

    return () => {
      ro?.disconnect();
      container?.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      chart?.remove();
      chart = null;
      series = null;
      closeSeries = null;
      vref = null;
    };
  });

  // Keep the marker lines in sync if any ref prop changes.
  $effect(() => {
    void cutoff; void lookbackStart; void entryTime;
    vref?.setRefs(buildRefs(), '#fbbf24');
  });

  // Entry-price horizontal line on the close (left) price scale.
  let entryLine: IPriceLine | null = null;
  $effect(() => {
    if (!closeSeries) return;
    if (entryLine) { closeSeries.removePriceLine(entryLine); entryLine = null; }
    if (entryPrice != null && Number.isFinite(entryPrice)) {
      entryLine = closeSeries.createPriceLine({
        price: entryPrice,
        color: entryColor,
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        axisLabelVisible: true,
        title: 'entry'
      });
    }
  });

  // Close-price overlay: push data + show/hide the left axis with it.
  $effect(() => {
    if (!chart || !closeSeries) return;
    closeSeries.setData(
      closeData
        .filter((d) => Number.isFinite(d.value))
        .map((d) => ({ time: d.time as UTCTimestamp, value: d.value }))
    );
    chart.priceScale('left').applyOptions({ visible: closeData.length > 0 });
  });

  // Re-theme on theme toggle. lwcChartOptions() re-enables scroll/scale, so
  // re-merge the lock to keep the view pinned.
  $effect(() => {
    void themeStore.theme;
    if (chart) {
      const base = lwcChartOptions();
      chart.applyOptions({ ...base, layout: { ...base.layout, fontSize: 13 }, ...LOCK_INTERACTION });
    }
  });

  // Push data + fit.
  $effect(() => {
    if (!series || !chart) return;
    const pts: Array<{ time: UTCTimestamp; value?: number }> = data
      .filter((d) => Number.isFinite(d.value))
      .map((d) => ({ time: d.time as UTCTimestamp, value: d.value }));
    // Anchor any marker time (cutoff / lookback start) that falls OUTSIDE the
    // data's range with a whitespace point (time only, no value). Without it,
    // timeToCoordinate() returns null for a time not on the scale and the
    // vertical reference line silently doesn't render — which is why the
    // lookback line is missing for wallets whose history starts after the
    // lookback window began.
    if (pts.length) {
      const minT = pts[0].time as number;
      const maxT = pts[pts.length - 1].time as number;
      // Also anchor the fixed range edges + entry marker so setVisibleRange has
      // bars to pin to and the entry line's time resolves to a coordinate.
      for (const t of [cutoff, lookbackStart, rangeFrom, rangeTo, entryTime]) {
        if (t != null && (t < minT || t > maxT)) pts.push({ time: t as UTCTimestamp });
      }
      pts.sort((a, b) => (a.time as number) - (b.time as number));
    }
    series.setData(pts as { time: UTCTimestamp; value: number }[]);
    if (rangeFrom != null && rangeTo != null) {
      chart.timeScale().setVisibleRange({
        from: rangeFrom as UTCTimestamp,
        to: rangeTo as UTCTimestamp
      });
    } else {
      chart.timeScale().fitContent();
    }
    onAxisWidth?.(chart.priceScale('right').width());
  });
</script>

<div class="relative w-full" style="height: {height}px">
  <div bind:this={container} class="absolute inset-0" class:cursor-crosshair={interactive}></div>
  {#if dragSel}
    <div class="pointer-events-none absolute top-0 bottom-0 z-10 bg-blue-500/15 border-x border-blue-400/60"
      style="left: {dragSel.left}px; width: {dragSel.width}px"></div>
  {/if}
  {#if entryNote}
    <div
      class="pointer-events-none absolute bottom-1 left-1 z-10 flex items-center gap-1.5 rounded border border-emerald-700/60 bg-zinc-900/90 px-2 py-1 text-xs text-emerald-300 shadow-lg"
    >
      <span class="inline-block h-2.5 w-0.5 bg-emerald-400"></span>{entryNote}
    </div>
  {/if}
  {#if tip}
    <div
      class="pointer-events-none absolute top-1 z-10 rounded border border-zinc-700 bg-zinc-900/95 px-2.5 py-1.5 text-sm leading-snug shadow-lg"
      style="left: {Math.min(Math.max(tip.x - 80, 4), (container?.clientWidth ?? 200) - 170)}px"
    >
      <div class="text-zinc-400">{fmtUtcTime(tip.time).slice(0, 14)}</div>
      <div class={tip.value >= 0 ? 'text-emerald-300' : 'text-red-300'}>
        {label} {fmtUsdTooltip(tip.value)}
      </div>
    </div>
  {/if}
</div>
