<script lang="ts">
  import { onMount } from 'svelte';
  import * as d3 from 'd3';
  import { cssVar, themeStore } from '$lib/stores/theme.svelte';
  import { fmtUtcTime } from '$lib/components/charts/config';
  import type { Candle } from '$lib/api';
  import { transformToView, viewToTransform, type View } from '$lib/chart-zoom';

  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Candle, i: number, data: Candle[]) => number;
    dash?: string;
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
    onHover
  }: {
    candles: Candle[];
    lines?: Line[];
    vRefLines?: VRefLine[];
    showCandles?: boolean;
    /** Formatter for the volume value in the hover tooltip. Defaults to
     *  `.toFixed(2)` (token units); ChartInstance passes a compact USD
     *  formatter (e.g. `$42.3M`) when volumeUnit is 'usd'. */
    formatVolume?: (v: number) => string;
    height?: number;
    xExtent?: [number, number];
    view?: View;
    onView?: (v: View) => void;
    hoverTime?: number | null;
    onHover?: (t: number | null) => void;
  } = $props();

  let wrapper = $state<HTMLDivElement | null>(null);
  let svgEl = $state<SVGSVGElement | null>(null);
  let width = $state(800);

  // Bottom margin used to be 26 (same as LineChart) but OHLCV doesn't
  // really need that much room for the date-only tick labels, so the
  // chart ended up with visible dead space below the volume pane.
  const MARGIN = { top: 12, right: 70, bottom: 18, left: 56 };
  const PRICE_FRACTION = 0.78;
  const GAP = 8;

  let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null;
  let chartXScale: d3.ScaleTime<number, number> | null = null;
  let chartYScale: d3.ScaleLinear<number, number> | null = null;
  let chartPlotH = 0;
  let chartPlotW = 0;
  let chartBaseStart = 0;
  let chartBaseEnd = 0;

  // O(log N) hover hit-test. `candles` is time-sorted; bisect to the
  // insertion point then snap to whichever neighbour is closer. The
  // old O(N) linear scan ran on every mouse-move per chart and dominated
  // hover self-time on pages with many charts.
  let hoverIdx = $derived.by(() => {
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
  let hoverCandle = $derived(hoverIdx !== null ? candles[hoverIdx] : null);

  function drawCrosshair() {
    if (!svgEl || !chartXScale || !chartYScale) return;
    const g = d3.select(svgEl).select<SVGGElement>('g.chart-root');
    if (g.empty()) return;
    // Reuse the existing crosshair <g> and its two lines instead of
    // tearing them down + recreating per hover frame. See LineChart for
    // the rationale; OHLCV has a horizontal price line in addition to
    // the vertical time line.
    let cross = g.select<SVGGElement>('g.crosshair');
    let vline: d3.Selection<SVGLineElement, unknown, SVGElement | null, unknown>;
    let hline: d3.Selection<SVGLineElement, unknown, SVGElement | null, unknown>;
    if (cross.empty()) {
      cross = g.append('g').attr('class', 'crosshair').attr('pointer-events', 'none');
      vline = cross
        .append('line')
        .attr('class', 'v')
        .attr('stroke-dasharray', '3,3');
      hline = cross
        .append('line')
        .attr('class', 'h')
        .attr('stroke-dasharray', '3,3');
    } else {
      vline = cross.select<SVGLineElement>('line.v');
      hline = cross.select<SVGLineElement>('line.h');
    }
    if (hoverCandle === null) {
      cross.style('display', 'none');
      return;
    }
    const c = hoverCandle;
    const cx = chartXScale(new Date(c.time * 1000));
    const cy = chartYScale(c.close);
    const stroke = cssVar('--chart-crosshair', '#71717a');
    cross.style('display', null);
    vline.attr('stroke', stroke).attr('x1', cx).attr('x2', cx).attr('y1', 0).attr('y2', chartPlotH);
    hline.attr('stroke', stroke).attr('x1', 0).attr('x2', chartPlotW).attr('y1', cy).attr('y2', cy);
  }

  // Stable clip-path id per component instance. Used to be a fresh
  // Math.random() per draw, which forced the <defs>/<clipPath> to be
  // rebuilt every redraw. With persistent layers we set it once on the
  // first mount and reuse forever.
  const _clipId = `clip-${Math.random().toString(36).slice(2, 9)}`;
  // True once the static SVG skeleton (chart-root, defs, layer groups,
  // overlay rect) has been laid down. Subsequent draws skip the setup
  // and only update data layers via d3 .data().join().
  let _structureBuilt = false;

  function draw() {
    if (!svgEl) return;
    const root = d3.select(svgEl);
    if (!candles.length) {
      // Hide everything but keep the structure so the next draw is fast.
      if (_structureBuilt) root.select<SVGGElement>('g.chart-root').style('display', 'none');
      return;
    }

    const plotW = Math.max(0, width - MARGIN.left - MARGIN.right);
    const plotH = Math.max(0, height - MARGIN.top - MARGIN.bottom);
    const priceH = Math.floor(plotH * PRICE_FRACTION);
    const volH = Math.max(0, plotH - priceH - GAP);

    const baseStart = xExtent ? xExtent[0] : candles[0].time;
    const baseEnd = xExtent ? xExtent[1] : candles[candles.length - 1].time;
    const visibleStart = view ? view[0] : baseStart;
    const visibleEnd = view ? view[1] : baseEnd;
    const xScale = d3
      .scaleTime()
      .domain([new Date(visibleStart * 1000), new Date(visibleEnd * 1000)])
      .range([0, plotW]);

    const v0 = xScale.invert(0).getTime() / 1000;
    const v1 = xScale.invert(plotW).getTime() / 1000;

    // Single pass — replaces the O(N²) `candles.filter` + `candles.indexOf(c)`
    // combo that caused pan/zoom to lag at large N.
    let yLo = Infinity;
    let yHi = -Infinity;
    let visibleCount = 0;
    let vMaxLocal = 0;
    for (let i = 0; i < candles.length; i++) {
      const c = candles[i];
      if (c.time < v0 || c.time > v1) continue;
      visibleCount++;
      if (showCandles) {
        if (c.low < yLo) yLo = c.low;
        if (c.high > yHi) yHi = c.high;
      }
      if (c.volume > vMaxLocal) vMaxLocal = c.volume;
      for (const ln of lines) {
        const v = ln.compute(c, i, candles);
        if (Number.isFinite(v)) {
          if (v < yLo) yLo = v;
          if (v > yHi) yHi = v;
        }
      }
    }
    if (visibleCount === 0) {
      for (let i = 0; i < candles.length; i++) {
        const c = candles[i];
        if (showCandles) {
          if (c.low < yLo) yLo = c.low;
          if (c.high > yHi) yHi = c.high;
        }
        if (c.volume > vMaxLocal) vMaxLocal = c.volume;
        for (const ln of lines) {
          const v = ln.compute(c, i, candles);
          if (Number.isFinite(v)) {
            if (v < yLo) yLo = v;
            if (v > yHi) yHi = v;
          }
        }
      }
    }
    if (!Number.isFinite(yLo) || !Number.isFinite(yHi)) {
      yLo = 0;
      yHi = 1;
    }
    if (yLo === yHi) {
      yLo -= 1;
      yHi += 1;
    }
    const pad = (yHi - yLo) * 0.05 || 1;
    const yScale = d3
      .scaleLinear()
      .domain([yLo - pad, yHi + pad])
      .range([priceH, 0])
      .nice();

    const vMax = vMaxLocal || 1;
    const yVol = d3
      .scaleLinear()
      .domain([0, vMax * 1.1 || 1])
      .range([volH, 0]);

    const bw = (() => {
      if (candles.length < 2) return 4;
      const a = xScale(new Date(candles[0].time * 1000));
      const b = xScale(new Date(candles[1].time * 1000));
      return Math.max(1, (b - a) * 0.8);
    })();

    chartXScale = xScale;
    chartYScale = yScale;
    chartPlotH = priceH;
    chartPlotW = plotW;
    chartBaseStart = baseStart;
    chartBaseEnd = baseEnd;

    // ── persistent SVG skeleton ─────────────────────────────────────
    // Build once; every subsequent draw just updates positions/data.
    // The old code called `root.selectAll('*').remove()` + re-appended
    // ~1000 nodes per zoom rAF; profiles showed that pattern eating
    // 25% of total CPU on a single 4320-candle chart. With persistent
    // layers + d3 .data().join(), only candles entering/leaving the
    // viewport touch the DOM; the rest get attribute updates only.
    let g = root.select<SVGGElement>('g.chart-root');
    if (!_structureBuilt) {
      root.selectAll('*').remove();
      g = root
        .append('g')
        .attr('class', 'chart-root')
        .attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);
      const clip = g
        .append('defs')
        .append('clipPath')
        .attr('id', _clipId)
        .append('rect');
      clip.attr('width', plotW).attr('height', plotH);
      g.append('g').attr('class', 'grid');
      g.append('g').attr('class', 'vrefs').attr('clip-path', `url(#${_clipId})`);
      g.append('g').attr('class', 'candles-wicks').attr('clip-path', `url(#${_clipId})`);
      g.append('g').attr('class', 'candles-bodies').attr('clip-path', `url(#${_clipId})`);
      g.append('g').attr('class', 'lines').attr('clip-path', `url(#${_clipId})`);
      g.append('g').attr('class', 'y-axis');
      g.append('g').attr('class', 'volume').attr('clip-path', `url(#${_clipId})`);
      g.append('g').attr('class', 'x-axis');
      const overlay = g
        .append('rect')
        .attr('class', 'overlay')
        .attr('fill', 'transparent')
        .style('cursor', 'crosshair');
      overlay.on('mousemove', function (event: MouseEvent) {
        const [mx, my] = d3.pointer(event, this);
        if (mx < 0 || mx > chartPlotW || my < 0 || my > chartPlotH) {
          onHover?.(null);
          return;
        }
        onHover?.(chartXScale!.invert(mx).getTime() / 1000);
      });
      overlay.on('mouseleave', () => onHover?.(null));
      _structureBuilt = true;
    } else {
      g.style('display', null);
      g.select<SVGRectElement>(`#${_clipId} rect`).attr('width', plotW).attr('height', plotH);
    }

    // ── grid (cheap to re-render via axis call) ─────────────────────
    const gridSel = g.select<SVGGElement>('g.grid');
    gridSel
      .call(
        d3
          .axisRight<number>(yScale)
          .tickSize(plotW)
          .tickFormat(() => '') as never
      )
      .call((sel) => sel.select('.domain').remove());
    gridSel
      .selectAll<SVGLineElement, unknown>('line')
      .attr('stroke', cssVar('--chart-grid', '#27272a'))
      .attr('stroke-dasharray', '2,3');

    // ── vertical reference lines (week markers etc.) ────────────────
    const visibleVrefs = vRefLines.filter((v) => {
      const x = xScale(new Date(v.time * 1000));
      return x >= -1 && x <= plotW + 1;
    });
    const vrefGrid = cssVar('--chart-grid', '#3f3f46');
    g.select<SVGGElement>('g.vrefs')
      .selectAll<SVGLineElement, VRefLine>('line')
      .data(visibleVrefs, (d) => d.time)
      .join(
        (enter) => enter.append('line').attr('stroke-width', 1),
        (update) => update,
        (exit) => exit.remove()
      )
      .attr('x1', (d) => xScale(new Date(d.time * 1000)))
      .attr('x2', (d) => xScale(new Date(d.time * 1000)))
      .attr('y1', 0)
      .attr('y2', plotH)
      .attr('stroke', (d) => d.color ?? vrefGrid)
      .attr('stroke-dasharray', (d) => d.dash ?? '1,4');

    // ── candles: wicks (<line>) + bodies (<rect>) via data-join ─────
    // Filter to the viewport ± one bar so partially-visible candles
    // still render. Keyed by candle.time so the same DOM element keeps
    // tracking the same candle across pan/zoom — d3 only diff-updates.
    const visibleCandles: Candle[] = showCandles
      ? candles.filter((c) => {
          const x = xScale(new Date(c.time * 1000));
          return x >= -bw && x <= plotW + bw;
        })
      : [];
    const wickKey = (c: Candle) => c.time;
    g.select<SVGGElement>('g.candles-wicks')
      .selectAll<SVGLineElement, Candle>('line')
      .data(visibleCandles, wickKey)
      .join(
        (enter) => enter.append('line'),
        (update) => update,
        (exit) => exit.remove()
      )
      .attr('x1', (c) => xScale(new Date(c.time * 1000)))
      .attr('x2', (c) => xScale(new Date(c.time * 1000)))
      .attr('y1', (c) => yScale(c.high))
      .attr('y2', (c) => yScale(c.low))
      .attr('stroke', (c) => (c.close >= c.open ? '#22c55e' : '#ef4444'));
    g.select<SVGGElement>('g.candles-bodies')
      .selectAll<SVGRectElement, Candle>('rect')
      .data(visibleCandles, wickKey)
      .join(
        (enter) => enter.append('rect'),
        (update) => update,
        (exit) => exit.remove()
      )
      .attr('x', (c) => xScale(new Date(c.time * 1000)) - bw / 2)
      .attr('y', (c) => yScale(Math.max(c.open, c.close)))
      .attr('width', bw)
      .attr('height', (c) =>
        Math.max(1, yScale(Math.min(c.open, c.close)) - yScale(Math.max(c.open, c.close)))
      )
      .attr('fill', (c) => (c.close >= c.open ? '#22c55e' : '#ef4444'));

    // ── indicator overlays ──────────────────────────────────────────
    // Always run the data-join (with an empty array when there are no
    // indicators) so previously-drawn paths get cleaned up.
    let lineSliceStart = 0;
    let lineSliceEnd = candles.length;
    if (candles.length > 2) {
      let lo = 0;
      let hi = candles.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (candles[mid].time < v0) lo = mid + 1;
        else hi = mid;
      }
      lineSliceStart = Math.max(0, lo - 1);
      lo = 0;
      hi = candles.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >>> 1;
        if (candles[mid].time > v1) hi = mid - 1;
        else lo = mid;
      }
      lineSliceEnd = Math.min(candles.length, lo + 2);
    }
    const lineData = candles.slice(lineSliceStart, lineSliceEnd);
    const gen = d3
      .line<Candle>()
      .x((d) => xScale(new Date(d.time * 1000)))
      .curve(d3.curveMonotoneX);
    g.select<SVGGElement>('g.lines')
      .selectAll<SVGPathElement, Line>('path')
      .data(lines, (ln) => ln.key)
      .join(
        (enter) =>
          enter
            .append('path')
            .attr('fill', 'none')
            .attr('stroke-width', 1.5),
        (update) => update,
        (exit) => exit.remove()
      )
      .attr('stroke', (ln) => ln.color)
      .attr('stroke-dasharray', (ln) => ln.dash ?? null)
      .attr('d', (ln) =>
        gen
          .y((d, i) => yScale(ln.compute(d, lineSliceStart + i, candles)))
          .defined((d, i) => Number.isFinite(ln.compute(d, lineSliceStart + i, candles)))(
            lineData
          )
      );

    // ── y-axis (right side) ─────────────────────────────────────────
    const yAxisSel = g.select<SVGGElement>('g.y-axis').attr('transform', `translate(${plotW},0)`);
    yAxisSel.call(d3.axisRight(yScale).ticks(6) as never).call((sel) => {
      sel.select('.domain').remove();
      sel
        .selectAll<SVGTextElement, unknown>('text')
        .attr('fill', cssVar('--chart-axis-text', '#a1a1aa'))
        .attr('font-size', '10px');
      sel
        .selectAll<SVGLineElement, unknown>('line')
        .attr('stroke', cssVar('--chart-axis-line', '#3f3f46'));
    });

    // ── volume pane ─────────────────────────────────────────────────
    const volSel = g
      .select<SVGGElement>('g.volume')
      .attr('transform', `translate(0,${priceH + GAP})`);
    volSel
      .selectAll<SVGRectElement, Candle>('rect')
      .data(visibleCandles, wickKey)
      .join(
        (enter) => enter.append('rect').attr('fill-opacity', 0.55),
        (update) => update,
        (exit) => exit.remove()
      )
      .attr('x', (c) => xScale(new Date(c.time * 1000)) - bw / 2)
      .attr('y', (c) => yVol(c.volume))
      .attr('width', bw)
      .attr('height', (c) => volH - yVol(c.volume))
      .attr('fill', (c) => (c.close >= c.open ? '#22c55e' : '#ef4444'));

    // ── x-axis ──────────────────────────────────────────────────────
    const xAxisSel = g
      .select<SVGGElement>('g.x-axis')
      .attr('transform', `translate(0,${priceH + GAP + volH})`);
    xAxisSel
      .call(d3.axisBottom(xScale).ticks(Math.max(2, Math.floor(plotW / 110))) as never)
      .call((sel) => {
        sel.select('.domain').remove();
        sel
          .selectAll<SVGTextElement, unknown>('text')
          .attr('fill', cssVar('--chart-axis-text', '#a1a1aa'))
          .attr('font-size', '10px');
        sel
          .selectAll<SVGLineElement, unknown>('line')
          .attr('stroke', cssVar('--chart-axis-line', '#3f3f46'));
      });

    // ── interaction overlay sizing ──────────────────────────────────
    g.select<SVGRectElement>('rect.overlay').attr('width', plotW).attr('height', plotH);

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
    candles;
    lines;
    vRefLines;
    showCandles;
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
  <svg bind:this={svgEl} {width} {height} class="block bg-zinc-950"></svg>
  {#if hoverCandle}
    <div
      class="absolute top-2 left-2 px-3 py-2 rounded border border-zinc-700/70 bg-zinc-900/70 text-xs font-mono text-zinc-100 pointer-events-none shadow"
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
          {@const v = ln.rawValue ? ln.rawValue(hoverCandle, hoverIdx, candles) : ln.compute(hoverCandle, hoverIdx, candles)}
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
