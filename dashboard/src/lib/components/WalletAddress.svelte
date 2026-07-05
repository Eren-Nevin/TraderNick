<script lang="ts">
  // Shared wallet-address cell. Renders the truncated address as a button
  // (click = copy, middle-click = open wallet page or Arkham) followed by small
  // inline capsules:
  //   - GROUP pins (from walletPinsStore) as rounded-FULL pills, colored by the
  //     group's color. Up to 3 inline; a "…" pill if there are more.
  //   - TAGS / categories (Perp, Deposit, … from the wallets table, passed in by
  //     the table) as rounded-SQUARE chips — a different shape so they read as a
  //     distinct system from groups. Up to 3 inline; a "…" chip if more.
  // On hover a fixed-position popover shows the FULL set of both (escapes table
  // overflow clipping). Display-only.

  import {
    truncateAddr,
    onAuxClickWalletHl,
    onAuxClickArkham,
    onMouseDownSuppressMiddle
  } from '$lib/arkham';
  import { walletPinsStore, NEUTRAL_GROUP_COLOR } from '$lib/stores/walletPins.svelte';

  let {
    address,
    auxKind = 'arkham',
    snapshot = null,
    token = null,
    tags = [],
    class: extraClass = ''
  }: {
    address: string;
    auxKind?: 'wallet' | 'arkham';
    // For auxKind 'wallet': pre-select this as-of day (YYYY-MM-DD) on the wallet
    // page opened by middle-click. Ignored for 'arkham'.
    snapshot?: string | null;
    // For auxKind 'wallet': pre-select this token in the positions table on the
    // opened wallet page (?token=). Ignored for 'arkham'. Optional.
    token?: string | null;
    // Wallet categories/labels from the (separate) wallets-table tagging system
    // — shown as square chips to differentiate from group pins. Optional.
    tags?: string[];
    class?: string;
  } = $props();

  let copied = $state(false);
  const aux = $derived(auxKind === 'wallet' ? onAuxClickWalletHl(address, snapshot, token) : onAuxClickArkham(address));
  const pins = $derived(walletPinsStore.groupsForWallet(address));
  const MAX_INLINE = 3;

  async function copyAddr() {
    try {
      await navigator.clipboard.writeText(address);
      copied = true;
      setTimeout(() => (copied = false), 1000);
    } catch {
      /* clipboard unavailable */
    }
  }

  // Fixed-position hover popover (so it isn't clipped by overflow-auto tables).
  let pop = $state<{ left: number; top: number } | null>(null);
  function showPop(e: MouseEvent) {
    if (pins.length === 0 && tags.length === 0) return;
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    pop = { left: r.left, top: r.bottom + 4 };
  }
  function hidePop() {
    pop = null;
  }
</script>

<!-- No native `title`: its tooltip overlaps the capsule hover popover. -->
<span
  class="inline-flex items-center gap-1 align-middle"
  onmouseenter={showPop}
  onmouseleave={hidePop}
  role="group"
>
  <button
    type="button"
    onclick={copyAddr}
    onauxclick={aux}
    onmousedown={onMouseDownSuppressMiddle}
    class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer {extraClass}"
  >{copied ? '✓ copied' : truncateAddr(address)}</button>

  {#if pins.length || tags.length}
    <span class="inline-flex items-center gap-0.5 whitespace-nowrap">
      {#each pins.slice(0, MAX_INLINE) as g (g.id)}
        <span
          class="text-[9px] leading-none px-1 py-0.5 rounded-full text-zinc-100 border"
          style="background-color: {(g.color ?? NEUTRAL_GROUP_COLOR) + '33'}; border-color: {g.color ?? NEUTRAL_GROUP_COLOR}"
        >{g.name}</span>
      {/each}
      {#if pins.length > MAX_INLINE}
        <span class="text-[9px] leading-none px-1 py-0.5 rounded-full text-zinc-300 border border-zinc-600 bg-zinc-800">…</span>
      {/if}
      {#each tags.slice(0, MAX_INLINE) as t (t)}
        <span class="text-[9px] leading-none px-1 py-0.5 rounded-[2px] border border-amber-700/70 bg-amber-900/30 text-amber-200 uppercase tracking-wide">{t}</span>
      {/each}
      {#if tags.length > MAX_INLINE}
        <span class="text-[9px] leading-none px-1 py-0.5 rounded-[2px] border border-amber-700/70 bg-amber-900/30 text-amber-200">…</span>
      {/if}
    </span>
  {/if}
</span>

{#if pop && (pins.length || tags.length)}
  <div
    class="fixed z-50 pointer-events-none flex flex-wrap gap-1 rounded-md border border-zinc-700 bg-zinc-900/95 px-2 py-1.5 shadow-lg max-w-[20rem]"
    style="left: {pop.left}px; top: {pop.top}px"
  >
    {#each pins as g (g.id)}
      <span
        class="text-[10px] leading-none px-1.5 py-1 rounded-full text-zinc-100 border"
        style="background-color: {(g.color ?? NEUTRAL_GROUP_COLOR) + '33'}; border-color: {g.color ?? NEUTRAL_GROUP_COLOR}"
      >{g.name}</span>
    {/each}
    {#each tags as t (t)}
      <span class="text-[10px] leading-none px-1.5 py-1 rounded-[2px] border border-amber-700/70 bg-amber-900/30 text-amber-200 uppercase tracking-wide">{t}</span>
    {/each}
  </div>
{/if}
