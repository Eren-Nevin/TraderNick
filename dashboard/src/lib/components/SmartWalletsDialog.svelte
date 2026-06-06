<script lang="ts">
  // Modal showing the wallets a SmartSelector picked on one day. Triggered
  // by clicking on the wallet-count line on an hl_smart_oi chart. Each
  // wallet row:
  //   - left-click → copy address to clipboard (toast feedback inline)
  //   - middle-click → open https://www.coinglass.com/hyperliquid/<address>
  //     in a new tab (browser default — we just provide the link target)
  // Hide via the ✕, the backdrop, or Escape.

  type Props = {
    open: boolean;
    /** Loaded list of wallet addresses (may be empty during fetch). */
    wallets: string[];
    /** Loading state — true while the smart_wallets fetch is in flight. */
    loading?: boolean;
    /** Error message if the fetch failed. */
    error?: string | null;
    /** ISO date the wallets are for (display label only). */
    day?: string;
    /** Token context (shown for clarity). */
    token?: string;
    /** Close handler. */
    onClose: () => void;
  };

  let {
    open,
    wallets,
    loading = false,
    error: errMsg = null,
    day = '',
    token = '',
    onClose
  }: Props = $props();

  let toast = $state<{ text: string; at: number } | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function flashToast(text: string) {
    toast = { text, at: Date.now() };
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast = null; }, 1200);
  }

  async function copyAddress(w: string) {
    try {
      await navigator.clipboard.writeText(w);
      flashToast(`Copied ${w.slice(0, 6)}…${w.slice(-4)}`);
    } catch {
      flashToast('Copy failed');
    }
  }

  function coinglassUrl(w: string): string {
    // Coinglass HL wallet page — same URL shape the user requested. The
    // anchor target="_blank" + middle-click both honour this.
    return `https://www.coinglass.com/hyperliquid/${w}`;
  }

  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') onClose();
  }
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    role="dialog"
    aria-modal="true"
    onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
    tabindex="-1"
  >
    <div class="w-[42rem] max-w-[90vw] max-h-[85vh] bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl flex flex-col text-sm">
      <header class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
        <div class="flex items-center gap-2">
          <span class="text-zinc-300 font-medium">Smart wallets</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-400">{token}</span>{/if}
          {#if day}<span class="text-zinc-500">·</span><span class="text-zinc-400">{day}</span>{/if}
        </div>
        <button
          type="button"
          class="text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5"
          onclick={onClose}
          aria-label="Close"
        >✕</button>
      </header>

      <div class="px-4 py-2 text-[11px] text-zinc-500 border-b border-zinc-800">
        <span class="text-zinc-400">Click</span> to copy address ·
        <span class="text-zinc-400">middle-click</span> (or Ctrl-click)
        to open on Coinglass in a new tab.
      </div>

      <div class="flex-1 overflow-auto">
        {#if loading}
          <div class="px-4 py-6 text-zinc-400 text-center">Loading wallets…</div>
        {:else if errMsg}
          <div class="px-4 py-6 text-red-400 text-center">{errMsg}</div>
        {:else if wallets.length === 0}
          <div class="px-4 py-6 text-zinc-500 text-center">No wallets passed the criteria on this day.</div>
        {:else}
          <table class="w-full text-xs font-mono">
            <thead class="text-zinc-500 text-[10px] uppercase tracking-widest">
              <tr class="border-b border-zinc-800">
                <th class="px-4 py-1.5 text-left">#</th>
                <th class="px-4 py-1.5 text-left">Address</th>
                <th class="px-2 py-1.5 text-right"></th>
              </tr>
            </thead>
            <tbody>
              {#each wallets as w, i (w)}
                <tr class="border-b border-zinc-800 hover:bg-zinc-900/60">
                  <td class="px-4 py-1.5 text-zinc-500 tabular-nums w-12">{i + 1}</td>
                  <td class="px-4 py-1.5">
                    <!-- Anchor so middle-click + Ctrl-click open the
                         Coinglass URL via the browser's default new-tab
                         behaviour. Left-click is intercepted (preventDefault)
                         and routed to copy-to-clipboard instead. -->
                    <a
                      href={coinglassUrl(w)}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-zinc-100 hover:text-emerald-300 break-all"
                      onclick={(e) => { e.preventDefault(); copyAddress(w); }}
                      title="Click to copy · middle-click / Ctrl-click to open Coinglass"
                    >{w}</a>
                  </td>
                  <td class="px-2 py-1.5 text-right">
                    <a
                      href={coinglassUrl(w)}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-zinc-500 hover:text-emerald-300"
                      title="Open Coinglass in new tab"
                    >↗</a>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      {#if wallets.length > 0 && !loading}
        <footer class="px-4 py-1.5 border-t border-zinc-800 text-[11px] text-zinc-500 text-right">
          {wallets.length} wallet{wallets.length === 1 ? '' : 's'}
        </footer>
      {/if}
    </div>
  </div>

  {#if toast}
    <div class="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] bg-zinc-900 border border-zinc-700 rounded-md px-3 py-1.5 text-xs text-zinc-200 shadow-lg pointer-events-none">
      {toast.text}
    </div>
  {/if}
{/if}
