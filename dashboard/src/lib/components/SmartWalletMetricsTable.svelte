<script lang="ts">
  // Experimental smart-wallet finder table. One bespoke tableview whose
  // "metric" selector swaps the right-most column AND the server-side ranking
  // (the top-N candidate set). Fixed core columns — Volume / OI / Realized /
  // Unrealized — plus the selected metric column. Header clicks re-sort the
  // *returned* set client-side (no refetch), same UX as WalletLeaderboardTable.
  //
  // Owns its own chrome: metric / lookback / token selectors + a 1-day-grain
  // snapshot slider. The min-days / min-volume noise guards live in the chart's
  // gear settings panel (owned by ChartInstance) since they're advanced knobs.

  import {
    SMART_WALLET_METRICS,
    SMART_WALLET_LOOKBACKS,
    smartWalletMetricDef,
    type SmartWalletMetric,
    type SmartWalletLookback
  } from '$lib/components/charts/config';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';
  import SnapshotSlider from '$lib/components/SnapshotSlider.svelte';

  export type SmartWalletRow = {
    wallet: string;
    volume: number;
    realized_pnl: number;
    unrealized_pnl: number;
    oi_token: number | null;
    oi_usd: number;
    avg_oi: number | null;
    metric: number;
    n_days: number;
    n_tokens: number;
    categories: string[];
  };

  let {
    rows = [],
    total = 0,
    tokens = [],
    metric,
    lookback,
    token,
    snapshot,
    onChangeMetric,
    onChangeLookback,
    onChangeToken,
    onChangeSnapshot,
    loading = false,
    error = null,
    notRun = false,
    lookbacks = SMART_WALLET_LOOKBACKS,
    dynamic = false,
    cutoff = false,
    cutoffLookbacks = [],
    cutoffOptions = [],
    onToggleCutoffLookback = () => {},
    cutoffCombine = 'union',
    onChangeCutoffCombine = () => {},
    rowLimit = 100,
    rowLimits = [100, 250, 500, 1000],
    onChangeRowLimit = () => {},
    groupMode = false,
    groups = [],
    selectedGroup = '',
    onChangeGroup = () => {}
  }: {
    rows: SmartWalletRow[];
    total?: number;
    tokens: string[];
    metric: SmartWalletMetric;
    lookback: SmartWalletLookback;
    token: string | null;
    snapshot: string; // resolved ISO date YYYY-MM-DD
    onChangeMetric: (m: SmartWalletMetric) => void;
    onChangeLookback: (l: SmartWalletLookback) => void;
    onChangeToken: (t: string | null) => void;
    onChangeSnapshot: (iso: string) => void;
    loading?: boolean;
    error?: string | null;
    /** True when the finder hasn't been run yet for the current widget (the
     *  finder is refresh-only). Shows a "click refresh" hint instead of the
     *  "no wallets pass" message. */
    notRun?: boolean;
    /** Which lookback options to offer (Fixed: 1/7/30/90/150; Dynamic: 1/3/7/14/30). */
    lookbacks?: ReadonlyArray<SmartWalletLookback>;
    /** Dynamic widget: the set ROLLS per day, so there's no snapshot slider —
     *  this table view shows the LATEST day's set. Swaps the slider for a note. */
    dynamic?: boolean;
    /** Cutoff widget: lookback is a MULTI-select unioned at the cutoff. */
    cutoff?: boolean;
    cutoffLookbacks?: number[];
    cutoffOptions?: ReadonlyArray<number>;
    onToggleCutoffLookback?: (l: number) => void;
    cutoffCombine?: 'union' | 'intersection';
    onChangeCutoffCombine?: (m: 'union' | 'intersection') => void;
    /** Table row count selector (all smart-wallet tables). */
    rowLimit?: number;
    rowLimits?: ReadonlyArray<number>;
    onChangeRowLimit?: (n: number) => void;
    /** Group widget: the wallet set is a pinned group (no criteria). */
    groupMode?: boolean;
    groups?: ReadonlyArray<{ id: string; name: string }>;
    selectedGroup?: string;
    onChangeGroup?: (id: string) => void;
  } = $props();

  const metricDef = $derived(smartWalletMetricDef(metric));
  const isGlobal = $derived(token === null || token === '');

  // OI display unit (token scope only — global oi_token is null). Pure display
  // toggle; both oi_usd and oi_token are already in each row.
  let oiUnit = $state<'usd' | 'token'>('usd');
  // Which OI value the column shows/sorts by, given scope + unit.
  const oiCol = $derived(!isGlobal && oiUnit === 'token' ? 'oi_token' : 'oi_usd');

  // The lookback window is ANCHORED to the chosen end date: [end − lookback,
  // end]. The slider sets the end (`snapshot`); we surface the resolved start
  // here so the window is explicit (e.g. end 06-01, 30d ⇒ starts 05-02).
  const lookbackStartIso = $derived.by(() => {
    const t = Date.parse(snapshot + 'T00:00:00Z');
    if (!isFinite(t)) return '';
    return new Date(t - lookback * 86_400_000).toISOString().slice(0, 10);
  });

  function fmtUsd(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(0);
  }
  function fmtToken(n: number | null): string {
    if (n === null || !isFinite(n) || n === 0) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(2) + 'K';
    if (abs >= 1) return sign + abs.toFixed(2);
    return sign + abs.toPrecision(3);
  }
  function fmtMetric(n: number): string {
    if (!isFinite(n)) return '—';
    if (metricDef.format === 'usd') return fmtUsd(n);
    return (n >= 0 ? '+' : '') + n.toFixed(2);
  }

  // ── Client-side resort. '' = server order (already sorted by the metric). ──
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
      const an = (a as unknown as Record<string, number>)[sortKey] ?? 0;
      const bn = (b as unknown as Record<string, number>)[sortKey] ?? 0;
      return (an - bn) * dir;
    });
  });

  // Snapshot picker lives in the shared <SnapshotSlider> so the table and the
  // dual-view chart toolbar use the exact same control (identical range/grain/
  // applied day). Snapshot only changes the filtered wallet SET.
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- Selectors row -->
  <div class="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-sm font-semibold uppercase tracking-wide px-2 py-1 rounded bg-zinc-900 border border-zinc-700 text-zinc-200"
      title={!loading && total > rows.length ? `${total.toLocaleString()} wallets found; showing top ${rows.length}` : undefined}
      >Smart Wallets{#if !loading} ({total.toLocaleString()}){/if}</span
    >
    {#if groupMode}
      <span class="text-zinc-500">Group:</span>
      <select
        value={selectedGroup}
        onchange={(e) => onChangeGroup(e.currentTarget.value)}
        class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
        title="Pinned wallet group whose members are the set"
      >
        {#each groups as g (g.id)}
          <option value={g.id}>{g.name}</option>
        {/each}
      </select>
    {/if}
    {#if !groupMode}
      <span class="text-zinc-500">Metric:</span>
      <select
        value={metric}
        onchange={(e) => onChangeMetric(e.currentTarget.value as SmartWalletMetric)}
        title={metricDef.desc}
        class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
      >
        {#each SMART_WALLET_METRICS as m (m.key)}
          <option value={m.key}>{m.label}</option>
        {/each}
      </select>
    {/if}

    {#if cutoff}
      <!-- Cutoff: union the passing sets over the SELECTED lookback windows. -->
      <span class="text-zinc-500 ml-1" title="A wallet joins the static set if it passes over ANY checked window">Lookbacks (union):</span>
      <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
        {#each cutoffOptions as l, i (l)}
          <button
            type="button"
            onclick={() => onToggleCutoffLookback(l)}
            class={'px-2 py-0.5 text-[11px] ' + (i > 0 ? 'border-l border-zinc-700 ' : '') + (cutoffLookbacks.includes(l)
              ? 'bg-emerald-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-500 hover:text-zinc-200')}
            title={`Include the ${l}-day window`}
          >{l}d</button>
        {/each}
      </div>
      <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden"
        title="Union = wallet passing ANY window; Intersection = passing EVERY window">
        <button type="button" onclick={() => onChangeCutoffCombine('union')}
          class={'px-2 py-0.5 text-[11px] ' + (cutoffCombine === 'union' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
        >Union</button>
        <button type="button" onclick={() => onChangeCutoffCombine('intersection')}
          class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + (cutoffCombine === 'intersection' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
        >Intersect</button>
      </div>
    {:else if !groupMode}
      <span class="text-zinc-500 ml-1">Lookback:</span>
      <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
        {#each lookbacks as l, i (l)}
          <button
            type="button"
            onclick={() => onChangeLookback(l)}
            class={'px-2 py-0.5 text-[11px] ' + (i > 0 ? 'border-l border-zinc-700 ' : '') + (lookback === l
              ? 'bg-zinc-800 text-zinc-100'
              : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
            title={dynamic ? `${l}-day rolling window` : `${l}-day window`}
          >{l}d</button>
        {/each}
      </div>
    {/if}

    <span class="text-zinc-500 ml-1">Show:</span>
    <select
      value={rowLimit}
      onchange={(e) => onChangeRowLimit(Number(e.currentTarget.value))}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
      title="Number of rows to fetch/show"
    >
      {#each rowLimits as n (n)}
        <option value={n}>{n}</option>
      {/each}
    </select>

    {#if !groupMode}
      <span class="text-zinc-500 ml-1">Token:</span>
      <select
        value={token ?? ''}
        onchange={(e) => onChangeToken(e.currentTarget.value === '' ? null : e.currentTarget.value)}
        class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
      >
        <option value="">All tokens</option>
        {#each tokens as t (t)}
          <option value={t}>{t}</option>
        {/each}
      </select>
    {/if}

    {#if !isGlobal}
      <!-- OI display unit — token amount vs USD. Pure frontend (both values are
           already loaded). Only in token scope; global OI is USD-only. -->
      <span class="text-zinc-500 ml-1">OI:</span>
      <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
        <button type="button" onclick={() => (oiUnit = 'usd')}
          class={'px-2 py-0.5 text-[11px] ' + (oiUnit === 'usd' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Show open interest in USD">$</button>
        <button type="button" onclick={() => (oiUnit = 'token')}
          class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + (oiUnit === 'token' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Show open interest in token amount">{token}</button>
      </div>
    {/if}

    <span class="text-[10px] text-zinc-600 ml-auto">
      {#if loading}loading…{:else}Click a column to re-sort{/if}
    </span>
  </div>

  {#if groupMode}
    <!-- Group: stats are as of the latest snapshot; lookback is the stats window. -->
    <div class="flex items-center px-3 py-1.5 border-b border-zinc-800 bg-zinc-900/30 text-[10px] text-zinc-500">
      Stats for the group's wallets over the last {lookback}d, as of {snapshot}.
    </div>
  {:else if dynamic}
    <!-- Dynamic: no snapshot slider (the set rolls per day). This table view
         shows the LATEST day's set; the chart view plots the full rolling series. -->
    <div class="flex items-center px-3 py-1.5 border-b border-zinc-800 bg-zinc-900/30 text-[10px] text-zinc-500">
      Latest day of the {lookback}d rolling window (snapshot {snapshot}). Switch to the chart view for the full per-day series.
    </div>
  {:else}
    <!-- Lookback end-date slider: sets the day the lookback window ENDS on; the
         window is [end − lookback, end]. The resolved start is shown as a hint. -->
    <div class="flex items-center px-3 py-1.5 border-b border-zinc-800 bg-zinc-900/30">
      <SnapshotSlider
        {snapshot}
        {onChangeSnapshot}
        label="End date"
        hint={lookbackStartIso ? `← ${lookbackStartIso} (${lookback}d window)` : ''}
      />
    </div>
  {/if}

  <div class="flex-1 overflow-auto scrollbar-none">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4">{error}</div>
    {:else if !loading && notRun && rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4">
        Set your metric, lookback, token and filters, then click&nbsp;<span class="text-amber-300">↻</span>&nbsp;to run the finder.
      </div>
    {:else if !loading && rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4">
        No wallets pass the filters for this window.<br />Lower the min-days / min-volume / min-realized / min-OI guards in settings.
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">#</th>
            <th class="text-left px-3 py-1.5 font-normal">Wallet</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('volume')} title="Window volume (USD)">Volume{sortArrow('volume')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('n_tokens')} title="Distinct tokens traded over the window">Tokens{sortArrow('n_tokens')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort(oiCol)}
                title={oiCol === 'oi_token' ? 'Open interest (token units) as of the snapshot' : 'Open interest (USD) as of the snapshot'}
            >OI{oiCol === 'oi_token' ? ` (${token})` : ' ($)'}{sortArrow(oiCol)}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('avg_oi')}
                title="Average open interest (USD) over the lookback window — time-averaged (idle hours count as 0). Global scope only; in token scope it shows only when an OI-share filter is active.">Avg OI{sortArrow('avg_oi')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('realized_pnl')} title="Realized PnL over the window (USD)">Realized{sortArrow('realized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('unrealized_pnl')} title="Unrealized PnL as of the snapshot (USD)">Unrealized{sortArrow('unrealized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                class:text-zinc-200={!sortKey || sortKey === 'metric'}
                onclick={() => onSort('metric')} title={metricDef.desc}>{metricDef.label}{sortArrow('metric')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as r, idx (r.wallet)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
              <td class="px-3 py-1">
                <WalletAddress address={r.wallet} auxKind="wallet" {snapshot} />
                {#if r.categories && r.categories.length > 0}
                  <span class="ml-1 inline-block text-[9px] uppercase tracking-wide px-1 py-0 rounded bg-zinc-900 border border-zinc-700 text-zinc-400"
                        title={r.categories.join(', ')}>{r.categories[0]}</span>
                {/if}
                <span class="ml-1 text-[9px] text-zinc-600">{r.n_days}d</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(r.volume)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{r.n_tokens}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">
                {#if isGlobal}
                  {fmtUsd(r.oi_usd)}
                {:else if oiCol === 'oi_token'}
                  <div>{fmtToken(r.oi_token)}</div><div class="text-[10px] text-zinc-500">{fmtUsd(r.oi_usd)}</div>
                {:else}
                  <div>{fmtUsd(r.oi_usd)}</div><div class="text-[10px] text-zinc-500">{fmtToken(r.oi_token)}</div>
                {/if}
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">
                {r.avg_oi == null ? '—' : fmtUsd(r.avg_oi)}
              </td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={r.realized_pnl > 0}
                  class:text-rose-400={r.realized_pnl < 0}
                  class:text-zinc-500={r.realized_pnl === 0}>{fmtUsd(r.realized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={r.unrealized_pnl > 0}
                  class:text-rose-400={r.unrealized_pnl < 0}
                  class:text-zinc-500={r.unrealized_pnl === 0}>{fmtUsd(r.unrealized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono font-semibold"
                  class:text-emerald-400={r.metric > 0}
                  class:text-rose-400={r.metric < 0}
                  class:text-zinc-500={r.metric === 0}>{fmtMetric(r.metric)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
