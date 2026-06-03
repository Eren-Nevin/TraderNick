<script lang="ts">
  // Top LPs into HL vaults, ranked by net deposited (deposits - withdrawals).
  // Categories surface wallet labels (Whale / Trader / etc.) if known.

  type Lp = {
    rank: number;
    wallet: string;
    deposits: number;
    withdrawals: number;
    net: number;
    vaults_used: number;
    event_count: number;
    categories: string[];
  };

  let { lps = [] }: { lps: Lp[] } = $props();

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
    return sign + '$' + abs.toFixed(0);
  }
  function fmtNum(n: number): string {
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { onAuxClickArkham, onMouseDownSuppressMiddle } from '$lib/arkham';

  let copiedAddr = $state('');
  async function copyAddr(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
      copiedAddr = addr;
      setTimeout(() => { if (copiedAddr === addr) copiedAddr = ''; }, 1200);
    } catch { /* no-op */ }
  }
</script>

<!-- use:stopDragEvents — see actions/stopDragEvents.ts. -->
<div class="h-full overflow-auto scrollbar-none text-xs" use:stopDragEvents>
  {#if lps.length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">No vault LPs in this window</div>
  {:else}
    <table class="w-full">
      <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
        <tr>
          <th class="text-left  px-3 py-1.5 font-normal">#</th>
          <th class="text-left  px-3 py-1.5 font-normal">LP Wallet</th>
          <th class="text-right px-3 py-1.5 font-normal">Deposits</th>
          <th class="text-right px-3 py-1.5 font-normal">Withdrawals</th>
          <th class="text-right px-3 py-1.5 font-normal">Net</th>
          <th class="text-right px-3 py-1.5 font-normal">Vaults</th>
          <th class="text-right px-3 py-1.5 font-normal">Events</th>
          <th class="text-left  px-3 py-1.5 font-normal">Tags</th>
        </tr>
      </thead>
      <tbody>
        {#each lps as l (l.wallet)}
          <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
            <td class="px-3 py-1 text-zinc-500">{l.rank}</td>
            <td class="px-3 py-1">
              <button type="button" onclick={() => copyAddr(l.wallet)}
                      onauxclick={onAuxClickArkham(l.wallet)}
                      onmousedown={onMouseDownSuppressMiddle}
                      title={l.wallet + ' — click to copy · middle-click to open in Arkham'}
                      class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer">
                {copiedAddr === l.wallet ? '✓ copied' : truncate(l.wallet)}
              </button>
            </td>
            <td class="px-3 py-1 text-right font-mono text-emerald-400">{fmtUsd(l.deposits)}</td>
            <td class="px-3 py-1 text-right font-mono text-rose-400">{fmtUsd(l.withdrawals)}</td>
            <td class="px-3 py-1 text-right font-mono"
                class:text-emerald-400={l.net > 0}
                class:text-rose-400={l.net < 0}
                class:text-zinc-400={l.net === 0}
            >{fmtUsd(l.net)}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtNum(l.vaults_used)}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-500">{fmtNum(l.event_count)}</td>
            <td class="px-3 py-1">
              {#if l.categories.length > 0}
                {#each l.categories as cat (cat)}
                  <span class="inline-block px-1.5 py-0.5 mr-1 text-[10px] uppercase tracking-wider bg-zinc-900 border border-zinc-800 rounded text-zinc-400">{cat}</span>
                {/each}
              {:else}
                <span class="text-zinc-700">—</span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
