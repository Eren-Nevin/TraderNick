<script lang="ts">
  // Overview = the whole admin page as it was before the redesign, just with
  // the per-group table extracted into a component and the form pulled out
  // into a component. State + polling are owned by the (admin) layout.

  import { getContext } from 'svelte';
  import { ADMIN_CTX_KEY, lifecycle, type AdminContext } from '$lib/admin/types';
  import StreamsGroupTable from '$lib/admin/components/StreamsGroupTable.svelte';
  import BackfillJobsTable from '$lib/admin/components/BackfillJobsTable.svelte';
  import BackfillForm from '$lib/admin/components/BackfillForm.svelte';
  import { BACKFILL_FORMS } from '$lib/admin/backfill_forms';
  import { PROVIDERS } from '$lib/admin/providers';

  const ctx = getContext<AdminContext>(ADMIN_CTX_KEY);

  // Group streams by their `group` field, in PROVIDERS order. Anything not
  // in the explicit ordering gets appended alphabetically (same behavior
  // as the previous monolith).
  let streamsByGroup = $derived.by(() => {
    const map = new Map<string, typeof ctx.streams>();
    for (const r of ctx.streams) {
      if (!map.has(r.group)) map.set(r.group, []);
      map.get(r.group)!.push(r);
    }
    const ordered: [string, typeof ctx.streams][] = [];
    for (const g of PROVIDERS) {
      if (map.has(g)) {
        ordered.push([g, map.get(g)!]);
        map.delete(g);
      }
    }
    for (const g of [...map.keys()].sort()) {
      ordered.push([g, map.get(g)!]);
    }
    return ordered;
  });

  let selectedFormType = $state<string>('hyperliquid_events');
  let selectedForm = $derived(
    BACKFILL_FORMS.find((f) => f.type === selectedFormType) ?? BACKFILL_FORMS[0],
  );
</script>

<div class="px-8 py-6 space-y-6">
  <div class="flex items-center justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Overview</h1>
      <div class="text-xs text-zinc-500">
        Live streams + backfill jobs. Auto-refresh every 1s.
        Last refresh {ctx.lastRefresh ? new Date(ctx.lastRefresh).toLocaleTimeString() : '—'}.
      </div>
    </div>
    <button
      class="text-xs px-3 py-1.5 bg-zinc-900 border border-zinc-700 rounded-md hover:border-zinc-500"
      onclick={() => ctx.refresh()}
    >Refresh</button>
  </div>

  <!-- ============================ Live streams ============================ -->
  <section class="space-y-3">
    <div class="flex items-baseline gap-3">
      <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">
        Live streams ({ctx.streams.length})
      </h2>
      <span class="text-xs text-zinc-500">
        <span class="text-green-300">{ctx.streams.filter((s) => lifecycle(s) === 'RUNNING').length} running</span> ·
        <span class="text-green-500">{ctx.streams.filter((s) => lifecycle(s) === 'ON').length} on</span> ·
        <span class="text-amber-300">{ctx.streams.filter((s) => lifecycle(s) === 'STARTING').length} starting</span> ·
        <span class="text-zinc-500">{ctx.streams.filter((s) => lifecycle(s) === 'OFF').length} off</span>
      </span>
    </div>
    {#if ctx.streamsErr}
      <div class="text-xs text-red-300 bg-red-950/30 p-2 rounded">{ctx.streamsErr}</div>
    {/if}

    {#each streamsByGroup as [groupName, groupRows] (groupName)}
      <StreamsGroupTable
        {groupName}
        rows={groupRows}
        streamAction={ctx.streamAction}
      />
    {/each}
  </section>

  <!-- ============================ Backfill jobs ============================ -->
  {#if ctx.jobsErr}
    <div class="text-xs text-red-300 bg-red-950/30 p-2 rounded">{ctx.jobsErr}</div>
  {/if}
  <BackfillJobsTable
    jobs={ctx.jobs}
    cancelJob={ctx.cancelJob}
    clearFinished={ctx.clearFinishedJobs}
  />

  <!-- ============================ Kick backfill ============================ -->
  <section class="space-y-3">
    <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">Kick new backfill</h2>
    <label class="flex flex-col text-xs gap-1 max-w-xs">
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
    <BackfillForm form={selectedForm} onSubmitted={() => ctx.refresh()} />
  </section>
</div>
