<script lang="ts">
  // One group's live-stream table — header + sortable columns + status pills
  // + per-row action buttons. Extracted from the admin monolith so it can be
  // reused on the Overview page (in a loop over all groups) and on the
  // per-provider page (one instance).
  //
  // Sort + collapse state are scoped to this instance — different groups
  // sort independently, and the state lives with the table itself.

  import { lifecycle, type StreamRow, type StreamAction } from '$lib/admin/types';
  import { ageMs, fmtAge, fmtCadence, fmtDurations, fmtTime } from '$lib/admin/fmt';

  type Props = {
    groupName: string;
    rows: StreamRow[];
    streamAction: (name: string, action: StreamAction) => Promise<void>;
    // Hide the collapsible header (used by the per-provider page where the
    // page title already names the group and collapsing would be useless).
    hideHeader?: boolean;
  };
  let { groupName, rows, streamAction, hideHeader = false }: Props = $props();

  type StreamSortKey =
    | 'name' | 'pid' | 'running' | 'enabled' | 'cadence_s'
    | 'last_tick_at' | 'last_success_at' | 'last_rows' | 'tick_count'
    | 'crash_count' | 'duration' | 'last_error';
  type GroupSort = { key: StreamSortKey; dir: 'asc' | 'desc' };
  let sort = $state<GroupSort>({ key: 'name', dir: 'asc' });
  let collapsed = $state(false);

  function toggleSort(k: StreamSortKey) {
    sort = sort.key === k
      ? { key: k, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
      : { key: k, dir: 'asc' };
  }

  function streamValue(r: StreamRow, k: StreamSortKey): string | number {
    switch (k) {
      case 'name': return r.name;
      case 'pid': return r.pid ?? -1;
      case 'running': return r.running ? 1 : 0;
      case 'enabled': return r.enabled ? 1 : 0;
      case 'cadence_s': return r.cadence_s ?? -1;
      case 'last_tick_at': return r.status?.last_tick_at ?? '';
      case 'last_success_at': return r.status?.last_success_at ?? '';
      case 'last_rows': return r.status?.last_rows ?? -1;
      case 'tick_count': return r.status?.tick_count ?? -1;
      case 'crash_count': return r.status?.crash_count ?? r.crash_count ?? 0;
      case 'duration': return r.status?.last_live_duration_s ?? -1;
      case 'last_error': return r.status?.last_error ?? '';
    }
  }

  let sortedRows = $derived.by(() => {
    const list = [...rows];
    list.sort((a, b) => {
      const va = streamValue(a, sort.key);
      const vb = streamValue(b, sort.key);
      const cmp = va === vb ? 0 : va < vb ? -1 : 1;
      return sort.dir === 'asc' ? cmp : -cmp;
    });
    return list;
  });

  let nRunning = $derived(rows.filter((s) => lifecycle(s) === 'RUNNING').length);
  let nOn = $derived(rows.filter((s) => lifecycle(s) === 'ON').length);
  let nStarting = $derived(rows.filter((s) => lifecycle(s) === 'STARTING').length);
  let nOff = $derived(rows.filter((s) => lifecycle(s) === 'OFF').length);
  let aggCrashes = $derived(rows.reduce(
    (acc, s) => acc + (s.status?.crash_count ?? s.crash_count ?? 0), 0,
  ));
  let errorRows = $derived(rows.filter((s) => s.status?.last_error));

  const COLS: [StreamSortKey, string][] = [
    ['name', 'Name'],
    ['running', 'Status'],
    ['pid', 'PID'],
    ['cadence_s', 'Refresh'],
    ['last_tick_at', 'Last tick'],
    ['last_success_at', 'Last Ran'],
    ['duration', 'Dur (live/sweep s)'],
    ['last_rows', 'Rows/tick'],
    ['tick_count', 'Ticks'],
    ['crash_count', 'Crashes'],
    ['last_error', 'Last error'],
  ];
</script>

<div class="space-y-1">
  {#if !hideHeader}
    <div class="flex items-baseline gap-2 px-1">
      <button
        type="button"
        class="text-zinc-500 hover:text-zinc-200 select-none w-3 text-xs font-mono"
        onclick={() => (collapsed = !collapsed)}
        title={collapsed ? 'Expand' : 'Collapse'}
      >{collapsed ? '▶' : '▼'}</button>
      <h3
        class="text-xs font-semibold text-zinc-200 cursor-pointer select-none"
        onclick={() => (collapsed = !collapsed)}
      >{groupName}</h3>
      <span class="text-[10px] text-zinc-500">
        {rows.length} streams ·
        <span class="text-green-300">{nRunning} running</span> ·
        <span class="text-green-500">{nOn} on</span>
        {#if nStarting > 0}
          · <span class="text-amber-300">{nStarting} starting</span>
        {/if}
        {#if nOff > 0}
          · <span class="text-zinc-500">{nOff} off</span>
        {/if}
        {#if collapsed && aggCrashes > 0}
          · <span class="text-amber-400">{aggCrashes} crashes</span>
        {/if}
        {#if collapsed && errorRows.length > 0}
          · <span class="text-red-400">{errorRows.length} with error{errorRows.length === 1 ? '' : 's'}</span>
        {/if}
      </span>
    </div>
    {#if collapsed && errorRows.length > 0}
      <div class="text-[10px] text-red-300 bg-red-950/20 border border-red-900/40 rounded px-2 py-1 mx-1">
        {#each errorRows.slice(0, 3) as r}
          <div class="truncate" title={r.status?.last_error ?? ''}>
            <span class="font-mono text-zinc-400">{r.name}</span>: {r.status?.last_error}
          </div>
        {/each}
        {#if errorRows.length > 3}
          <div class="text-zinc-500">+{errorRows.length - 3} more</div>
        {/if}
      </div>
    {/if}
  {/if}

  {#if hideHeader || !collapsed}
    <div class="overflow-auto border border-zinc-800 rounded-md">
      <table class="text-xs w-full">
        <thead class="bg-zinc-900 text-zinc-400">
          <tr>
            {#each COLS as [k, label]}
              <th
                class="px-2 py-1.5 text-left whitespace-nowrap select-none cursor-pointer hover:text-zinc-100"
                onclick={() => toggleSort(k)}
              >
                {label}
                {#if sort.key === k}<span class="text-zinc-500">{sort.dir === 'asc' ? '▲' : '▼'}</span>{/if}
              </th>
            {/each}
            <th class="px-2 py-1.5 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as r (r.name)}
            {@const lc = lifecycle(r)}
            <tr class="border-t border-zinc-800 hover:bg-zinc-900/40">
              <td class="px-2 py-1 font-mono">{r.name}</td>
              <td class="px-2 py-1">
                {#if lc === 'RUNNING'}
                  <span class="inline-flex items-center gap-1.5 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-green-500/25 text-green-200 border border-green-400">
                    <span class="size-1.5 rounded-full bg-green-300 shadow-[0_0_8px_currentColor] animate-pulse"></span>
                    RUNNING
                  </span>
                {:else if lc === 'ON'}
                  <span class="inline-flex items-center gap-1.5 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-green-900/60 text-green-300 border border-green-700">
                    <span class="size-1.5 rounded-full bg-green-500 shadow-[0_0_6px_currentColor]"></span>
                    ON
                  </span>
                {:else if lc === 'STARTING'}
                  <span class="inline-flex items-center gap-1.5 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/40">
                    <span class="size-1.5 rounded-full bg-amber-400 shadow-[0_0_6px_currentColor] animate-pulse"></span>
                    STARTING
                  </span>
                {:else}
                  <span class="inline-flex items-center gap-1.5 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-zinc-950 text-zinc-500 border border-zinc-800">
                    <span class="size-1.5 rounded-full bg-zinc-700"></span>
                    OFF
                  </span>
                {/if}
              </td>
              <td class="px-2 py-1 font-mono text-zinc-400">{r.pid ?? '—'}</td>
              <td class="px-2 py-1 text-zinc-400 tabular-nums">{fmtCadence(r.cadence_s)}</td>
              <td
                class="px-2 py-1 whitespace-nowrap"
                class:text-zinc-500={!r.status?.last_tick_at}
                class:text-amber-400={r.status?.last_tick_at && (ageMs(r.status.last_tick_at) ?? 0) > Math.max((r.cadence_s ?? 60) * 1000 * 5, 600_000)}
                title={r.status?.last_tick_at ?? ''}
              >{fmtAge(ageMs(r.status?.last_tick_at))}</td>
              <td
                class="px-2 py-1 whitespace-nowrap tabular-nums font-mono"
                class:text-zinc-500={!r.status?.last_success_at}
                title={r.status?.last_success_at ?? ''}
              >{fmtTime(r.status?.last_success_at)}</td>
              <td
                class="px-2 py-1 whitespace-nowrap tabular-nums font-mono text-zinc-300"
                title="live / sweep seconds"
              >{fmtDurations(r.status?.last_live_duration_s, r.status?.last_sweep_duration_s)}</td>
              <td class="px-2 py-1 text-right tabular-nums">{r.status?.last_rows ?? '—'}</td>
              <td class="px-2 py-1 text-right tabular-nums text-zinc-400">{r.status?.tick_count ?? '—'}</td>
              <td class="px-2 py-1 text-right tabular-nums"
                class:text-amber-400={(r.status?.crash_count ?? r.crash_count) > 0}
              >{r.status?.crash_count ?? r.crash_count}</td>
              <td class="px-2 py-1 text-red-300 max-w-md truncate" title={r.status?.last_error ?? ''}>
                {r.status?.last_error ?? ''}
              </td>
              <td class="px-2 py-1 text-right whitespace-nowrap">
                {#if r.running}
                  <button
                    class="text-xs px-2 py-0.5 bg-zinc-900 border border-zinc-700 rounded hover:border-amber-500 hover:text-amber-300"
                    onclick={() => streamAction(r.name, 'stop')}
                  >stop</button>
                  <button
                    class="text-xs px-2 py-0.5 bg-zinc-900 border border-zinc-700 rounded hover:border-blue-500 hover:text-blue-300 ml-1"
                    onclick={() => streamAction(r.name, 'restart')}
                  >restart</button>
                {:else}
                  <button
                    class="text-xs px-2 py-0.5 bg-zinc-900 border border-zinc-700 rounded hover:border-emerald-500 hover:text-emerald-300"
                    onclick={() => streamAction(r.name, 'start')}
                  >start</button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
