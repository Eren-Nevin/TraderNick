<script lang="ts">
  import { onMount } from 'svelte';
  import {
    createChart,
    LineStyle,
    type IChartApi,
    type IPriceLine,
    type ISeriesApi,
    type LineData,
    type SeriesMarker,
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
    /** When 'secondary', the line is scaled against the secondary (left-
     *  side) y-axis. Default 'primary' (right-side, the existing axis). */
    axis?: 'primary' | 'secondary';
    /** Render as isolated dots (no connecting line) — for scatter-style series
     *  whose finite points are sparse/non-contiguous. */
    pointsOnly?: boolean;
    /** If set, draw this text beside each of the series' finite points (with
     *  de-overlap). For scatter series where each point needs a label (e.g. a
     *  token symbol on each dot). */
    pointLabel?: string;
    /** Per-point markers (e.g. a labeled dot per data point). When present, the
     *  series' own point markers are suppressed — these replace them. Must be in
     *  ascending-time order. */
    markers?: {
      time: number;
      position: 'aboveBar' | 'belowBar' | 'inBar';
      color: string;
      shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown';
      text?: string;
      size?: number;
    }[];
    rawValue?: (d: Datum, i: number, data: Datum[]) => number;
    rawFormat?: (v: number) => string;
    /** Optional secondary value shown in parentheses after the main value in
     *  the hover legend (e.g. a bucket's share of the total, as a %). Display-
     *  only — never plotted. */
    pct?: (d: Datum, i: number, data: Datum[]) => number;
  };
  type RefLine = { value: number; label?: string; color?: string; axis?: 'primary' | 'secondary'; width?: number; bold?: boolean };
  type VRefLine = { time: number; color?: string; dash?: string };

  let {
    data = [] as Datum[],
    lines = [] as Line[],
    refLines = [] as RefLine[],
    vRefLines = [] as VRefLine[],
    height = 240,
    title = '',
    xExtent,
    view = null as View,
    onView,
    hoverTime = null,
    onHover,
    onClick,
    formatY = (v: number) => v.toFixed(2),
    formatTooltip = (v: number) => v.toFixed(4),
    formatY2,
    formatTooltip2,
    legendRight = null as { label: string; color: string }[] | null
  }: {
    data: Datum[];
    lines: Line[];
    refLines?: RefLine[];
    vRefLines?: VRefLine[];
    /** Optional persistent legend pinned to the chart's right edge (name + color). */
    legendRight?: { label: string; color: string }[] | null;
    height?: number;
    title?: string;
    xExtent?: [number, number];
    view?: View;
    onView?: (v: View) => void;
    hoverTime?: number | null;
    onHover?: (t: number | null) => void;
    onClick?: (t: number, evt: MouseEvent) => void;
    formatY?: (v: number) => string;
    formatTooltip?: (v: number) => string;
    formatY2?: (v: number) => string;
    formatTooltip2?: (v: number) => string;
  } = $props();

  let wrapper = $state<HTMLDivElement | null>(null);

  let chart: IChartApi | null = null;
  // Keep per-line series + their refLine handles for diff-and-update.
  type LineEntry = {
    series: ISeriesApi<'Line'>;
    axis: 'primary' | 'secondary';
    priceLines: IPriceLine[];
  };
  const lineSeries = new Map<string, LineEntry>();
  let vRefPrimitive: VRefLinesPrimitive | null = null;
  let suppressViewEmit = false;
  // Echo-skip state. Without this, every reactive re-render re-applies
  // setVisibleRange on our own pan state and Lightweight's time-clamped
  // emission narrows the visible window each frame the user drags.
  let lastEmittedFrom: number | null = null;
  let lastEmittedTo: number | null = null;
  // First/last bar time at the last view application. When the data extent
  // changes (e.g. dynamic-loading prepends older history), Lightweight would
  // otherwise hold the logical range and visually shift the window; detecting
  // the extent change lets us re-assert the absolute-time view to pin it.
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

  // First primary series — refLines are drawn against it.
  function firstPrimarySeries(): ISeriesApi<'Line'> | null {
    for (const entry of lineSeries.values()) {
      if (entry.axis === 'primary') return entry.series;
    }
    return null;
  }

  function priceFormatFor(axis: 'primary' | 'secondary') {
    const fn = axis === 'secondary' ? (formatY2 ?? formatY) : formatY;
    return { type: 'custom' as const, formatter: fn, minMove: 0.00000001 };
  }

  // Per-point labels drawn beside each dot (for lines with `pointLabel`). Positions are
  // recomputed from the chart's scales on every data/view change; a greedy de-overlap
  // keeps them readable (labels that would collide are dropped — zoom in for more).
  let pointLabels = $state<{ x: number; y: number; text: string }[]>([]);
  function recomputeLabels() {
    if (!chart || data.length === 0 || !lines.some((l) => l.pointLabel)) {
      if (pointLabels.length) pointLabels = [];
      return;
    }
    const ts = chart.timeScale();
    const lr = ts.getVisibleLogicalRange();
    const lo = lr ? Math.max(0, Math.floor(lr.from)) : 0;
    const hi = lr ? Math.min(data.length - 1, Math.ceil(lr.to)) : data.length - 1;
    const out: { x: number; y: number; text: string }[] = [];
    const placed: { x: number; y: number }[] = [];
    for (const ln of lines) {
      if (!ln.pointLabel) continue;
      const entry = lineSeries.get(ln.key);
      if (!entry) continue;
      for (let i = lo; i <= hi; i++) {
        const v = ln.compute(data[i], i, data);
        if (!Number.isFinite(v)) continue;
        const x = ts.timeToCoordinate(data[i].time as UTCTimestamp);
        if (x === null) continue;
        const y = entry.series.priceToCoordinate(v);
        if (y === null) continue;
        const xn = x as number, yn = y as number;
        if (placed.some((p) => Math.abs(p.x - xn) < 30 && Math.abs(p.y - yn) < 11)) continue;
        placed.push({ x: xn, y: yn });
        out.push({ x: xn + 5, y: yn - 6, text: ln.pointLabel });
      }
    }
    pointLabels = out;
  }
  let _labelRaf = 0;
  function scheduleLabels() {
    if (_labelRaf) return;
    _labelRaf = requestAnimationFrame(() => { _labelRaf = 0; recomputeLabels(); });
  }

  onMount(() => {
    if (!wrapper) return;
    const c = createChart(wrapper, { ...lwcChartOptions(), height, autoSize: true });
    chart = c;

    vRefPrimitive = new VRefLinesPrimitive(vRefLines.slice());

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
      scheduleLabels();
    });

    // Subscribe unconditionally and read `onClick` at click time. Gating the
    // subscription on `onClick` here in onMount meant that enabling wallet-count
    // (which flips `onClick` from undefined to a handler) AFTER the chart had
    // already mounted never bound a listener — onMount doesn't re-run — so the
    // wallet number was unclickable until a full reload. Reading the prop in the
    // callback makes it work whenever the handler is present.
    c.subscribeClick((p) => {
      if (!p.time || !onClick) return;
      const evt = (p.sourceEvent as unknown as MouseEvent) ?? (new MouseEvent('click'));
      onClick(p.time as unknown as number, evt);
    });

    return () => {
      c.remove();
      chart = null;
      lineSeries.clear();
      vRefPrimitive = null;
    };
  });

  $effect(() => {
    void themeStore.theme;
    chart?.applyOptions(lwcChartOptions());
  });

  // Diff line series by key, recompute data each prop change.
  $effect(() => {
    if (!chart) return;
    const wanted = new Set(lines.map((l) => l.key));

    for (const [k, entry] of lineSeries) {
      if (!wanted.has(k)) {
        for (const pl of entry.priceLines) entry.series.removePriceLine(pl);
        chart.removeSeries(entry.series);
        lineSeries.delete(k);
      }
    }

    let firstPrimaryKey: string | null = null;

    for (const ln of lines) {
      const axis: 'primary' | 'secondary' = ln.axis ?? 'primary';
      if (axis === 'primary' && firstPrimaryKey === null) firstPrimaryKey = ln.key;

      let entry = lineSeries.get(ln.key);
      if (!entry || entry.axis !== axis) {
        // Series doesn't exist or has the wrong axis — recreate.
        if (entry) {
          for (const pl of entry.priceLines) entry.series.removePriceLine(pl);
          chart.removeSeries(entry.series);
        }
        const series = chart.addLineSeries({
          color: ln.color,
          lineWidth: 1,
          lineVisible: !ln.pointsOnly,
          pointMarkersVisible: (ln.pointsOnly ?? false) && !(ln.markers?.length),
          pointMarkersRadius: ln.pointsOnly ? 3 : undefined,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid,
          priceScaleId: axis === 'secondary' ? 'left' : 'right',
          priceFormat: priceFormatFor(axis),
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        entry = { series, axis, priceLines: [] };
        lineSeries.set(ln.key, entry);
      } else {
        entry.series.applyOptions({
          color: ln.color,
          lineVisible: !ln.pointsOnly,
          pointMarkersVisible: (ln.pointsOnly ?? false) && !(ln.markers?.length),
          pointMarkersRadius: ln.pointsOnly ? 3 : undefined,
          lineStyle: ln.dash ? LineStyle.Dashed : LineStyle.Solid,
          priceFormat: priceFormatFor(axis)
        });
      }

      const points: LineData[] = [];
      for (let i = 0; i < data.length; i++) {
        const v = ln.compute(data[i], i, data);
        if (Number.isFinite(v)) points.push({ time: data[i].time as UTCTimestamp, value: v });
      }
      entry.series.setData(points);
      entry.series.setMarkers((ln.markers ?? []) as unknown as SeriesMarker<UTCTimestamp>[]);
    }
    scheduleLabels();

    // Show the LEFT price scale's axis labels only when a series actually uses
    // the secondary (left) axis. Lightweight-charts hides the left scale by
    // default, so without this a secondary line (e.g. the close-price overlay
    // in smart-OI chart mode) plots against an invisible axis with no readable
    // scale. Toggling per-render keeps it hidden when nothing needs it.
    const hasSecondary = lines.some((l) => (l.axis ?? 'primary') === 'secondary');
    chart.priceScale('left').applyOptions({ visible: hasSecondary });

    // vRefs primitive is attached to the first primary series (it just needs
    // some series to live on; the renderer reads the chart's time scale).
    if (vRefPrimitive) {
      const host = firstPrimarySeries();
      if (host) {
        host.attachPrimitive(vRefPrimitive);
      }
    }
  });

  // Horizontal reference lines — recreated against the first primary series.
  $effect(() => {
    let primaryEntry: LineEntry | null = null;
    let secondaryEntry: LineEntry | null = null;
    for (const entry of lineSeries.values()) {
      for (const pl of entry.priceLines) entry.series.removePriceLine(pl);
      entry.priceLines = [];
      if (primaryEntry === null && entry.axis === 'primary') primaryEntry = entry;
      if (secondaryEntry === null && entry.axis === 'secondary') secondaryEntry = entry;
    }
    for (const r of refLines) {
      // A price line lives on its host series' scale, so a secondary-axis ref
      // (e.g. the 0% line for a % series) must be drawn on a secondary series.
      const host = r.axis === 'secondary' ? secondaryEntry : primaryEntry;
      if (!host) continue;
      const pl = host.series.createPriceLine({
        price: r.value,
        color: r.color ?? '#71717a',
        lineStyle: r.bold ? LineStyle.Solid : LineStyle.Dashed,
        lineWidth: (r.width ?? 1) as 1 | 2 | 3 | 4,
        axisLabelVisible: true,
        title: r.label ?? ''
      });
      host.priceLines.push(pl);
    }
  });

  $effect(() => {
    vRefPrimitive?.setRefs(vRefLines.slice());
  });

  // View sync with echo-skip. Same shape as LwcCandlestickChart.
  $effect(() => {
    if (!chart || !data.length) return;
    const target = view ?? xExtent;
    if (!target) return;
    const first = data[0].time;
    const last = data[data.length - 1].time;
    // Re-apply when the view changed OR when the data extent shifted under us
    // (prepend/append) — the latter is what keeps a dynamic-loading backfill
    // from jumping the visible window.
    const extentChanged = first !== lastAppliedFirst || last !== lastAppliedLast;
    if (!extentChanged && lastEmittedFrom === target[0] && lastEmittedTo === target[1]) return;
    suppressViewEmit = true;
    // Applying a range can throw "Value is null" if the chart's time scale is
    // momentarily empty — e.g. when this LineChart is freshly mounted by a
    // dual-view Table→Chart swap and the range effect races the first setData.
    // It self-heals on the next data/extent tick, so contain the transient.
    try {
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
    } catch {
      /* transient empty time scale — re-applied on the next tick */
    }
    lastEmittedFrom = target[0];
    lastEmittedTo = target[1];
    lastAppliedFirst = first;
    lastAppliedLast = last;
    queueMicrotask(() => {
      suppressViewEmit = false;
    });
  });

  // Parent → chart crosshair. Pin to the first primary line's value at the
  // snapped index so Lightweight has a valid price to draw the marker at.
  $effect(() => {
    if (!chart || hoverDatum === null) {
      chart?.clearCrosshairPosition();
      return;
    }
    const host = firstPrimarySeries();
    if (!host) {
      chart.clearCrosshairPosition();
      return;
    }
    // Use the first primary line's compute() output for the y position.
    const firstPrimaryLine = lines.find((l) => (l.axis ?? 'primary') === 'primary');
    if (!firstPrimaryLine || hoverIdx === null) {
      chart.clearCrosshairPosition();
      return;
    }
    const v = firstPrimaryLine.compute(hoverDatum, hoverIdx, data);
    if (!Number.isFinite(v)) {
      chart.clearCrosshairPosition();
      return;
    }
    chart.setCrosshairPosition(v, hoverDatum.time as UTCTimestamp, host);
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
      {#each lines as ln (ln.key)}
        {@const v = ln.rawValue
          ? ln.rawValue(hoverDatum, hoverIdx ?? 0, data)
          : ln.compute(hoverDatum, hoverIdx ?? 0, data)}
        <!-- Only list series that actually have a value at this snapshot (a NaN means
             the series has no point here — e.g. a token not in the top-N this bucket). -->
        {#if Number.isFinite(v)}
          {@const fmt =
            ln.rawValue && ln.rawFormat
              ? ln.rawFormat
              : ln.axis === 'secondary'
                ? (formatTooltip2 ?? formatTooltip)
                : formatTooltip}
          {@const pct = ln.pct ? ln.pct(hoverDatum, hoverIdx ?? 0, data) : null}
          <div class="flex items-center gap-2">
            <span class="inline-block w-3 h-[2px]" style="background: {ln.color}"></span>
            <span class="text-zinc-400 w-28">{ln.label}</span>
            <span class="w-20 text-right">{fmt(v)}</span>
            {#if pct !== null && Number.isFinite(pct)}
              <span class="w-12 text-right text-zinc-500">{pct.toFixed(1)}%</span>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  {#if legendRight && legendRight.length}
    <!-- Persistent name list pinned to the right edge (e.g. Relative Performance's
         top-N tokens), so labels don't crowd the dots. -->
    <div class="absolute top-1 right-14 max-h-[94%] overflow-y-auto flex flex-col gap-0.5 px-2 py-1 rounded border border-zinc-600 bg-zinc-950/90 text-[10px] font-mono z-30 scrollbar-none pointer-events-none">
      {#each legendRight as e (e.label)}
        <div class="flex items-center gap-1.5 whitespace-nowrap">
          <span class="inline-block w-2 h-2 rounded-full shrink-0" style="background: {e.color}"></span>
          <span class="text-zinc-200">{e.label}</span>
        </div>
      {/each}
    </div>
  {/if}

  {#each pointLabels as p, i (i)}
    <span
      class="absolute text-[9px] leading-none font-mono text-zinc-100 pointer-events-none whitespace-nowrap z-20"
      style="left: {p.x}px; top: {p.y}px; text-shadow: 0 0 3px #000, 0 0 2px #000;"
    >{p.text}</span>
  {/each}
</div>
