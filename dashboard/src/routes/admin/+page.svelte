<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { BACKFILL_FORMS, type BackfillFormSpec, type FieldSpec } from '$lib/admin/backfill_forms';

  type StreamRow = {
    name: string;
    group: string;
    cadence_s: number | null;
    kind: 'stream' | 'group';
    module: string;
    pid: number | null;
    running: boolean;
    started_at: number | null;
    crash_count: number;
    last_exit_code: number | null;
    requested_stop: boolean;
    enabled: boolean;
    status: {
      last_tick_at?: string;
      last_rows?: number;
      total_rows_since_start?: number;
      tick_count?: number;
      crash_count?: number;
      last_error?: string | null;
      last_error_at?: string | null;
      last_success_at?: string | null;
      last_live_duration_s?: number | null;
      last_sweep_duration_s?: number | null;
      tick_in_progress?: boolean;
      tick_started_at?: string | null;
    };
  };

  function fmtCadence(seconds: number | null | undefined): string {
    if (seconds == null) return '—';
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${seconds / 60}m`;
    return `${seconds / 3600}h`;
  }

  // Four-state lifecycle:
  //   OFF       — user disabled it (or never enabled). enabled=false.
  //   STARTING  — enabled, but no live subprocess at this instant
  //               (startup jitter, crash backoff, or in-flight restart).
  //   ON        — subprocess alive, sleeping between ticks.
  //   RUNNING   — subprocess actively inside a fetch tick (tick_in_progress=1).
  type Lifecycle = 'OFF' | 'STARTING' | 'ON' | 'RUNNING';
  function lifecycle(r: StreamRow): Lifecycle {
    if (!r.enabled || r.requested_stop) return 'OFF';
    if (!r.running) return 'STARTING';
    return r.status?.tick_in_progress ? 'RUNNING' : 'ON';
  }

  type JobRow = {
    job_id: string;
    job_type: string;
    args: Record<string, unknown>;
    status: string;
    progress: number;
    started_at: string;
    finished_at: string | null;
    error: string | null;
    updated_at: string;
    subprocess_alive?: boolean;
  };

  let streams = $state<StreamRow[]>([]);
  let jobs = $state<JobRow[]>([]);
  let streamsErr = $state<string | null>(null);
  let jobsErr = $state<string | null>(null);
  let lastRefresh = $state<number | null>(null);

  // Refresh both panels in parallel.
  async function refresh() {
    try {
      const res = await fetch('/api/admin/streams');
      if (!res.ok) throw new Error(`streams ${res.status}`);
      const body = await res.json();
      streams = body.streams ?? [];
      streamsErr = null;
    } catch (e) {
      streamsErr = String(e);
    }
    try {
      const res = await fetch('/api/admin/jobs?limit=100');
      if (!res.ok) throw new Error(`jobs ${res.status}`);
      const body = await res.json();
      jobs = (Array.isArray(body) ? body : body.jobs) ?? [];
      jobsErr = null;
    } catch (e) {
      jobsErr = String(e);
    }
    lastRefresh = Date.now();
  }

  let refreshTimer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    refresh();
    refreshTimer = setInterval(refresh, 1000);
  });
  onDestroy(() => {
    if (refreshTimer) clearInterval(refreshTimer);
  });

  // Stream actions.
  let actionMsg = $state<string | null>(null);
  async function streamAction(name: string, action: 'start' | 'stop' | 'restart') {
    try {
      const res = await fetch(`/api/admin/streams/${encodeURIComponent(name)}/${action}`, {
        method: 'POST'
      });
      if (!res.ok) throw new Error(`${action} ${name} → ${res.status} ${await res.text()}`);
      actionMsg = `${action} ${name}: OK`;
      await refresh();
    } catch (e) {
      actionMsg = String(e);
    }
  }

  async function cancelJob(id: string) {
    try {
      const res = await fetch(`/api/admin/jobs/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`cancel ${id} → ${res.status}`);
      actionMsg = `cancel ${id.slice(0, 8)}: OK`;
      await refresh();
    } catch (e) {
      actionMsg = String(e);
    }
  }

  // Sorting for streams table — PER GROUP. Each protocol's table has its
  // own (key, dir) so sorting one doesn't reflow the others.
  type StreamSortKey =
    | 'name' | 'pid' | 'running' | 'enabled' | 'cadence_s'
    | 'last_tick_at' | 'last_success_at' | 'last_rows' | 'tick_count' | 'crash_count'
    | 'duration' | 'last_error';
  type GroupSort = { key: StreamSortKey; dir: 'asc' | 'desc' };
  let groupSorts = $state<Record<string, GroupSort>>({});
  function getGroupSort(group: string): GroupSort {
    return groupSorts[group] ?? { key: 'name', dir: 'asc' };
  }
  function toggleGroupSort(group: string, k: StreamSortKey) {
    const cur = getGroupSort(group);
    const dir: 'asc' | 'desc' =
      cur.key === k ? (cur.dir === 'asc' ? 'desc' : 'asc') : 'asc';
    groupSorts = { ...groupSorts, [group]: { key: k, dir } };
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
  function sortRows(rows: StreamRow[], gs: GroupSort): StreamRow[] {
    const list = [...rows];
    list.sort((a, b) => {
      const va = streamValue(a, gs.key);
      const vb = streamValue(b, gs.key);
      const cmp = va === vb ? 0 : va < vb ? -1 : 1;
      return gs.dir === 'asc' ? cmp : -cmp;
    });
    return list;
  }

  // Collapsed-group toggling. When collapsed, header shows aggregate
  // ON/RUNNING/STARTING counts + crash total + first error.
  let collapsedGroups = $state<Record<string, boolean>>({});
  function toggleCollapsed(group: string) {
    collapsedGroups = { ...collapsedGroups, [group]: !collapsedGroups[group] };
  }

  // Streams grouped by `group` field, with a stable preferred ordering so
  // the most-watched protocols appear up top. Anything not in the explicit
  // list gets appended alphabetically.
  const GROUP_ORDER = [
    'Hyperliquid', 'Binance', 'Transfers',
    'AAVE V3', 'AAVE V2', 'AAVE V4',
    'Uniswap V3', 'Uniswap V2', 'Uniswap V4',
    'Aerodrome', 'Aerodrome Basic',
    'Lido', 'Morpho', 'Spark', 'GMX',
  ];
  let streamsByGroup = $derived.by(() => {
    const map = new Map<string, StreamRow[]>();
    for (const r of streams) {
      if (!map.has(r.group)) map.set(r.group, []);
      map.get(r.group)!.push(r);
    }
    const ordered: [string, StreamRow[]][] = [];
    for (const g of GROUP_ORDER) {
      if (map.has(g)) {
        ordered.push([g, sortRows(map.get(g)!, getGroupSort(g))]);
        map.delete(g);
      }
    }
    // Append any groups not in the preferred ordering, alphabetically.
    for (const g of [...map.keys()].sort()) {
      ordered.push([g, sortRows(map.get(g)!, getGroupSort(g))]);
    }
    return ordered;
  });

  function ageMs(iso: string | undefined | null): number | null {
    if (!iso) return null;
    const t = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
    return Number.isFinite(t) ? Date.now() - t : null;
  }
  function fmtAge(ms: number | null): string {
    if (ms === null) return '—';
    if (ms < 0) return 'in future';
    if (ms < 1000) return `${ms}ms`;
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h`;
    return `${Math.floor(h / 24)}d`;
  }
  // "X/Y" for live/sweep seconds — em-dash for unknown halves.
  function fmtDurations(live: number | null | undefined, sweep: number | null | undefined): string {
    const f = (v: number | null | undefined) => (v == null ? '—' : (v >= 10 ? Math.round(v).toString() : v.toFixed(1)));
    return `${f(live)}/${f(sweep)}`;
  }
  // Local time HH:MM:SS for compact datetime column.
  function fmtTime(iso: string | undefined | null): string {
    if (!iso) return '—';
    const t = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
    if (!Number.isFinite(t)) return '—';
    const d = new Date(t);
    return d.toLocaleTimeString('en-GB', { hour12: false });
  }

  // ---- backfill form state -------------------------------------------------
  let selectedFormType = $state<string>('hyperliquid_events');
  let selectedForm = $derived(
    BACKFILL_FORMS.find((f) => f.type === selectedFormType) ?? BACKFILL_FORMS[0]
  );
  let fSince = $state('');
  let fUntil = $state('');
  let fForce = $state(false);
  let fieldValues = $state<Record<string, string[] | string>>({});
  // pair-multiselect uses string[] of "left/right" tuples
  let submitMsg = $state<string | null>(null);

  function resetForm() {
    fieldValues = {};
    submitMsg = null;
  }
  $effect(() => {
    // when form type changes, clear inputs
    selectedFormType;
    fieldValues = {};
    submitMsg = null;
  });

  function toggleMulti(name: string, val: string) {
    const cur = (fieldValues[name] as string[] | undefined) ?? [];
    if (cur.includes(val)) {
      fieldValues[name] = cur.filter((x) => x !== val);
    } else {
      fieldValues[name] = [...cur, val];
    }
  }
  function isSelected(name: string, val: string): boolean {
    return ((fieldValues[name] as string[] | undefined) ?? []).includes(val);
  }

  function buildBody(): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (!fSince) throw new Error('since is required');
    body.since = new Date(fSince).toISOString();
    if (fUntil) body.until = new Date(fUntil).toISOString();
    if (fForce) body.force = true;
    for (const field of selectedForm.fields) {
      const v = fieldValues[field.name];
      if (field.kind === 'multiselect') {
        const arr = (v as string[] | undefined) ?? [];
        if (field.required && arr.length === 0) throw new Error(`${field.label} is required`);
        if (arr.length > 0) body[field.name] = arr;
      } else if (field.kind === 'pair-multiselect') {
        const arr = ((v as string[] | undefined) ?? [])
          .map((s) => s.split('/'))
          .filter((p) => p.length === 2);
        if (field.required && arr.length === 0) throw new Error(`${field.label} is required`);
        if (arr.length > 0) body[field.name] = arr;
      } else if (field.kind === 'tokens-csv' || field.kind === 'pools-csv') {
        const s = (v as string | undefined) ?? '';
        const arr = s
          .split(',')
          .map((t) => t.trim())
          .filter((t) => t.length > 0);
        if (field.required && arr.length === 0) throw new Error(`${field.label} is required`);
        if (arr.length > 0) body[field.name] = arr;
      }
    }
    return body;
  }

  async function submitBackfill() {
    submitMsg = null;
    try {
      const body = buildBody();
      const res = await fetch(`/api/admin/jobs/backfill/${selectedFormType}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const j = await res.json();
      submitMsg = `OK: job ${j.job_id?.slice(0, 8) ?? '?'} kicked`;
      await refresh();
    } catch (e) {
      submitMsg = `ERROR: ${e}`;
    }
  }

  // Pair-multiselect helpers — combine to "left/right" tokens.
  function pairKey(left: string, right: string): string {
    return `${left}/${right}`;
  }
</script>

<svelte:head>
  <title>Admin — TraderNick</title>
</svelte:head>

<div class="px-8 py-6 space-y-6">
  <div class="flex items-center justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Admin</h1>
      <div class="text-xs text-zinc-500">
        Live streams + backfill jobs. Auto-refresh every 5s. Last refresh
        {lastRefresh ? new Date(lastRefresh).toLocaleTimeString() : '—'}.
      </div>
    </div>
    <button
      class="text-xs px-3 py-1.5 bg-zinc-900 border border-zinc-700 rounded-md hover:border-zinc-500"
      onclick={() => refresh()}
    >Refresh</button>
  </div>

  {#if actionMsg}
    <div class="text-xs px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-md">
      {actionMsg}
    </div>
  {/if}

  <!-- ============================ Live streams ============================ -->
  <section class="space-y-3">
    <div class="flex items-baseline gap-3">
      <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">
        Live streams ({streams.length})
      </h2>
      <span class="text-xs text-zinc-500">
        <span class="text-green-300">{streams.filter((s) => lifecycle(s) === 'RUNNING').length} running</span> ·
        <span class="text-green-500">{streams.filter((s) => lifecycle(s) === 'ON').length} on</span> ·
        <span class="text-amber-300">{streams.filter((s) => lifecycle(s) === 'STARTING').length} starting</span> ·
        <span class="text-zinc-500">{streams.filter((s) => lifecycle(s) === 'OFF').length} off</span>
      </span>
    </div>
    {#if streamsErr}
      <div class="text-xs text-red-300 bg-red-950/30 p-2 rounded">{streamsErr}</div>
    {/if}

    {#each streamsByGroup as [groupName, groupRows] (groupName)}
      {@const collapsed = collapsedGroups[groupName] === true}
      {@const nRunning = groupRows.filter((s) => lifecycle(s) === 'RUNNING').length}
      {@const nOn = groupRows.filter((s) => lifecycle(s) === 'ON').length}
      {@const nStarting = groupRows.filter((s) => lifecycle(s) === 'STARTING').length}
      {@const nOff = groupRows.filter((s) => lifecycle(s) === 'OFF').length}
      {@const aggCrashes = groupRows.reduce((acc, s) => acc + (s.status?.crash_count ?? s.crash_count ?? 0), 0)}
      {@const errorRows = groupRows.filter((s) => s.status?.last_error)}
      <div class="space-y-1">
        <div class="flex items-baseline gap-2 px-1">
          <button
            type="button"
            class="text-zinc-500 hover:text-zinc-200 select-none w-3 text-xs font-mono"
            onclick={() => toggleCollapsed(groupName)}
            title={collapsed ? 'Expand' : 'Collapse'}
          >{collapsed ? '▶' : '▼'}</button>
          <h3
            class="text-xs font-semibold text-zinc-200 cursor-pointer select-none"
            onclick={() => toggleCollapsed(groupName)}
          >{groupName}</h3>
          <span class="text-[10px] text-zinc-500">
            {groupRows.length} streams ·
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
        {#if !collapsed}
        <div class="overflow-auto border border-zinc-800 rounded-md">
          <table class="text-xs w-full">
            <thead class="bg-zinc-900 text-zinc-400">
              <tr>
                {#each [
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
                  ['last_error', 'Last error']
                ] as [k, label]}
                  {@const gs = getGroupSort(groupName)}
                  <th
                    class="px-2 py-1.5 text-left whitespace-nowrap select-none cursor-pointer hover:text-zinc-100"
                    onclick={() => toggleGroupSort(groupName, k as StreamSortKey)}
                  >
                    {label}
                    {#if gs.key === k}<span class="text-zinc-500">{gs.dir === 'asc' ? '▲' : '▼'}</span>{/if}
                  </th>
                {/each}
                <th class="px-2 py-1.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {#each groupRows as r (r.name)}
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
    {/each}
  </section>

  <!-- ============================ Backfill jobs ============================ -->
  <section class="space-y-2">
    <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">
      Backfill jobs ({jobs.filter((j) => j.status === 'running').length})
    </h2>
    {#if jobsErr}
      <div class="text-xs text-red-300 bg-red-950/30 p-2 rounded">{jobsErr}</div>
    {/if}
    <div class="overflow-auto border border-zinc-800 rounded-md">
      <table class="text-xs w-full">
        <thead class="bg-zinc-900 text-zinc-400">
          <tr>
            <th class="px-2 py-1.5 text-left">Job</th>
            <th class="px-2 py-1.5 text-left">Type</th>
            <th class="px-2 py-1.5 text-left">Status</th>
            <th class="px-2 py-1.5 text-right">%</th>
            <th class="px-2 py-1.5 text-left">Window</th>
            <th class="px-2 py-1.5 text-left">Args</th>
            <th class="px-2 py-1.5 text-left">Started</th>
            <th class="px-2 py-1.5 text-left">Updated</th>
            <th class="px-2 py-1.5 text-left">Error</th>
            <th class="px-2 py-1.5 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {#each jobs as j (j.job_id)}
            <tr class="border-t border-zinc-800 hover:bg-zinc-900/40">
              <td class="px-2 py-1 font-mono" title={j.job_id}>{j.job_id.slice(0, 12)}…</td>
              <td class="px-2 py-1 text-zinc-400">{j.job_type.replace(/^backfill_/, '')}</td>
              <td class="px-2 py-1">
                {#if j.status === 'running'}<span class="text-emerald-400">{j.status}</span>
                {:else if j.status === 'failed'}<span class="text-red-400">{j.status}</span>
                {:else if j.status === 'cancelled'}<span class="text-amber-400">{j.status}</span>
                {:else if j.status === 'completed'}<span class="text-zinc-400">{j.status}</span>
                {:else}<span class="text-zinc-500">{j.status}</span>
                {/if}
              </td>
              <td class="px-2 py-1 text-right tabular-nums">{(j.progress * 100).toFixed(0)}</td>
              <td class="px-2 py-1 text-zinc-400 whitespace-nowrap" title={`${j.args.since ?? ''} → ${j.args.until ?? ''}`}>
                {(j.args.since as string ?? '?').slice(0, 10)} → {(j.args.until as string ?? '?').slice(0, 10)}
              </td>
              <td class="px-2 py-1 text-zinc-500 max-w-xs truncate font-mono" title={JSON.stringify(j.args)}>
                {Object.entries(j.args)
                  .filter(([k]) => !['since', 'until', 'force', 'completed_chunks'].includes(k))
                  .map(([k, v]) => `${k}=${Array.isArray(v) ? v.length : v}`)
                  .join(' ')}
              </td>
              <td class="px-2 py-1 text-zinc-500 whitespace-nowrap">{(j.started_at ?? '').slice(11, 19)}</td>
              <td class="px-2 py-1 text-zinc-500 whitespace-nowrap">{fmtAge(ageMs(j.updated_at))}</td>
              <td class="px-2 py-1 text-red-300 max-w-xs truncate" title={j.error ?? ''}>{j.error ?? ''}</td>
              <td class="px-2 py-1 text-right whitespace-nowrap">
                {#if j.status === 'running' || j.status === 'pending'}
                  <button
                    class="text-xs px-2 py-0.5 bg-zinc-900 border border-zinc-700 rounded hover:border-amber-500 hover:text-amber-300"
                    onclick={() => cancelJob(j.job_id)}
                  >cancel</button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>

  <!-- ============================ Kick backfill ============================ -->
  <section class="space-y-3">
    <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">Kick new backfill</h2>

    <div class="flex flex-wrap gap-3 items-end">
      <label class="flex flex-col text-xs gap-1">
        <span class="text-zinc-400">Type</span>
        <select
          bind:value={selectedFormType}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
        >
          {#each BACKFILL_FORMS as f (f.type)}
            <option value={f.type}>{f.label}</option>
          {/each}
        </select>
      </label>
      <label class="flex flex-col text-xs gap-1">
        <span class="text-zinc-400">Since (UTC, required)</span>
        <input
          type="datetime-local"
          bind:value={fSince}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
        />
      </label>
      <label class="flex flex-col text-xs gap-1">
        <span class="text-zinc-400">Until (UTC, optional)</span>
        <input
          type="datetime-local"
          bind:value={fUntil}
          class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
        />
      </label>
      <label class="flex items-center text-xs gap-2 text-zinc-300">
        <input type="checkbox" bind:checked={fForce} />
        Force (delete existing rows in window)
      </label>
    </div>

    {#if selectedForm.description}
      <div class="text-xs text-zinc-500">{selectedForm.description}</div>
    {/if}

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      {#each selectedForm.fields as field (field.name)}
        <div class="space-y-1">
          <div class="text-xs text-zinc-400">
            {field.label}{#if field.required}<span class="text-red-400"> *</span>{/if}
          </div>
          {#if field.kind === 'multiselect'}
            <div class="flex flex-wrap gap-1.5">
              {#each (field.options ?? []) as opt (opt)}
                <button
                  type="button"
                  class="text-xs px-2 py-0.5 rounded border"
                  class:border-blue-500={isSelected(field.name, opt)}
                  class:bg-blue-950={isSelected(field.name, opt)}
                  class:text-blue-200={isSelected(field.name, opt)}
                  class:border-zinc-700={!isSelected(field.name, opt)}
                  class:text-zinc-400={!isSelected(field.name, opt)}
                  onclick={() => toggleMulti(field.name, opt)}
                >{opt}</button>
              {/each}
            </div>
          {:else if field.kind === 'pair-multiselect'}
            <div class="flex flex-col gap-1">
              {#each (field.options ?? []) as left (left)}
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="text-xs text-zinc-500 w-16">{left}</span>
                  {#each (field.optionsRight ?? []) as right (right)}
                    {@const k = pairKey(left, right)}
                    <button
                      type="button"
                      class="text-xs px-2 py-0.5 rounded border"
                      class:border-blue-500={isSelected(field.name, k)}
                      class:bg-blue-950={isSelected(field.name, k)}
                      class:text-blue-200={isSelected(field.name, k)}
                      class:border-zinc-700={!isSelected(field.name, k)}
                      class:text-zinc-400={!isSelected(field.name, k)}
                      onclick={() => toggleMulti(field.name, k)}
                    >{right}</button>
                  {/each}
                </div>
              {/each}
            </div>
          {:else if field.kind === 'tokens-csv' || field.kind === 'pools-csv'}
            <input
              type="text"
              placeholder={field.placeholder ?? ''}
              value={(fieldValues[field.name] as string) ?? ''}
              oninput={(e) => (fieldValues[field.name] = (e.currentTarget as HTMLInputElement).value)}
              class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100 font-mono"
            />
          {/if}
        </div>
      {/each}
    </div>

    <div class="flex items-center gap-3">
      <button
        class="text-sm px-3 py-1.5 bg-blue-700 hover:bg-blue-600 rounded-md text-white"
        onclick={submitBackfill}
      >Kick backfill</button>
      <button
        class="text-xs px-3 py-1.5 bg-zinc-900 border border-zinc-700 rounded-md hover:border-zinc-500"
        onclick={resetForm}
      >Reset</button>
      {#if submitMsg}
        <span class="text-xs"
          class:text-emerald-400={submitMsg.startsWith('OK')}
          class:text-red-300={submitMsg.startsWith('ERROR')}
        >{submitMsg}</span>
      {/if}
    </div>
  </section>
</div>
