<script lang="ts">
  // Bridge transfers (deposits / withdrawals) for the HL wallet detail page.
  // Snapshot-independent — it lists the wallet's full transfer history (newest
  // first), with a deposits / withdrawals / net summary in the header.

  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { fmtTzDateTime } from '$lib/stores/timezone.svelte';

  export type TransferRow = { time: number; direction: string; amount: number };

  let {
    transfers = [],
    loading = false,
    error = null
  }: {
    transfers?: TransferRow[];
    loading?: boolean;
    error?: string | null;
  } = $props();

  const deposits = $derived(
    transfers.filter((t) => t.direction === 'deposit').reduce((s, t) => s + t.amount, 0)
  );
  const withdrawals = $derived(
    transfers.filter((t) => t.direction === 'withdrawal').reduce((s, t) => s + t.amount, 0)
  );
  const net = $derived(deposits - withdrawals);

  function fmtUsd(n: number): string {
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtWhen(unix: number): string {
    return fmtTzDateTime(unix);
  }
</script>

<div class="h-full flex flex-col text-sm border border-zinc-800 rounded-lg overflow-hidden" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-200 font-medium">Transfers</span>
    {#if loading}
      <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading…"></span>
    {/if}
    {#if transfers.length}
      <span class="text-zinc-500 ml-auto text-xs">
        <span class="text-emerald-400">{fmtUsd(deposits)}</span> in ·
        <span class="text-rose-400">{fmtUsd(withdrawals)}</span> out ·
        net <span class={net >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{fmtUsd(net)}</span>
      </span>
    {/if}
  </div>

  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-50' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4 py-8">{error}</div>
    {:else if transfers.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">
        No transfers for this wallet.
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">Date (UTC)</th>
            <th class="text-left px-3 py-1.5 font-normal">Type</th>
            <th class="text-right px-3 py-1.5 font-normal">Amount</th>
          </tr>
        </thead>
        <tbody>
          {#each transfers as t (t.time + t.direction + t.amount)}
            {@const dep = t.direction === 'deposit'}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-300 tabular-nums">{fmtWhen(t.time)}</td>
              <td class="px-3 py-1">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {dep
                  ? 'border-emerald-800 text-emerald-400'
                  : 'border-rose-800 text-rose-400'}">{dep ? 'Deposit' : 'Withdrawal'}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-[15px] tabular-nums {dep ? 'text-emerald-400' : 'text-rose-400'}">
                {dep ? '+' : '-'}{fmtUsd(t.amount)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
