<script lang="ts">
  // Shared wallet-address cell. Renders the truncated address as a button
  // (click = copy, middle-click = open wallet page or Arkham), and on hover
  // shows the wallet's group pins as colored capsules in a fixed-position
  // popover (escapes table overflow clipping). Display-only for now.

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
    class: extraClass = ''
  }: {
    address: string;
    auxKind?: 'wallet' | 'arkham';
    // For auxKind 'wallet': pre-select this as-of day (YYYY-MM-DD) on the wallet
    // page opened by middle-click. Ignored for 'arkham'.
    snapshot?: string | null;
    class?: string;
  } = $props();

  let copied = $state(false);
  const aux = $derived(auxKind === 'wallet' ? onAuxClickWalletHl(address, snapshot) : onAuxClickArkham(address));
  const pins = $derived(walletPinsStore.groupsForWallet(address));

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
    if (pins.length === 0) return;
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    pop = { left: r.left, top: r.bottom + 4 };
  }
  function hidePop() {
    pop = null;
  }
</script>

<!-- No native `title`: its tooltip overlaps the pin-capsule hover popover. -->
<button
  type="button"
  onclick={copyAddr}
  onauxclick={aux}
  onmousedown={onMouseDownSuppressMiddle}
  onmouseenter={showPop}
  onmouseleave={hidePop}
  class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer {extraClass}"
>{copied ? '✓ copied' : truncateAddr(address)}</button>

{#if pop && pins.length}
  <div
    class="fixed z-50 pointer-events-none flex flex-wrap gap-1 rounded-md border border-zinc-700 bg-zinc-900/95 px-2 py-1.5 shadow-lg max-w-[16rem]"
    style="left: {pop.left}px; top: {pop.top}px"
  >
    {#each pins as g (g.id)}
      <span
        class="text-[10px] leading-none px-1.5 py-1 rounded-full text-zinc-100 border"
        style="background-color: {(g.color ?? NEUTRAL_GROUP_COLOR) + '33'}; border-color: {g.color ?? NEUTRAL_GROUP_COLOR}"
      >{g.name}</span>
    {/each}
  </div>
{/if}
