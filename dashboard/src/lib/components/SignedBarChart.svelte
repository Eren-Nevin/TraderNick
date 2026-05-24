<script lang="ts">
  import { onMount } from 'svelte';
  import * as d3 from 'd3';
  import { cssVar, themeStore } from '$lib/stores/theme.svelte';
  import { transformToView, viewToTransform, type View } from '$lib/chart-zoom';

  type Datum = { time: number } & Record<string, number>;
  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Datum, i: number, data: Datum[]) => number;
    dash?: string;
  };

  let {
    data = [] as Datum[],
    valueKey,
    lines = [] as Line[],
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
    minBarWidthPx = 2,
    valueLabel = 'Value'
  }: {
    data: Datum[];
    valueKey: string;
    lines?: Line[];
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
    minBarWidthPx?: number;
    valueLabel?: string;
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

  let hoverIdx = $derived.by(() => {
    if (hoverTime === null || !data.length) return null;
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < data.length; i++) {
      const dd = Math.abs(data[i].time - hoverTime);
      if (dd < bestDist) {
        bestDist = dd;
        best = i;
      }
    }
    return best;
  });
  let hoverDatum = $derived(hoverIdx !== null ? data[hoverIdx] : null);

  function drawCrosshair() {
    if (!svgEl || !chartXScale) return;
    const g = d3.select(svgEl).select<SVGGElement>('g.chart-root');
    if (g.empty()) return;
    g.select('.crosshair').remove();
    if (hoverDatum === null) return;
    const cx = chartXScale(new Date(hoverDatum.time * 1000));
    g.append('line')
      .attr('class', 'crosshair')
      .attr('pointer-events', 'none')
      .attr('x1', cx)
      .attr('x2', cx)
      .attr('y1', 0)
      .attr('y2', chartPlotH)
      .attr('stroke', cssVar('--chart-crosshair', '#71717a'))
      .attr('stroke-dasharray', '3,3');
  }

  function draw() {
    if (!svgEl) return;
    const root = d3.select(svgEl);
    root.selectAll('*').remove();
    if (!data.length) return;

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

    // Single pass — replaces O(N²) `data.filter` + `data.indexOf(r)` combo.
    let yMin = 0;
    let yMax = 0;
    for (let i = 0; i < data.length; i++) {
      const d = data[i];
      if (d.time < v0 || d.time > v1) continue;
      if (showBars) {
        const v = d[valueKey] ?? 0;
        if (v < yMin) yMin = v;
        if (v > yMax) yMax = v;
      }
      for (const ln of lines) {
        const v = ln.compute(d, i, data);
        if (Number.isFinite(v)) {
          if (v < yMin) yMin = v;
          if (v > yMax) yMax = v;
        }
      }
    }
    const spread = Math.max(Math.abs(yMin), Math.abs(yMax)) || 1;
    yMin = -spread;
    yMax = spread;
    const pad = (yMax - yMin) * 0.05;
    const yScale = d3
      .scaleLinear()
      .domain([yMin - pad, yMax + pad])
      .range([plotH, 0])
      .nice();

    const bw = (() => {
      if (data.length < 2) return Math.max(minBarWidthPx, 6);
      const a = xScale(new Date(data[0].time * 1000));
      const b = xScale(new Date(data[1].time * 1000));
      return Math.max(minBarWidthPx, (b - a) * 0.7);
    })();

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

    const zeroY = yScale(0);
    g.append('line')
      .attr('class', 'zero')
      .attr('x1', 0)
      .attr('x2', plotW)
      .attr('y1', zeroY)
      .attr('y2', zeroY)
      .attr('stroke', '#52525b')
      .attr('stroke-width', 1);

    if (showBars) {
      const bars = g.append('g').attr('class', 'bars').attr('clip-path', `url(#${clipId})`);
      for (const d of data) {
        const x = xScale(new Date(d.time * 1000));
        if (x < -bw || x > plotW + bw) continue;
        const v = d[valueKey] ?? 0;
        const y = yScale(v);
        const yTop = Math.min(y, zeroY);
        const h = Math.max(0.5, Math.abs(y - zeroY));
        bars
          .append('rect')
          .attr('x', x - bw / 2)
          .attr('y', yTop)
          .attr('width', bw)
          .attr('height', h)
          .attr('fill', v >= 0 ? posColor : negColor);
      }
    }

    if (lines.length) {
      const lineLayer = g
        .append('g')
        .attr('class', 'lines')
        .attr('clip-path', `url(#${clipId})`);
      for (const ln of lines) {
        const gen = d3
          .line<Datum>()
          .x((d) => xScale(new Date(d.time * 1000)))
          .y((d, i) => yScale(ln.compute(d, i, data)))
          .defined((d, i) => Number.isFinite(ln.compute(d, i, data)))
          .curve(d3.curveMonotoneX);
        const path = lineLayer
          .append('path')
          .datum(data)
          .attr('fill', 'none')
          .attr('stroke', ln.color)
          .attr('stroke-width', 1.5)
          .attr('d', gen);
        if (ln.dash) path.attr('stroke-dasharray', ln.dash);
      }
    }

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
    valueKey;
    lines;
    showBars;
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

  let hoverVal = $derived(hoverDatum ? (hoverDatum[valueKey] ?? 0) : 0);
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
      class="absolute top-2 right-20 px-3 py-2 rounded border border-zinc-700 bg-zinc-900/90 text-xs font-mono text-zinc-100 pointer-events-none shadow"
    >
      <div class="text-zinc-400">
        {new Date(hoverDatum.time * 1000).toISOString().replace('T', ' ').slice(0, 19)} UTC
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
