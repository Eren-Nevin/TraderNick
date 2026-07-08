<script lang="ts">
  // Trading Pit: the wallets (in the selected group) that traded ONE token over the
  // lookback — opened from an Overview token click. Fills-only (net directional flow,
  // gross value, fill count); sortable; middle-click a wallet → live wallet page.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';

  type Row = { wallet: string; net_value: number; gross_value: number; fills: number; time?: number; categories?: string[] };

  let {
    open = false,
    token = '',
    lookback = '',
    groupName = '',
    rows = [],
    loading = false,
    error = null,
    onClose = () => {}
  }: {
    open?: boolean;
    token?: string;
    lookback?: string;
    groupName?: string;
    rows?: Row[];
    loading?: boolean;
    error?: string | null;
    onClose?: () => void;
  } = $props();

  let sortKey = $state<string>('gross_value');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  const arrow = (k: string) => (sortKey !== k ? '' : sortDir === 1 ? ' ↑' : ' ↓');
  let sorted = $derived.by(() => {
    const arr = [...(rows as Row[])];
    const k = sortKey, dir = sortDir;
    arr.sort((a, b) => {
      if (k === 'wallet') return String(a.wallet).localeCompare(String(b.wallet)) * dir;
      const av = (a[k as keyof Row] as number) ?? 0, bv = (b[k as keyof Row] as number) ?? 0;
      return (av - bv) * dir;
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
          <span class="text-zinc-300 font-medium">Wallets</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-200">{token}</span>{/if}
          {#if groupName}<span class="text-zinc-500">·</span><span class="text-zinc-400">{groupName}</span>{/if}
          {#if lookback}<span class="text-zinc-500">· last</span><span class="text-zinc-400">{lookback}</span>{/if}
          {#if loading}<span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin ml-1"></span>{/if}
          {#if rows.length}<span class="text-zinc-500 ml-1">· {rows.length} wallets</span>{/if}
        </div>
        <button type="button" onclick={onClose} class="w-7 h-7 rounded text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 text-base leading-none">×</button>
      </header>

      <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-60' : ''}">
        {#if error}
          <div class="h-40 flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
        {:else if rows.length === 0}
          <div class="h-40 flex items-center justify-center text-zinc-500">{loading ? 'Loading…' : 'No wallets traded this token in the window'}</div>
        {:else}
          <table class="w-full">
            <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
              <tr>
                <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('wallet')}>Wallet{arrow('wallet')}</th>
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('net_value')} title="Net directional flow: increased longs + decreased shorts − increased shorts − decreased longs">Net Pos Change{arrow('net_value')}</th>
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('gross_value')} title="Total $ traded across all fills">Total Value{arrow('gross_value')}</th>
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('fills')}>Fills{arrow('fills')}</th>
              </tr>
            </thead>
            <tbody>
              {#each sorted as r (r.wallet)}
                <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
                  <td class="px-3 py-1.5"><WalletAddress address={r.wallet} auxKind="wallet" tags={r.categories ?? []} /></td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {r.net_value > 0 ? 'text-emerald-400' : r.net_value < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtUsd(r.net_value)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-200">{fmtUsd(r.gross_value)}</td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-400">{r.fills}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    </div>
  </div>
{/if}
