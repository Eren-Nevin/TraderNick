<script lang="ts">
  // Group Snapshot: the wallets in the group holding ONE token — opened from a table
  // token click. Uses the SAME per-wallet data the table aggregates (group_snapshot?token=),
  // so counts match. Middle-click a wallet → wallet page (token pre-selected).
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';

  type Row = { wallet: string; side: string; size_usd: number; entry: number; unrealized_pnl: number; categories?: string[] };

  let {
    open = false,
    token = '',
    asOf = 'snapshot',
    groupName = '',
    rows = [],
    loading = false,
    error = null,
    onClose = () => {}
  }: {
    open?: boolean;
    token?: string;
    asOf?: string;
    groupName?: string;
    rows?: Row[];
    loading?: boolean;
    error?: string | null;
    onClose?: () => void;
  } = $props();

  let sortKey = $state<string>('size_usd');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  const arrow = (k: string) => (sortKey !== k ? '' : sortDir === 1 ? ' ↑' : ' ↓');
  const nLong = $derived(rows.filter((r) => r.side === 'long').length);
  const nShort = $derived(rows.filter((r) => r.side === 'short').length);
  let sorted = $derived.by(() => {
    const arr = [...(rows as Row[])];
    const k = sortKey, dir = sortDir;
    arr.sort((a, b) => {
      if (k === 'wallet' || k === 'side') return String(a[k]).localeCompare(String(b[k])) * dir;
      return (((a[k as keyof Row] as number) ?? 0) - ((b[k as keyof Row] as number) ?? 0)) * dir;
    });
    return arr;
  });

  function fmtUsd(n: number): string {
    const a = Math.abs(n), s = n < 0 ? '-' : '';
    if (a >= 1e9) return s + '$' + (a / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return s + '$' + (a / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return s + '$' + (a / 1e3).toFixed(1) + 'K';
    return s + '$' + a.toFixed(0);
  }
  const fmtPrice = (n: number) => (n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toPrecision(5));
</script>

{#if open}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm cursor-default"
    role="dialog" aria-modal="true" tabindex="-1"
    onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
    use:stopDragEvents
  >
    <div class="w-[64rem] max-w-[96vw] max-h-[88vh] bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl flex flex-col text-sm">
      <header class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
        <div class="flex items-center gap-2">
          <span class="text-zinc-300 font-medium">Holders</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-200">{token}</span>{/if}
          {#if groupName}<span class="text-zinc-500">·</span><span class="text-zinc-400">{groupName}</span>{/if}
          <span class="text-zinc-500">·</span><span class="text-zinc-400">{asOf === 'live' ? 'live' : 'snapshot'}</span>
          {#if loading}<span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin ml-1"></span>{/if}
          {#if rows.length}<span class="text-zinc-500 ml-1">· {rows.length} (<span class="text-emerald-400">{nLong}L</span>/<span class="text-rose-400">{nShort}S</span>)</span>{/if}
        </div>
        <button type="button" onclick={onClose} class="w-7 h-7 rounded text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 text-base leading-none">×</button>
      </header>

      <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-60' : ''}">
        {#if error}
          <div class="h-40 flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
        {:else if rows.length === 0}
          <div class="h-40 flex items-center justify-center text-zinc-500">{loading ? 'Loading…' : 'No wallets holding this token'}</div>
        {:else}
          <table class="w-full">
            <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
              <tr>
                <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('wallet')}>Wallet{arrow('wallet')}</th>
                <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('side')}>Side{arrow('side')}</th>
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('size_usd')}>Size{arrow('size_usd')}</th>
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('entry')}>Entry{arrow('entry')}</th>
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('unrealized_pnl')}>uPnL{arrow('unrealized_pnl')}</th>
              </tr>
            </thead>
            <tbody>
              {#each sorted as r (r.wallet)}
                <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
                  <td class="px-3 py-1.5"><WalletAddress address={r.wallet} auxKind="wallet" token={token} tags={r.categories ?? []} /></td>
                  <td class="px-3 py-1.5">
                    <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {r.side === 'long' ? 'border-emerald-800 text-emerald-400' : 'border-rose-800 text-rose-400'}">{r.side}</span>
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-200">{fmtUsd(r.size_usd)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-400">{fmtPrice(r.entry)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {r.unrealized_pnl > 0 ? 'text-emerald-400' : r.unrealized_pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtUsd(r.unrealized_pnl)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    </div>
  </div>
{/if}
