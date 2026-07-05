<script lang="ts">
  // One backfill form. Renders the shared since/until/force inputs at top,
  // then the form spec's dynamic fields (multiselect / pair-multiselect /
  // tokens-csv / pools-csv), then a "Kick" button + status message.
  //
  // Used both on Overview (dropdown picks which spec to show) and on
  // Backfill/{provider} (one instance per spec, stacked vertically).
  //
  // Stand-alone in its since/until/force state — each form has its own
  // window. (On the per-provider page Binance has 5 forms, each
  // independently kickable; sharing the date pickers would leak state.)

  import { onMount } from 'svelte';
  import { BACKFILL_FORMS, type BackfillFormSpec } from '$lib/admin/backfill_forms';

  type Props = {
    form: BackfillFormSpec;
    // Called by parent to refresh jobs table after submit.
    onSubmitted?: () => void;
  };
  let { form, onSubmitted }: Props = $props();

  // Ingestion token batches, fetched once for any form that has a
  // 'token-batches' field. Selecting batches → expanded to their union of
  // tokens (sent as the `tokens` arg). Batches are an ingestion concept;
  // source of truth is the ingestion config, surfaced via this endpoint.
  type TokenBatch = { name: string; tokens: string[]; count: number };
  let batches = $state<TokenBatch[]>([]);
  function batchTokens(name: string): string[] {
    return batches.find((b) => b.name === name)?.tokens ?? [];
  }

  onMount(async () => {
    if (!form.fields.some((f) => f.kind === 'token-batches')) return;
    try {
      const res = await fetch('/api/admin/config/token_batches');
      if (res.ok) {
        batches = ((await res.json()).batches ?? []) as TokenBatch[];
        // Seed any token-batches field that hasn't been touched yet → all batches.
        for (const field of form.fields) {
          if (field.kind === 'token-batches') {
            const cur = fieldValues[field.name] as string[] | undefined;
            if (!cur || cur.length === 0) {
              fieldValues = { ...fieldValues, [field.name]: batches.map((b) => b.name) };
            }
          }
        }
      }
    } catch {
      /* leave batches empty; the field shows a loading/error hint */
    }
  });

  // Default `since` = yesterday 00:00:00 UTC, rendered in local time for the
  // datetime-local input. new Date(fSince).toISOString() round-trips back to
  // 00:00:00Z yesterday regardless of the user's timezone.
  function defaultSinceLocal(): string {
    const now = new Date();
    const utcYesterdayMidnight = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - 1, 0, 0, 0, 0),
    );
    const pad = (n: number) => n.toString().padStart(2, '0');
    return (
      `${utcYesterdayMidnight.getFullYear()}-${pad(utcYesterdayMidnight.getMonth() + 1)}-` +
      `${pad(utcYesterdayMidnight.getDate())}T` +
      `${pad(utcYesterdayMidnight.getHours())}:${pad(utcYesterdayMidnight.getMinutes())}`
    );
  }

  let fSince = $state(defaultSinceLocal());
  let fUntil = $state('');
  let fForce = $state(false);
  let fieldValues = $state<Record<string, string[] | string>>({});
  let submitMsg = $state<string | null>(null);

  function seedDefaults() {
    const next: Record<string, string[] | string> = {};
    for (const field of form.fields) {
      if (field.kind === 'token-batches') {
        // Default = every batch selected (mirrors live = all batches). If
        // batches haven't loaded yet, onMount fills this in once they do.
        next[field.name] = batches.map((b) => b.name);
      } else if (field.defaultSelected && field.defaultSelected.length > 0) {
        next[field.name] = [...field.defaultSelected];
      }
    }
    fieldValues = next;
  }
  // Reseed whenever the parent swaps the form spec (Overview's dropdown).
  $effect(() => {
    form.type;
    seedDefaults();
    submitMsg = null;
  });

  function toggleMulti(name: string, val: string) {
    const cur = (fieldValues[name] as string[] | undefined) ?? [];
    fieldValues[name] = cur.includes(val) ? cur.filter((x) => x !== val) : [...cur, val];
  }
  function isSelected(name: string, val: string): boolean {
    return ((fieldValues[name] as string[] | undefined) ?? []).includes(val);
  }
  function pairKey(left: string, right: string): string {
    return `${left}/${right}`;
  }

  function buildBody(): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (!fSince) throw new Error('since is required');
    body.since = new Date(fSince).toISOString();
    if (fUntil) body.until = new Date(fUntil).toISOString();
    if (fForce) body.force = true;
    for (const field of form.fields) {
      const v = fieldValues[field.name];
      if (field.kind === 'multiselect') {
        const arr = (v as string[] | undefined) ?? [];
        if (field.required && arr.length === 0) throw new Error(`${field.label} is required`);
        if (arr.length > 0) body[field.name] = arr;
      } else if (field.kind === 'pair-multiselect') {
        const arr = ((v as string[] | undefined) ?? [])
          .map((s) => s.split('/'))
          .filter((p) => p.length === 2);
        if (field.required && arr.length === 0) throw new Error(`${field.label} is required`);
        if (arr.length > 0) body[field.name] = arr;
      } else if (field.kind === 'tokens-csv' || field.kind === 'pools-csv') {
        const s = (v as string | undefined) ?? '';
        const arr = s.split(',').map((t) => t.trim()).filter((t) => t.length > 0);
        if (field.required && arr.length === 0) throw new Error(`${field.label} is required`);
        if (arr.length > 0) body[field.name] = arr;
      } else if (field.kind === 'token-batches') {
        // Expand the selected batch names to their union of tokens and send
        // as `tokens` (the backend stays token-based). Empty → omit, so the
        // backend falls back to the full live roster.
        const names = (v as string[] | undefined) ?? [];
        const toks = [...new Set(names.flatMap((n) => batchTokens(n)))];
        if (toks.length > 0) body['tokens'] = toks;
      }
    }
    return body;
  }

  async function submit() {
    submitMsg = null;
    try {
      const body = buildBody();
      const res = await fetch(`/api/admin/jobs/backfill/${form.type}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const j = await res.json();
      submitMsg = `OK: job ${j.job_id?.slice(0, 8) ?? '?'} kicked`;
      onSubmitted?.();
    } catch (e) {
      submitMsg = `ERROR: ${e}`;
    }
  }

  function reset() {
    seedDefaults();
    submitMsg = null;
  }
</script>

<section class="space-y-3 border border-zinc-800 rounded-md p-3 bg-zinc-950/40">
  <div class="flex items-baseline gap-2 flex-wrap">
    <h3 class="text-sm font-semibold text-zinc-200">{form.label}</h3>
    <span class="text-[10px] text-zinc-500 font-mono">{form.type}</span>
  </div>

  <div class="flex flex-wrap gap-3 items-end">
    <label class="flex flex-col text-xs gap-1">
      <span class="text-zinc-400">Since (UTC, required)</span>
      <input
        type="datetime-local"
        bind:value={fSince}
        class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
      />
    </label>
    <label class="flex flex-col text-xs gap-1">
      <span class="text-zinc-400">Until (UTC, optional)</span>
      <input
        type="datetime-local"
        bind:value={fUntil}
        class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100"
      />
    </label>
    {#if !form.hideForce}
      <label class="flex items-center text-xs gap-2 text-zinc-300">
        <input type="checkbox" bind:checked={fForce} />
        Force (delete existing rows in window)
      </label>
    {/if}
  </div>

  {#if form.description}
    <div class="text-xs text-zinc-500">{form.description}</div>
  {/if}

  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    {#each form.fields as field (field.name)}
      <div class="space-y-1">
        <div class="text-xs text-zinc-400">
          {field.label}{#if field.required}<span class="text-red-400"> *</span>{/if}
        </div>
        {#if field.kind === 'multiselect'}
          <div class="flex flex-wrap gap-1.5">
            {#each (field.options ?? []) as opt (opt)}
              <button
                type="button"
                class="text-xs px-2 py-0.5 rounded border"
                class:border-blue-500={isSelected(field.name, opt)}
                class:bg-blue-950={isSelected(field.name, opt)}
                class:text-blue-200={isSelected(field.name, opt)}
                class:border-zinc-700={!isSelected(field.name, opt)}
                class:text-zinc-400={!isSelected(field.name, opt)}
                onclick={() => toggleMulti(field.name, opt)}
              >{opt}</button>
            {/each}
          </div>
        {:else if field.kind === 'pair-multiselect'}
          <div class="flex flex-col gap-1">
            {#each (field.options ?? []) as left (left)}
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-xs text-zinc-500 w-16">{left}</span>
                {#each (field.optionsRight ?? []) as right (right)}
                  {@const k = pairKey(left, right)}
                  <button
                    type="button"
                    class="text-xs px-2 py-0.5 rounded border"
                    class:border-blue-500={isSelected(field.name, k)}
                    class:bg-blue-950={isSelected(field.name, k)}
                    class:text-blue-200={isSelected(field.name, k)}
                    class:border-zinc-700={!isSelected(field.name, k)}
                    class:text-zinc-400={!isSelected(field.name, k)}
                    onclick={() => toggleMulti(field.name, k)}
                  >{right}</button>
                {/each}
              </div>
            {/each}
          </div>
        {:else if field.kind === 'token-batches'}
          <div class="flex flex-wrap gap-1.5">
            {#each batches as b (b.name)}
              <button
                type="button"
                class="text-xs px-2 py-0.5 rounded border"
                class:border-blue-500={isSelected(field.name, b.name)}
                class:bg-blue-950={isSelected(field.name, b.name)}
                class:text-blue-200={isSelected(field.name, b.name)}
                class:border-zinc-700={!isSelected(field.name, b.name)}
                class:text-zinc-400={!isSelected(field.name, b.name)}
                onclick={() => toggleMulti(field.name, b.name)}
                title={b.tokens.join(', ')}
              >{b.name} ({b.count})</button>
            {/each}
            {#if batches.length === 0}
              <span class="text-xs text-zinc-600">loading batches…</span>
            {/if}
          </div>
        {:else if field.kind === 'tokens-csv' || field.kind === 'pools-csv'}
          <input
            type="text"
            placeholder={field.placeholder ?? ''}
            value={(fieldValues[field.name] as string) ?? ''}
            oninput={(e) => (fieldValues[field.name] = (e.currentTarget as HTMLInputElement).value)}
            class="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs text-zinc-100 font-mono"
          />
        {/if}
      </div>
    {/each}
  </div>

  <div class="flex items-center gap-3">
    <button
      class="text-sm px-3 py-1.5 bg-blue-700 hover:bg-blue-600 rounded-md text-white"
      onclick={submit}
    >Kick backfill</button>
    <button
      class="text-xs px-3 py-1.5 bg-zinc-900 border border-zinc-700 rounded-md hover:border-zinc-500"
      onclick={reset}
    >Reset</button>
    {#if submitMsg}
      <span class="text-xs"
        class:text-emerald-400={submitMsg.startsWith('OK')}
        class:text-red-300={submitMsg.startsWith('ERROR')}
      >{submitMsg}</span>
    {/if}
  </div>
</section>
