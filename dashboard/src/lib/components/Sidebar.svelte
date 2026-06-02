<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { themeStore } from '$lib/stores/theme.svelte';
  import { pagesStore } from '$lib/stores/pages.svelte';

  let collapsed = $state(false);

  // The Examples section is the static curated category pages — each
  // hosts one consolidated picker for a single category. Order matches
  // the previous flat sidebar.
  type ExampleLink = { href: string; label: string; short: string };
  const examples: ExampleLink[] = [
    { href: '/trades',  label: 'Trades',  short: 'T' },
    { href: '/flows',   label: 'Flows',   short: 'F' },
    { href: '/lending', label: 'Lending', short: 'L' },
    // DeX uses 'X' (collapsed letter) so it doesn't collide with Dashboard's
    // 'D' chip when the sidebar is in icon-only mode.
    { href: '/dex',     label: 'DeX',     short: 'X' },
    { href: '/perp',    label: 'Perp',    short: 'P' },
    { href: '/staking', label: 'Staking', short: 'S' }
  ];

  onMount(() => {
    themeStore.hydrate();
    pagesStore.hydrate();
  });

  function activePage(): string | null {
    // /dashboard/{id} – the active page is the path segment after /dashboard/.
    // Used to highlight the matching sidebar entry and to pick a fallback
    // navigation target on delete.
    const m = $page.url.pathname.match(/^\/dashboard\/([^/]+)/);
    return m ? m[1] : null;
  }

  function addPage() {
    const name = (typeof window !== 'undefined'
      ? window.prompt('Page name', `Page ${pagesStore.pages.length + 1}`)
      : null);
    if (name === null) return; // user cancelled
    const p = pagesStore.add(name);
    goto(`/dashboard/${p.id}`);
  }

  function renamePage(id: string, current: string, ev: Event) {
    ev.preventDefault();
    ev.stopPropagation();
    if (typeof window === 'undefined') return;
    const name = window.prompt('Rename page', current);
    if (name === null) return;
    pagesStore.rename(id, name);
  }

  function deletePage(id: string, name: string, ev: Event) {
    ev.preventDefault();
    ev.stopPropagation();
    if (pagesStore.pages.length <= 1) return;
    if (typeof window === 'undefined') return;
    if (!window.confirm(`Delete page "${name}"?`)) return;
    const wasActive = activePage() === id;
    pagesStore.remove(id);
    // If we just removed the active page, fall back to the first remaining.
    if (wasActive) {
      const first = pagesStore.pages[0];
      if (first) goto(`/dashboard/${first.id}`);
    }
  }
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
    <!-- =========================================================
         User-created Dashboard pages.
         Top section: a list of pages from pagesStore + Add button.
         ========================================================= -->
    {#if !collapsed}
      <div class="px-3 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 select-none">
        Pages
      </div>
    {/if}

    {#each pagesStore.pages as p (p.id)}
      {@const isActive = activePage() === p.id}
      {#if collapsed}
        <a
          href={`/dashboard/${p.id}`}
          title={p.name}
          class="block rounded text-sm transition-colors {isActive
            ? 'bg-zinc-800 text-zinc-50'
            : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} px-0 py-2 text-center"
        >{p.name.slice(0, 1).toUpperCase()}</a>
      {:else}
        <!-- Page row: link + inline rename/delete buttons (visible on hover).
             The buttons sit inside the same row but stop event propagation
             so clicking them doesn't navigate. -->
        <div class="group relative">
          <a
            href={`/dashboard/${p.id}`}
            title={p.name}
            class="flex items-center justify-between gap-1 rounded text-sm transition-colors {isActive
              ? 'bg-zinc-800 text-zinc-50'
              : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} px-3 py-2 pr-12"
          >
            <span class="truncate">{p.name}</span>
          </a>
          <div class="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              title="Rename page"
              onclick={(e) => renamePage(p.id, p.name, e)}
              class="w-5 h-5 flex items-center justify-center rounded text-xs text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800"
              aria-label="Rename page"
            >✎</button>
            {#if pagesStore.pages.length > 1}
              <button
                type="button"
                title="Delete page"
                onclick={(e) => deletePage(p.id, p.name, e)}
                class="w-5 h-5 flex items-center justify-center rounded text-xs text-zinc-400 hover:text-red-300 hover:bg-zinc-800"
                aria-label="Delete page"
              >×</button>
            {/if}
          </div>
        </div>
      {/if}
    {/each}

    <!-- Add page action. Same style as the page links so it sits in-line. -->
    <button
      type="button"
      onclick={addPage}
      title="Add page"
      class="w-full block rounded text-sm transition-colors text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100 {collapsed
        ? 'px-0 py-2 text-center'
        : 'px-3 py-2 text-left'}"
    >
      {collapsed ? '+' : '+ Add page'}
    </button>

    <!-- =========================================================
         Separator + Examples section.
         The static category pages used to be top-level entries; now
         they're grouped under a single "Examples" heading.
         ========================================================= -->
    <div class="mx-2 my-2 border-t border-zinc-800"></div>

    {#if !collapsed}
      <div class="px-3 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 select-none">
        Examples
      </div>
    {/if}

    {#each examples as link (link.href)}
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
