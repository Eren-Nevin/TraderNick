<script lang="ts">
  // Pin-management popover: checkbox per group (checked = wallet pinned to it)
  // plus an inline "+ New group" with an optional color. Parent renders it in a
  // `relative` container and controls visibility; closes on click-away / Esc.

  import { onMount } from 'svelte';
  import { walletPinsStore } from '$lib/stores/walletPins.svelte';

  let { address, onClose }: { address: string; onClose: () => void } = $props();

  const groups = $derived(walletPinsStore.groups);

  // Opening the menu on a not-yet-pinned wallet selects Default by default
  // (the default pin target). Already-pinned wallets keep their groups.
  onMount(() => {
    if (!walletPinsStore.isPinned(address)) walletPinsStore.quickPin(address);
  });

  // New-group form.
  let newName = $state('');
  let newColor = $state<string | null>(null);
  const SWATCHES: Array<{ label: string; value: string | null }> = [
    { label: 'Neutral', value: null },
    { label: 'Amber', value: '#f59e0b' },
    { label: 'Emerald', value: '#10b981' },
    { label: 'Sky', value: '#38bdf8' },
    { label: 'Rose', value: '#f43f5e' },
    { label: 'Violet', value: '#8b5cf6' }
  ];

  function addGroup() {
    const name = newName.trim();
    if (!name) return;
    const g = walletPinsStore.addGroup(name, newColor);
    walletPinsStore.togglePin(address, g.id); // pin to the freshly-made group
    newName = '';
    newColor = null;
  }

  // Click-away + Esc to close.
  function clickAway(node: HTMLElement) {
    function onDown(e: MouseEvent) {
      if (!node.contains(e.target as Node)) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    // defer so the opening click doesn't immediately close it
    setTimeout(() => window.addEventListener('mousedown', onDown), 0);
    window.addEventListener('keydown', onKey);
    return {
      destroy() {
        window.removeEventListener('mousedown', onDown);
        window.removeEventListener('keydown', onKey);
      }
    };
  }
</script>

<div
  use:clickAway
  class="absolute z-50 mt-1 w-64 rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-xl text-sm"
>
  <div class="text-xs uppercase tracking-wide text-zinc-500 mb-2">Pin to groups</div>
  <div class="max-h-48 overflow-auto space-y-1">
    {#each groups as g (g.id)}
      <label class="flex items-center gap-2 px-1 py-1 rounded hover:bg-zinc-800 cursor-pointer">
        <input
          type="checkbox"
          checked={walletPinsStore.isInGroup(address, g.id)}
          onchange={() => walletPinsStore.togglePin(address, g.id)}
          class="accent-blue-500"
        />
        <span class="inline-block w-2.5 h-2.5 rounded-full border border-zinc-600"
          style="background-color: {g.color ?? '#3f3f46'}"></span>
        <span class="text-zinc-200">{g.name}</span>
      </label>
    {/each}
  </div>

  <div class="border-t border-zinc-800 mt-2 pt-2">
    <div class="text-xs text-zinc-500 mb-1">New group</div>
    <div class="flex items-center gap-1.5 mb-2">
      <input
        type="text"
        bind:value={newName}
        placeholder="Name"
        onkeydown={(e) => e.key === 'Enter' && addGroup()}
        class="flex-1 min-w-0 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
      />
      <button
        type="button"
        onclick={addGroup}
        disabled={!newName.trim()}
        class="rounded border border-zinc-700 bg-emerald-700 px-2 py-1 text-xs text-zinc-100 hover:bg-emerald-600 disabled:opacity-40"
      >Add</button>
    </div>
    <div class="flex items-center gap-1.5">
      {#each SWATCHES as s (s.label)}
        <button
          type="button"
          title={s.label}
          onclick={() => (newColor = s.value)}
          class="w-5 h-5 rounded-full border-2 {newColor === s.value ? 'border-zinc-100' : 'border-zinc-700'}"
          style="background-color: {s.value ?? '#3f3f46'}"
          aria-label={s.label}
        ></button>
      {/each}
    </div>
  </div>
</div>
