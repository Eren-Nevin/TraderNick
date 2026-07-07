<script lang="ts">
  // Backtracker "Net Position" dialog: the FULL position book in one token for the
  // selected wallet group at the clicked bar — every group member HOLDING the token
  // (not just those who changed), with the wallets-page position columns plus the
  // position CHANGE over the bar window. The "Query by" selector picks the
  // SERVER-SIDE ranking column (which top-N comes back); "Show" picks the limit
  // (20/50). Column-header clicks re-sort the returned rows CLIENT-side only.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';
  import { tzShortLabel } from '$lib/stores/timezone.svelte';

  type Row = {
    wallet: string;
    side: 'long' | 'short' | 'flat';
    closed?: boolean;       // traded in the window but flat at t_end (closed-out)
    amount: number;         // positive magnitude (side is separate)
    size_usd: number;       // positive magnitude
    entry_px: number | null;
    unrealized_pnl: number | null; // null when the position flipped since the snapshot
    roe: number | null;     // return on entry notional (fraction)
    funding: number | null;
    change_amount: number;  // signed: + net bought, − net sold
    change_usd: number;     // signed
    last_change: number;    // unix s of the wallet's most recent fill in this token (0 = never)
    categories?: string[];
  };

  let {
    open,
    rows = [] as Row[],
    price = 0,
    token = '',
    lookback = '',
    timeLabel = '',
    startLabel = '',
    endLabel = '',
    snapshotDate = '',
    loading = false,
    error: errMsg = null,
    groupName = null,
    order = 'change',
    onOrderChange,
    limit = 20,
    onLimitChange,
    lastChangeSince = '',
    onLastChangeSinceChange,
    changeLookback = '',
    onChangeLookbackChange,
    onClose
  }: {
    open: boolean;
    rows?: Row[];
    price?: number;
    token?: string;
    lookback?: string;
    timeLabel?: string;
    startLabel?: string;
    endLabel?: string;
    snapshotDate?: string;
    loading?: boolean;
    error?: string | null;
    groupName?: string | null;
    /** Server-side ranking column (which top-N is fetched). */
    order?: string;
    onOrderChange: (o: string) => void;
    /** Server-side row limit (20/50). */
    limit?: number;
    onLimitChange: (n: number) => void;
    /** "Last change since" filter (YYYY-MM-DD, UTC; '' = off) — keep only wallets
     *  whose most recent fill in the token is on/after this date. */
    lastChangeSince?: string;
    onLastChangeSinceChange: (d: string) => void;
    /** Override for the Change column's window START ('' = the clicked bar's own
     *  window; otherwise 1h/4h/1d/3d/7d/14d/30d ending at the bar). */
    changeLookback?: string;
    onChangeLookbackChange: (v: string) => void;
    onClose: () => void;
  } = $props();

  const CHANGE_LB_OPTS = ['', '15m', '1h', '4h', '12h', '1d', '3d', '7d', '14d', '30d'];

  const ORDER_LABELS: Record<string, string> = {
    change: 'Net change', value: 'Position value', upnl: 'Unrealized PnL', roe: 'ROE',
    entry: 'Entry', last_change: 'Last change'
  };

  function fmtUsd(n: number | null): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtAmt(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(1) + 'K';
    return sign + abs.toFixed(2);
  }
  function fmtPrice(n: number | null | undefined): string {
    if (!n) return '—';
    if (n >= 1000) return n.toFixed(0);
    if (n >= 1) return n.toFixed(2);
    if (n >= 0.01) return n.toFixed(4);
    return n.toFixed(6);
  }
  function fmtRoe(r: number | null | undefined): string {
    if (r === null || r === undefined || !isFinite(r)) return '—';
    return (r >= 0 ? '+' : '') + (r * 100).toFixed(1) + '%';
  }
  // Latest fill time in the token, "MM-DD HH:MM" in the active zone (0 = never traded).
  function fmtLastChange(ts: number): string {
    if (!ts) return '—';
    let s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    const parts: string[] = [];
    if (d) parts.push(`${d}d`);
    if (h) parts.push(`${h}h`);
    if (m || !parts.length) parts.push(`${m}m`);
    return `${parts.join(' ')} ago`;
  }

  // ── client-side sort. '' = server order (already ranked by `order`). ──
  let sortKey = $state<string>('');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  function sortVal(r: Row, k: string): number {
    if (k === 'value') return r.size_usd;
    if (k === 'amount') return r.amount;
    if (k === 'entry') return r.entry_px ?? 0;
    if (k === 'upnl') return r.unrealized_pnl ?? 0;
    if (k === 'roe') return r.roe ?? 0;
    if (k === 'funding') return r.funding ?? 0;
    if (k === 'change') return Math.abs(r.change_amount);
    if (k === 'last_change') return r.last_change;
    return 0;
  }
  let sortedRows = $derived.by(() => {
    if (!sortKey) return rows;
    const dir = sortDir;
    return [...rows].sort((a, b) => (sortVal(a, sortKey) - sortVal(b, sortKey)) * dir);
  });

  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') onClose();
  }
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm cursor-default"
    role="dialog" aria-modal="true" tabindex="-1"
    onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
    use:stopDragEvents
  >
    <div class="w-[96rem] max-w-[98vw] max-h-[90vh] bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl flex flex-col text-sm">
      <header class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
        <div class="flex items-center gap-2">
          <span class="text-zinc-300 font-medium">Positions</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-200">{token}</span>{/if}
          {#if groupName}<span class="text-zinc-500">·</span><span class="text-zinc-400">{groupName}</span>{/if}
          {#if lookback === 'none'}
            <span class="text-zinc-500">· current bar</span>
          {:else}
            {#if lookback}<span class="text-zinc-500">· Δ</span><span class="text-zinc-400">{lookback}</span>{/if}
            {#if timeLabel}<span class="text-zinc-500">to</span><span class="text-zinc-400">{timeLabel} {tzShortLabel()}</span>{/if}
          {/if}
        </div>
        <div class="flex items-center gap-2">
          <label class="flex items-center gap-1 text-xs text-zinc-500">
            Query by
            <select
              value={order}
              onchange={(e) => onOrderChange(e.currentTarget.value)}
              class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500"
              title="Server-side ranking column — picks WHICH wallets come back (a real top-N cut, not a client re-sort)"
            >
              {#each Object.entries(ORDER_LABELS) as [k, label] (k)}
                <option value={k}>{label}</option>
              {/each}
            </select>
          </label>
          <label class="flex items-center gap-1 text-xs text-zinc-500">
            Show
            <select
              value={String(limit)}
              onchange={(e) => onLimitChange(Number(e.currentTarget.value))}
              class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500"
              title="How many top wallets to query"
            >
              <option value="20">Top 20</option>
              <option value="50">Top 50</option>
            </select>
          </label>
          <label class="flex items-center gap-1 text-xs text-zinc-500"
            title="Measure the Change column over this window ending at the bar (— = the clicked bar's own window)">
            Change over
            <select
              value={changeLookback}
              onchange={(e) => onChangeLookbackChange(e.currentTarget.value)}
              class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500"
            >
              {#each CHANGE_LB_OPTS as o (o)}
                <option value={o}>{o === '' ? '—' : o}</option>
              {/each}
            </select>
          </label>
          <label class="flex items-center gap-1 text-xs text-zinc-500"
            title="Only wallets whose most recent fill in this token is on/after this date (UTC)">
            Active since
            <input
              type="date"
              value={lastChangeSince}
              onchange={(e) => onLastChangeSinceChange(e.currentTarget.value)}
              class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500" />
            {#if lastChangeSince}
              <button type="button" onclick={() => onLastChangeSinceChange('')}
                class="text-zinc-500 hover:text-zinc-200 text-xs leading-none" title="Clear date filter">✕</button>
            {/if}
          </label>
          <button type="button" class="text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 cursor-pointer" onclick={onClose} aria-label="Close">✕</button>
        </div>
      </header>
      <div class="px-4 py-2 text-xs text-zinc-500 border-b border-zinc-800">
        Group members holding {token || 'the token'} <span class="text-zinc-400">or who traded it in the window (marked <span class="uppercase text-[10px] tracking-wider">closed</span>)</span>, ranked server-side by
        <span class="text-zinc-300">{ORDER_LABELS[order] ?? order}</span>. Column headers re-sort these rows only.
      </div>

      <div class="flex-1 overflow-auto scrollbar-none">
        {#if loading}
          <div class="px-4 py-6 text-zinc-400 text-center">Loading…</div>
        {:else if errMsg}
          <div class="px-4 py-6 text-red-400 text-center">{errMsg}</div>
        {:else if rows.length === 0}
          <div class="px-4 py-6 text-zinc-500 text-center">No group positions in {token || 'this token'} at this bar.</div>
        {:else}
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-zinc-950 text-zinc-500 text-[11px] uppercase tracking-wide border-b border-zinc-800">
              <tr>
                <th class="px-3 py-1.5 text-left font-normal">#</th>
                <th class="px-3 py-1.5 text-left font-normal">Address</th>
                <th class="px-3 py-1.5 text-left font-normal">Side</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('value')}>Value($){sortArrow('value')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('amount')}>Amount{sortArrow('amount')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('entry')}>Entry{sortArrow('entry')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('upnl')}>Unrealized{sortArrow('upnl')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('roe')} title="Return on entry notional (unleveraged) — margin isn't stored for past snapshots">ROE{sortArrow('roe')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('funding')}>Funding{sortArrow('funding')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('change')} title="Net position change over the bar window (fills-based)">Change{sortArrow('change')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('last_change')} title="Time since the wallet's most recent fill in this token (relative to now)">Last change{sortArrow('last_change')}</th>
              </tr>
            </thead>
            <tbody>
              {#each sortedRows as r, i (r.wallet)}
                <tr class="border-b border-zinc-900 hover:bg-zinc-900/50 {r.closed ? 'bg-zinc-900/40' : ''}">
                  <td class="px-3 py-1.5 text-zinc-500 tabular-nums">{i + 1}</td>
                  <td class="px-3 py-1.5">
                    <WalletAddress address={r.wallet} auxKind="wallet" snapshot={snapshotDate} token={token} tags={r.categories ?? []} />
                  </td>
                  <td class="px-3 py-1.5">
                    <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {r.closed
                      ? 'bg-zinc-800/60 border-zinc-600 text-zinc-400'
                      : r.side === 'long'
                      ? 'bg-emerald-950/40 border-emerald-800 text-emerald-400'
                      : 'bg-rose-950/40 border-rose-800 text-rose-400'}" title={r.closed ? 'Traded in the window but flat now (closed out)' : ''}>{r.closed ? 'closed' : r.side}</span>
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-300">{r.closed ? '—' : fmtUsd(r.size_usd)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-400">{r.closed ? '—' : fmtAmt(r.amount)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-400">{fmtPrice(r.entry_px)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {(r.unrealized_pnl ?? 0) > 0 ? 'text-emerald-400' : (r.unrealized_pnl ?? 0) < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtUsd(r.unrealized_pnl)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {(r.roe ?? 0) > 0 ? 'text-emerald-400' : (r.roe ?? 0) < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtRoe(r.roe)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-500">{fmtUsd(r.funding)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums whitespace-nowrap">
                    {#if Math.abs(r.change_amount) < 1e-9}
                      <span class="text-zinc-600">—</span>
                    {:else}
                      <span class={r.change_amount > 0 ? 'text-emerald-400' : 'text-rose-400'}>
                        {r.change_amount > 0 ? '+' : '−'}{fmtUsd(Math.abs(r.change_usd))}
                      </span>
                      <div class="text-[11px] text-zinc-500">{fmtAmt(Math.abs(r.change_amount))} {token}</div>
                    {/if}
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-[11px] whitespace-nowrap {r.last_change ? 'text-zinc-400' : 'text-zinc-600'}">{fmtLastChange(r.last_change)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      {#if rows.length > 0 && !loading}
        <footer class="px-4 py-1.5 border-t border-zinc-800 text-xs text-zinc-500 text-right">
          {startLabel}<span class="text-zinc-600"> → </span>{endLabel} · {rows.length} of top {limit} · {token} @ {fmtUsd(price)}
        </footer>
      {/if}
    </div>
  </div>
{/if}
