<script lang="ts">
  // Top-Positions chart kind for /hyperliquid. Shows the top 10 wallets by
  // current unrealized PnL (filtered to a single token, or summed across
  // all tokens) — and for the selected wallet from that top-10, the full
  // breakdown of their currently-open positions.
  //
  // One fetch hydrates BOTH the wallet list and every wallet's positions,
  // so flipping the wallet dropdown is instant (no re-fetch).

  type Position = {
    token: string;
    side: 'long' | 'short';
    unrealized_pnl: number;
    size: number;
    amount: number;
    avg_entry: number;
    mark_price: number;
    funding: number;
    fee: number;
    opened_at: number | null;
    as_of: number;
  };
  type WalletEntry = {
    rank: number;
    wallet: string;
    score_unrealized_pnl: number;
    categories: string[];
    positions: Position[];
  };

  let {
    wallets = [],
    selectedWallet = '',
    onSelectWallet
  }: {
    wallets: WalletEntry[];
    selectedWallet: string;
    onSelectWallet: (wallet: string) => void;
  } = $props();

  // Fall back to the top-ranked wallet whenever the saved selection is
  // missing from the freshly-fetched leaderboard (token changed, wallet
  // dropped out of the top 10, etc.).
  let effectiveSelected = $derived.by(() => {
    if (wallets.length === 0) return '';
    const found = wallets.find((w) => w.wallet.toLowerCase() === selectedWallet.toLowerCase());
    return found ? found.wallet : wallets[0].wallet;
  });
  let selectedEntry = $derived(
    wallets.find((w) => w.wallet === effectiveSelected) ?? null
  );

  function truncate(addr: string): string {
    if (!addr) return '';
    if (addr.length < 14) return addr;
    return addr.slice(0, 6) + '…' + addr.slice(-4);
  }
  function fmtUsd(n: number): string {
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtPrice(n: number): string {
    if (!n) return '—';
    if (n >= 1000) return n.toFixed(0);
    if (n >= 1)    return n.toFixed(2);
    if (n >= 0.01) return n.toFixed(4);
    return n.toFixed(6);
  }
  function fmtSize(n: number, token: string): string {
    // Tokens trade in very different unit magnitudes. Keep 4 sig figs.
    if (!n) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M ' + token;
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(2) + 'K ' + token;
    if (abs >= 1)   return sign + abs.toFixed(2) + ' ' + token;
    return sign + abs.toFixed(4) + ' ' + token;
  }
  function fmtPct(entry: number, mark: number, side: 'long' | 'short'): string {
    if (!entry || !mark) return '—';
    const raw = (mark - entry) / entry;
    const signed = side === 'short' ? -raw : raw;
    return (signed >= 0 ? '+' : '') + (signed * 100).toFixed(2) + '%';
  }
  function fmtAge(opened_at: number | null, as_of: number): string {
    if (!opened_at) return '—';
    const secs = Math.max(0, as_of - opened_at);
    const d = Math.floor(secs / 86400);
    const h = Math.floor((secs % 86400) / 3600);
    if (d > 0) return `${d}d ${h}h`;
    const m = Math.floor((secs % 3600) / 60);
    return `${h}h ${m}m`;
  }

  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { onAuxClickArkham, onMouseDownSuppressMiddle } from '$lib/arkham';

  let copied = $state(false);
  async function copyAddr() {
    if (!effectiveSelected) return;
    try {
      await navigator.clipboard.writeText(effectiveSelected);
      copied = true;
      setTimeout(() => (copied = false), 1200);
    } catch {
      // older browsers / non-secure context — silently no-op
    }
  }

  // Sortable columns. Empty sortKey = use server order (already sorted by
  // abs(unrealized_pnl) DESC, which is the most informative default for
  // "biggest moves in this wallet's book"). Clicking a column header sets
  // the key + starts DESC (the more useful direction for first click on
  // signed columns); clicking again flips ASC↔DESC.
  type SortKey = '' | 'token' | 'side' | 'value' | 'roe' | 'upnl' | 'funding' | 'age';
  let sortKey = $state<SortKey>('');
  let sortDir = $state<1 | -1>(-1);

  function roeOf(p: Position): number {
    if (!p.avg_entry || !p.mark_price) return 0;
    const raw = (p.mark_price - p.avg_entry) / p.avg_entry;
    return p.side === 'short' ? -raw : raw;
  }
  function ageOf(p: Position): number {
    if (!p.opened_at) return 0;
    return Math.max(0, p.as_of - p.opened_at);
  }
  function keyValue(p: Position, k: SortKey): number | string {
    switch (k) {
      case 'token':   return p.token;
      case 'side':    return p.side;
      case 'value':   return p.amount;
      case 'roe':     return roeOf(p);
      case 'upnl':    return p.unrealized_pnl;
      case 'funding': return p.funding;
      case 'age':     return ageOf(p);
      default:        return 0;
    }
  }
  let sortedPositions = $derived.by(() => {
    const arr = selectedEntry?.positions ?? [];
    if (!sortKey) return arr;
    const dir = sortDir;
    return [...arr].sort((a, b) => {
      const av = keyValue(a, sortKey);
      const bv = keyValue(b, sortKey);
      if (typeof av === 'string' && typeof bv === 'string') {
        return av < bv ? -dir : av > bv ? dir : 0;
      }
      return ((av as number) - (bv as number)) * dir;
    });
  });
  function onSort(k: SortKey) {
    if (sortKey === k) {
      sortDir = (sortDir === 1 ? -1 : 1);
    } else {
      sortKey = k;
      sortDir = -1;
    }
  }
  function sortArrow(k: SortKey): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
</script>

<!-- use:stopDragEvents — see actions/stopDragEvents.ts. -->
<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- Top bar: wallet picker + copy + tags -->
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-500">Wallet:</span>
    <select
      value={effectiveSelected}
      onchange={(e) => onSelectWallet(e.currentTarget.value)}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 flex-1 min-w-0"
      disabled={wallets.length === 0}
    >
      {#each wallets as w (w.wallet)}
        <option value={w.wallet}>
          #{w.rank} · {truncate(w.wallet)} · {fmtUsd(w.score_unrealized_pnl)}
        </option>
      {/each}
      {#if wallets.length === 0}
        <option value="">— no data —</option>
      {/if}
    </select>
    <button
      type="button"
      onclick={copyAddr}
      onauxclick={onAuxClickArkham(effectiveSelected ?? '')}
      onmousedown={onMouseDownSuppressMiddle}
      title="Click to copy full address · middle-click to open in Arkham"
      disabled={!effectiveSelected}
      class="px-2 py-1 rounded-md border border-zinc-700 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
    >{copied ? '✓ copied' : 'Copy'}</button>
    {#if selectedEntry && selectedEntry.categories.length > 0}
      <div class="flex items-center gap-1">
        {#each selectedEntry.categories as cat (cat)}
          <span class="inline-block px-1.5 py-0.5 text-[10px] uppercase tracking-wider bg-zinc-900 border border-zinc-800 rounded text-zinc-400">{cat}</span>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Position breakdown for selected wallet -->
  <div class="flex-1 overflow-auto scrollbar-none">
    {#if !selectedEntry || selectedEntry.positions.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">
        {wallets.length === 0 ? 'No data — backfill or live tick not caught up yet' : 'No open positions for this wallet'}
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left  px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('token')}>Token{sortArrow('token')}</th>
            <th class="text-left  px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('side')}>Side{sortArrow('side')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('value')}>Value{sortArrow('value')}</th>
            <th class="text-right px-3 py-1.5 font-normal">Size</th>
            <th class="text-right px-3 py-1.5 font-normal">Entry</th>
            <th class="text-right px-3 py-1.5 font-normal">Mark</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('roe')}>ROE{sortArrow('roe')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('upnl')}>Unrealized PnL{sortArrow('upnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('funding')}>Funding{sortArrow('funding')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('age')}>Age{sortArrow('age')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedPositions as p (p.token + '|' + p.side)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-100">{p.token}</td>
              <td class="px-3 py-1">
                <span
                  class="font-mono uppercase text-[10px] tracking-wider px-1.5 py-0.5 rounded border"
                  class:text-emerald-300={p.side === 'long'}
                  class:border-emerald-900={p.side === 'long'}
                  class:text-rose-300={p.side === 'short'}
                  class:border-rose-900={p.side === 'short'}
                >{p.side}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-200">{fmtUsd(p.amount)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-500">{fmtSize(p.size, p.token)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtPrice(p.avg_entry)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-200">{fmtPrice(p.mark_price)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={(p.mark_price - p.avg_entry) * (p.side === 'short' ? -1 : 1) > 0}
                  class:text-rose-400={(p.mark_price - p.avg_entry) * (p.side === 'short' ? -1 : 1) < 0}
              >{fmtPct(p.avg_entry, p.mark_price, p.side)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={p.unrealized_pnl > 0}
                  class:text-rose-400={p.unrealized_pnl < 0}
              >{fmtUsd(p.unrealized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-zinc-400={p.funding === 0}
                  class:text-emerald-400={p.funding > 0}
                  class:text-rose-400={p.funding < 0}
              >{fmtUsd(p.funding)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtAge(p.opened_at, p.as_of)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
