<script lang="ts">
  import { onMount } from 'svelte';
  import { filtersStore } from '$lib/stores/filters.svelte';
  import {
    filterStatus,
    filterKindLabel,
    missingRefs,
    type SavedFilter,
    type FilterConfig,
  } from '$lib/components/charts/filters';
  import { metricDef } from '$lib/components/charts/smartSelector';
  import FilterBuilderDialog from '$lib/components/FilterBuilderDialog.svelte';

  onMount(() => filtersStore.hydrate());

  // null = closed; { filter: null } = create; { filter } = edit.
  let dialog = $state<{ filter: SavedFilter | null } | null>(null);
  let errorMsg = $state<string | null>(null);

  function openNew() {
    errorMsg = null;
    dialog = { filter: null };
  }
  function openEdit(f: SavedFilter) {
    errorMsg = null;
    dialog = { filter: f };
  }

  function handleSave(patch: { name: string; config: FilterConfig; refs: string[] }) {
    // Drop refs to filters that no longer exist.
    const refs = patch.refs.filter((id) => filtersStore.getById(id));
    const editing = dialog?.filter;
    if (editing) {
      const ok = filtersStore.update(editing.id, { name: patch.name, config: patch.config, refs });
      if (!ok) {
        errorMsg = `Couldn't save "${patch.name}" — the chosen building blocks would form a cycle.`;
        return;
      }
    } else {
      try {
        filtersStore.add(patch.name, patch.config, refs);
      } catch (e) {
        errorMsg = e instanceof Error ? e.message : String(e);
        return;
      }
    }
    dialog = null;
  }

  function del(f: SavedFilter) {
    if (typeof window !== 'undefined' && !window.confirm(`Delete filter "${f.name}"?`)) return;
    filtersStore.remove(f.id);
  }

  // One-line human summary of a filter's makeup.
  function summarize(f: SavedFilter): string {
    const parts: string[] = [];
    if (f.config.kind === 'hl') {
      const sel = f.config.selector;
      const active = sel.criteria.filter((c) => !(c.disabled ?? false));
      if (active.length) {
        const labels = active.map((c) => metricDef(c.metric)?.label ?? c.metric);
        parts.push(`${labels.join(', ')} · ${sel.lookback}d · top ${sel.top_n}`);
      }
    }
    if (f.refs.length) parts.push(`AND ${f.refs.length} filter${f.refs.length > 1 ? 's' : ''}`);
    return parts.join('  ') || 'empty';
  }

  function refNames(f: SavedFilter): string {
    return f.refs
      .map((id) => filtersStore.getById(id)?.name ?? '(deleted)')
      .join(', ');
  }
</script>

<div class="mx-auto max-w-4xl px-6 py-6">
  <div class="mb-4 flex items-center justify-between">
    <div>
      <h1 class="text-lg font-semibold text-zinc-100">Wallet Filters</h1>
      <p class="text-xs text-zinc-500">
        Reusable wallet selections — every filter resolves to a set of wallets,
        so any kind can be combined and used on any smart-money chart. Today's
        filters are <span class="text-zinc-400">Hyperliquid</span> leaderboards;
        more kinds (e.g. exchange-flow) will plug in later.
      </p>
    </div>
    <button
      type="button"
      onclick={openNew}
      class="rounded border border-zinc-700 bg-emerald-700 px-3 py-1.5 text-xs text-zinc-100 hover:bg-emerald-600"
    >+ New filter</button>
  </div>

  {#if errorMsg}
    <div class="mb-3 flex items-center gap-2 rounded border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
      <span>{errorMsg}</span>
      <button type="button" class="ml-auto text-red-400 hover:text-red-200" onclick={() => (errorMsg = null)}>✕</button>
    </div>
  {/if}

  {#if filtersStore.filters.length === 0}
    <div class="rounded border border-dashed border-zinc-800 px-6 py-12 text-center text-sm text-zinc-500">
      No filters yet. Create one to get started.
    </div>
  {:else}
    <div class="space-y-2">
      {#each filtersStore.filters as f (f.id)}
        {@const status = filterStatus(f.id, filtersStore.getById)}
        <div class="flex items-center gap-3 rounded border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-400" title={filterKindLabel(f.config.kind)}>{f.config.kind}</span>
              <span class="truncate text-sm font-medium text-zinc-100">{f.name}</span>
              {#if status === 'broken'}
                <span class="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] text-amber-300" title={`Missing building blocks: ${missingRefs(f.id, filtersStore.getById).join(', ')}`}>broken refs</span>
              {/if}
            </div>
            <div class="truncate text-xs text-zinc-500">{summarize(f)}</div>
            {#if f.refs.length}
              <div class="truncate text-[11px] text-zinc-600">refs: {refNames(f)}</div>
            {/if}
          </div>
          <button
            type="button"
            onclick={() => openEdit(f)}
            class="rounded border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-700"
          >Edit</button>
          <button
            type="button"
            onclick={() => del(f)}
            class="rounded border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-400 hover:text-red-300 hover:bg-zinc-700"
          >Delete</button>
        </div>
      {/each}
    </div>
  {/if}
</div>

{#if dialog}
  <FilterBuilderDialog
    filter={dialog.filter}
    onSave={handleSave}
    onClose={() => (dialog = null)}
  />
{/if}
