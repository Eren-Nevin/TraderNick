<script lang="ts">
  // Latest individual trades (fills) for the HL wallet detail page. Owns its own fetch
  // (/api/hyperliquid/wallet_fills) + loading state so a long window can't block the
  // rest of the page. In RANGE mode it shows the selected date range; otherwise a
  // lookback selector (default 1d = last 24h) bounds the window — longer picks (up to
  // 90d) are opt-in and show a loading indicator.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { fmtTzDateTime } from '$lib/stores/timezone.svelte';

  export type TradeRow = {
    time: number; token: string; dir: string; side: string;
    price: number; size: number; value: number; closed_pnl: number; fee: number;
  };

  let {
    wallet = '',
    rangeMode = false,
    rangeSince = '',
    rangeUntil = ''
  }: {
    wallet?: string;
    rangeMode?: boolean;
    rangeSince?: string; // ISO (range mode only)
    rangeUntil?: string; // ISO (range mode only)
  } = $props();

  const LOOKBACKS: Array<{ v: string; secs: number }> = [
    { v: '15m', secs: 900 }, { v: '1h', secs: 3600 }, { v: '4h', secs: 14400 },
    { v: '1d', secs: 86400 }, { v: '3d', secs: 259200 }, { v: '7d', secs: 604800 },
    { v: '30d', secs: 2592000 }, { v: '90d', secs: 7776000 }
  ];
  let lookback = $state('1h');

  let trades = $state<TradeRow[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let tokenFilter = $state('');
  let ctl: AbortController | null = null;

  const shown = $derived(
    tokenFilter.trim()
      ? trades.filter((t) => t.token.toLowerCase().includes(tokenFilter.trim().toLowerCase()))
      : trades
  );
  const isBuy = (t: TradeRow) => t.side === 'B';

  async function load(w: string, rm: boolean, rs: string, ru: string, lb: string) {
    if (!w) { trades = []; return; }
    ctl?.abort();
    const c = new AbortController();
    ctl = c;
    loading = true;
    error = null;
    try {
      let since: string, until: string;
      if (rm && rs && ru) {
        since = rs;
        until = ru;
      } else {
        const secs = LOOKBACKS.find((l) => l.v === lb)?.secs ?? 3600;
        const now = Date.now();
        since = new Date(now - secs * 1000).toISOString();
        until = new Date(now).toISOString();
      }
      const res = await fetch(
        `/api/hyperliquid/wallet_fills?wallet=${w}&since=${since}&until=${until}&limit=500`,
        { signal: c.signal }
      );
      if (!res.ok) throw new Error(`trades ${res.status}`);
      const body = await res.json();
      trades = (body.trades ?? []) as TradeRow[];
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        error = (e as Error).message;
        trades = [];
      }
    } finally {
      if (ctl === c) loading = false;
    }
  }
  // Reload on wallet / mode / range window / lookback change.
  $effect(() => {
    load(wallet, rangeMode, rangeSince, rangeUntil, lookback);
  });

  function fmtUsd(n: number): string {
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtNum(n: number): string {
    const a = Math.abs(n);
    if (a >= 1e3) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (a >= 1) return n.toFixed(3);
    return a === 0 ? '0' : n.toPrecision(3);
  }
  function fmtPrice(n: number): string {
    return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toPrecision(5);
  }
</script>

<div class="h-full flex flex-col text-sm border border-zinc-800 rounded-lg overflow-hidden" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-200 font-medium">Trades</span>
    {#if loading}
      <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading…"></span>
    {/if}
    {#if !rangeMode}
      <select
        class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-200 focus:outline-none focus:border-zinc-500"
        bind:value={lookback} title="How far back to load trades">
        {#each LOOKBACKS as l (l.v)}<option value={l.v}>{l.v}</option>{/each}
      </select>
    {:else}
      <span class="text-zinc-500 text-xs">range</span>
    {/if}
    <input
      class="ml-auto bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs text-zinc-200 w-24 focus:outline-none focus:border-zinc-500"
      placeholder="Token…" bind:value={tokenFilter} title="Filter by token" />
    <span class="text-zinc-500 text-xs whitespace-nowrap">{shown.length} fills</span>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-50' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4 py-8">{error}</div>
    {:else if loading && trades.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">Loading trades…</div>
    {:else if shown.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">
        {trades.length === 0 ? 'No trades in this window.' : 'No trades match the filter.'}
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">Time</th>
            <th class="text-left px-3 py-1.5 font-normal">Token</th>
            <th class="text-left px-3 py-1.5 font-normal">Direction</th>
            <th class="text-right px-3 py-1.5 font-normal">Price</th>
            <th class="text-right px-3 py-1.5 font-normal">Size</th>
            <th class="text-right px-3 py-1.5 font-normal">Value</th>
            <th class="text-right px-3 py-1.5 font-normal">Realized PnL</th>
            <th class="text-right px-3 py-1.5 font-normal">Fee</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as t, i (i)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-300 tabular-nums whitespace-nowrap">{fmtTzDateTime(t.time)}</td>
              <td class="px-3 py-1 text-zinc-200">{t.token}</td>
              <td class="px-3 py-1 whitespace-nowrap">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {isBuy(t)
                  ? 'border-emerald-800 text-emerald-400'
                  : 'border-rose-800 text-rose-400'}">{t.dir}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-300">{fmtPrice(t.price)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400">{fmtNum(t.size)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-300">{fmtUsd(t.value)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {t.closed_pnl > 0 ? 'text-emerald-400' : t.closed_pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">{t.closed_pnl ? fmtUsd(t.closed_pnl) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-500">{fmtUsd(t.fee)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
