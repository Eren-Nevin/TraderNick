<script lang="ts">
  // Hyperliquid wallet detail page (/wallet/hl/<address>). Glassnode-style:
  // a PnL equity curve + headline stats + a positions table, with a 1-day
  // "as of" slider. PnL curve is GLOBAL (all tokens) and always full-history
  // with a marker at the selected day; the slider drives the positions table
  // and the as-of stat cards. Positions are HYBRID: today → HL live API
  // (exact, all tokens, account value); past days → our stored snapshots.

  import WalletPnlChart from '$lib/components/WalletPnlChart.svelte';
  import WalletPositionsTable, {
    type PositionRow
  } from '$lib/components/WalletPositionsTable.svelte';
  import WalletTransfersTable, {
    type TransferRow
  } from '$lib/components/WalletTransfersTable.svelte';
  import {
    DAY_SLIDER_MAX_BACK,
    DAY_SLIDER_FLOOR_ISO,
    backToIso,
    isoToBack,
    isoToUnix,
    isToday
  } from '$lib/daySlider';
  import { arkhamUrl, coinglassHlUrl } from '$lib/arkham';
  import WalletPinMenu from '$lib/components/WalletPinMenu.svelte';
  import { walletPinsStore } from '$lib/stores/walletPins.svelte';
  import { onMount } from 'svelte';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  const address = $derived(data.address);

  type PnlPoint = {
    time: number; realized: number; total: number;
    realized_day: number; unrealized: number; oi: number;
    volume: number; trades: number;
  };
  type PnlStats = {
    realized_pnl: number; unrealized_pnl: number;
    sharpe: number; volatility: number;
  };

  // ── State ──────────────────────────────────────────────────────────
  // Slider position is the source of truth (bind:value) so the drag never
  // fights a re-asserted one-way value. 0 = oldest (left), MAX = today (right).
  let sliderPos = $state(DAY_SLIDER_MAX_BACK);
  // Range mode: a second knob. The two knobs are unconstrained; the range is
  // derived as [min, max] so neither needs clamping (avoids drag-fighting).
  // The MORE RECENT knob (max) drives the as-of stats/positions, exactly as in
  // single mode; the range stat row uses the span between the two.
  let rangeMode = $state(false);
  let sliderStartPos = $state(0);
  // Range stat row (lazy-fetched; the rest is derived from pnlSeries).
  let oiStart = $state<number | null>(null);
  let rangeLoading = $state(false);
  let pnlSeries = $state<PnlPoint[]>([]);
  let pnlStats = $state<PnlStats | null>(null);
  let pnlLoading = $state(true);
  let pnlError = $state<string | null>(null);
  let pnlMode = $state<'total' | 'realized' | 'unrealized' | 'oi'>('total');
  // PnL modes carry a 'PnL' suffix; OI is not a PnL metric so it stays bare.
  const modeLabel = $derived(
    pnlMode === 'total' ? 'Total PnL'
      : pnlMode === 'realized' ? 'Realized PnL'
        : pnlMode === 'unrealized' ? 'Unrealized PnL'
          : 'OI'
  );

  let positions = $state<PositionRow[]>([]);
  let posLoading = $state(false);
  let posError = $state<string | null>(null);
  let accountValue = $state<number | null>(null);

  // Transfers (deposits/withdrawals) — full history, snapshot-independent.
  let transfers = $state<TransferRow[]>([]);
  let transfersLoading = $state(false);
  let transfersError = $state<string | null>(null);

  // Execution-quality stats over the window (taker %, fee/PnL %, funding/PnL %),
  // snapshot-independent.
  type TradeStats = {
    avg_trade_size: number; taker_pct: number; trades_per_day: number;
    account_duration_days: number; win_rate: number | null;
    fee_pct: number | null; funding_pct: number | null;
    tokens?: Array<{ token: string; volume: number; pnl: number }>;
  };
  let tradeStats = $state<TradeStats | null>(null);
  // Same stats recomputed over the selected range (range mode only).
  let rangeTradeStats = $state<TradeStats | null>(null);
  // Tokens row metric: distribute by traded Volume (default) or by total PnL.
  let tokenMetric = $state<'volume' | 'pnl'>('volume');

  let copied = $state(false);
  // Pin menu (group checkboxes) open state. Reflect pinned groups in the button.
  let pinMenuOpen = $state(false);
  onMount(() => walletPinsStore.hydrate());
  const pinnedGroups = $derived(walletPinsStore.groupsForWallet(address));

  // Single token (max one) whose daily close price is overlaid on the PnL
  // chart, toggled from the positions table.
  let selectedToken = $state<string | null>(null);
  let closeSeries = $state<{ time: number; value: number }[]>([]);
  let closeCtl: AbortController | null = null;

  // 'Show Trades': per-day net buy/sell flow markers. Scoped to the selected
  // token when one is chosen, else summed across all tokens (one tag per day).
  let showTrades = $state(false);
  let tradesRaw = $state<Array<{ time: number; token: string; net_usd: number; net_tokens: number; avg_px: number }>>([]);
  let tradesLoading = $state(false);
  let tradesCtl: AbortController | null = null;
  // Chip label / hover unit. USD or per-token size. Aggregate (multi-token)
  // chip LABELS are always USD (token units can't sum across tokens); the unit
  // applies to single-token labels and to per-token hover lines.
  let tradeUnit = $state<'usd' | 'token'>('usd');

  // ── Derived ────────────────────────────────────────────────────────
  const MAX_BACK = DAY_SLIDER_MAX_BACK;
  // Fixed chart window: floor (01-01) → today. The slider spans the same span,
  // so the day marker and the slider thumb track together.
  const floorUnix = isoToUnix(DAY_SLIDER_FLOOR_ISO);
  const todayUnix = isoToUnix(backToIso(0));
  // End knob = the more recent of the two (drives as-of stats/positions);
  // start knob = the older. In single mode only sliderPos matters.
  const endPos = $derived(rangeMode ? Math.max(sliderStartPos, sliderPos) : sliderPos);
  const startPos = $derived(Math.min(sliderStartPos, sliderPos));
  const snapshotIso = $derived(backToIso(MAX_BACK - endPos));
  const startIso = $derived(backToIso(MAX_BACK - startPos));
  const live = $derived(isToday(snapshotIso));
  const selectedUnix = $derived(isoToUnix(snapshotIso));
  const startUnix = $derived(isoToUnix(startIso));

  const modeVal = (p: PnlPoint) =>
    pnlMode === 'total' ? p.total
      : pnlMode === 'realized' ? p.realized
        : pnlMode === 'unrealized' ? p.unrealized
          : p.oi;
  // In range mode the curve is rebased so the range-start day reads 0 on the
  // y-axis (and the green/red split is relative to that start, since the
  // baseline series' zero now sits at the start value). Base = the as-of value
  // at the start day (last point ≤ startUnix); 0 if the range starts before any
  // data. Single mode keeps absolute values (base 0).
  const rangeBase = $derived.by(() => {
    if (!rangeMode) return 0;
    let base = 0;
    for (const p of pnlSeries) {
      if (p.time <= startUnix) base = modeVal(p);
      else break;
    }
    return base;
  });
  const chartData = $derived(
    pnlSeries.map((p) => ({ time: p.time, value: modeVal(p) - rangeBase }))
  );

  // Compact token-amount formatter (the chip is small).
  function fmtAmt(n: number): string {
    const a = Math.abs(n);
    if (a >= 1e9) return (a / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (a / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (a / 1e3).toFixed(1) + 'K';
    return a.toFixed(a >= 100 ? 0 : 2);
  }
  // Format one (token) leg per the unit selector.
  const fmtLeg = (usd: number, tokens: number) =>
    tradeUnit === 'token' ? fmtAmt(tokens) : fmtUsd(Math.abs(usd)).replace('$', '');
  // Execution price (USD/token); precision scales with magnitude.
  function fmtPx(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    const a = Math.abs(n);
    const dp = a >= 1000 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8;
    return '$' + n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  // Buy/sell chips from the per-(day, token) net flow.
  //   • single token selected → one chip/day for that token (no hover)
  //   • otherwise → per day, one net-BUY chip (all net-bought tokens) and/or
  //     one net-SELL chip (all net-sold tokens); the chip label is the USD
  //     total and the hover lists each token (per the unit selector).
  // `value` anchors each chip to the curve point on that day.
  const tradeMarkers = $derived.by(() => {
    if (!showTrades) return [];
    const byTime = new Map(chartData.map((p) => [p.time, p.value]));

    if (selectedToken) {
      return tradesRaw.map((t) => ({
        time: t.time,
        value: byTime.get(t.time) ?? 0,
        side: (t.net_usd >= 0 ? 'buy' : 'sell') as 'buy' | 'sell',
        text: fmtLeg(t.net_usd, t.net_tokens)
      }));
    }

    const byDay = new Map<number, typeof tradesRaw>();
    for (const t of tradesRaw) {
      const arr = byDay.get(t.time);
      if (arr) arr.push(t);
      else byDay.set(t.time, [t]);
    }
    const out: Array<{
      time: number; value: number; side: 'buy' | 'sell'; text: string;
      tokens: Array<{ token: string; label: string; price: string }>;
    }> = [];
    for (const [time, rows] of byDay) {
      const value = byTime.get(time) ?? 0;
      const buys = rows.filter((r) => r.net_usd > 0);
      const sells = rows.filter((r) => r.net_usd < 0);
      if (buys.length) {
        const sum = buys.reduce((s, r) => s + r.net_usd, 0);
        out.push({
          time, value, side: 'buy',
          text: fmtUsd(sum).replace('$', ''),
          tokens: buys.map((r) => ({ token: r.token, label: fmtLeg(r.net_usd, r.net_tokens), price: fmtPx(r.avg_px) }))
        });
      }
      if (sells.length) {
        const sum = sells.reduce((s, r) => s + Math.abs(r.net_usd), 0);
        out.push({
          time, value, side: 'sell',
          text: fmtUsd(sum).replace('$', ''),
          tokens: sells.map((r) => ({ token: r.token, label: fmtLeg(r.net_usd, r.net_tokens), price: fmtPx(r.avg_px) }))
        });
      }
    }
    return out;
  });

  // The selected token's dominant position (largest notional if both sides are
  // held) → drives the entry-price line + open-date marker on the chart. Both
  // sit on the close-price (left) axis. opened_at is snapshot-only (the live
  // API has no open time), so the vertical line is simply skipped when null.
  const selectedPos = $derived.by(() => {
    if (!selectedToken) return null;
    const rows = positions.filter((p) => p.token === selectedToken);
    if (!rows.length) return null;
    return rows.reduce((a, b) => (Math.abs(b.size_usd) > Math.abs(a.size_usd) ? b : a));
  });
  const entryPrice = $derived(selectedPos?.entry_px ?? null);
  // Tokens-row view: sort + percentage + "Other" bucketing by the selected
  // metric (Volume or PnL). Tokens under 0.1% of the metric fold into "Other".
  const tokenView = $derived.by(() => {
    const toks = tradeStats?.tokens ?? [];
    if (!toks.length) return [] as Array<{ token: string; pnl: number; share: number; pnlPct: number }>;
    const totVol = toks.reduce((s, t) => s + t.volume, 0);
    const totPnl = toks.reduce((s, t) => s + t.pnl, 0);
    const metricVal = (t: { volume: number; pnl: number }) => (tokenMetric === 'volume' ? t.volume : t.pnl);
    const total = tokenMetric === 'volume' ? totVol : totPnl;
    const shareOf = (t: { volume: number; pnl: number }) => (total ? (100 * metricVal(t)) / total : 0);
    const kept: Array<{ token: string; volume: number; pnl: number }> = [];
    let oVol = 0, oPnl = 0, oHas = false;
    for (const t of toks) {
      if (Math.abs(shareOf(t)) < 0.1) { oVol += t.volume; oPnl += t.pnl; oHas = true; }
      else kept.push(t);
    }
    if (oHas) kept.push({ token: 'Other', volume: oVol, pnl: oPnl });
    kept.sort((a, b) => metricVal(b) - metricVal(a));
    return kept.map((t) => ({
      token: t.token,
      pnl: t.pnl,
      share: shareOf(t),
      pnlPct: totPnl ? (100 * t.pnl) / totPnl : 0
    }));
  });
  // Current price = the selected token's close as of the snapshot day (the
  // close-overlay value at/just before selectedUnix). Drawn like the entry line.
  const currentPrice = $derived.by(() => {
    if (!selectedToken || !closeSeries.length) return null;
    let v: number | null = null;
    for (const p of closeSeries) {
      if (p.time <= selectedUnix) v = p.value;
      else break;
    }
    return v;
  });
  // Snap the open time to its UTC midnight — the PnL series only has daily
  // bars, so an intraday timestamp won't resolve to a chart coordinate.
  const entryTime = $derived(
    selectedPos?.opened_at != null ? Math.floor(selectedPos.opened_at / 86400) * 86400 : null
  );
  // Entry line green when the position is in profit, red when underwater.
  const entryColor = $derived((selectedPos?.unrealized_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444');
  // The open date may predate the chart's visible window (floor). When it's
  // inside, draw the thin vertical marker; when it's before, draw no marker and
  // show a note instead (with the actual open date).
  const entryInRange = $derived(entryTime != null && entryTime >= floorUnix);
  const entryNote = $derived(
    selectedToken && entryTime != null && !entryInRange && selectedPos?.opened_at != null
      ? `${selectedToken} opened ${new Date(selectedPos.opened_at * 1000)
          .toISOString()
          .slice(0, 10)} — before chart range`
      : null
  );

  // Last daily point at or before the selected day → as-of realized/unrealized.
  const asOf = $derived.by(() => {
    let pick: PnlPoint | null = null;
    for (const p of pnlSeries) {
      if (p.time <= selectedUnix) pick = p;
      else break;
    }
    return pick;
  });

  const realizedAsOf = $derived(asOf ? asOf.realized : 0);
  const oiUsd = $derived(positions.reduce((s, p) => s + Math.abs(p.size_usd), 0));
  // Prefer the positions-source unrealized (live or snapshot); fall back to the
  // EOD series value when there are no position rows for the day.
  const unrealAsOf = $derived(
    positions.length ? positions.reduce((s, p) => s + p.unrealized_pnl, 0) : asOf ? asOf.unrealized : 0
  );
  const totalAsOf = $derived(realizedAsOf + unrealAsOf);
  // Cumulative within-window volume ($) + trade count as of the selected day.
  const volumeAsOf = $derived(asOf ? asOf.volume : 0);
  const tradesAsOf = $derived(asOf ? asOf.trades : 0);
  // Average notional traded per trade over the window.
  const avgTradeAsOf = $derived(tradesAsOf > 0 ? volumeAsOf / tradesAsOf : 0);

  // ── Range stats (only meaningful in range mode) ────────────────────
  // Relative deltas over [start, end] from the already-loaded daily curve:
  // realized/unrealized are end − start; sharpe = mean/σ of daily realized
  // flows within (start, end]; volume + start-OI are lazy-fetched.
  const asOfStart = $derived.by(() => {
    let pick: PnlPoint | null = null;
    for (const p of pnlSeries) {
      if (p.time <= startUnix) pick = p;
      else break;
    }
    return pick;
  });
  const realizedRange = $derived((asOf?.realized ?? 0) - (asOfStart?.realized ?? 0));
  const unrealRange = $derived((asOf?.unrealized ?? 0) - (asOfStart?.unrealized ?? 0));
  const oiRange = $derived(oiUsd - (oiStart ?? 0));
  // Volume + trades traded within (start, end] = cumulative end − cumulative start.
  const volumeRange = $derived((asOf?.volume ?? 0) - (asOfStart?.volume ?? 0));
  const tradesRange = $derived((asOf?.trades ?? 0) - (asOfStart?.trades ?? 0));
  // Annualized so ranges of different lengths are comparable: the daily
  // mean/σ ratio is scaled by √365 (24/7 crypto), matching the smart_selector.
  const sharpeRange = $derived.by(() => {
    const flows = pnlSeries
      .filter((p) => p.time > startUnix && p.time <= selectedUnix)
      .map((p) => p.realized_day);
    if (!flows.length) return 0;
    const mean = flows.reduce((a, b) => a + b, 0) / flows.length;
    const sd = Math.sqrt(flows.reduce((a, b) => a + (b - mean) ** 2, 0) / flows.length);
    return sd > 0 ? (mean / sd) * Math.sqrt(365) : 0;
  });

  // ── Formatters ─────────────────────────────────────────────────────
  function fmtUsd(n: number | null | undefined): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function truncate(addr: string): string {
    if (!addr || addr.length < 14) return addr;
    return addr.slice(0, 8) + '…' + addr.slice(-6);
  }
  function pnlClass(n: number): string {
    return n > 0 ? 'text-emerald-400' : n < 0 ? 'text-rose-400' : 'text-zinc-300';
  }
  async function copyAddr() {
    try {
      await navigator.clipboard.writeText(address);
      copied = true;
      setTimeout(() => (copied = false), 1200);
    } catch { /* no-op */ }
  }

  // ── Mappers (live API shape vs stored-snapshot shape → PositionRow) ──
  function mapLive(p: Record<string, unknown>): PositionRow {
    // HL's return_on_equity is leverage-true (PnL / margin). Past-day ROE can't
    // be (margin isn't stored) so we report return on entry notional there;
    // de-leverage the live value here (÷ leverage) so both modes mean the same.
    const lev = Number(p.leverage_value) || 0;
    const roeLev = Number(p.return_on_equity);
    return {
      token: String(p.token), side: p.side as 'long' | 'short',
      amount: Number(p.amount), size_usd: Number(p.size),
      unrealized_pnl: Number(p.unrealized_pnl),
      entry_px: p.entry_px as number, liquidation_px: p.liquidation_px as number,
      roe: lev ? roeLev / lev : roeLev, funding: p.cum_funding_since_open as number,
      leverage: p.leverage_value as number, leverage_type: p.leverage_type as string,
      // Live API has no open time; enriched from our latest stored snapshot.
      opened_at: p.opened_at != null ? Number(p.opened_at) : null
    };
  }
  function mapHist(p: Record<string, unknown>): PositionRow {
    const amount = Number(p.amount);
    const size_usd = Number(p.size_usd);
    const unrealized_pnl = Number(p.unrealized_pnl);
    const entry_px = p.entry_px != null ? Number(p.entry_px) : null;
    // ROE we can't get leverage-true for past days (margin isn't stored), so
    // report the return on the position's entry notional (= price move %).
    // entry_notional = |amount| × entry; equivalently size_usd ∓ unrealized.
    const entryNotional = entry_px ? Math.abs(amount) * entry_px : 0;
    const roe = entryNotional ? unrealized_pnl / entryNotional : null;
    return {
      token: String(p.token), side: p.side as 'long' | 'short',
      amount, size_usd, unrealized_pnl, entry_px, roe,
      funding: p.funding != null ? Number(p.funding) : null,
      opened_at: p.opened_at != null ? Number(p.opened_at) : null
    };
  }

  // ── Fetching ───────────────────────────────────────────────────────
  async function loadPnl() {
    pnlLoading = true; pnlError = null;
    try {
      const since = backToIso(MAX_BACK);
      const until = backToIso(0);
      const res = await fetch(
        `/api/hyperliquid/wallet_pnl?wallet=${address}&since=${since}&until=${until}`
      );
      if (!res.ok) throw new Error(`PnL ${res.status}`);
      const body = await res.json();
      pnlSeries = body.series ?? [];
      pnlStats = body.stats ?? null;
    } catch (e) {
      pnlError = (e as Error).message;
    } finally {
      pnlLoading = false;
    }
  }

  // Monotonic request token: only the latest loadPositions call may write
  // state. Without it the slow live-API fetch (mount) can resolve AFTER a fast
  // ClickHouse fetch for a past day and clobber it back to today's book.
  let posSeq = 0;
  async function loadPositions(iso: string) {
    const seq = ++posSeq;
    posLoading = true; posError = null;
    try {
      let nextPositions: PositionRow[];
      let nextAccount: number | null = null;
      if (isToday(iso)) {
        const res = await fetch(`/api/hyperliquid/live_positions?wallet=${address}`);
        if (!res.ok) throw new Error(`positions ${res.status}`);
        const body = await res.json();
        nextAccount = body.margin_summary?.account_value ?? null;
        nextPositions = (body.positions ?? []).map(mapLive);
      } else {
        const res = await fetch(`/api/hyperliquid/wallet_positions?wallet=${address}&day=${iso}`);
        if (!res.ok) throw new Error(`positions ${res.status}`);
        const body = await res.json();
        nextPositions = (body.positions ?? []).map(mapHist);
      }
      if (seq !== posSeq) return; // superseded by a newer request — drop this
      positions = nextPositions;
      accountValue = nextAccount;
      // Drop the close overlay if the newly-loaded day no longer holds that
      // token (the $effect on selectedToken then clears the chart series).
      if (selectedToken && !nextPositions.some((p) => p.token === selectedToken)) {
        selectedToken = null;
      }
    } catch (e) {
      if (seq !== posSeq) return;
      posError = (e as Error).message;
      positions = [];
      accountValue = null;
    } finally {
      if (seq === posSeq) posLoading = false;
    }
  }

  // PnL curve loads once (full history; slider doesn't move its right edge).
  $effect(() => {
    address; // re-run if the route address ever changes
    loadPnl();
  });

  // Transfers load once per wallet (snapshot-independent full history).
  async function loadTransfers() {
    transfersLoading = true;
    transfersError = null;
    try {
      const res = await fetch(`/api/hyperliquid/wallet_transfers?wallet=${address}`);
      if (!res.ok) throw new Error(`transfers ${res.status}`);
      const body = await res.json();
      transfers = (body.transfers ?? []) as TransferRow[];
    } catch (e) {
      transfersError = (e as Error).message;
      transfers = [];
    } finally {
      transfersLoading = false;
    }
  }
  $effect(() => {
    address;
    loadTransfers();
  });

  // Execution-quality stats over the full window [floor, today].
  async function loadTradeStats() {
    try {
      const since = backToIso(MAX_BACK);
      const until = backToIso(0);
      const res = await fetch(`/api/hyperliquid/wallet_trade_stats?wallet=${address}&since=${since}&until=${until}`);
      if (!res.ok) throw new Error(`trade_stats ${res.status}`);
      tradeStats = await res.json();
    } catch {
      tradeStats = null;
    }
  }
  $effect(() => {
    address;
    loadTradeStats();
  });

  // Same execution-quality stats recomputed over the selected range (range mode
  // only), debounced like the other range fetches.
  let rangeTsTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    if (!rangeMode) { rangeTradeStats = null; return; }
    const s = startIso, e = snapshotIso;
    clearTimeout(rangeTsTimer);
    rangeTsTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/hyperliquid/wallet_trade_stats?wallet=${address}&since=${s}&until=${e}`);
        rangeTradeStats = res.ok ? ((await res.json()) as TradeStats) : null;
      } catch {
        rangeTradeStats = null;
      }
    }, 200);
    return () => clearTimeout(rangeTsTimer);
  });

  // Positions refetch when the snapshot day changes — debounced so dragging
  // the slider doesn't fire a request (incl. the live API) per integer step.
  let posTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const iso = snapshotIso;
    clearTimeout(posTimer);
    posTimer = setTimeout(() => loadPositions(iso), 200);
    return () => clearTimeout(posTimer);
  });

  // Close-price overlay: (re)fetch the selected token's daily close series.
  // token_close already returns the full history from the floor date.
  // Monotonic token so a slow close fetch from a *select* can't resolve after a
  // later *deselect* and clobber state (the deselect→black race; the 2nd cycle
  // hid it because the response was HTTP-cached and resolved before deselect).
  let closeSeq = 0;
  async function loadClose(tok: string | null) {
    const seq = ++closeSeq;
    if (closeCtl) { closeCtl.abort(); closeCtl = null; }
    if (!tok) { closeSeries = []; return; }
    const ctl = new AbortController();
    closeCtl = ctl;
    try {
      // Scope to the chart window [floor, today] so the close overlay doesn't
      // extend the time-scale data range past the edge-locked bounds.
      const since = backToIso(MAX_BACK);
      const until = backToIso(0);
      const res = await fetch(
        `/api/hyperliquid/token_close?token=${encodeURIComponent(tok)}&since=${since}&until=${until}`,
        { signal: ctl.signal }
      );
      if (!res.ok) throw new Error(`token_close ${res.status}`);
      const body = await res.json();
      if (seq !== closeSeq) return; // superseded by a newer select/deselect
      closeSeries = ((body.series ?? []) as Array<{ time: number; close: number }>)
        .map((r) => ({ time: r.time, value: r.close }));
    } catch (e) {
      if (seq === closeSeq && (e as DOMException)?.name !== 'AbortError') {
        selectedToken = null;
        closeSeries = [];
      }
    }
  }
  $effect(() => {
    loadClose(selectedToken);
  });

  // 'Show Trades': fetch the per-day net buy/sell flow. Scoped to the selected
  // token when one is chosen; refetched when the toggle or token changes.
  async function loadTrades() {
    if (tradesCtl) { tradesCtl.abort(); tradesCtl = null; }
    if (!showTrades) { tradesRaw = []; tradesLoading = false; return; }
    // Clear the previous token's markers immediately so they don't linger while
    // the new token's flow loads (otherwise stale chips confuse the user).
    tradesRaw = [];
    const ctl = new AbortController();
    tradesCtl = ctl;
    tradesLoading = true;
    const since = backToIso(MAX_BACK);
    const until = backToIso(0);
    const tokQ = selectedToken ? `&token=${encodeURIComponent(selectedToken)}` : '';
    try {
      const res = await fetch(
        `/api/hyperliquid/wallet_trades?wallet=${address}&since=${since}&until=${until}${tokQ}`,
        { signal: ctl.signal }
      );
      if (!res.ok) throw new Error(`wallet_trades ${res.status}`);
      const body = await res.json();
      tradesRaw = (body.series ?? []) as Array<{ time: number; token: string; net_usd: number; net_tokens: number; avg_px: number }>;
    } catch (e) {
      if ((e as DOMException)?.name !== 'AbortError') tradesRaw = [];
    } finally {
      // Only the latest in-flight request clears the spinner.
      if (tradesCtl === ctl) tradesLoading = false;
    }
  }
  $effect(() => {
    void showTrades; void selectedToken; void address;
    loadTrades();
  });

  // Range-mode extras: total volume (backend) + start-day OI (sum of the start
  // snapshot's notional). realized/unrealized/sharpe are derived from pnlSeries
  // and need no fetch. Debounced + sequenced like positions.
  let rangeSeq = 0;
  let rangeTimer: ReturnType<typeof setTimeout> | undefined;
  async function loadRange(sIso: string) {
    const seq = ++rangeSeq;
    rangeLoading = true;
    try {
      // Only the start-day OI needs a fetch now; range volume + trades are
      // derived from the cumulative pnl series (see volumeRange / tradesRange).
      const pRes = await fetch(`/api/hyperliquid/wallet_positions?wallet=${address}&day=${sIso}`);
      let oi = 0;
      if (pRes.ok) {
        const body = await pRes.json();
        oi = (body.positions ?? []).reduce(
          (s: number, p: { size_usd: number }) => s + Math.abs(Number(p.size_usd)), 0
        );
      }
      if (seq !== rangeSeq) return;
      oiStart = oi;
    } catch {
      if (seq === rangeSeq) oiStart = null;
    } finally {
      if (seq === rangeSeq) rangeLoading = false;
    }
  }
  $effect(() => {
    if (!rangeMode) { oiStart = null; return; }
    const s = startIso;
    clearTimeout(rangeTimer);
    rangeTimer = setTimeout(() => loadRange(s), 200);
    return () => clearTimeout(rangeTimer);
  });

  function toggleRange() {
    if (!rangeMode) sliderStartPos = Math.max(0, sliderPos - 30); // default ~30d span
    rangeMode = !rangeMode;
  }

  // Chart picking → snapshot/range state. unix (UTC-midnight bar) → day index.
  const isoFromUnix = (unix: number) => new Date(unix * 1000).toISOString().slice(0, 10);
  const posForIso = (iso: string) => Math.max(0, Math.min(MAX_BACK, MAX_BACK - isoToBack(iso)));

  // Click a point on the chart → set the snapshot (as-of) day. Range mode is
  // now toggled explicitly, so a click no longer exits it — in range mode this
  // just moves the as-of (end) knob.
  function pickDay(unix: number) {
    sliderPos = posForIso(isoFromUnix(unix));
  }
  // Drag across the chart → range mode over the dragged [start, end] days.
  function pickRange(startUnix: number, endUnix: number) {
    sliderStartPos = posForIso(isoFromUnix(startUnix));
    sliderPos = posForIso(isoFromUnix(endUnix));
    rangeMode = true;
  }
</script>

<div class="px-8 py-6 space-y-6">
  <!-- Header -->
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <div class="flex items-center gap-2">
        <span class="text-zinc-300 text-sm px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">HL</span>
        <h1 class="text-2xl font-semibold font-mono">{truncate(address)}</h1>
        <button
          type="button"
          onclick={copyAddr}
          title="Copy full address"
          class="text-sm text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 rounded border border-zinc-800 hover:border-zinc-600"
        >{copied ? '✓ copied' : 'copy'}</button>
        <div class="relative">
          <button
            type="button"
            onclick={() => (pinMenuOpen = !pinMenuOpen)}
            title="Pin this wallet to groups"
            class="text-sm px-1.5 py-0.5 rounded border transition-colors {pinnedGroups.length
              ? 'border-amber-700 bg-amber-950/40 text-amber-300 hover:border-amber-600'
              : 'border-zinc-800 text-zinc-500 hover:text-zinc-200 hover:border-zinc-600'}"
          >{pinnedGroups.length ? `★ Pinned (${pinnedGroups.length})` : '☆ Pin'}</button>
          {#if pinMenuOpen}
            <WalletPinMenu {address} onClose={() => (pinMenuOpen = false)} />
          {/if}
        </div>
      </div>
      <div class="text-sm text-zinc-500 mt-1 flex items-center gap-3">
        <span class="font-mono break-all">{address}</span>
        <a href={coinglassHlUrl(address)} target="_blank" rel="noopener noreferrer"
           class="underline decoration-dotted hover:text-zinc-200">Coinglass ↗</a>
        <a href={arkhamUrl(address)} target="_blank" rel="noopener noreferrer"
           class="underline decoration-dotted hover:text-zinc-200">Arkham ↗</a>
      </div>
    </div>
    <!-- As-of indicator (drives the positions/stats below the chart) -->
    <div class="text-right">
      <div class="text-zinc-500 text-xs uppercase tracking-wide flex items-center justify-end gap-2">
        As of
        {#if posLoading}
          <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading positions…"></span>
        {/if}
      </div>
      <div class="font-mono text-xl text-zinc-100 tabular-nums">
        {snapshotIso}{#if live}<span class="text-emerald-400 text-sm ml-1">· live</span>{/if}
      </div>
    </div>
  </div>

  <!-- Stat cards (as of the selected day), in three rows. -->
  <div class="space-y-2">
    {#snippet card(label: string, value: string, cls = 'text-zinc-200', sub = '')}
      <div class="rounded-lg bg-zinc-900/70 border border-zinc-800 px-3 py-2">
        <div class="text-zinc-500 text-xs uppercase tracking-wide">{label}</div>
        <div class="tabular-nums text-lg font-medium {cls}">{value}</div>
        {#if sub}<div class="text-zinc-600 text-[11px]">{sub}</div>{/if}
      </div>
    {/snippet}
    <!-- Row 1: PnLs + Sharpe -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {@render card('Realized PnL', fmtUsd(realizedAsOf), pnlClass(realizedAsOf))}
      {@render card('Unrealized PnL', fmtUsd(unrealAsOf), pnlClass(unrealAsOf))}
      {@render card('Total PnL', fmtUsd(totalAsOf), pnlClass(totalAsOf))}
      {@render card('Sharpe (ann.)', pnlStats ? pnlStats.sharpe.toFixed(2) : '—', 'text-zinc-300', 'window')}
    </div>
    <!-- Row 2: OI, Positions, Account Value -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {@render card('Open Interest', fmtUsd(oiUsd))}
      {@render card('Positions', String(positions.length))}
      {@render card('Account Value', fmtUsd(accountValue), 'text-zinc-200', 'only perp')}
      {@render card('Account Age', tradeStats ? tradeStats.account_duration_days.toLocaleString('en-US') + ' d' : '—', 'text-zinc-200', 'since 1st trade')}
    </div>
    <!-- Row 3: Volume, Trades, Avg trade value, Trades/day -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {@render card('Volume', fmtUsd(volumeAsOf), 'text-zinc-200', 'since 01-01')}
      {@render card('Trades', tradesAsOf.toLocaleString('en-US'), 'text-zinc-200', 'since 01-01')}
      {@render card('Avg Trade Value', fmtUsd(avgTradeAsOf), 'text-zinc-200', 'per trade')}
      {@render card('Trades / Day', tradeStats ? tradeStats.trades_per_day.toLocaleString('en-US', { maximumFractionDigits: 1 }) : '—', 'text-zinc-200', 'per active day')}
    </div>
    <!-- Row 4: execution-quality (taker %, fee/PnL %, funding/PnL %); full window -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {@render card('Taker %', tradeStats ? tradeStats.taker_pct.toFixed(1) + '%' : '—', 'text-zinc-200', 'by volume')}
      {@render card('Fee / PnL %', tradeStats && tradeStats.fee_pct != null ? tradeStats.fee_pct.toFixed(1) + '%' : '—', 'text-zinc-300', 'since 01-01')}
      {@render card('Funding / PnL %', tradeStats && tradeStats.funding_pct != null ? tradeStats.funding_pct.toFixed(1) + '%' : '—', 'text-zinc-300', 'since 01-01')}
      {@render card('Win Rate', tradeStats && tradeStats.win_rate != null ? tradeStats.win_rate.toFixed(1) + '%' : '—', 'text-zinc-200', 'profitable days')}
    </div>
    <!-- Token mix: share per token by Volume or PnL (since 01-01); tokens under
         0.1% of the selected metric fold into "Other". TODO: token icons. -->
    {#if tokenView.length}
      <div class="rounded-lg bg-zinc-900/70 border border-zinc-800 px-3 py-2">
        <div class="flex items-center gap-2 mb-1.5">
          <span class="text-zinc-500 text-xs uppercase tracking-wide">Traded tokens · {tokenMetric === 'volume' ? 'volume' : 'PnL'} share <span class="text-zinc-600 normal-case">(since 01-01)</span></span>
          <div class="ml-auto inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
            <button type="button" onclick={() => (tokenMetric = 'volume')}
              class={'px-2 py-0.5 text-xs ' + (tokenMetric === 'volume' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}>Volume</button>
            <button type="button" onclick={() => (tokenMetric = 'pnl')}
              class={'px-2 py-0.5 text-xs border-l border-zinc-700 ' + (tokenMetric === 'pnl' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}>PnL</button>
          </div>
        </div>
        <div class="flex flex-wrap gap-1.5">
          {#each tokenView as t (t.token)}
            <span class="inline-flex flex-col items-start gap-0.5 text-xs px-2 py-1 rounded-md border bg-zinc-950 {t.token === 'Other' ? 'border-zinc-800' : 'border-zinc-700'}">
              <span class="flex items-center gap-1.5">
                <span class="font-mono {t.token === 'Other' ? 'text-zinc-500' : 'text-zinc-200'}">{t.token}</span>
                <span class="tabular-nums text-zinc-400">{t.share.toFixed(1)}%</span>
              </span>
              <span class="tabular-nums text-[11px] {t.pnl > 0 ? 'text-emerald-400' : t.pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">
                {fmtUsd(t.pnl)}{#if tokenMetric === 'volume'}<span class="text-zinc-500"> ({t.pnlPct.toFixed(1)}%)</span>{/if}
              </span>
            </span>
          {/each}
        </div>
      </div>
    {/if}
  </div>

  <!-- Range stat row: relative deltas + volume/sharpe over [start, end] -->
  {#if rangeMode}
    {@const rsub = `${startIso} → ${snapshotIso}`}
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
      {#snippet rcard(label: string, value: string, cls = 'text-zinc-200')}
        <div class="rounded-lg bg-blue-950/30 border border-blue-900/50 px-3 py-2">
          <div class="text-blue-300/70 text-xs uppercase tracking-wide flex items-center gap-1.5">
            {label}
            {#if rangeLoading}<span class="inline-block w-2.5 h-2.5 rounded-full border-2 border-blue-900 border-t-blue-300 animate-spin"></span>{/if}
          </div>
          <div class="tabular-nums text-lg font-medium {cls}">{value}</div>
          <div class="text-zinc-600 text-[11px]">{rsub}</div>
        </div>
      {/snippet}
      {@render rcard('Range Volume', fmtUsd(volumeRange))}
      {@render rcard('Range Trades', tradesRange.toLocaleString('en-US'))}
      {@render rcard('Range OI Δ', oiStart != null ? fmtUsd(oiRange) : '…', pnlClass(oiRange))}
      {@render rcard('Range Realized', fmtUsd(realizedRange), pnlClass(realizedRange))}
      {@render rcard('Range Unrealized', fmtUsd(unrealRange), pnlClass(unrealRange))}
      {@render rcard('Range Sharpe (ann.)', sharpeRange.toFixed(2), 'text-zinc-300')}
      {@render rcard('Range Avg Trade', rangeTradeStats ? fmtUsd(rangeTradeStats.avg_trade_size) : '…')}
      {@render rcard('Range Trades / Day', rangeTradeStats ? rangeTradeStats.trades_per_day.toLocaleString('en-US', { maximumFractionDigits: 1 }) : '…')}
      {@render rcard('Range Taker %', rangeTradeStats ? rangeTradeStats.taker_pct.toFixed(1) + '%' : '…')}
      {@render rcard('Range Fee / PnL %', rangeTradeStats && rangeTradeStats.fee_pct != null ? rangeTradeStats.fee_pct.toFixed(1) + '%' : '…', 'text-zinc-300')}
      {@render rcard('Range Funding / PnL %', rangeTradeStats && rangeTradeStats.funding_pct != null ? rangeTradeStats.funding_pct.toFixed(1) + '%' : '…', 'text-zinc-300')}
      {@render rcard('Range Win Rate', rangeTradeStats && rangeTradeStats.win_rate != null ? rangeTradeStats.win_rate.toFixed(1) + '%' : '…')}
    </div>
  {/if}

  <!-- PnL equity curve + aligned date slider -->
  <div class="rounded-lg border border-zinc-800 overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
      <span class="text-zinc-200 font-medium text-base">{modeLabel}</span>
      <span class="text-xs text-zinc-600">global · all tokens</span>
      <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden ml-2">
        <button type="button" onclick={() => (pnlMode = 'total')}
          class={'px-2 py-0.5 text-xs ' + (pnlMode === 'total' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Realized + unrealized equity curve">Total</button>
        <button type="button" onclick={() => (pnlMode = 'realized')}
          class={'px-2 py-0.5 text-xs border-l border-zinc-700 ' + (pnlMode === 'realized' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Cumulative realized PnL only">Realized</button>
        <button type="button" onclick={() => (pnlMode = 'unrealized')}
          class={'px-2 py-0.5 text-xs border-l border-zinc-700 ' + (pnlMode === 'unrealized' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Unrealized (open-position) PnL only">Unrealized</button>
        <button type="button" onclick={() => (pnlMode = 'oi')}
          class={'px-2 py-0.5 text-xs border-l border-zinc-700 ' + (pnlMode === 'oi' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="End-of-day open interest (total notional $)">OI</button>
      </div>
      <button type="button" onclick={toggleRange}
        class="ml-1 text-xs px-2 py-0.5 rounded border transition-colors {rangeMode
          ? 'bg-blue-600 border-blue-500 text-white'
          : 'border-zinc-700 text-zinc-400 hover:text-zinc-200'}"
        title="Range mode: drag across the chart to pick a [start, end] window; a range stat row is added below.">Range</button>
      <button type="button" onclick={() => (showTrades = !showTrades)}
        class="text-xs px-2 py-0.5 rounded border transition-colors {showTrades
          ? 'bg-blue-600 border-blue-500 text-white'
          : 'border-zinc-700 text-zinc-400 hover:text-zinc-200'}"
        title="Show per-day net buy (green ▲) / sell (red ▼) markers. Scoped to the selected token, else summed across all tokens.">Show Trades</button>
      {#if showTrades}
        <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
          <button type="button" onclick={() => (tradeUnit = 'usd')}
            class={'px-2 py-0.5 text-xs ' + (tradeUnit === 'usd' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Show trade values in USD">USD</button>
          <button type="button" onclick={() => (tradeUnit = 'token')}
            class={'px-2 py-0.5 text-xs border-l border-zinc-700 ' + (tradeUnit === 'token' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title="Show trade values in token units (single-token labels and per-token hover; multi-token chip totals stay in USD)">Token</button>
        </div>
      {/if}
      {#if selectedToken}
        <span class="ml-auto inline-flex items-center gap-1.5 text-xs text-blue-300">
          <span class="inline-block w-3 h-0.5 rounded bg-blue-500"></span>
          {selectedToken} close
          <button type="button" onclick={() => (selectedToken = null)}
            class="text-zinc-500 hover:text-zinc-200" title="Remove overlay">✕</button>
        </span>
      {/if}
      {#if pnlStats}
        <span class="text-xs text-zinc-500 {selectedToken ? '' : 'ml-auto'}">σ {pnlStats.volatility.toFixed(0)} · Sharpe(ann.) {pnlStats.sharpe.toFixed(2)}</span>
      {/if}
    </div>
    <div class="px-2 pt-2">
      {#if pnlError}
        <div class="h-[360px] flex items-center justify-center text-rose-400">{pnlError}</div>
      {:else if pnlLoading && pnlSeries.length === 0}
        <div class="h-[360px] flex items-center justify-center text-zinc-500">loading…</div>
      {:else if pnlSeries.length === 0}
        <div class="h-[360px] flex items-center justify-center text-zinc-500">No PnL history for this wallet.</div>
      {:else}
        <WalletPnlChart
          data={chartData}
          closeData={closeSeries}
          entryPrice={selectedToken ? entryPrice : null}
          currentPrice={selectedToken ? currentPrice : null}
          entryTime={selectedToken && entryInRange ? entryTime : null}
          entryNote={selectedToken ? entryNote : null}
          entryColor={entryColor}
          height={360}
          cutoff={selectedUnix}
          lookbackStart={rangeMode ? startUnix : null}
          rangeFrom={floorUnix}
          rangeTo={todayUnix}
          bandFrom={rangeMode ? startUnix : null}
          bandTo={rangeMode ? selectedUnix : null}
          trades={tradeMarkers}
          rangeMode={rangeMode}
          valueHeader={tradeUnit === 'token' ? 'Amount' : 'Value($)'}
          loading={posLoading || tradesLoading}
          onPickDay={pickDay}
          onPickRange={pickRange}
          label={modeLabel}
        />
      {/if}
    </div>
  </div>

  <!-- Positions table -->
  <div class="h-[420px]">
    <WalletPositionsTable
      {positions} {live} loading={posLoading} error={posError}
      {selectedToken}
      onToggleToken={(t) => (selectedToken = selectedToken === t ? null : t)}
    />
  </div>

  <!-- Transfers (deposits / withdrawals) — full history, snapshot-independent -->
  <div class="h-[360px]">
    <WalletTransfersTable transfers={transfers} loading={transfersLoading} error={transfersError} />
  </div>
</div>
