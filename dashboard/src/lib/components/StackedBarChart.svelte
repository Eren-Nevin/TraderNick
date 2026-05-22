<script lang="ts">
  import { onMount } from 'svelte';
  import * as d3 from 'd3';

  type Datum = { time: number } & Record<string, number>;
  type Series = { key: string; label: string; color: string };
  type Line = {
    key: string;
    label: string;
    color: string;
    compute: (d: Datum, i: number, data: Datum[]) => number;
    dash?: string;
  };

  let {
    data = [] as Datum[],
    series = [] as Series[],
    lines = [] as Line[],
    height = 220,
    title = '',
    xExtent,
    transform = d3.zoomIdentity,
    onZoom,
    hoverTime = null,
    onHover
  }: {
    data: Datum[];
    series: Series[];
    lines?: Line[];
    height?: number;
    title?: string;
    xExtent?: [number, number];
    transform?: d3.ZoomTransform;
    onZoom?: (t: d3.ZoomTransform) => void;
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
      .attr('stroke', '#71717a')
      .attr('stroke-dasharray', '3,3');
  }

  function draw() {
    if (!svgEl) return;
    const root = d3.select(svgEl);
    root.selectAll('*').remove();
    if (!data.length || (!series.length && !lines.length)) return;

    const plotW = Math.max(0, width - MARGIN.left - MARGIN.right);
    const plotH = Math.max(0, height - MARGIN.top - MARGIN.bottom);

    const xDomain: [Date, Date] = xExtent
      ? [new Date(xExtent[0] * 1000), new Date(xExtent[1] * 1000)]
      : [new Date(data[0].time * 1000), new Date(data[data.length - 1].time * 1000)];
    const xBase = d3.scaleTime().domain(xDomain).range([0, plotW]);
    const xScale = transform.rescaleX(xBase);

    const v0 = xScale.invert(0).getTime() / 1000;
    const v1 = xScale.invert(plotW).getTime() / 1000;
    const visible = data.filter((d) => d.time >= v0 && d.time <= v1);
    const ref = visible.length ? visible : data;

    const yMaxRaw =
      d3.max(ref, (d) => series.reduce((s, ser) => s + (d[ser.key] || 0), 0)) ?? 1;
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
      .attr('stroke', '#27272a')
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
        const gen = d3
          .line<Datum>()
          .x((d) => xScale(new Date(d.time * 1000)))
          .y((d, i) => yScalePct(ln.compute(d, i, data)))
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
          sel.selectAll('text').attr('fill', '#a1a1aa').attr('font-size', '10px');
          sel.selectAll('line').attr('stroke', '#3f3f46');
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
        sel.selectAll('text').attr('fill', '#a1a1aa').attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', '#3f3f46');
      });

    g.append('g')
      .attr('transform', `translate(0,${plotH})`)
      .call(d3.axisBottom(xScale).ticks(Math.max(2, Math.floor(plotW / 110))))
      .call((sel) => {
        sel.select('.domain').remove();
        sel.selectAll('text').attr('fill', '#a1a1aa').attr('font-size', '10px');
        sel.selectAll('line').attr('stroke', '#3f3f46');
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
      .filter((event) => event.type !== 'dblclick')
      .on('zoom', (event) => {
        if (!event.sourceEvent) return;
        onZoom?.(event.transform);
      });
    const root = d3.select(svgEl);
    root.call(zoomBehavior).on('dblclick.zoom', null);
    root.on('dblclick', () => onZoom?.(d3.zoomIdentity));

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

  $effect(() => {
    data;
    series;
    lines;
    xExtent;
    transform;
    width;
    if (svgEl && zoomBehavior) {
      d3.select(svgEl).call(zoomBehavior.transform, transform);
    }
    draw();
  });

  $effect(() => {
    hoverTime;
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
      class="absolute top-2 right-20 px-3 py-2 rounded border border-zinc-700 bg-zinc-900/90 text-xs font-mono text-zinc-100 pointer-events-none shadow"
    >
      <div class="text-zinc-400">
        {new Date(hoverDatum.time * 1000).toISOString().replace('T', ' ').slice(0, 19)} UTC
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
