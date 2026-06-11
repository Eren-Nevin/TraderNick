<script lang="ts">
  import { onMount } from 'svelte';
  import * as d3 from 'd3';
  import { transformToView, viewToTransform, type View } from '$lib/chart-zoom';
  import { cssVar, themeStore } from '$lib/stores/theme.svelte';
  import { fmtUtcTime } from '$lib/components/charts/config';

  type Datum = { time: number } & Record<string, number>;
  type Series = { key: string; label: string; color: string };
  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Datum, i: number, data: Datum[]) => number;
    dash?: string;
    scale?: 'pct' | 'value';
    /** Compound-overlay tooltip override (see LineChart Line type for details). */
    rawValue?: (d: Datum, i: number, data: Datum[]) => number;
    rawFormat?: (v: number) => string;
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
    onHover
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
  let hoverTotal = $derived(
    hoverDatum ? series.reduce((s, ser) => s + (hoverDatum![ser.key] || 0), 0) : 0
  );

  function fmtUsd(v: number) {
    const abs = Math.abs(v);
    if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  }

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
    if (!data.length || (!series.length && !lines.length)) return;

    // subscribe to theme changes via $effect — read so this redraw triggers when toggled
    void themeStore.theme;
    const C_GRID = cssVar('--chart-grid', '#27272a');
    const C_AXIS_LINE = cssVar('--chart-axis-line', '#3f3f46');
    const C_AXIS_TEXT = cssVar('--chart-axis-text', '#a1a1aa');
    const C_CROSSHAIR = cssVar('--chart-crosshair', '#71717a');

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
    const visible = data.filter((d) => d.time >= v0 && d.time <= v1);
    const ref = visible.length ? visible : data;

    const valueLines = lines.filter((ln) => ln.scale === 'value');
    const seriesMax =
      d3.max(ref, (d) => series.reduce((s, ser) => s + (d[ser.key] || 0), 0)) ?? 0;
    let lineMax = 0;
    if (valueLines.length) {
      for (let i = 0; i < data.length; i++) {
        const d = data[i];
        if (d.time < v0 || d.time > v1) continue;
        for (const ln of valueLines) {
          const lv = ln.compute(d, i, data);
          if (Number.isFinite(lv) && lv > lineMax) lineMax = lv;
        }
      }
    }
    const yMaxRaw = Math.max(seriesMax, lineMax) || 1;
    const yScale = d3
      .scaleLinear()
      .domain([0, yMaxRaw * 1.05 || 1])
      .range([plotH, 0])
      .nice();
    const yDomainTop = yScale.domain()[1];
    const yScalePct = d3
      .scaleLinear()
      .domain([0, (yDomainTop / (yMaxRaw || 1)) * 100])
      .range([plotH, 0]);

    const bw = (() => {
      if (data.length < 2) return 4;
      const a = xScale(new Date(data[0].time * 1000));
      const b = xScale(new Date(data[1].time * 1000));
      return Math.max(1, (b - a) * 0.85);
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
      .attr('stroke', C_GRID)
      .attr('stroke-dasharray', '2,3');

    const bars = g.append('g').attr('class', 'bars').attr('clip-path', `url(#${clipId})`);
    for (const d of data) {
      const x = xScale(new Date(d.time * 1000));
      if (x < -bw || x > plotW + bw) continue;
      let stackBottom = plotH;
      for (const ser of series) {
        const v = d[ser.key] || 0;
        if (v <= 0) continue;
        const segH = plotH - yScale(v);
        const yTop = stackBottom - segH;
        bars
          .append('rect')
          .attr('x', x - bw / 2)
          .attr('y', yTop)
          .attr('width', bw)
          .attr('height', Math.max(0.5, segH))
          .attr('fill', ser.color);
        stackBottom = yTop;
      }
    }

    if (lines.length) {
      const lineLayer = g
        .append('g')
        .attr('class', 'lines')
        .attr('clip-path', `url(#${clipId})`);
      for (const ln of lines) {
        const yForLine = ln.scale === 'value' ? yScale : yScalePct;
        const gen = d3
          .line<Datum>()
          .x((d) => xScale(new Date(d.time * 1000)))
          .y((d, i) => yForLine(ln.compute(d, i, data)))
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

    if (series.length) {
      g.append('g')
        .attr('transform', `translate(${plotW},0)`)
        .call(
          d3
            .axisRight(yScale)
            .ticks(5)
            .tickFormat((d) => fmtUsd(d as number))
        )
        .call((sel) => {
          sel.select('.domain').remove();
          sel.selectAll('text').attr('fill', C_AXIS_TEXT).attr('font-size', '10px');
          sel.selectAll('line').attr('stroke', C_AXIS_LINE);
        });
    }

    g.append('g')
      .call(
        d3
          .axisLeft(yScalePct)
          .ticks(5)
          .tickFormat((d) => `${Math.round(d as number)}%`)
      )
      .call((sel) => {
        sel.select('.domain').remove();
        sel.selectAll('text').attr('fill', C_AXIS_TEXT).attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', C_AXIS_LINE);
      });

    g.append('g')
      .attr('transform', `translate(0,${plotH})`)
      .call(d3.axisBottom(xScale).ticks(Math.max(2, Math.floor(plotW / 110))))
      .call((sel) => {
        sel.select('.domain').remove();
        sel.selectAll('text').attr('fill', C_AXIS_TEXT).attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', C_AXIS_LINE);
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
        // Drop no-op zooms (stuck touch gestures, FP round-trip noise) — would otherwise
        // re-render forever because view gets a new array ref with identical numeric values.
        if (
          view !== null &&
          Math.abs(v[0] - view[0]) < 1 &&
          Math.abs(v[1] - view[1]) < 1
        )
          return;
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
      draw();
    });
  }

  $effect(() => {
    data;
    series;
    lines;
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
      {#if series.length}
        {#each series as ser (ser.key)}
          {@const v = hoverDatum[ser.key] || 0}
          {@const pct = hoverTotal > 0 ? (v / hoverTotal) * 100 : 0}
          <div class="flex items-center gap-2">
            <span class="inline-block w-2 h-2 rounded-sm" style="background: {ser.color}"></span>
            <span class="text-zinc-400 w-20">{ser.label}</span>
            <span class="w-20 text-right">{fmtUsd(v)}</span>
            <span class="w-14 text-right text-zinc-500">{pct.toFixed(1)}%</span>
          </div>
        {/each}
        <div class="mt-1 pt-1 border-t border-zinc-800 flex items-center gap-2">
          <span class="inline-block w-2 h-2"></span>
          <span class="text-zinc-400 w-20">Total</span>
          <span class="w-20 text-right">{fmtUsd(hoverTotal)}</span>
          <span class="w-14 text-right text-zinc-500">100.0%</span>
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
