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
  import { VBandPrimitive } from '$lib/components/charts/lwc/primitives/vBand';
  import { TradeChipsPrimitive } from '$lib/components/charts/lwc/primitives/tradeChips';

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
    // When true, show a non-blocking loading overlay (e.g. while the snapshot's
    // positions are being fetched after the user picks a day).
    loading = false,
    // Selected date-range band (range mode): tints [bandFrom, bandTo] blue.
    bandFrom = null as number | null,
    bandTo = null as number | null,
    // Buy/sell chips ('Show Trades'): one per day. `value` anchors the chip to
    // the curve point at that time; `side` sets colour/position; `text` is the
    // label (net magnitude).
    trades = [] as Array<{ time: number; value: number; side: 'buy' | 'sell'; text: string }>,
    // Range mode: left-drag selects a range (else it pans; middle-drag always
    // pans). Changes the gesture the pointer handler performs on a body drag.
    rangeMode = false,
    // Click → pick a single day (unix s). Drag → pick a [start, end] range.
    // When either is set the chart becomes interactive (cursor + selection).
    onPickDay = undefined as ((unix: number) => void) | undefined,
    onPickRange = undefined as ((startUnix: number, endUnix: number) => void) | undefined
  }: { data?: Point[]; height?: number; cutoff?: number | null; lookbackStart?: number | null; closeData?: Point[]; label?: string; rangeFrom?: number | null; rangeTo?: number | null; onAxisWidth?: (w: number) => void; entryPrice?: number | null; entryTime?: number | null; entryNote?: string | null; entryColor?: string; onPickDay?: (unix: number) => void; onPickRange?: (startUnix: number, endUnix: number) => void; loading?: boolean; bandFrom?: number | null; bandTo?: number | null; trades?: Array<{ time: number; value: number; side: 'buy' | 'sell'; text: string }>; rangeMode?: boolean } = $props();

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

  // Range-mode shading. The selected [bandFrom, bandTo] window gets a subtle
  // blue tint behind the curve; everything outside it is dimmed by a heavy gray
  // mask drawn on top, so attention is pinned to the selected range.
  const BLUE = 'rgba(59,130,246,0.12)';
  const GRAY = 'rgba(24,24,27,0.72)';
  function bandRange(): { lo: number; hi: number } | null {
    if (bandFrom == null || bandTo == null || bandFrom === bandTo) return null;
    return { lo: Math.min(bandFrom, bandTo), hi: Math.max(bandFrom, bandTo) };
  }
  // Blue highlight over the selected range (drawn behind the curve).
  function highlightBands() {
    const r = bandRange();
    return r ? [{ from: r.lo, to: r.hi, color: BLUE }] : [];
  }
  // Gray dimming of the regions before/after the selected range (drawn on top).
  // Null edges clamp to the chart's left/right border.
  function maskBands() {
    const r = bandRange();
    if (!r) return [];
    return [
      { from: null, to: r.lo, color: GRAY },
      { from: r.hi, to: null, color: GRAY }
    ];
  }

  // Fully locked view: no zoom/pan from any input. Reasserted after every
  // applyOptions(lwcChartOptions()) because lwcChartOptions() turns these
  // back ON — without re-merging, the theme effect re-enables interaction.
  //
  // X-axis only: wheel/pinch zoom + horizontal-wheel/touch/axis-drag pan, with
  // the PRICE (vertical) axis locked. Body `pressedMouseMove` stays OFF so a
  // plain drag remains a range-selection (not a pan); panning by drag is done
  // on the time axis (axisPressedMouseMove), which the pointer handler yields to
  // by ignoring presses that start in the axis strip. Double-click the axis to
  // reset the zoom.
  const INTERACTION = {
    handleScale: {
      mouseWheel: true,
      pinch: true,
      axisPressedMouseMove: { time: true, price: false },
      axisDoubleClickReset: { time: true, price: false }
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: false,
      horzTouchDrag: true,
      vertTouchDrag: false
    }
  };

  let container = $state<HTMLDivElement | null>(null);
  let chart: IChartApi | null = null;
  let series: ISeriesApi<'Baseline'> | null = null;
  let closeSeries: ISeriesApi<'Line'> | null = null;
  let vref: VRefLinesPrimitive | null = null;
  let bandHi: VBandPrimitive | null = null;
  let bandMask: VBandPrimitive | null = null;
  let tradeChips: TradeChipsPrimitive | null = null;
  let ro: ResizeObserver | null = null;
  // Last applied initial window. Pin setVisibleRange only when the window
  // (rangeFrom/rangeTo) actually changes — NOT on every cutoff/marker/data
  // update — so the user's manual zoom/pan isn't reset on each interaction.
  let pinnedFrom: number | null = null;
  let pinnedTo: number | null = null;

  let tip = $state<{ x: number; time: number; value: number } | null>(null);

  // Pointer gestures:
  //   • click (any mode)            → pick a single day (snapshot)
  //   • drag in range mode (left)   → select a range
  //   • drag otherwise / middle-drag→ pan the x-axis
  const interactive = $derived(!!onPickDay || !!onPickRange);
  let gesture: 'none' | 'range' | 'pan' = 'none';
  let dragStartX: number | null = null;
  let dragIsLeft = true;
  let dragSel = $state<{ left: number; width: number } | null>(null);
  // Pan state captured at press: the visible window + seconds-per-pixel, so the
  // drag maps pixels → a window shift.
  let panFrom = 0;
  let panTo = 0;
  let panPerPx = 0;

  function xInContainer(clientX: number): number {
    const rect = container!.getBoundingClientRect();
    return clientX - rect.left;
  }
  function timeAtX(x: number): number | null {
    if (!chart) return null;
    // `x` is measured from the container's left edge, but the time scale's
    // coordinate space starts at the plot area — i.e. AFTER the left price axis
    // (the close-price scale, shown when a token is selected). Subtract its
    // width or clicks drift right by that many pixels (the snapshot-time bug).
    const leftAxisW = chart.priceScale('left').width();
    const t = chart.timeScale().coordinateToTime(x - leftAxisW);
    return t == null ? null : (t as unknown as number);
  }
  function beginPan(): boolean {
    if (!chart) return false;
    const r = chart.timeScale().getVisibleRange();
    const w = chart.timeScale().width();
    if (!r || w <= 0) return false;
    panFrom = r.from as unknown as number;
    panTo = r.to as unknown as number;
    panPerPx = (panTo - panFrom) / w;
    return true;
  }
  function doPan(x: number) {
    if (!chart || dragStartX == null) return;
    // Drag right → reveal earlier data (window shifts left). clampVisible (the
    // visible-range subscription) keeps it inside [rangeFrom, rangeTo].
    const dt = -(x - dragStartX) * panPerPx;
    try {
      chart.timeScale().setVisibleRange({
        from: (panFrom + dt) as UTCTimestamp,
        to: (panTo + dt) as UTCTimestamp
      });
    } catch {
      /* out-of-range edge — ignore */
    }
  }
  // Keep the visible window inside the fixed total range [rangeFrom, rangeTo]:
  // never wider (no zooming out past the full span) and never panned past an
  // edge. Runs on every visible-range change (native zoom/pan + manual pan).
  let clamping = false;
  function clampVisible() {
    if (clamping || !chart || rangeFrom == null || rangeTo == null) return;
    const r = chart.timeScale().getVisibleRange();
    if (!r) return;
    const from = r.from as unknown as number;
    const to = r.to as unknown as number;
    const total = rangeTo - rangeFrom;
    let nf = from;
    let nt = to;
    if (to - from >= total) {
      // Wider than the full span → snap to the full range.
      nf = rangeFrom;
      nt = rangeTo;
    } else {
      // Same width, shifted out of bounds → slide back inside.
      if (nf < rangeFrom) { nt += rangeFrom - nf; nf = rangeFrom; }
      if (nt > rangeTo) { nf -= nt - rangeTo; nt = rangeTo; }
      nf = Math.max(nf, rangeFrom);
      nt = Math.min(nt, rangeTo);
    }
    if (Math.abs(nf - from) > 0.5 || Math.abs(nt - to) > 0.5) {
      clamping = true;
      try {
        chart.timeScale().setVisibleRange({ from: nf as UTCTimestamp, to: nt as UTCTimestamp });
      } catch {
        /* ignore */
      }
      clamping = false;
    }
  }
  function onPointerDown(e: PointerEvent) {
    if (!interactive || !container || !chart) return;
    // Yield presses that start in the bottom time-axis strip to the chart's
    // native handlers (axis drag = pan/scale, double-click = reset zoom).
    const rect = container.getBoundingClientRect();
    const axisH = chart.timeScale().height();
    if (e.clientY - rect.top >= rect.height - axisH) return;
    if (e.button !== 0 && e.button !== 1) return; // left or middle only
    dragIsLeft = e.button === 0;
    dragStartX = xInContainer(e.clientX);
    // Left-drag in range mode selects; everything else (non-range left, or any
    // middle drag) pans.
    if (rangeMode && dragIsLeft) {
      gesture = 'range';
    } else {
      gesture = beginPan() ? 'pan' : 'none';
      if (e.button === 1) e.preventDefault(); // suppress middle-click autoscroll
    }
  }
  function onPointerMove(e: PointerEvent) {
    if (gesture === 'none' || dragStartX == null || !container) return;
    const x = xInContainer(e.clientX);
    if (gesture === 'range') {
      dragSel = Math.abs(x - dragStartX) > 3
        ? { left: Math.min(x, dragStartX), width: Math.abs(x - dragStartX) }
        : null;
    } else {
      doPan(x);
    }
  }
  function onPointerUp(e: PointerEvent) {
    if (gesture === 'none' || dragStartX == null || !container) return;
    const x = xInContainer(e.clientX);
    const moved = Math.abs(x - dragStartX);
    if (moved < 4) {
      // A click (negligible movement) picks the day — but only on left button.
      if (dragIsLeft) {
        const t = timeAtX(dragStartX);
        if (t != null) onPickDay?.(t);
      }
    } else if (gesture === 'range') {
      const a = timeAtX(Math.min(x, dragStartX));
      const b = timeAtX(Math.max(x, dragStartX));
      if (a != null && b != null) onPickRange?.(a, b);
    }
    gesture = 'none';
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
      ...INTERACTION
    });
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } });
    // Baseline series split at PnL = 0: green line + green fill above the zero
    // line, red line + red fill below it. The fill spans line → baseline (0),
    // so above-zero it sits under the line and below-zero it sits above the
    // line, up to zero — the standard sign-coded equity-curve look.
    series = chart.addBaselineSeries({
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#22c55e',
      topFillColor1: 'rgba(34,197,94,0.35)',
      topFillColor2: 'rgba(34,197,94,0.04)',
      bottomLineColor: '#ef4444',
      bottomFillColor1: 'rgba(239,68,68,0.04)',
      bottomFillColor2: 'rgba(239,68,68,0.35)',
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
    // Range-mode shading: blue highlight behind the curve, gray mask on top.
    bandHi = new VBandPrimitive(highlightBands(), 'bottom');
    series.attachPrimitive(bandHi);

    vref = new VRefLinesPrimitive(buildRefs(), '#fbbf24');
    series.attachPrimitive(vref);

    // Attached last + drawn on top so it dims the curve outside the range.
    bandMask = new VBandPrimitive(maskBands(), 'top');
    series.attachPrimitive(bandMask);

    // Buy/sell chips on top of everything (incl. the mask) so they stay legible.
    tradeChips = new TradeChipsPrimitive(trades);
    series.attachPrimitive(tradeChips);

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

    // Enforce the zoom-out / pan bounds on every visible-range change.
    chart.timeScale().subscribeVisibleTimeRangeChange(clampVisible);

    return () => {
      ro?.disconnect();
      chart?.timeScale().unsubscribeVisibleTimeRangeChange(clampVisible);
      container?.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      chart?.remove();
      chart = null;
      series = null;
      closeSeries = null;
      vref = null;
      bandHi = null;
      bandMask = null;
      tradeChips = null;
    };
  });

  // Keep the marker lines in sync if any ref prop changes.
  $effect(() => {
    void cutoff; void lookbackStart; void entryTime;
    vref?.setRefs(buildRefs(), '#fbbf24');
  });

  // Keep the range-mode shading in sync.
  $effect(() => {
    void bandFrom; void bandTo;
    bandHi?.setBands(highlightBands());
    bandMask?.setBands(maskBands());
  });

  // Buy/sell chips ('Show Trades') — styled tags anchored to the curve.
  $effect(() => {
    void trades;
    tradeChips?.setChips(trades.map((t) => ({ ...t })));
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
    // Sort + dedupe defensively — setData throws on unsorted/duplicate times,
    // which would blank the chart (see the main series effect below).
    const seen = new Set<number>();
    const cpts = closeData
      .filter((d) => Number.isFinite(d.value))
      .map((d) => ({ time: d.time as UTCTimestamp, value: d.value }))
      .sort((a, b) => (a.time as number) - (b.time as number))
      .filter((d) => (seen.has(d.time as number) ? false : (seen.add(d.time as number), true)));
    try {
      closeSeries.setData(cpts);
      chart.priceScale('left').applyOptions({ visible: cpts.length > 0 });
    } catch (err) {
      console.error('WalletPnlChart: close setData failed', err);
    }
  });

  // Re-theme on theme toggle. lwcChartOptions() re-enables scroll/scale, so
  // re-merge the lock to keep the view pinned.
  $effect(() => {
    void themeStore.theme;
    if (chart) {
      const base = lwcChartOptions();
      chart.applyOptions({ ...base, layout: { ...base.layout, fontSize: 13 }, ...INTERACTION });
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
    // Dedupe by time: several anchor markers can resolve to the same out-of-range
    // timestamp (e.g. entryTime == rangeFrom), and setData() throws on duplicate
    // times — an uncaught throw here blanks the whole chart. Keep the valued
    // point over a bare whitespace anchor when both share a time.
    const deduped: Array<{ time: UTCTimestamp; value?: number }> = [];
    for (const p of pts) {
      const prev = deduped[deduped.length - 1];
      if (prev && (prev.time as number) === (p.time as number)) {
        if (prev.value === undefined && p.value !== undefined) deduped[deduped.length - 1] = p;
      } else {
        deduped.push(p);
      }
    }
    // setData / setVisibleRange validate their inputs and throw on bad data;
    // never let that escape the effect (a throw leaves the chart painted black).
    try {
      series.setData(deduped as { time: UTCTimestamp; value: number }[]);
      if (rangeFrom != null && rangeTo != null) {
        // Only (re)pin when the requested window changes; otherwise leave the
        // user's current zoom/pan untouched (setData keeps the visible range).
        if (rangeFrom !== pinnedFrom || rangeTo !== pinnedTo) {
          chart.timeScale().setVisibleRange({
            from: rangeFrom as UTCTimestamp,
            to: rangeTo as UTCTimestamp
          });
          pinnedFrom = rangeFrom;
          pinnedTo = rangeTo;
        }
      } else {
        chart.timeScale().fitContent();
      }
      onAxisWidth?.(chart.priceScale('right').width());
    } catch (err) {
      console.error('WalletPnlChart: setData/setVisibleRange failed', err);
    }
  });
</script>

<div class="relative w-full" style="height: {height}px">
  <div bind:this={container} class="absolute inset-0"
    class:cursor-crosshair={interactive && rangeMode}
    class:cursor-grab={interactive && !rangeMode}></div>
  {#if dragSel}
    <div class="pointer-events-none absolute top-0 bottom-0 z-10 bg-blue-500/15 border-x border-blue-400/60"
      style="left: {dragSel.left}px; width: {dragSel.width}px"></div>
  {/if}
  {#if loading}
    <!-- Non-blocking loading badge: chart stays interactive while the picked
         snapshot's positions are fetched. -->
    <div class="pointer-events-none absolute top-1 right-1 z-20 flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900/90 px-2 py-1 text-xs text-zinc-300 shadow-lg">
      <span class="inline-block h-3 w-3 animate-spin rounded-full border-2 border-zinc-600 border-t-blue-400"></span>
      loading…
    </div>
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
