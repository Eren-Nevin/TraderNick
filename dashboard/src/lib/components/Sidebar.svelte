<script lang="ts">
  import { page } from '$app/stores';
  const links = [{ href: '/trades', label: 'Trades' }];
</script>

<aside class="w-56 shrink-0 h-full border-r border-zinc-800 bg-zinc-950 flex flex-col">
  <div class="px-4 py-4 border-b border-zinc-800">
    <div class="text-lg font-semibold tracking-wide">TraderNick</div>
    <div class="text-[10px] uppercase tracking-widest text-zinc-500">Phase 1</div>
  </div>
  <nav class="flex-1 px-2 py-3 space-y-1">
    {#each links as link (link.href)}
      {@const active = $page.url.pathname.startsWith(link.href)}
      <a
        href={link.href}
        class="block px-3 py-2 rounded text-sm transition-colors {active
          ? 'bg-zinc-800 text-zinc-50'
          : 'text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100'}"
      >
        {link.label}
      </a>
    {/each}
  </nav>
</aside>
