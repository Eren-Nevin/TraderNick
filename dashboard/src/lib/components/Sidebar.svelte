<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { themeStore } from '$lib/stores/theme.svelte';

  let collapsed = $state(false);

  // Two kinds of nav entries:
  //   - flat link (label + href + short)
  //   - group header (label + short + sub-links). Group header itself is
  //     NOT a link — it's a section label; only the sub-links navigate.
  type FlatLink = { kind: 'link'; href: string; label: string; short: string };
  type SubLink  = { href: string; label: string };
  type Group    = { kind: 'group'; label: string; short: string; links: SubLink[] };
  type Entry    = FlatLink | Group;

  const entries: Entry[] = [
    { kind: 'link',  href: '/trades', label: 'Trades', short: 'T' },
    { kind: 'link',  href: '/flows',  label: 'Flows',  short: 'F' },
    {
      kind: 'group', label: 'Lending', short: 'L',
      links: [
        { href: '/lending/aave',   label: 'AAVE' },
        { href: '/lending/morpho', label: 'Morpho' },
        { href: '/lending/spark',  label: 'Spark' }
      ]
    },
    {
      kind: 'group', label: 'DeX', short: 'D',
      links: [
        { href: '/dex/uniswap',   label: 'Uniswap' },
        { href: '/dex/aerodrome', label: 'Aerodrome' }
      ]
    },
    {
      kind: 'group', label: 'Perp', short: 'P',
      links: [
        { href: '/perp/gmx',         label: 'GMX' },
        { href: '/perp/hyperliquid', label: 'Hyperliquid' }
      ]
    },
    {
      kind: 'group', label: 'Staking', short: 'S',
      links: [
        { href: '/staking/lido', label: 'Lido' }
      ]
    }
  ];

  onMount(() => themeStore.hydrate());
</script>

<aside
  class="shrink-0 h-full border-r border-zinc-800 bg-zinc-950 flex flex-col"
  class:w-56={!collapsed}
  class:w-12={collapsed}
>
  <div
    class="flex items-center justify-between gap-2 px-3 py-4 border-b border-zinc-800 h-[68px]"
  >
    {#if !collapsed}
      <div class="min-w-0">
        <div class="text-lg font-semibold tracking-wide truncate">TraderNick</div>
        <div class="text-[10px] uppercase tracking-widest text-zinc-500">Phase 1</div>
      </div>
    {/if}
    <button
      type="button"
      onclick={() => (collapsed = !collapsed)}
      title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      class="w-6 h-6 flex items-center justify-center rounded text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 shrink-0"
    >
      {collapsed ? '»' : '«'}
    </button>
  </div>
  <nav class="flex-1 px-2 py-3 space-y-1 overflow-y-auto">
    {#each entries as entry, idx (idx)}
      {#if entry.kind === 'link'}
        {@const active = $page.url.pathname.startsWith(entry.href)}
        <a
          href={entry.href}
          title={entry.label}
          class="block rounded text-sm transition-colors {active
            ? 'bg-zinc-800 text-zinc-50'
            : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} {collapsed
            ? 'px-0 py-2 text-center'
            : 'px-3 py-2'}"
        >
          {collapsed ? entry.short : entry.label}
        </a>
      {:else}
        <!-- Group: non-clickable header + indented sub-links. When collapsed
             the header chip shows the short letter (still non-clickable) and
             sub-links render as their own short chips below it. -->
        {#if collapsed}
          <div
            title={entry.label}
            class="px-0 py-1 text-center text-[10px] font-semibold uppercase tracking-wider text-zinc-500"
          >{entry.short}</div>
          {#each entry.links as sub (sub.href)}
            {@const active = $page.url.pathname.startsWith(sub.href)}
            <a
              href={sub.href}
              title={`${entry.label} · ${sub.label}`}
              class="block rounded text-sm transition-colors {active
                ? 'bg-zinc-800 text-zinc-50'
                : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100'} px-0 py-2 text-center"
            >{sub.label.slice(0, 1)}</a>
          {/each}
        {:else}
          <div class="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 select-none">
            {entry.label}
          </div>
          {#each entry.links as sub (sub.href)}
            {@const active = $page.url.pathname.startsWith(sub.href)}
            <a
              href={sub.href}
              title={`${entry.label} · ${sub.label}`}
              class="block rounded text-sm transition-colors px-6 py-1.5 {active
                ? 'bg-zinc-800 text-zinc-50'
                : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100'}"
            >{sub.label}</a>
          {/each}
        {/if}
      {/if}
    {/each}
  </nav>
  <div class="px-2 py-3 border-t border-zinc-800">
    <button
      type="button"
      onclick={() => themeStore.toggle()}
      title={themeStore.theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      class="w-full flex items-center justify-center gap-2 rounded text-sm text-zinc-400 hover:text-zinc-100 hover:bg-zinc-900 {collapsed ? 'px-0 py-2' : 'px-3 py-2'}"
    >
      <span class="text-base leading-none">{themeStore.theme === 'dark' ? '☀' : '🌙'}</span>
      {#if !collapsed}
        <span class="text-xs">{themeStore.theme === 'dark' ? 'Light' : 'Dark'}</span>
      {/if}
    </button>
  </div>
</aside>
