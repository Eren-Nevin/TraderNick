<script lang="ts">
  // Token Batches admin — CRUD over the ingestion token-batch store
  // (tradernick.ingestion_token_batches). Batches group tokens for backfill
  // targeting; the live streams poll the de-duped union of every batch. Edits
  // here are written through /api/admin/config/token_batches and take effect
  // across all ingestion processes within the store's ~30s cache TTL — no
  // restart needed.

  import { onMount } from 'svelte';

  type Batch = { name: string; tokens: string[]; count: number };
  // Row view-model: the editable CSV text + a busy/dirty flag per row.
  type Row = { name: string; tokensText: string; original: string; busy: boolean };

  let rows = $state<Row[]>([]);
  let loadErr = $state<string | null>(null);
  let loading = $state(true);

  // New-batch form.
  let newName = $state('');
  let newTokens = $state('');
  let addBusy = $state(false);
  let msg = $state<string | null>(null);

  function tokensOf(text: string): string[] {
    return text
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean);
  }

  async function load() {
    loading = true;
    try {
      const res = await fetch('/api/admin/config/token_batches');
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const body = await res.json();
      const batches: Batch[] = body.batches ?? [];
      rows = batches.map((b) => {
        const text = (b.tokens ?? []).join(', ');
        return { name: b.name, tokensText: text, original: text, busy: false };
      });
      loadErr = null;
    } catch (e) {
      loadErr = String(e);
    } finally {
      loading = false;
    }
  }

  async function saveRow(row: Row) {
    row.busy = true;
    msg = null;
    try {
      const res = await fetch('/api/admin/config/token_batches', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: row.name, tokens: tokensOf(row.tokensText) })
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      msg = `Saved “${row.name}”.`;
      await load();
    } catch (e) {
      msg = `Save failed: ${e}`;
    } finally {
      row.busy = false;
    }
  }

  async function deleteRow(row: Row) {
    if (!confirm(`Remove batch “${row.name}”? Its tokens stay ingested if they're in another batch.`)) return;
    row.busy = true;
    msg = null;
    try {
      const res = await fetch(`/api/admin/config/token_batches/${encodeURIComponent(row.name)}`, {
        method: 'DELETE'
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      msg = `Removed “${row.name}”.`;
      await load();
    } catch (e) {
      msg = `Delete failed: ${e}`;
    } finally {
      row.busy = false;
    }
  }

  async function addBatch() {
    const name = newName.trim();
    if (!name) {
      msg = 'Batch name required.';
      return;
    }
    addBusy = true;
    msg = null;
    try {
      const res = await fetch('/api/admin/config/token_batches', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name, tokens: tokensOf(newTokens) })
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      msg = `Added “${name}”.`;
      newName = '';
      newTokens = '';
      await load();
    } catch (e) {
      msg = `Add failed: ${e}`;
    } finally {
      addBusy = false;
    }
  }

  // ── Token overrides (deprecated + renamed) ──────────────────────────────
  type Renamed = { old: string; new: string };
  let deprecated = $state<string[]>([]);
  let renamed = $state<Renamed[]>([]);
  let ovrErr = $state<string | null>(null);
  let newDep = $state('');
  let newRenOld = $state('');
  let newRenNew = $state('');
  let ovrBusy = $state(false);

  async function loadOverrides() {
    try {
      const res = await fetch('/api/admin/config/token_overrides');
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const body = await res.json();
      deprecated = body.deprecated ?? [];
      renamed = body.renamed ?? [];
      ovrErr = null;
    } catch (e) {
      ovrErr = String(e);
    }
  }

  async function putOverride(payload: Record<string, string>): Promise<boolean> {
    ovrBusy = true;
    msg = null;
    try {
      const res = await fetch('/api/admin/config/token_overrides', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      await loadOverrides();
      return true;
    } catch (e) {
      msg = `Failed: ${e}`;
      return false;
    } finally {
      ovrBusy = false;
    }
  }

  async function addDeprecated() {
    const token = newDep.trim().toUpperCase();
    if (!token) return;
    if (await putOverride({ kind: 'deprecated', token })) {
      msg = `Deprecated “${token}”.`;
      newDep = '';
    }
  }

  async function addRenamed() {
    const oldT = newRenOld.trim().toUpperCase();
    const newT = newRenNew.trim().toUpperCase();
    if (!oldT || !newT) {
      msg = 'Renamed needs both old and new token.';
      return;
    }
    if (await putOverride({ kind: 'renamed', token: oldT, new_token: newT })) {
      msg = `Renamed “${oldT}” → “${newT}”.`;
      newRenOld = '';
      newRenNew = '';
    }
  }

  async function removeOverride(kind: string, token: string) {
    ovrBusy = true;
    msg = null;
    try {
      const res = await fetch(
        `/api/admin/config/token_overrides/${encodeURIComponent(kind)}/${encodeURIComponent(token)}`,
        { method: 'DELETE' }
      );
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      msg = `Removed ${kind} “${token}”.`;
      await loadOverrides();
    } catch (e) {
      msg = `Delete failed: ${e}`;
    } finally {
      ovrBusy = false;
    }
  }

  onMount(() => {
    load();
    loadOverrides();
  });
</script>

<svelte:head><title>Token Batches — Admin</title></svelte:head>

<div class="p-6 max-w-4xl">
  <h1 class="text-lg font-semibold text-zinc-100">Token Batches</h1>
  <p class="text-xs text-zinc-500 mt-1 max-w-2xl">
    Batches group tokens so a backfill can target a subset. Live streams poll the
    de-duped union of every batch. Edits take effect within ~30s — no restart.
  </p>

  {#if msg}
    <div class="mt-3 text-xs text-zinc-300 bg-zinc-900 border border-zinc-800 rounded px-3 py-2">{msg}</div>
  {/if}

  <!-- Add batch -->
  <div class="mt-4 border border-zinc-800 rounded p-3 bg-zinc-950">
    <div class="text-xs font-medium text-zinc-300 mb-2">Add batch</div>
    <div class="flex flex-col gap-2 sm:flex-row sm:items-start">
      <input
        bind:value={newName}
        placeholder="Name (e.g. Majors)"
        class="w-full sm:w-48 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600"
      />
      <input
        bind:value={newTokens}
        placeholder="Tokens, comma or space separated (BTC, ETH, SOL)"
        class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600"
      />
      <button
        onclick={addBatch}
        disabled={addBusy}
        class="shrink-0 px-3 py-1 text-xs rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-50"
      >{addBusy ? 'Adding…' : 'Add'}</button>
    </div>
  </div>

  <!-- Existing batches -->
  <div class="mt-4">
    {#if loading}
      <div class="text-xs text-zinc-500">Loading…</div>
    {:else if loadErr}
      <div class="text-xs text-red-400">Failed to load: {loadErr}</div>
    {:else if rows.length === 0}
      <div class="text-xs text-zinc-500">No batches yet.</div>
    {:else}
      <table class="w-full text-xs">
        <thead>
          <tr class="text-left text-zinc-500 border-b border-zinc-800">
            <th class="py-1.5 pr-3 font-medium w-44">Name</th>
            <th class="py-1.5 pr-3 font-medium w-14 text-right">Count</th>
            <th class="py-1.5 pr-3 font-medium">Tokens</th>
            <th class="py-1.5 font-medium w-32 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.name)}
            {@const dirty = row.tokensText.trim() !== row.original.trim()}
            <tr class="border-b border-zinc-900 align-top">
              <td class="py-2 pr-3 text-zinc-200 font-mono">{row.name}</td>
              <td class="py-2 pr-3 text-right text-zinc-400">{tokensOf(row.tokensText).length}</td>
              <td class="py-2 pr-3">
                <textarea
                  bind:value={row.tokensText}
                  rows="2"
                  class="w-full bg-zinc-900 border rounded px-2 py-1 text-zinc-100 font-mono leading-snug resize-y
                         {dirty ? 'border-amber-600' : 'border-zinc-800'}"
                ></textarea>
              </td>
              <td class="py-2 text-right whitespace-nowrap">
                <button
                  onclick={() => saveRow(row)}
                  disabled={row.busy || !dirty}
                  class="px-2 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-100 disabled:opacity-40"
                >Save</button>
                <button
                  onclick={() => deleteRow(row)}
                  disabled={row.busy}
                  class="px-2 py-1 rounded bg-red-900/60 hover:bg-red-800 text-red-200 disabled:opacity-40"
                >Delete</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <!-- Token overrides -->
  <div class="mt-8 border-t border-zinc-800 pt-5">
    <h2 class="text-sm font-semibold text-zinc-100">Token Overrides</h2>
    <p class="text-xs text-zinc-500 mt-1 max-w-2xl">
      <span class="text-zinc-300">Deprecated</span> tokens are dropped from the
      <em>live</em> roster (still backfillable).
      <span class="text-zinc-300">Renamed</span> tokens are swapped old→new for
      live; backfill keeps both. Applies to Binance + Hyperliquid.
    </p>
    {#if ovrErr}
      <div class="mt-2 text-xs text-red-400">Failed to load overrides: {ovrErr}</div>
    {/if}

    <div class="mt-4 grid gap-4 md:grid-cols-2">
      <!-- Deprecated -->
      <div class="border border-zinc-800 rounded p-3 bg-zinc-950">
        <div class="text-xs font-medium text-zinc-300 mb-2">Deprecated tokens</div>
        <div class="flex gap-2">
          <input
            bind:value={newDep}
            placeholder="Token (e.g. PAXG)"
            onkeydown={(e) => e.key === 'Enter' && addDeprecated()}
            class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600"
          />
          <button
            onclick={addDeprecated}
            disabled={ovrBusy}
            class="shrink-0 px-3 py-1 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-100 disabled:opacity-40"
          >Add</button>
        </div>
        <div class="mt-2 flex flex-wrap gap-1.5">
          {#each deprecated as t (t)}
            <span class="inline-flex items-center gap-1 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs font-mono text-zinc-200">
              {t}
              <button
                onclick={() => removeOverride('deprecated', t)}
                disabled={ovrBusy}
                class="text-zinc-500 hover:text-red-400 disabled:opacity-40"
                title="Remove">×</button>
            </span>
          {:else}
            <span class="text-xs text-zinc-600">none</span>
          {/each}
        </div>
      </div>

      <!-- Renamed -->
      <div class="border border-zinc-800 rounded p-3 bg-zinc-950">
        <div class="text-xs font-medium text-zinc-300 mb-2">Renamed tokens (old → new)</div>
        <div class="flex gap-2">
          <input
            bind:value={newRenOld}
            placeholder="Old (MKR)"
            class="w-24 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600"
          />
          <span class="text-zinc-600 self-center">→</span>
          <input
            bind:value={newRenNew}
            placeholder="New (SKY)"
            onkeydown={(e) => e.key === 'Enter' && addRenamed()}
            class="w-24 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600"
          />
          <button
            onclick={addRenamed}
            disabled={ovrBusy}
            class="shrink-0 px-3 py-1 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-100 disabled:opacity-40"
          >Add</button>
        </div>
        <div class="mt-2 flex flex-wrap gap-1.5">
          {#each renamed as r (r.old)}
            <span class="inline-flex items-center gap-1 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs font-mono text-zinc-200">
              {r.old} → {r.new}
              <button
                onclick={() => removeOverride('renamed', r.old)}
                disabled={ovrBusy}
                class="text-zinc-500 hover:text-red-400 disabled:opacity-40"
                title="Remove">×</button>
            </span>
          {:else}
            <span class="text-xs text-zinc-600">none</span>
          {/each}
        </div>
      </div>
    </div>
  </div>
</div>
