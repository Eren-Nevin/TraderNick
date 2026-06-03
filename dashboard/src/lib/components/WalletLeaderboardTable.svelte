<script lang="ts">
  // Generic top-wallets leaderboard table. Driven entirely by the
  // `columns` prop (a LeaderboardColumn[]); reused across AAVE V2/V3/V4
  // today and any future Morpho/Spark/… leaderboards. The toolbar carries
  // the *server* sort selector (changing it refetches a different top-N
  // set) and the Top N input. Header clicks resort the *returned* rows
  // client-side without re-fetching — same UX as HlTopVaultsTable.

  import type { LeaderboardColumn, LeaderboardMetric } from '$lib/components/charts/config';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { onAuxClickArkham, onMouseDownSuppressMiddle } from '$lib/arkham';

  export type LeaderboardRow = {
    rank: number;
    wallet: string;
    labels: string;
    [k: string]: number | string;
  };

  let {
    rows = [],
    columns,
    orderBy,
    topN,
    onChangeOrderBy,
    onChangeTopN,
    loading = false,
    error = null,
    protocolLabel = ''
  }: {
    rows: LeaderboardRow[];
    columns: ReadonlyArray<LeaderboardColumn>;
    orderBy: LeaderboardMetric;
    topN: number;
    onChangeOrderBy: (m: LeaderboardMetric) => void;
    onChangeTopN: (n: number) => void;
    loading?: boolean;
    error?: string | null;
    protocolLabel?: string;
  } = $props();

  function truncate(addr: string): string {
    if (!addr) return '';
    if (addr.length < 14) return addr;
    return addr.slice(0, 6) + '…' + addr.slice(-4);
  }
  function fmtUsd(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(0);
  }
  function fmtCount(n: number): string {
    if (!isFinite(n) || n === 0) return '';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }

  let copiedAddr = $state('');
  async function copyAddr(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
      copiedAddr = addr;
      setTimeout(() => { if (copiedAddr === addr) copiedAddr = ''; }, 1200);
    } catch { /* no-op */ }
  }

  // Client-side resort of the returned set. '' = use server order (which
  // already reflects the toolbar `orderBy` selection).
  let sortKey = $state<string>('');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = (sortDir === 1 ? -1 : 1);
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  let sortedRows = $derived.by(() => {
    if (!sortKey) return rows;
    const dir = sortDir;
    return [...rows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const an = typeof av === 'number' ? av : 0;
      const bn = typeof bv === 'number' ? bv : 0;
      return (an - bn) * dir;
    });
  });

  // Top N input — debounce/commit on blur or Enter, not on every keystroke
  // (avoids one refetch per digit).
  let topNDraft = $state(String(topN));
  $effect(() => { topNDraft = String(topN); });
  function commitTopN() {
    const n = Math.max(1, Math.min(200, parseInt(topNDraft, 10) || 10));
    topNDraft = String(n);
    if (n !== topN) onChangeTopN(n);
  }
</script>

<!-- use:stopDragEvents — see actions/stopDragEvents.ts (same reason as
     HlTopVaultsTable: Svelte 5 delegated onmousedown clashes with the
     dnd-action listener; the action bypasses delegation). -->
<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <div class="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    {#if protocolLabel}
      <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-700 text-zinc-400">{protocolLabel}</span>
    {/if}
    <span class="text-zinc-500">Top by:</span>
    <select
      value={orderBy}
      onchange={(e) => onChangeOrderBy(e.currentTarget.value as LeaderboardMetric)}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
    >
      {#each columns as c (c.key)}
        <option value={c.key}>{c.label}</option>
      {/each}
    </select>
    <span class="text-zinc-500 ml-2">Top N:</span>
    <input
      type="number"
      min="1"
      max="200"
      bind:value={topNDraft}
      onblur={commitTopN}
      onkeydown={(e) => { if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur(); }}
      class="w-16 bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
    />
    <span class="text-[10px] text-zinc-600 ml-auto">
      {#if loading}loading…{:else}Click a column to re-sort the returned set{/if}
    </span>
  </div>
  <div class="flex-1 overflow-auto scrollbar-none">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4">{error}</div>
    {:else if !loading && rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No activity in this window</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left  px-3 py-1.5 font-normal">#</th>
            <th class="text-left  px-3 py-1.5 font-normal">Wallet</th>
            {#each columns as c (c.key)}
              <th
                class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                class:text-zinc-200={orderBy === c.key}
                onclick={() => onSort(c.usdField)}
                title={`Click to re-sort returned rows by ${c.label}`}
              >{c.label}{sortArrow(c.usdField)}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as r, idx (r.wallet)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 text-zinc-500">{sortKey ? idx + 1 : r.rank}</td>
              <td class="px-3 py-1">
                <button type="button" onclick={() => copyAddr(r.wallet)}
                        onauxclick={onAuxClickArkham(r.wallet)}
                        onmousedown={onMouseDownSuppressMiddle}
                        title={r.wallet + ' — click to copy · middle-click to open in Arkham'}
                        class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer">
                  {copiedAddr === r.wallet ? '✓ copied' : truncate(r.wallet)}
                </button>
                {#if r.labels}
                  <span class="ml-1 inline-block text-[9px] uppercase tracking-wide px-1 py-0 rounded bg-zinc-900 border border-zinc-700 text-zinc-400"
                        title={String(r.labels)}>{String(r.labels).split(',')[0]}</span>
                {/if}
              </td>
              {#each columns as c (c.key)}
                {@const val = (r[c.usdField] as number) ?? 0}
                {@const cnt = c.countField ? ((r[c.countField] as number) ?? 0) : 0}
                <td class="px-3 py-1 text-right font-mono"
                    class:text-emerald-400={val > 0 && (c.key === 'deposit' || c.key === 'repay' || c.key === 'swap' || c.key === 'collect' || (c.key === 'net_deposit' && val > 0) || (c.key === 'net_borrow' && val < 0) || (c.key === 'net_lp' && val > 0))}
                    class:text-rose-400={val > 0 && (c.key === 'withdraw' || c.key === 'borrow' || c.key === 'liquidation' || (c.key === 'net_deposit' && val < 0) || (c.key === 'net_borrow' && val > 0) || (c.key === 'net_lp' && val < 0))}
                    class:text-zinc-500={val === 0}
                >
                  <div>{fmtUsd(val)}</div>
                  {#if c.countField && cnt > 0}
                    <div class="text-[10px] font-sans text-zinc-500">
                      {fmtCount(cnt)}× <span class="text-zinc-300">avg {fmtUsd(val / cnt)}</span>
                    </div>
                  {/if}
                </td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
