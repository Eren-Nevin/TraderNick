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

// One shaded vertical region spanning [from, to] across the full pane height.
// A null edge clamps to the corresponding pane edge, so a band can run to the
// chart's left/right border. Used to highlight the selected range (blue, drawn
// behind the curve) and to dim everything outside it (gray, drawn on top).
export type Band = { from: number | null; to: number | null; color: string };

type BitmapScope = {
  readonly context: CanvasRenderingContext2D;
  readonly bitmapSize: { width: number; height: number };
  readonly horizontalPixelRatio: number;
};
type Target = {
  useBitmapCoordinateSpace(f: (scope: BitmapScope) => void): void;
};

export class VBandPrimitive implements ISeriesPrimitive<Time> {
  private _bands: Band[];
  private _zOrder: SeriesPrimitivePaneViewZOrder;
  private _chart: IChartApi | null = null;
  private _requestUpdate: (() => void) | null = null;
  private readonly _view: VBandPaneView;

  constructor(bands: Band[], zOrder: SeriesPrimitivePaneViewZOrder = 'bottom') {
    this._bands = bands;
    this._zOrder = zOrder;
    this._view = new VBandPaneView(this, zOrder);
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

  setBands(bands: Band[]): void {
    this._bands = bands;
    this._requestUpdate?.();
  }

  _state(): { bands: Band[]; chart: IChartApi | null } {
    return { bands: this._bands, chart: this._chart };
  }
}

class VBandPaneView implements ISeriesPrimitivePaneView {
  constructor(
    private _src: VBandPrimitive,
    private _zOrder: SeriesPrimitivePaneViewZOrder
  ) {}
  zOrder(): SeriesPrimitivePaneViewZOrder {
    return this._zOrder;
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
      const { bands, chart } = this._src._state();
      if (!chart || bands.length === 0) return;
      const ts = chart.timeScale();
      const ratio = scope.horizontalPixelRatio;
      const w = scope.bitmapSize.width;
      const ctx = scope.context;
      for (const band of bands) {
        // Resolve both edges; a null (or out-of-range) edge clamps to the pane
        // bounds so a band that starts before / ends after the visible window
        // still fills the on-screen portion.
        const cFrom = band.from === null ? null : ts.timeToCoordinate(band.from as Time);
        const cTo = band.to === null ? null : ts.timeToCoordinate(band.to as Time);
        let x1 = cFrom === null ? 0 : cFrom * ratio;
        let x2 = cTo === null ? w : cTo * ratio;
        if (x1 > x2) [x1, x2] = [x2, x1];
        x1 = Math.max(0, x1);
        x2 = Math.min(w, x2);
        if (x2 <= x1) continue;
        ctx.save();
        ctx.fillStyle = band.color;
        ctx.fillRect(x1, 0, x2 - x1, scope.bitmapSize.height);
        ctx.restore();
      }
    });
  }
}
