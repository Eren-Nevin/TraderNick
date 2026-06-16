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
    // Tooltip label for the plotted value (e.g. "Realized", "Total").
    label = 'PnL'
  }: { data?: Point[]; height?: number; cutoff?: number | null; lookbackStart?: number | null; label?: string } = $props();

  // Cutoff = amber (filter/as-of day); lookback start = thinner sky-blue.
  function buildRefs() {
    const refs: { time: number; color?: string; dash?: string; width?: number }[] = [];
    if (lookbackStart != null) refs.push({ time: lookbackStart, color: '#38bdf8', dash: '2,3', width: 0.6 });
    if (cutoff != null) refs.push({ time: cutoff, color: '#fbbf24', dash: '4,3' });
    return refs;
  }

  let container = $state<HTMLDivElement | null>(null);
  let chart: IChartApi | null = null;
  let series: ISeriesApi<'Baseline'> | null = null;
  let vref: VRefLinesPrimitive | null = null;
  let ro: ResizeObserver | null = null;

  let tip = $state<{ x: number; time: number; value: number } | null>(null);

  onMount(() => {
    if (!container) return;
    chart = createChart(container, {
      ...lwcChartOptions(),
      height,
      handleScale: false,
      handleScroll: false
    });
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } });
    series = chart.addBaselineSeries({
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#22c55e',
      topFillColor1: 'rgba(34,197,94,0.28)',
      topFillColor2: 'rgba(34,197,94,0.02)',
      bottomLineColor: '#ef4444',
      bottomFillColor1: 'rgba(239,68,68,0.02)',
      bottomFillColor2: 'rgba(239,68,68,0.28)',
      lineWidth: 2,
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
      if (chart && container) chart.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);

    return () => {
      ro?.disconnect();
      chart?.remove();
      chart = null;
      series = null;
      vref = null;
    };
  });

  // Keep the marker lines in sync if either prop changes.
  $effect(() => {
    void cutoff; void lookbackStart;
    vref?.setRefs(buildRefs(), '#fbbf24');
  });

  // Re-theme on theme toggle.
  $effect(() => {
    void themeStore.theme;
    if (chart) chart.applyOptions(lwcChartOptions());
  });

  // Push data + fit.
  $effect(() => {
    if (!series || !chart) return;
    series.setData(
      data
        .filter((d) => Number.isFinite(d.value))
        .map((d) => ({ time: d.time as UTCTimestamp, value: d.value }))
    );
    chart.timeScale().fitContent();
  });
</script>

<div class="relative w-full" style="height: {height}px">
  <div bind:this={container} class="absolute inset-0"></div>
  {#if tip}
    <div
      class="pointer-events-none absolute top-1 z-10 rounded border border-zinc-700 bg-zinc-900/95 px-2 py-1 text-[10px] leading-tight shadow-lg"
      style="left: {Math.min(Math.max(tip.x - 60, 4), (container?.clientWidth ?? 200) - 124)}px"
    >
      <div class="text-zinc-400">{fmtUtcTime(tip.time).slice(0, 14)}</div>
      <div class={tip.value >= 0 ? 'text-emerald-300' : 'text-red-300'}>
        {label} {fmtUsdTooltip(tip.value)}
      </div>
    </div>
  {/if}
</div>
