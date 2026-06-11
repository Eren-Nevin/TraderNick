<script lang="ts">
  import { onMount } from 'svelte';
  import * as d3 from 'd3';
  import { cssVar, themeStore } from '$lib/stores/theme.svelte';
  import { fmtUtcTime } from '$lib/components/charts/config';
  import { transformToView, viewToTransform, type View } from '$lib/chart-zoom';

  type Datum = { time: number } & Record<string, number>;
  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Datum, i: number, data: Datum[]) => number;
    dash?: string;
    /** When 'secondary', the line is scaled against the secondary (left-
     *  side) y-axis. Default 'primary' (right-side, the existing axis).
     *  Used by Uniswap amount-mode charts so token0 + token1 can render
     *  on the same chart with independent scales. */
    axis?: 'primary' | 'secondary';
    /** Compound overlay lines call `compute` with a *remapped* value so the
     *  drawn path fits the primary chart's Y range. When set, the tooltip
     *  reads `rawValue` (the line's native unit) instead so the user sees
     *  the real number — e.g. "$42.3M" — rather than the remap scalar. */
    rawValue?: (d: Datum, i: number, data: Datum[]) => number;
    /** Formatter used by the tooltip when `rawValue` is present. Defaults
     *  to the chart's `formatTooltip`. */
    rawFormat?: (v: number) => string;
  };
  type RefLine = { value: number; label?: string; color?: string };
  /** Vertical reference line at a specific Unix-second timestamp. Used for
   *  the optional week-marker overlay (start of each Sat / Mon). */
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
    formatTooltip2
  }: {
    data: Datum[];
    lines: Line[];
    refLines?: RefLine[];
    vRefLines?: VRefLine[];
    height?: number;
    title?: string;
    xExtent?: [number, number];
    view?: View;
    onView?: (v: View) => void;
    hoverTime?: number | null;
    onHover?: (t: number | null) => void;
    /** Single-click on the plot area. Fired with the Unix-second time at
     *  the click x position (snapped to the nearest datum) and the
     *  underlying MouseEvent so the parent can inspect modifiers /
     *  button. The plot's cursor stays as crosshair; parents that wire
     *  this up should make it visually clear what is clickable. */
    onClick?: (t: number, evt: MouseEvent) => void;
    formatY?: (v: number) => string;
    formatTooltip?: (v: number) => string;
    /** Optional axis-2 formatters. Fall back to the primary formatters
     *  when omitted so charts with only one axis still work unchanged. */
    formatY2?: (v: number) => string;
    formatTooltip2?: (v: number) => string;
  } = $props();

  let wrapper = $state<HTMLDivElement | null>(null);
  let svgEl = $state<SVGSVGElement | null>(null);
  let width = $state(800);

  const MARGIN = { top: 12, right: 70, bottom: 26, left: 56 };
  let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null;
  let chartXScale: d3.ScaleTime<number, number> | null = null;
  let chartPlotH = 0;
  let chartPlotW = 0;
  let chartBaseStart = 0;
  let chartBaseEnd = 0;

  // O(log N) hover hit-test. `data` is time-sorted; bisect to the
  // insertion point then snap to whichever neighbour is closer. The
  // old O(N) linear scan ran on every mouse-move per chart and dominated
  // hover self-time on pages with many charts.
  let hoverIdx = $derived.by(() => {
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
  let hoverDatum = $derived(hoverIdx !== null ? data[hoverIdx] : null);

  function drawCrosshair() {
    if (!svgEl || !chartXScale) return;
    const g = d3.select(svgEl).select<SVGGElement>('g.chart-root');
    if (g.empty()) return;
    // Reuse the existing crosshair <line> instead of removing+appending
    // on every mouse-move. Old path created and destroyed ~one DOM node
    // per chart per hover frame; profile shows that dominated chart-side
    // hover cost. The line is hidden via display:none when there's no
    // hovered point, so we don't even pay for the attribute updates in
    // the "moved off the plot" case.
    let line = g.select<SVGLineElement>('line.crosshair');
    if (line.empty()) {
      line = g
        .append('line')
        .attr('class', 'crosshair')
        .attr('pointer-events', 'none')
        .attr('stroke-dasharray', '3,3');
    }
    if (hoverDatum === null) {
      line.style('display', 'none');
      return;
    }
    const cx = chartXScale(new Date(hoverDatum.time * 1000));
    line
      .style('display', null)
      .attr('stroke', cssVar('--chart-crosshair', '#71717a'))
      .attr('x1', cx)
      .attr('x2', cx)
      .attr('y1', 0)
      .attr('y2', chartPlotH);
  }

  function draw() {
    if (!svgEl) return;
    const root = d3.select(svgEl);
    root.selectAll('*').remove();
    if (!data.length || !lines.length) return;

    const plotW = Math.max(0, width - MARGIN.left - MARGIN.right);
    const plotH = Math.max(0, height - MARGIN.top - MARGIN.bottom);

    const baseStart = xExtent ? xExtent[0] : data[0].time;
    const baseEnd = xExtent ? xExtent[1] : data[data.length - 1].time;
    const visibleStart = view ? view[0] : baseStart;
    const visibleEnd = view ? view[1] : baseEnd;
    const xScale = d3
      .scaleTime()
      .domain([new Date(visibleStart * 1000), new Date(visibleEnd * 1000)])
      .range([0, plotW]);

    const v0 = xScale.invert(0).getTime() / 1000;
    const v1 = xScale.invert(plotW).getTime() / 1000;

    // Partition lines by axis so each scale only sees its own values.
    // Secondary defaults to empty → no second axis drawn unless at least
    // one line opts in via axis: 'secondary'.
    const primaryLines = lines.filter((ln) => (ln.axis ?? 'primary') === 'primary');
    const secondaryLines = lines.filter((ln) => ln.axis === 'secondary');
    const hasSecondary = secondaryLines.length > 0;

    function rangeFor(subset: Line[]): [number, number] {
      let lo = Infinity;
      let hi = -Infinity;
      let count = 0;
      for (let i = 0; i < data.length; i++) {
        const d = data[i];
        if (d.time < v0 || d.time > v1) continue;
        count++;
        for (const ln of subset) {
          const v = ln.compute(d, i, data);
          if (Number.isFinite(v)) {
            if (v < lo) lo = v;
            if (v > hi) hi = v;
          }
        }
      }
      if (count === 0) {
        // Nothing in the visible window — scan all data so the axis still
        // has a sane range (matches the prior behaviour).
        for (let i = 0; i < data.length; i++) {
          for (const ln of subset) {
            const v = ln.compute(data[i], i, data);
            if (Number.isFinite(v)) {
              if (v < lo) lo = v;
              if (v > hi) hi = v;
            }
          }
        }
      }
      return [lo, hi];
    }

    let [yMin, yMax] = rangeFor(primaryLines.length ? primaryLines : lines);
    // refLines (e.g. neutral=1 for L/S charts) are always against the primary.
    for (const r of refLines) {
      if (r.value < yMin) yMin = r.value;
      if (r.value > yMax) yMax = r.value;
    }
    if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) {
      yMin = 0;
      yMax = 1;
    }
    if (yMin === yMax) {
      yMin -= 1;
      yMax += 1;
    }
    const pad = (yMax - yMin) * 0.05;
    const yScale = d3
      .scaleLinear()
      .domain([yMin - pad, yMax + pad])
      .range([plotH, 0])
      .nice();

    let yScale2: d3.ScaleLinear<number, number> | null = null;
    if (hasSecondary) {
      let [y2Min, y2Max] = rangeFor(secondaryLines);
      if (!Number.isFinite(y2Min) || !Number.isFinite(y2Max)) {
        y2Min = 0;
        y2Max = 1;
      }
      if (y2Min === y2Max) {
        y2Min -= 1;
        y2Max += 1;
      }
      const pad2 = (y2Max - y2Min) * 0.05;
      yScale2 = d3
        .scaleLinear()
        .domain([y2Min - pad2, y2Max + pad2])
        .range([plotH, 0])
        .nice();
    }

    chartXScale = xScale;
    chartPlotH = plotH;
    chartPlotW = plotW;
    chartBaseStart = baseStart;
    chartBaseEnd = baseEnd;

    const g = root
      .append('g')
      .attr('class', 'chart-root')
      .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    const clipId = `clip-${Math.random().toString(36).slice(2, 9)}`;
    g.append('defs')
      .append('clipPath')
      .attr('id', clipId)
      .append('rect')
      .attr('width', plotW)
      .attr('height', plotH);

    g.append('g')
      .attr('class', 'grid')
      .call(
        d3
          .axisRight(yScale)
          .tickSize(plotW)
          .tickFormat(() => '')
      )
      .call((sel) => sel.select('.domain').remove())
      .selectAll('line')
      .attr('stroke', cssVar('--chart-grid', '#27272a'))
      .attr('stroke-dasharray', '2,3');

    const refLayer = g.append('g').attr('class', 'refs').attr('clip-path', `url(#${clipId})`);
    for (const r of refLines) {
      refLayer
        .append('line')
        .attr('x1', 0)
        .attr('x2', plotW)
        .attr('y1', yScale(r.value))
        .attr('y2', yScale(r.value))
        .attr('stroke', r.color ?? '#52525b')
        .attr('stroke-dasharray', '4,2');
    }
    // Vertical reference lines (e.g. week markers). Clip to plot area.
    for (const v of vRefLines) {
      const x = xScale(new Date(v.time * 1000));
      if (x < -1 || x > plotW + 1) continue;
      refLayer
        .append('line')
        .attr('x1', x)
        .attr('x2', x)
        .attr('y1', 0)
        .attr('y2', plotH)
        .attr('stroke', v.color ?? cssVar('--chart-grid', '#3f3f46'))
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', v.dash ?? '1,4');
    }

    // Viewport-slice the data for the line path. d3.line() walks every
    // input point, runs the curve interpolator on each (curveMonotoneX
    // is the dominant pan/zoom cost in profiles), and emits a path
    // string the SVG clip-path then trims. At 180d×1h that's 4320 points
    // even when only ~336 are visible — ~93% wasted work per redraw.
    // Slice to first-before-viewport … last-after-viewport so the curve
    // continues smoothly past the clip edge. `compute(d, i, data)`
    // semantics are preserved by remapping `i` back to the original
    // index (MAs etc. read other rows in `data` by index).
    let lineSliceStart = 0;
    let lineSliceEnd = data.length;
    if (data.length > 2) {
      let lo = 0;
      let hi = data.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (data[mid].time < v0) lo = mid + 1;
        else hi = mid;
      }
      lineSliceStart = Math.max(0, lo - 1);
      lo = 0;
      hi = data.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >>> 1;
        if (data[mid].time > v1) hi = mid - 1;
        else lo = mid;
      }
      lineSliceEnd = Math.min(data.length, lo + 2);
    }
    const lineData = data.slice(lineSliceStart, lineSliceEnd);

    const lineLayer = g.append('g').attr('class', 'lines').attr('clip-path', `url(#${clipId})`);
    for (const ln of lines) {
      const scale = ln.axis === 'secondary' && yScale2 ? yScale2 : yScale;
      const gen = d3
        .line<Datum>()
        .x((d) => xScale(new Date(d.time * 1000)))
        .y((d, i) => scale(ln.compute(d, lineSliceStart + i, data)))
        .defined((d, i) => Number.isFinite(ln.compute(d, lineSliceStart + i, data)))
        .curve(d3.curveMonotoneX);
      const path = lineLayer
        .append('path')
        .datum(lineData)
        .attr('fill', 'none')
        .attr('stroke', ln.color)
        .attr('stroke-width', 1.5)
        .attr('d', gen);
      if (ln.dash) path.attr('stroke-dasharray', ln.dash);
    }

    // Primary (right-side) axis — unchanged location.
    g.append('g')
      .attr('transform', `translate(${plotW},0)`)
      .call(
        d3
          .axisRight(yScale)
          .ticks(5)
          .tickFormat((d) => formatY(d as number))
      )
      .call((sel) => {
        sel.select('.domain').remove();
        sel.selectAll('text').attr('fill', cssVar('--chart-axis-text', '#a1a1aa')).attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', cssVar('--chart-axis-line', '#3f3f46'));
      });

    // Secondary (left-side) axis — only drawn when at least one line opted
    // in. Uses the leftover MARGIN.left padding so we don't have to widen
    // the chart for the extra labels.
    if (yScale2) {
      const fmt2 = formatY2 ?? formatY;
      g.append('g')
        .attr('transform', `translate(0,0)`)
        .call(
          d3
            .axisLeft(yScale2)
            .ticks(5)
            .tickFormat((d) => fmt2(d as number))
        )
        .call((sel) => {
          sel.select('.domain').remove();
          sel.selectAll('text').attr('fill', cssVar('--chart-axis-text', '#a1a1aa')).attr('font-size', '10px');
          sel.selectAll('line').attr('stroke', cssVar('--chart-axis-line', '#3f3f46'));
        });
    }

    g.append('g')
      .attr('transform', `translate(0,${plotH})`)
      .call(d3.axisBottom(xScale).ticks(Math.max(2, Math.floor(plotW / 110))))
      .call((sel) => {
        sel.select('.domain').remove();
        sel.selectAll('text').attr('fill', cssVar('--chart-axis-text', '#a1a1aa')).attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', cssVar('--chart-axis-line', '#3f3f46'));
      });

    const overlay = g
      .append('rect')
      .attr('class', 'overlay')
      .attr('width', plotW)
      .attr('height', plotH)
      .attr('fill', 'transparent')
      .style('cursor', 'crosshair');

    overlay.on('mousemove', function (event: MouseEvent) {
      const [mx, my] = d3.pointer(event, this);
      if (mx < 0 || mx > plotW || my < 0 || my > plotH) {
        onHover?.(null);
        return;
      }
      onHover?.(xScale.invert(mx).getTime() / 1000);
    });
    overlay.on('mouseleave', () => onHover?.(null));
    if (onClick) {
      overlay.on('click', function (event: MouseEvent) {
        const [mx, my] = d3.pointer(event, this);
        if (mx < 0 || mx > plotW || my < 0 || my > plotH) return;
        const t = xScale.invert(mx).getTime() / 1000;
        onClick(t, event);
      });
      overlay.style('cursor', 'pointer');
    }

    drawCrosshair();
  }

  onMount(() => {
    if (!svgEl || !wrapper) return;
    width = wrapper.clientWidth || 800;

    zoomBehavior = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.5, 80])
      .filter((event) => {
        if (event.type === 'dblclick') return false;
        if (svgEl) {
          const t = viewToTransform(view, chartBaseStart, chartBaseEnd, chartPlotW);
          (svgEl as unknown as { __zoom: d3.ZoomTransform }).__zoom = t;
        }
        return true;
      })
      .on('zoom', (event) => {
        if (!event.sourceEvent) return;
        const v = transformToView(event.transform, chartBaseStart, chartBaseEnd, chartPlotW);
        onView?.(v);
      });
    const root = d3.select(svgEl);
    root.call(zoomBehavior).on('dblclick.zoom', null);
    root.on('dblclick', () => onView?.(null));

    const ro = new ResizeObserver((entries) => {
      const w = entries[0].contentRect.width;
      if (w > 0 && Math.abs(w - width) > 0.5) {
        width = w;
        draw();
      }
    });
    ro.observe(wrapper);
    return () => ro.disconnect();
  });

  let _drawRaf: number | null = null;
  function scheduleDraw() {
    if (_drawRaf != null) return;
    _drawRaf = requestAnimationFrame(() => {
      _drawRaf = null;
      if (svgEl && zoomBehavior) {
        const t = viewToTransform(view, chartBaseStart, chartBaseEnd, chartPlotW);
        d3.select(svgEl).call(zoomBehavior.transform, t);
      }
      draw();
    });
  }

  $effect(() => {
    data;
    lines;
    refLines;
    vRefLines;
    xExtent;
    view;
    width;
    void themeStore.theme;
    scheduleDraw();
  });

  $effect(() => {
    hoverTime;
    void themeStore.theme;
    drawCrosshair();
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
  <svg bind:this={svgEl} {width} {height} class="block bg-zinc-950"></svg>
  {#if hoverDatum}
    <div
      class="absolute top-2 right-20 px-3 py-2 rounded border border-zinc-700/70 bg-zinc-900/70 text-xs font-mono text-zinc-100 pointer-events-none shadow"
    >
      <div class="text-zinc-400">
        {fmtUtcTime(hoverDatum.time)}
      </div>
      {#each lines as ln (ln.key)}
        {@const v = ln.rawValue ? ln.rawValue(hoverDatum, hoverIdx ?? 0, data) : ln.compute(hoverDatum, hoverIdx ?? 0, data)}
        {@const fmt = ln.rawValue && ln.rawFormat
                      ? ln.rawFormat
                      : (ln.axis === 'secondary' ? (formatTooltip2 ?? formatTooltip) : formatTooltip)}
        <div class="flex items-center gap-2">
          <span class="inline-block w-3 h-[2px]" style="background: {ln.color}"></span>
          <span class="text-zinc-400 w-28">{ln.label}</span>
          <span class="w-20 text-right">{fmt(v)}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>
