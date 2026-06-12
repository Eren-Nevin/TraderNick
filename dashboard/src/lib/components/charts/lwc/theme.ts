import { CrosshairMode, LineStyle, type ChartOptions, type DeepPartial } from 'lightweight-charts';
import { cssVar } from '$lib/stores/theme.svelte';

export function lwcChartOptions(): DeepPartial<ChartOptions> {
  const bg = cssVar('--chart-bg', '#09090b');
  const text = cssVar('--chart-axis-text', '#a1a1aa');
  const grid = cssVar('--chart-grid', '#27272a');
  const axis = cssVar('--chart-axis-line', '#3f3f46');
  const crosshair = cssVar('--chart-crosshair', '#71717a');
  return {
    layout: {
      background: { color: bg },
      textColor: text,
      fontSize: 11,
      fontFamily: 'inherit',
      attributionLogo: false
    },
    grid: {
      vertLines: { color: grid, style: LineStyle.Solid },
      horzLines: { color: grid, style: LineStyle.Solid }
    },
    rightPriceScale: { borderColor: axis, borderVisible: true },
    leftPriceScale: { borderColor: axis, borderVisible: true },
    timeScale: {
      borderColor: axis,
      timeVisible: true,
      secondsVisible: false,
      // 8 bars of whitespace to the right of the last candle. Native
      // Lightweight option — works as long as we don't set fixRightEdge,
      // which silently suppresses it.
      rightOffset: 8,
      barSpacing: 6,
      // Was Lightweight's default 0.5 — capped zoom-out at ~3 months of
      // 1h data on a typical chart. 0.2 lets the full 180-day TTL window
      // fit on screen before hitting the data wall. Bump back to 0.5 for
      // readable-at-max-zoom-out individual bars.
      minBarSpacing: 0.2
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: crosshair, style: LineStyle.Dashed, width: 1, labelBackgroundColor: bg },
      horzLine: { color: crosshair, style: LineStyle.Dashed, width: 1, labelBackgroundColor: bg }
    },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    handleScroll: {
      mouseWheel: false,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false
    }
  };
}

export function lwcTooltipColors(): { bg: string; text: string; muted: string } {
  return {
    bg: cssVar('--chart-tooltip-bg', '#18181b'),
    text: cssVar('--chart-tooltip-text', '#e4e4e7'),
    muted: cssVar('--chart-axis-text', '#a1a1aa')
  };
}
