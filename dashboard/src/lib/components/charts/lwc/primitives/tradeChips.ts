import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesPrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time
} from 'lightweight-charts';

// A styled buy/sell "chip" drawn at a trade day: a rounded background + colored
// border + arrow + label, anchored just below (buy) or above (sell) the curve
// point. Native lightweight-charts markers can't carry a border/background, so
// this primitive replaces them for a more readable, stylish tag.
export type TradeChip = {
  time: number;
  value: number; // curve value at `time` — used to anchor the chip to the bar
  side: 'buy' | 'sell';
  text: string;
  // Per-token breakdown for the hover tooltip (omitted when a single token is
  // selected — there's nothing to break down).
  tokens?: Array<{ token: string; label: string; price: string }>;
};

// A drawn chip's hit-box (pane-relative CSS px) + payload, for hover testing.
export type ChipHit = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  side: 'buy' | 'sell';
  tokens: Array<{ token: string; label: string; price: string }>;
};

type BitmapScope = {
  readonly context: CanvasRenderingContext2D;
  readonly bitmapSize: { width: number; height: number };
  readonly horizontalPixelRatio: number;
  readonly verticalPixelRatio: number;
};
type Target = {
  useBitmapCoordinateSpace(f: (scope: BitmapScope) => void): void;
};

const BUY = { border: '#22c55e', text: '#4ade80', glyph: '▲' };
const SELL = { border: '#ef4444', text: '#f87171', glyph: '▼' };
const BG = 'rgba(24,24,27,0.92)';

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export class TradeChipsPrimitive implements ISeriesPrimitive<Time> {
  private _chips: TradeChip[];
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private readonly _view = new TradeChipsPaneView(this);
  // Hit-boxes from the last draw (pane-relative CSS px), newest on top.
  _hitboxes: ChipHit[] = [];

  constructor(chips: TradeChip[]) {
    this._chips = chips;
  }

  // Return the topmost chip (with a token breakdown) under the pane-relative
  // point, or null. Used by the chart to drive the hover tooltip.
  chipAt(x: number, y: number): ChipHit | null {
    for (let i = this._hitboxes.length - 1; i >= 0; i--) {
      const b = this._hitboxes[i];
      if (b.tokens.length && x >= b.x1 && x <= b.x2 && y >= b.y1 && y <= b.y2) return b;
    }
    return null;
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this._chart = param.chart;
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return [this._view];
  }

  updateAllViews(): void {}

  setChips(chips: TradeChip[]): void {
    this._chips = chips;
    this._requestUpdate?.();
  }

  _state() {
    return { chips: this._chips, chart: this._chart, series: this._series };
  }
}

class TradeChipsPaneView implements ISeriesPrimitivePaneView {
  constructor(private _src: TradeChipsPrimitive) {}
  // Above the curve and the range mask so the tags stay readable.
  zOrder(): SeriesPrimitivePaneViewZOrder {
    return 'top';
  }
  renderer(): ISeriesPrimitivePaneRenderer {
    return new TradeChipsRenderer(this._src);
  }
}

class TradeChipsRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private _src: TradeChipsPrimitive) {}
  draw(target: unknown): void {
    const t = target as Target;
    t.useBitmapCoordinateSpace((scope) => {
     try {
      const { chips, chart, series } = this._src._state();
      if (!chart || !series || chips.length === 0) return;
      const ts = chart.timeScale();
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;

      const fontPx = 10 * vr;
      const padX = 4 * hr;
      const padY = 2 * vr;
      const gap = 7 * vr; // distance from the bar point to the chip
      const radius = 3 * hr;

      const boxes: ChipHit[] = [];
      for (const c of chips) {
        const x = ts.timeToCoordinate(c.time as Time);
        const yBar = series.priceToCoordinate(c.value);
        if (x === null || yBar === null) continue;
        const palette = c.side === 'buy' ? BUY : SELL;
        const label = `${palette.glyph} ${c.text}`;

        ctx.save();
        ctx.font = `600 ${fontPx}px ui-sans-serif, system-ui, -apple-system, sans-serif`;
        ctx.textBaseline = 'middle';
        const textW = ctx.measureText(label).width;
        const w = textW + padX * 2;
        const h = fontPx + padY * 2;

        // Centre horizontally on the bar; clamp inside the pane.
        const px = x * hr;
        let left = px - w / 2;
        left = Math.max(1, Math.min(left, scope.bitmapSize.width - w - 1));

        // Buy below the point, sell above it.
        const py = yBar * vr;
        const top = c.side === 'buy' ? py + gap : py - gap - h;

        roundRect(ctx, left, top, w, h, radius);
        ctx.fillStyle = BG;
        ctx.fill();
        ctx.lineWidth = Math.max(1, hr);
        ctx.strokeStyle = palette.border;
        ctx.stroke();

        ctx.fillStyle = palette.text;
        ctx.textAlign = 'left';
        ctx.fillText(label, left + padX, top + h / 2 + 0.5 * vr);
        ctx.restore();

        // Record the hit-box in pane-relative CSS px for hover testing.
        boxes.push({
          x1: left / hr,
          y1: top / vr,
          x2: (left + w) / hr,
          y2: (top + h) / vr,
          side: c.side,
          tokens: c.tokens ?? []
        });
      }
      this._src._hitboxes = boxes;
     } catch (err) {
       // A throw inside draw() (called by lightweight-charts' paint loop) would
       // blank the whole pane — contain it so the chart never blacks out.
       console.error('TradeChipsPrimitive draw failed', err);
     }
    });
  }
}
