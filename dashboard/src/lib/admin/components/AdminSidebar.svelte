<script lang="ts">
  // Admin left nav: Overview + collapsible LiveStreams/Backfill parents,
  // each with one child link per provider. Active item is highlighted by
  // matching $page.url.pathname against the link href.
  //
  // Badges on each per-provider entry: green pulse = running streams,
  // red = at least one stream has last_error. Same data the table cells
  // already render — derived from the context's streams[] / jobs[].

  import { page } from '$app/state';
  import { getContext } from 'svelte';
  import { PROVIDERS, providerSlug, jobProvider } from '$lib/admin/providers';
  import { lifecycle, ADMIN_CTX_KEY, type AdminContext } from '$lib/admin/types';

  const ctx = getContext<AdminContext>(ADMIN_CTX_KEY);

  let expandLive = $state(true);
  let expandBackfill = $state(true);

  function isActive(href: string): boolean {
    return page.url.pathname === href;
  }
  function isUnder(prefix: string): boolean {
    return page.url.pathname.startsWith(prefix);
  }

  // Derive per-provider counters from the shared state. These flow through
  // because ctx.streams is a $state proxy from the layout.
  let liveCounts = $derived.by(() => {
    const m: Record<string, { running: number; errors: number; total: number }> = {};
    for (const p of PROVIDERS) m[p] = { running: 0, errors: 0, total: 0 };
    for (const s of ctx.streams) {
      const bucket = m[s.group];
      if (!bucket) continue;
      bucket.total++;
      const lc = lifecycle(s);
      if (lc === 'RUNNING' || lc === 'ON') bucket.running++;
      if (s.status?.last_error) bucket.errors++;
    }
    return m;
  });
  let backfillCounts = $derived.by(() => {
    const m: Record<string, { running: number }> = {};
    for (const p of PROVIDERS) m[p] = { running: 0 };
    for (const j of ctx.jobs) {
      if (j.status !== 'running') continue;
      const p = jobProvider(j.job_type);
      if (p && m[p]) m[p].running++;
    }
    return m;
  });
</script>

<aside class="w-56 shrink-0 border-r border-zinc-800 bg-zinc-950 text-xs flex flex-col">
  <div class="px-4 py-4 border-b border-zinc-800">
    <h1 class="text-sm font-semibold text-zinc-100">Admin</h1>
    <div class="text-[10px] text-zinc-500 mt-0.5">Live streams + backfills</div>
  </div>

  <nav class="flex-1 overflow-y-auto py-2">
    <!-- Overview -->
    <a
      href="/admin"
      class="block px-4 py-1.5 hover:bg-zinc-900"
      class:bg-zinc-900={isActive('/admin')}
      class:text-zinc-100={isActive('/admin')}
      class:text-zinc-400={!isActive('/admin')}
    >Overview</a>

    <!-- LiveStreams parent -->
    <button
      type="button"
      class="w-full text-left px-4 py-1.5 hover:bg-zinc-900 flex items-center justify-between"
      class:text-zinc-200={isUnder('/admin/live')}
      class:text-zinc-400={!isUnder('/admin/live')}
      onclick={() => (expandLive = !expandLive)}
    >
      <span>Live streams</span>
      <span class="text-zinc-600 text-[10px]">{expandLive ? '▾' : '▸'}</span>
    </button>
    {#if expandLive}
      {#each PROVIDERS as p (p)}
        {@const slug = providerSlug(p)}
        {@const href = `/admin/live/${slug}`}
        {@const c = liveCounts[p]}
        <a
          {href}
          class="flex items-center justify-between pl-8 pr-4 py-1 hover:bg-zinc-900"
          class:bg-zinc-900={isActive(href)}
          class:text-zinc-100={isActive(href)}
          class:text-zinc-500={!isActive(href)}
        >
          <span class="truncate">{p}</span>
          <span class="flex items-center gap-1 text-[10px]">
            {#if c.errors > 0}
              <span class="text-red-400" title={`${c.errors} stream(s) with error`}>{c.errors}!</span>
            {/if}
            {#if c.running > 0}
              <span class="inline-flex items-center gap-1 text-green-400">
                <span class="size-1 rounded-full bg-green-400"></span>
                {c.running}
              </span>
            {/if}
          </span>
        </a>
      {/each}
    {/if}

    <!-- Backfill parent -->
    <button
      type="button"
      class="w-full text-left px-4 py-1.5 hover:bg-zinc-900 flex items-center justify-between mt-2"
      class:text-zinc-200={isUnder('/admin/backfill')}
      class:text-zinc-400={!isUnder('/admin/backfill')}
      onclick={() => (expandBackfill = !expandBackfill)}
    >
      <span>Backfill</span>
      <span class="text-zinc-600 text-[10px]">{expandBackfill ? '▾' : '▸'}</span>
    </button>
    {#if expandBackfill}
      {#each PROVIDERS as p (p)}
        {@const slug = providerSlug(p)}
        {@const href = `/admin/backfill/${slug}`}
        {@const c = backfillCounts[p]}
        <a
          {href}
          class="flex items-center justify-between pl-8 pr-4 py-1 hover:bg-zinc-900"
          class:bg-zinc-900={isActive(href)}
          class:text-zinc-100={isActive(href)}
          class:text-zinc-500={!isActive(href)}
        >
          <span class="truncate">{p}</span>
          {#if c.running > 0}
            <span class="inline-flex items-center gap-1 text-[10px] text-emerald-400">
              <span class="size-1 rounded-full bg-emerald-400 animate-pulse"></span>
              {c.running}
            </span>
          {/if}
        </a>
      {/each}
    {/if}
  </nav>

  <div class="px-4 py-2 border-t border-zinc-800 text-[10px] text-zinc-600">
    Last refresh
    {ctx.lastRefresh ? new Date(ctx.lastRefresh).toLocaleTimeString() : '—'}
  </div>
</aside>
