<script lang="ts">
  // Wallets page: jump-to-wallet-by-address field + the user's pinned wallets
  // grouped by tag (Default first), each group collapsible. Pins live in
  // localStorage via walletPinsStore.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    walletPinsStore,
    DEFAULT_GROUP_ID,
    NEUTRAL_GROUP_COLOR,
    type WalletGroup
  } from '$lib/stores/walletPins.svelte';
  import { isValidWalletAddress, normalizeAddress, walletHlUrl } from '$lib/arkham';
  import WalletAddress from '$lib/components/WalletAddress.svelte';

  onMount(() => {
    walletPinsStore.hydrate();
  });

  // ── Address entry ──────────────────────────────────────────────────
  let addr = $state('');
  let addrError = $state<string | null>(null);
  function openWallet() {
    const a = addr.trim();
    if (!isValidWalletAddress(a)) {
      addrError = 'Enter a valid wallet address (0x + 40 hex characters).';
      return;
    }
    addrError = null;
    goto(walletHlUrl(normalizeAddress(a)));
  }

  // ── Groups (Default first, then creation order) ────────────────────
  const orderedGroups = $derived([
    ...walletPinsStore.groups.filter((g) => g.id === DEFAULT_GROUP_ID),
    ...walletPinsStore.groups.filter((g) => g.id !== DEFAULT_GROUP_ID)
  ]);

  // Groups start COLLAPSED by default: an unset entry means collapsed, so the
  // page opens compact and the user expands what they want.
  let collapsed = $state<Record<string, boolean>>({});
  const isCollapsed = (id: string) => collapsed[id] ?? true;
  function toggleCollapse(id: string) {
    collapsed = { ...collapsed, [id]: !isCollapsed(id) };
  }

  // Inline group editing (rename / color / delete) — non-default only.
  let editing = $state<string | null>(null);
  let editName = $state('');
  const SWATCHES: Array<string | null> = [null, '#f59e0b', '#10b981', '#38bdf8', '#f43f5e', '#8b5cf6'];

  function startEdit(g: WalletGroup) {
    editing = g.id;
    editName = g.name;
  }
  function commitName(id: string) {
    if (editName.trim()) walletPinsStore.renameGroup(id, editName);
  }
  function deleteGroup(id: string) {
    walletPinsStore.removeGroup(id);
    if (editing === id) editing = null;
  }

  // ── New group ──────────────────────────────────────────────────────
  let newName = $state('');
  let newColor = $state<string | null>(null);
  function addGroup() {
    if (!newName.trim()) return;
    walletPinsStore.addGroup(newName, newColor);
    newName = '';
    newColor = null;
  }
</script>

<div class="mx-auto max-w-4xl px-6 py-6">
  <div class="mb-4">
    <h1 class="text-lg font-semibold text-zinc-100">Wallets</h1>
    <p class="text-xs text-zinc-500">
      Open any wallet by address, and keep the ones you care about. Pins are
      tag-like: a wallet can belong to several groups. Stored locally for now.
    </p>
  </div>

  <!-- Address entry -->
  <div class="mb-6">
    <div class="flex items-center gap-2">
      <input
        type="text"
        bind:value={addr}
        placeholder="0x… wallet address"
        oninput={() => (addrError = null)}
        onkeydown={(e) => e.key === 'Enter' && openWallet()}
        class="flex-1 min-w-0 rounded border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
      />
      <button
        type="button"
        onclick={openWallet}
        class="rounded border border-zinc-700 bg-emerald-700 px-3 py-2 text-sm text-zinc-100 hover:bg-emerald-600"
      >Open</button>
    </div>
    {#if addrError}
      <div class="mt-1 text-xs text-red-400">{addrError}</div>
    {/if}
  </div>

  <!-- New group -->
  <div class="mb-4 flex items-center gap-2">
    <input
      type="text"
      bind:value={newName}
      placeholder="New group name"
      onkeydown={(e) => e.key === 'Enter' && addGroup()}
      class="w-48 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500"
    />
    <div class="flex items-center gap-1">
      {#each SWATCHES as c (c ?? 'neutral')}
        <button type="button" onclick={() => (newColor = c)} aria-label="group color"
          class="w-5 h-5 rounded-full border-2 {newColor === c ? 'border-zinc-100' : 'border-zinc-700'}"
          style="background-color: {c ?? NEUTRAL_GROUP_COLOR}"></button>
      {/each}
    </div>
    <button type="button" onclick={addGroup} disabled={!newName.trim()}
      class="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">+ Add group</button>
  </div>

  <!-- Grouped pinned wallets -->
  <div class="space-y-3">
    {#each orderedGroups as g (g.id)}
      {@const wallets = walletPinsStore.walletsInGroup(g.id)}
      <section class="rounded-lg border border-zinc-800 overflow-hidden">
        <div class="flex items-center gap-2 px-3 py-2 bg-zinc-950 border-b border-zinc-800">
          <button type="button" onclick={() => toggleCollapse(g.id)}
            class="text-zinc-500 hover:text-zinc-200 w-4 text-center" title="Collapse / expand">
            {isCollapsed(g.id) ? '▸' : '▾'}
          </button>
          <span class="inline-block w-2.5 h-2.5 rounded-full border border-zinc-600"
            style="background-color: {g.color ?? NEUTRAL_GROUP_COLOR}"></span>
          {#if editing === g.id}
            <input
              type="text"
              bind:value={editName}
              onkeydown={(e) => { if (e.key === 'Enter') { commitName(g.id); editing = null; } }}
              onblur={() => { commitName(g.id); editing = null; }}
              class="rounded border border-zinc-700 bg-zinc-950 px-2 py-0.5 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500"
            />
            <div class="flex items-center gap-1">
              {#each SWATCHES as c (c ?? 'neutral')}
                <button type="button" onclick={() => walletPinsStore.setGroupColor(g.id, c)} aria-label="set color"
                  class="w-4 h-4 rounded-full border-2 {g.color === c ? 'border-zinc-100' : 'border-zinc-700'}"
                  style="background-color: {c ?? NEUTRAL_GROUP_COLOR}"></button>
              {/each}
            </div>
            <button type="button" onclick={() => deleteGroup(g.id)}
              class="ml-1 text-xs text-red-400 hover:text-red-300">Delete</button>
          {:else}
            <span class="text-sm font-medium text-zinc-100">{g.name}</span>
            <span class="text-xs text-zinc-500">{wallets.length}</span>
            {#if g.id !== DEFAULT_GROUP_ID}
              <button type="button" onclick={() => startEdit(g)}
                class="ml-auto text-xs text-zinc-500 hover:text-zinc-200">edit</button>
            {/if}
          {/if}
        </div>
        {#if !isCollapsed(g.id)}
          <div class="divide-y divide-zinc-900">
            {#if wallets.length === 0}
              <div class="px-3 py-3 text-xs text-zinc-600">No wallets pinned to this group yet.</div>
            {:else}
              {#each wallets as w, i (w.address)}
                <div class="flex items-center justify-between px-3 py-1.5 hover:bg-zinc-900/40">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="text-[11px] text-zinc-600 tabular-nums w-6 text-right shrink-0">{i + 1}</span>
                    <WalletAddress address={w.address} auxKind="wallet" />
                  </div>
                  <button type="button" onclick={() => walletPinsStore.unpin(w.address, g.id)}
                    title="Remove from this group"
                    class="text-xs text-zinc-500 hover:text-red-400">unpin</button>
                </div>
              {/each}
            {/if}
          </div>
        {/if}
      </section>
    {/each}
  </div>
</div>
