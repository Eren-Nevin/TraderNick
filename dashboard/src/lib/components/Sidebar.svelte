<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { themeStore } from '$lib/stores/theme.svelte';
  import { pagesStore } from '$lib/stores/pages.svelte';

  let collapsed = $state(false);

  // Inline-edit state for page rename / create-then-name. When `editingId`
  // matches a page, its row swaps the label for an <input> bound to
  // `editingName`. `editingIsNew` distinguishes a rename from a fresh
  // creation — cancelling a fresh creation discards the page entirely
  // (so the user can back out without leaving a junk "Page 3" behind).
  let editingId = $state<string | null>(null);
  let editingName = $state('');
  let editingIsNew = $state(false);

  // Active state for the Filters nav entry (route /filters).
  let filtersActive = $derived($page.url.pathname.startsWith('/filters'));

  onMount(() => {
    themeStore.hydrate();
    pagesStore.hydrate();
  });

  function activePage(): string | null {
    // /dashboard/{id} – the active page is the path segment after /dashboard/.
    const m = $page.url.pathname.match(/^\/dashboard\/([^/]+)/);
    return m ? m[1] : null;
  }

  function startRename(id: string, current: string, ev: Event) {
    ev.preventDefault();
    ev.stopPropagation();
    editingId = id;
    editingName = current;
    editingIsNew = false;
  }

  function addPage() {
    // Create with a placeholder name and immediately enter edit mode for it.
    // Cancelling (Escape / ×) will roll back the creation.
    const p = pagesStore.add(`Page ${pagesStore.pages.length + 1}`);
    editingId = p.id;
    editingName = p.name;
    editingIsNew = true;
    goto(`/dashboard/${p.id}`);
  }

  function duplicatePage(id: string, ev: Event) {
    ev.preventDefault();
    ev.stopPropagation();
    const p = pagesStore.duplicate(id);
    if (!p) return;
    // Open the copy's name for editing, then navigate to it. editingIsNew=false
    // so cancelling keeps the duplicated page (with its "… (copy)" name) instead
    // of discarding it.
    editingId = p.id;
    editingName = p.name;
    editingIsNew = false;
    goto(`/dashboard/${p.id}`);
  }

  function commitEdit() {
    if (!editingId) return;
    const name = editingName.trim();
    if (name) pagesStore.rename(editingId, name);
    editingId = null;
    editingName = '';
    editingIsNew = false;
  }

  function cancelEdit() {
    if (editingIsNew && editingId) {
      // Discard the freshly-created page entirely. If it was the active
      // route, fall back to the first remaining page.
      const id = editingId;
      const wasActive = activePage() === id;
      pagesStore.remove(id);
      if (wasActive && pagesStore.pages[0]) {
        goto(`/dashboard/${pagesStore.pages[0].id}`);
      }
    }
    editingId = null;
    editingName = '';
    editingIsNew = false;
  }

  function onEditKey(ev: KeyboardEvent) {
    if (ev.key === 'Enter') { ev.preventDefault(); commitEdit(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); cancelEdit(); }
  }

  // Svelte action: focus + select the input when it mounts so the user can
  // immediately overwrite the placeholder or current name.
  function focusInput(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  function deletePage(id: string, name: string, ev: Event) {
    ev.preventDefault();
    ev.stopPropagation();
    if (pagesStore.pages.length <= 1) return;
    if (typeof window === 'undefined') return;
    if (!window.confirm(`Delete page "${name}"?`)) return;
    const wasActive = activePage() === id;
    pagesStore.remove(id);
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
         ========================================================= -->
    {#if !collapsed}
      <div class="px-3 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 select-none">
        Pages
      </div>
    {/if}

    {#each pagesStore.pages as p (p.id)}
      {@const isActive = activePage() === p.id}
      {@const isEditing = editingId === p.id}
      {#if collapsed}
        <a
          href={`/dashboard/${p.id}`}
          title={p.name}
          class="block rounded text-sm transition-colors {isActive
            ? 'bg-zinc-800 text-zinc-50'
            : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} px-0 py-2 text-center"
        >{p.name.slice(0, 1).toUpperCase()}</a>
      {:else if isEditing}
        <!-- Edit row: input + confirm/cancel buttons. Enter confirms,
             Escape cancels. Inline check (✓) commits; × discards. -->
        <div class="flex items-center gap-1 rounded bg-zinc-900 px-2 py-1">
          <input
            type="text"
            bind:value={editingName}
            onkeydown={onEditKey}
            use:focusInput
            class="flex-1 min-w-0 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
            placeholder="Page name"
            aria-label="Page name"
          />
          <button
            type="button"
            title="Save (Enter)"
            onclick={commitEdit}
            class="w-5 h-5 flex items-center justify-center rounded text-xs text-zinc-300 hover:text-emerald-300 hover:bg-zinc-800"
            aria-label="Save page name"
          >✓</button>
          <button
            type="button"
            title="Cancel (Esc)"
            onclick={cancelEdit}
            class="w-5 h-5 flex items-center justify-center rounded text-xs text-zinc-400 hover:text-red-300 hover:bg-zinc-800"
            aria-label="Cancel"
          >×</button>
        </div>
      {:else}
        <!-- Display row: link + inline rename/delete buttons (on hover). -->
        <div class="group relative">
          <a
            href={`/dashboard/${p.id}`}
            title={p.name}
            class="flex items-center justify-between gap-1 rounded text-sm transition-colors {isActive
              ? 'bg-zinc-800 text-zinc-50'
              : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} px-3 py-2 pr-[4.5rem]"
          >
            <span class="truncate">{p.name}</span>
          </a>
          <div class="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              title="Rename page"
              onclick={(e) => startRename(p.id, p.name, e)}
              class="w-5 h-5 flex items-center justify-center rounded text-xs text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800"
              aria-label="Rename page"
            >✎</button>
            <button
              type="button"
              title="Duplicate page (with all its charts)"
              onclick={(e) => duplicatePage(p.id, e)}
              class="w-5 h-5 flex items-center justify-center rounded text-xs text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800"
              aria-label="Duplicate page"
            >⧉</button>
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
  </nav>

  <!-- =========================================================
       First-class wallet filters (create / compose / reuse).
       ========================================================= -->
  <div class="px-2 py-3 border-t border-zinc-800">
    <a
      href="/filters"
      title="Wallet filters"
      class="w-full flex items-center gap-2 rounded text-sm transition-colors {filtersActive
        ? 'bg-zinc-800 text-zinc-50'
        : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'} {collapsed ? 'px-0 py-2 justify-center' : 'px-3 py-2'}"
    >
      <span class="text-base leading-none">⛃</span>
      {#if !collapsed}<span>Filters</span>{/if}
    </a>
  </div>

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
