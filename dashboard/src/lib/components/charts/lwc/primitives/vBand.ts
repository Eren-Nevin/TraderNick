import type {
  IChartApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesPrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time
} from 'lightweight-charts';

// A shaded vertical band spanning [from, to] across the full pane height —
// used to tint the selected date range in range mode. Drawn at the bottom
// z-order so it sits behind the series line/fill.
export type Band = { from: number; to: number } | null;

type BitmapScope = {
  readonly context: CanvasRenderingContext2D;
  readonly bitmapSize: { width: number; height: number };
  readonly horizontalPixelRatio: number;
};
type Target = {
  useBitmapCoordinateSpace(f: (scope: BitmapScope) => void): void;
};

export class VBandPrimitive implements ISeriesPrimitive<Time> {
  private _band: Band;
  private _color: string;
  private _chart: IChartApi | null = null;
  private _requestUpdate: (() => void) | null = null;
  private readonly _view = new VBandPaneView(this);

  constructor(band: Band, color = 'rgba(59,130,246,0.12)') {
    this._band = band;
    this._color = color;
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

  setBand(band: Band): void {
    this._band = band;
    this._requestUpdate?.();
  }

  _state(): { band: Band; color: string; chart: IChartApi | null } {
    return { band: this._band, color: this._color, chart: this._chart };
  }
}

class VBandPaneView implements ISeriesPrimitivePaneView {
  constructor(private _src: VBandPrimitive) {}
  // Behind the series line/fill so the tint never dims the curve.
  zOrder(): SeriesPrimitivePaneViewZOrder {
    return 'bottom';
  }
  renderer(): ISeriesPrimitivePaneRenderer {
    return new VBandRenderer(this._src);
  }
}

class VBandRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private _src: VBandPrimitive) {}
  draw(target: unknown): void {
    const t = target as Target;
    t.useBitmapCoordinateSpace((scope) => {
      const { band, color, chart } = this._src._state();
      if (!chart || !band) return;
      const ts = chart.timeScale();
      const ratio = scope.horizontalPixelRatio;
      const w = scope.bitmapSize.width;
      // Resolve both edges; clamp a null (out-of-range) edge to the pane bounds
      // so a band that starts before / ends after the visible window still
      // fills the on-screen portion.
      const cFrom = ts.timeToCoordinate(band.from as Time);
      const cTo = ts.timeToCoordinate(band.to as Time);
      let x1 = cFrom === null ? 0 : cFrom * ratio;
      let x2 = cTo === null ? w : cTo * ratio;
      if (x1 > x2) [x1, x2] = [x2, x1];
      x1 = Math.max(0, x1);
      x2 = Math.min(w, x2);
      if (x2 <= x1) return;
      const ctx = scope.context;
      ctx.save();
      ctx.fillStyle = color;
      ctx.fillRect(x1, 0, x2 - x1, scope.bitmapSize.height);
      ctx.restore();
    });
  }
}
