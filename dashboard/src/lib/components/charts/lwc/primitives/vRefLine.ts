import type {
  IChartApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesType,
  Time
} from 'lightweight-charts';

// `clamp` pins the line to a pane edge when `time` falls outside the chart's
// data range (timeToCoordinate returns null) — e.g. a position opened before
// the chart's left edge. 'left' draws it at the start of the plot area.
export type VRef = {
  time: number;
  color?: string;
  dash?: string;
  width?: number;
  clamp?: 'left' | 'right';
  // Optional text tag drawn at the top of the line (e.g. 'entry').
  label?: string;
};

type BitmapScope = {
  readonly context: CanvasRenderingContext2D;
  readonly bitmapSize: { width: number; height: number };
  readonly horizontalPixelRatio: number;
};
type Target = {
  useBitmapCoordinateSpace(f: (scope: BitmapScope) => void): void;
};

function parseDash(spec: string | undefined, ratio: number): number[] {
  if (!spec) return [];
  return spec
    .split(/[,\s]+/)
    .map((s) => parseFloat(s))
    .filter((n) => Number.isFinite(n) && n > 0)
    .map((n) => n * ratio);
}

export class VRefLinesPrimitive implements ISeriesPrimitive<Time> {
  private _refs: VRef[];
  private _defaultColor: string;
  private _chart: IChartApi | null = null;
  private _requestUpdate: (() => void) | null = null;
  private readonly _view = new VRefLinesPaneView(this);

  constructor(refs: VRef[], defaultColor = '#71717a') {
    this._refs = refs;
    this._defaultColor = defaultColor;
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this._chart = param.chart;
    this._requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this._chart = null;
    this._requestUpdate = null;
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return [this._view];
  }

  updateAllViews(): void {}

  setRefs(refs: VRef[], defaultColor?: string): void {
    this._refs = refs;
    if (defaultColor) this._defaultColor = defaultColor;
    this._requestUpdate?.();
  }

  _state(): { refs: VRef[]; defaultColor: string; chart: IChartApi | null } {
    return { refs: this._refs, defaultColor: this._defaultColor, chart: this._chart };
  }
}

class VRefLinesPaneView implements ISeriesPrimitivePaneView {
  constructor(private _src: VRefLinesPrimitive) {}
  renderer(): ISeriesPrimitivePaneRenderer {
    return new VRefLinesRenderer(this._src);
  }
}

class VRefLinesRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private _src: VRefLinesPrimitive) {}
  draw(target: unknown): void {
    const t = target as Target;
    t.useBitmapCoordinateSpace((scope) => {
      const { refs, defaultColor, chart } = this._src._state();
      if (!chart || refs.length === 0) return;
      const ts = chart.timeScale();
      const ctx = scope.context;
      const ratio = scope.horizontalPixelRatio;
      const height = scope.bitmapSize.height;
      const widthCss = scope.bitmapSize.width / ratio;
      for (const r of refs) {
        // A time outside the data range yields a null coordinate; clamp it to
        // the requested pane edge if asked, otherwise skip it.
        const coord = ts.timeToCoordinate(r.time as Time);
        let xCss: number;
        if (coord === null) {
          // Outside the data's time scale entirely.
          if (r.clamp === 'left') xCss = 0;
          else if (r.clamp === 'right') xCss = widthCss;
          else continue;
        } else {
          xCss = coord;
        }
        // The view can be pinned to a sub-range, so a clamped time often maps to
        // an off-screen coordinate (negative / past the right edge) rather than
        // null — snap it back onto the visible edge.
        if (r.clamp === 'left' && xCss < 0) xCss = 0;
        else if (r.clamp === 'right' && xCss > widthCss) xCss = widthCss;
        const px = Math.round(xCss * ratio) + 0.5;
        ctx.save();
        ctx.beginPath();
        // `width` is a multiplier on the base device-pixel width (default 1);
        // a thinner secondary line (e.g. 0.6) stays visible via the 0.75 floor.
        ctx.lineWidth = Math.max(0.75, Math.max(1, ratio) * (r.width ?? 1));
        ctx.strokeStyle = r.color ?? defaultColor;
        const dash = parseDash(r.dash, ratio);
        if (dash.length) ctx.setLineDash(dash);
        ctx.moveTo(px, 0);
        ctx.lineTo(px, height);
        ctx.stroke();
        // Optional tag at the top of the line — a small filled chip with the
        // label, matching the line colour, so the marker is self-explanatory.
        if (r.label) {
          ctx.setLineDash([]);
          const color = r.color ?? defaultColor;
          const fontPx = 10 * Math.max(1, ratio);
          ctx.font = `${fontPx}px ui-sans-serif, system-ui, -apple-system, sans-serif`;
          ctx.textBaseline = 'top';
          const padX = 4 * ratio;
          const padY = 2 * ratio;
          const textW = ctx.measureText(r.label).width;
          const chipW = textW + padX * 2;
          const chipH = fontPx + padY * 2;
          const topY = 2 * ratio;
          // Place the chip to the right of the line, flipping left if it would
          // overflow the right edge.
          const fitsRight = px + 2 * ratio + chipW <= scope.bitmapSize.width;
          const chipX = fitsRight ? px + 2 * ratio : px - 2 * ratio - chipW;
          ctx.globalAlpha = 0.92;
          ctx.fillStyle = color;
          ctx.fillRect(chipX, topY, chipW, chipH);
          ctx.globalAlpha = 1;
          ctx.fillStyle = '#0a0a0a';
          ctx.fillText(r.label, chipX + padX, topY + padY);
        }
        ctx.restore();
      }
    });
  }
}
