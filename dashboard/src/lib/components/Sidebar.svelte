<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { themeStore } from '$lib/stores/theme.svelte';

  let collapsed = $state(false);
  const links = [
    { href: '/trades', label: 'Trades', short: 'T' },
    { href: '/flows', label: 'Flows', short: 'F' }
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
  <nav class="flex-1 px-2 py-3 space-y-1">
    {#each links as link (link.href)}
      {@const active = $page.url.pathname.startsWith(link.href)}
      <a
        href={link.href}
        title={link.label}
        class="block rounded text-sm transition-colors {active
          ? 'bg-zinc-800 text-zinc-50'
          : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} {collapsed
          ? 'px-0 py-2 text-center'
          : 'px-3 py-2'}"
      >
        {collapsed ? link.short : link.label}
      </a>
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
