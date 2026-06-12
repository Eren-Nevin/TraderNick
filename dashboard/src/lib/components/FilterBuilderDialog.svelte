<script lang="ts">
  import SmartWalletSelector from '$lib/components/SmartWalletSelector.svelte';
  import {
    defaultSmartSelectorState,
    type SmartSelectorState,
  } from '$lib/components/charts/smartSelector';
  import { type SavedFilter, type FilterConfig, hlConfig } from '$lib/components/charts/filters';
  import { filtersStore } from '$lib/stores/filters.svelte';

  let {
    /** The filter to edit, or null to create a new one. */
    filter = null,
    onSave,
    onClose,
  }: {
    filter?: SavedFilter | null;
    onSave: (patch: { name: string; config: FilterConfig; refs: string[] }) => void;
    onClose: () => void;
  } = $props();

  // Local editable copy — committed only on Save. Only the Hyperliquid filter
  // kind exists today, so the dialog edits its `selector` directly.
  let name = $state(filter?.name ?? '');
  let selector = $state<SmartSelectorState>(
    filter && filter.config.kind === 'hl'
      ? structuredClone($state.snapshot(filter.config.selector))
      : defaultSmartSelectorState(),
  );
  let refs = $state<string[]>(filter ? [...filter.refs] : []);

  const editingId = filter?.id ?? null;

  // Can `from` reach `target` by walking refs? Used to disable building blocks
  // that would create a cycle if AND-ed into the filter being edited.
  function reaches(from: string, target: string): boolean {
    const all = filtersStore.filters;
    const byId = new Map(all.map((f) => [f.id, f]));
    const seen = new Set<string>();
    const stack = [from];
    while (stack.length) {
      const cur = stack.pop()!;
      if (cur === target) return true;
      if (seen.has(cur)) continue;
      seen.add(cur);
      const f = byId.get(cur);
      if (f) stack.push(...f.refs);
    }
    return false;
  }

  // Candidate building blocks: every saved filter except self and any whose
  // subtree already reaches this one (would form a cycle).
  let candidates = $derived(
    filtersStore.filters.filter(
      (f) => f.id !== editingId && !(editingId && reaches(f.id, editingId)),
    ),
  );

  // Refs that point at filters no longer in the store — surfaced so the user
  // knows a building block was deleted out from under this filter.
  let missing = $derived(refs.filter((id) => !filtersStore.getById(id)));

  function toggleRef(id: string, on: boolean) {
    refs = on ? [...new Set([...refs, id])] : refs.filter((r) => r !== id);
  }

  let hasCriteria = $derived(selector.criteria.length > 0);
  let canSave = $derived(name.trim().length > 0 && (hasCriteria || refs.length > 0));

  function save() {
    if (!canSave) return;
    onSave({ name: name.trim(), config: hlConfig(selector), refs });
  }
</script>

<!-- Modal backdrop -->
<div
  class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-6"
  onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
  onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
  role="presentation"
>
  <div class="w-full max-w-3xl rounded-lg border border-zinc-700 bg-zinc-950 shadow-xl">
    <div class="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
      <h2 class="text-sm font-semibold text-zinc-100">
        {filter ? 'Edit Hyperliquid filter' : 'New Hyperliquid filter'}
        <span class="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-400">HL</span>
      </h2>
      <button
        type="button"
        onclick={onClose}
        class="h-6 w-6 rounded text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
        aria-label="Close"
      >×</button>
    </div>

    <div class="space-y-4 px-4 py-4">
      <!-- Name -->
      <div class="flex items-center gap-2">
        <span class="w-16 text-[10px] uppercase tracking-widest text-zinc-500">Name</span>
        <input
          type="text"
          bind:value={name}
          placeholder="e.g. PnL ≥ $10k / 3d"
          maxlength="80"
          class="flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-100"
        />
      </div>

      <!-- Own criteria (reuses the chart's selector editor; no server presets) -->
      <div>
        <div class="mb-1 text-[10px] uppercase tracking-widest text-zinc-500">
          Criteria (optional if you combine other filters)
        </div>
        <SmartWalletSelector
          value={selector}
          onChange={(v) => (selector = v)}
          showPresets={false}
        />
      </div>

      <!-- Combine with other saved filters (AND / intersection) -->
      <div>
        <div class="mb-1 text-[10px] uppercase tracking-widest text-zinc-500">
          AND with other filters (per-day intersection)
        </div>
        {#if candidates.length === 0}
          <div class="text-xs text-zinc-500">
            No other filters to combine yet. Save this one, then build a filter
            that references it.
          </div>
        {:else}
          <div class="flex flex-wrap gap-1.5">
            {#each candidates as c (c.id)}
              {@const on = refs.includes(c.id)}
              <button
                type="button"
                onclick={() => toggleRef(c.id, !on)}
                class="rounded border px-2 py-1 text-xs transition-colors {on
                  ? 'border-emerald-600 bg-emerald-700/30 text-emerald-200'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800'}"
              >{on ? '✓ ' : ''}{c.name}</button>
            {/each}
          </div>
        {/if}
        {#if missing.length > 0}
          <div class="mt-2 text-xs text-amber-400">
            ⚠ {missing.length} referenced filter{missing.length > 1 ? 's were' : ' was'}
            deleted and will be dropped on save.
          </div>
        {/if}
      </div>
    </div>

    <div class="flex items-center justify-end gap-2 border-t border-zinc-800 px-4 py-3">
      <button
        type="button"
        onclick={onClose}
        class="rounded px-3 py-1 text-xs text-zinc-400 hover:text-zinc-100"
      >Cancel</button>
      <button
        type="button"
        onclick={save}
        disabled={!canSave}
        class="rounded border border-zinc-700 bg-emerald-700 px-3 py-1 text-xs text-zinc-100 hover:bg-emerald-600 disabled:bg-zinc-800 disabled:text-zinc-500"
        title={!canSave ? 'Give it a name and at least one criterion or referenced filter' : ''}
      >{filter ? 'Save changes' : 'Create filter'}</button>
    </div>
  </div>
</div>
