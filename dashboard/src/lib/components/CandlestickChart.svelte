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
    g.select('.crosshair').remove();
    if (hoverCandle === null) return;
    const c = hoverCandle;
    const cx = chartXScale(new Date(c.time * 1000));
    const cy = chartYScale(c.close);
    const cross = g.append('g').attr('class', 'crosshair').attr('pointer-events', 'none');
    cross
      .append('line')
      .attr('x1', cx)
      .attr('x2', cx)
      .attr('y1', 0)
      .attr('y2', chartPlotH)
      .attr('stroke', cssVar('--chart-crosshair', '#71717a'))
      .attr('stroke-dasharray', '3,3');
    cross
      .append('line')
      .attr('x1', 0)
      .attr('x2', chartPlotW)
      .attr('y1', cy)
      .attr('y2', cy)
      .attr('stroke', cssVar('--chart-crosshair', '#71717a'))
      .attr('stroke-dasharray', '3,3');
  }

  function draw() {
    if (!svgEl) return;
    const root = d3.select(svgEl);
    root.selectAll('*').remove();
    if (!candles.length) return;

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

    // Vertical reference lines (e.g. week markers). Clip to plot area.
    if (vRefLines.length > 0) {
      const vLayer = g.append('g').attr('class', 'vrefs').attr('clip-path', `url(#${clipId})`);
      for (const v of vRefLines) {
        const x = xScale(new Date(v.time * 1000));
        if (x < -1 || x > plotW + 1) continue;
        vLayer
          .append('line')
          .attr('x1', x)
          .attr('x2', x)
          .attr('y1', 0)
          .attr('y2', plotH)
          .attr('stroke', v.color ?? cssVar('--chart-grid', '#3f3f46'))
          .attr('stroke-width', 1)
          .attr('stroke-dasharray', v.dash ?? '1,4');
      }
    }

    if (showCandles) {
      const gCandles = g.append('g').attr('class', 'candles').attr('clip-path', `url(#${clipId})`);
      for (const c of candles) {
        const x = xScale(new Date(c.time * 1000));
        if (x < -bw || x > plotW + bw) continue;
        const up = c.close >= c.open;
        const color = up ? '#22c55e' : '#ef4444';
        gCandles
          .append('line')
          .attr('x1', x)
          .attr('x2', x)
          .attr('y1', yScale(c.high))
          .attr('y2', yScale(c.low))
          .attr('stroke', color);
        const yTop = yScale(Math.max(c.open, c.close));
        const yBot = yScale(Math.min(c.open, c.close));
        gCandles
          .append('rect')
          .attr('x', x - bw / 2)
          .attr('y', yTop)
          .attr('width', bw)
          .attr('height', Math.max(1, yBot - yTop))
          .attr('fill', color);
      }
    }

    if (lines.length) {
      const lineLayer = g.append('g').attr('class', 'lines').attr('clip-path', `url(#${clipId})`);
      for (const ln of lines) {
        const gen = d3
          .line<Candle>()
          .x((d) => xScale(new Date(d.time * 1000)))
          .y((d, i) => yScale(ln.compute(d, i, candles)))
          .defined((d, i) => Number.isFinite(ln.compute(d, i, candles)))
          .curve(d3.curveMonotoneX);
        const path = lineLayer
          .append('path')
          .datum(candles)
          .attr('fill', 'none')
          .attr('stroke', ln.color)
          .attr('stroke-width', 1.5)
          .attr('d', gen);
        if (ln.dash) path.attr('stroke-dasharray', ln.dash);
      }
    }

    g.append('g')
      .attr('transform', `translate(${plotW},0)`)
      .call(d3.axisRight(yScale).ticks(6))
      .call((sel) => {
        sel.select('.domain').remove();
        sel.selectAll('text').attr('fill', cssVar('--chart-axis-text', '#a1a1aa')).attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', cssVar('--chart-axis-line', '#3f3f46'));
      });

    if (showCandles) {
      const gVol = g
        .append('g')
        .attr('transform', `translate(0,${priceH + GAP})`)
        .attr('clip-path', `url(#${clipId})`);
      for (const c of candles) {
        const x = xScale(new Date(c.time * 1000));
        if (x < -bw || x > plotW + bw) continue;
        const up = c.close >= c.open;
        gVol
          .append('rect')
          .attr('x', x - bw / 2)
          .attr('y', yVol(c.volume))
          .attr('width', bw)
          .attr('height', volH - yVol(c.volume))
          .attr('fill', up ? '#22c55e' : '#ef4444')
          .attr('fill-opacity', 0.55);
      }
    }

    g.append('g')
      .attr('transform', `translate(0,${priceH + GAP + volH})`)
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
      if (mx < 0 || mx > plotW || my < 0 || my > priceH) {
        onHover?.(null);
        return;
      }
      onHover?.(xScale.invert(mx).getTime() / 1000);
    });
    overlay.on('mouseleave', () => onHover?.(null));

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
