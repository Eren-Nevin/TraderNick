import type {
  IChartApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesType,
  Time
} from 'lightweight-charts';

export type VRef = { time: number; color?: string; dash?: string };

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
      for (const r of refs) {
        const x = ts.timeToCoordinate(r.time as Time);
        if (x === null) continue;
        const px = Math.round(x * ratio) + 0.5;
        ctx.save();
        ctx.beginPath();
        ctx.lineWidth = Math.max(1, ratio);
        ctx.strokeStyle = r.color ?? defaultColor;
        const dash = parseDash(r.dash, ratio);
        if (dash.length) ctx.setLineDash(dash);
        ctx.moveTo(px, 0);
        ctx.lineTo(px, height);
        ctx.stroke();
        ctx.restore();
      }
    });
  }
}
