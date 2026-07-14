<script lang="ts">
  // Sidebar Token Shortlist: two exclusive, ordered watchlists (Short | Long),
  // each capped at 10. Add via the selector + Long/Short buttons; × removes; drag
  // reorders within a side; clicking a capsule copies its symbol. Persisted in
  // localStorage via tokenShortlistStore.
  import { onMount } from 'svelte';
  import { dndzone, type DndEvent } from 'svelte-dnd-action';
  import { tokenShortlistStore, type ShortlistSide } from '$lib/stores/tokenShortlist.svelte';

  type Item = { id: string };

  let roster = $state<string[]>([]);
  let selected = $state('');
  let copied = $state<string | null>(null);

  const rosterSet = $derived(new Set(roster));

  // Local dnd item arrays mirror the store; drag mutates them locally and commits
  // on finalize. The $effects re-sync when the store changes via buttons / ×.
  let shortItems = $state<Item[]>([]);
  let longItems = $state<Item[]>([]);
  $effect(() => {
    shortItems = tokenShortlistStore.short.map((t) => ({ id: t }));
  });
  $effect(() => {
    longItems = tokenShortlistStore.long.map((t) => ({ id: t }));
  });

  onMount(async () => {
    tokenShortlistStore.hydrate();
    try {
      const res = await fetch('/api/tokens');
      if (res.ok) {
        const body = await res.json();
        roster = ((body.tokens ?? []) as string[])
          .map((t) => String(t).toUpperCase())
          .filter(Boolean)
          .sort();
      }
    } catch {
      /* roster unavailable — the input still accepts free text */
    }
  });

  function addTo(side: ShortlistSide) {
    const sym = selected.trim().toUpperCase();
    if (!sym) return;
    if (roster.length > 0 && !rosterSet.has(sym)) return; // must be a known token
    if (tokenShortlistStore.add(sym, side)) selected = '';
  }

  function considerShort(e: CustomEvent<DndEvent<Item>>) {
    shortItems = e.detail.items;
  }
  function finalizeShort(e: CustomEvent<DndEvent<Item>>) {
    shortItems = e.detail.items;
    tokenShortlistStore.setOrder('short', e.detail.items.map((i) => i.id));
  }
  function considerLong(e: CustomEvent<DndEvent<Item>>) {
    longItems = e.detail.items;
  }
  function finalizeLong(e: CustomEvent<DndEvent<Item>>) {
    longItems = e.detail.items;
    tokenShortlistStore.setOrder('long', e.detail.items.map((i) => i.id));
  }

  async function copySym(sym: string) {
    try {
      await navigator.clipboard.writeText(sym);
      copied = sym;
      setTimeout(() => { if (copied === sym) copied = null; }, 900);
    } catch {
      /* clipboard blocked */
    }
  }

  const DND = { flipDurationMs: 150, dropTargetStyle: {} };
</script>

<div class="px-2 pt-3 border-t border-zinc-800">
  <div class="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 select-none">
    Token Shortlist
  </div>

  <!-- selector + Long / Short -->
  <div class="flex items-center gap-1 mb-2">
    <input
      list="tokenShortlistRoster"
      bind:value={selected}
      placeholder="Token…"
      aria-label="Token to shortlist"
      onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); } }}
      class="flex-1 min-w-0 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-1 text-xs uppercase text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
    />
    <datalist id="tokenShortlistRoster">
      {#each roster as t (t)}<option value={t}></option>{/each}
    </datalist>
    <button
      type="button"
      onclick={() => addTo('long')}
      disabled={tokenShortlistStore.long.length >= tokenShortlistStore.max}
      title="Add to Long"
      class="text-[10px] font-medium px-1.5 py-1 rounded border border-emerald-800 text-emerald-300 hover:bg-emerald-950/50 disabled:opacity-30 disabled:cursor-not-allowed"
    >Long</button>
    <button
      type="button"
      onclick={() => addTo('short')}
      disabled={tokenShortlistStore.short.length >= tokenShortlistStore.max}
      title="Add to Short"
      class="text-[10px] font-medium px-1.5 py-1 rounded border border-rose-800 text-rose-300 hover:bg-rose-950/50 disabled:opacity-30 disabled:cursor-not-allowed"
    >Short</button>
  </div>

  <!-- Short (left) · Long (right); each side wraps ~2-wide. Capped height as a safety net. -->
  <div class="grid grid-cols-2 gap-1.5 max-h-56 overflow-y-auto scrollbar-none">
    <!-- Short -->
    <div class="min-w-0">
      <div class="flex items-center justify-between px-0.5 mb-1">
        <span class="text-[9px] uppercase tracking-wider text-rose-400 font-semibold">Short</span>
        <span class="text-[9px] text-zinc-600 tabular-nums">{tokenShortlistStore.short.length}/{tokenShortlistStore.max}</span>
      </div>
      <div
        class="flex flex-wrap gap-1 content-start min-h-[1.75rem] rounded"
        use:dndzone={{ items: shortItems, ...DND }}
        onconsider={considerShort}
        onfinalize={finalizeShort}
      >
        {#each shortItems as it (it.id)}
          <div class="group flex items-center rounded bg-rose-950/40 border border-rose-900/60 pl-1.5 pr-0.5 py-0.5 cursor-grab active:cursor-grabbing" title="Drag to reorder">
            <button type="button" class="text-[10px] font-medium text-rose-200 max-w-[56px] truncate leading-none" onclick={() => copySym(it.id)} title="Copy {it.id}">{copied === it.id ? '✓' : it.id}</button>
            <button type="button" class="text-rose-400/70 hover:text-rose-200 text-[11px] leading-none px-0.5 ml-0.5" title="Remove {it.id}" onclick={(e) => { e.stopPropagation(); tokenShortlistStore.remove(it.id, 'short'); }}>×</button>
          </div>
        {/each}
        {#if shortItems.length === 0}<span class="text-[9px] text-zinc-600 italic px-0.5 self-center">empty</span>{/if}
      </div>
    </div>

    <!-- Long -->
    <div class="min-w-0">
      <div class="flex items-center justify-between px-0.5 mb-1">
        <span class="text-[9px] uppercase tracking-wider text-emerald-400 font-semibold">Long</span>
        <span class="text-[9px] text-zinc-600 tabular-nums">{tokenShortlistStore.long.length}/{tokenShortlistStore.max}</span>
      </div>
      <div
        class="flex flex-wrap gap-1 content-start min-h-[1.75rem] rounded"
        use:dndzone={{ items: longItems, ...DND }}
        onconsider={considerLong}
        onfinalize={finalizeLong}
      >
        {#each longItems as it (it.id)}
          <div class="group flex items-center rounded bg-emerald-950/40 border border-emerald-900/60 pl-1.5 pr-0.5 py-0.5 cursor-grab active:cursor-grabbing" title="Drag to reorder">
            <button type="button" class="text-[10px] font-medium text-emerald-200 max-w-[56px] truncate leading-none" onclick={() => copySym(it.id)} title="Copy {it.id}">{copied === it.id ? '✓' : it.id}</button>
            <button type="button" class="text-emerald-400/70 hover:text-emerald-200 text-[11px] leading-none px-0.5 ml-0.5" title="Remove {it.id}" onclick={(e) => { e.stopPropagation(); tokenShortlistStore.remove(it.id, 'long'); }}>×</button>
          </div>
        {/each}
        {#if longItems.length === 0}<span class="text-[9px] text-zinc-600 italic px-0.5 self-center">empty</span>{/if}
      </div>
    </div>
  </div>
</div>
