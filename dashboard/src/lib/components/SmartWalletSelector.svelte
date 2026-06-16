<script lang="ts">
  import {
    METRIC_CATALOGUE,
    defaultSmartSelectorState,
    sanitizeSmartSelectorState,
    metricDef,
    type SmartSelectorState,
    type SmartCriterionState,
    type SmartMetricKey
  } from '$lib/components/charts/smartSelector';

  let {
    value = defaultSmartSelectorState() as SmartSelectorState,
    onChange,
    /** Display only — when set, used to label "Token" scope option as
     *  "Token (BTC)" so the user knows which token is being scoped to. */
    tokenLabel = '',
    /** Hide the legacy server-side preset load/save/manage UI. Set false
     *  when embedding inside the first-class Filters builder, where saving
     *  is owned by the filters store, not the smart_selector_presets table. */
    showPresets = true
  }: {
    value?: SmartSelectorState;
    onChange: (v: SmartSelectorState) => void;
    tokenLabel?: string;
    showPresets?: boolean;
  } = $props();

  // When exactly one criterion is active (not disabled), the sort metric
  // becomes ambiguous-by-default — there's no other knob to rank against.
  // Snap sort_by onto that lone enabled metric so the user doesn't have to
  // re-click the sort radio after toggling sibling criteria off.
  function autoSort(state: SmartSelectorState): SmartSelectorState {
    const enabled = state.criteria.filter((c) => !(c.disabled ?? false));
    if (enabled.length === 1 && state.sort_by !== enabled[0].metric) {
      return { ...state, sort_by: enabled[0].metric };
    }
    return state;
  }
  function set(patch: Partial<SmartSelectorState>) {
    onChange(autoSort({ ...value, ...patch }));
  }
  function setCriterion(i: number, patch: Partial<SmartCriterionState>) {
    const next = value.criteria.slice();
    next[i] = { ...next[i], ...patch };
    onChange(autoSort({ ...value, criteria: next }));
  }
  function removeCriterion(i: number) {
    onChange(autoSort({ ...value, criteria: value.criteria.filter((_, j) => j !== i) }));
  }
  function addCriterion() {
    // Pick a metric not already in use. Falls back to 'realized_pnl' if
    // every metric is taken (unlikely; the UI can show duplicates).
    const used = new Set(value.criteria.map((c) => c.metric));
    const next = METRIC_CATALOGUE.find((m) => !used.has(m.key)) ?? METRIC_CATALOGUE[0];
    // New criteria default to token scope — most useful queries are
    // token-specific and the engine path is dramatically cheaper when
    // the source CTEs can be prefiltered on token. Users still flip a
    // criterion to global on the per-row dropdown when they want a
    // cross-token aggregate.
    const fresh: SmartCriterionState = {
      metric: next.key as SmartMetricKey,
      scope: next.defaultScope ?? 'token',
    };
    if (next.defaultMin !== undefined) fresh.min = next.defaultMin;
    onChange(autoSort({ ...value, criteria: [...value.criteria, fresh] }));
  }
  function setSort(metric: SmartMetricKey) {
    // Convenience: clicking the "sort" radio also makes sure that metric
    // is somewhere in the criteria list (with no min/max — it's just a
    // ranking hint). Otherwise picking, say, "sharpe" sort wouldn't show
    // anywhere in the criteria UI. The auto-added criterion defaults to
    // token scope to match the addCriterion path.
    let criteria = value.criteria;
    if (!criteria.some((c) => c.metric === metric)) {
      criteria = [...criteria, { metric, scope: metricDef(metric)?.defaultScope ?? 'token' }];
    }
    onChange({ ...value, sort_by: metric, criteria });
  }

  function numOrUndef(s: string): number | undefined {
    if (s === '' || s === null) return undefined;
    const n = Number(s);
    return isFinite(n) ? n : undefined;
  }

  function placeholderFor(key: SmartMetricKey, which: 'min' | 'max'): string {
    const def = metricDef(key);
    if (!def) return '';
    if (def.kind === 'usd')   return which === 'min' ? '$ min' : '$ max';
    if (def.kind === 'token') return which === 'min' ? '# tokens min' : '';
    if (def.kind === 'pct')   return which === 'min' ? '0.10 = 10%' : '';
    if (def.kind === 'ratio') return which === 'min' ? '0.50' : '';
    if (def.kind === 'count') return which === 'min' ? '# min' : '';
    return '';
  }

  // ── Saved presets ─────────────────────────────────────────────────
  // Server-side "criteria groups": the full SmartSelectorState saved
  // under a name. Loaded into the picker via the Load dropdown; saved
  // via the Save button with a name prompt. Persisted in
  // tradernick.smart_selector_presets (ReplacingMergeTree keyed on
  // name) so re-saving the same name updates the row.
  type Preset = { name: string; config: string; updated_at?: string };
  let presets = $state<Preset[]>([]);
  let presetsLoading = $state(false);
  let presetsError = $state<string | null>(null);
  let saveOpen = $state(false);
  let saveName = $state('');
  let saving = $state(false);
  // Manage panel — hidden by default so a fresh chart isn't visually
  // decorated with chips from other charts' saved presets.
  let manageOpen = $state(false);

  async function loadPresetList() {
    presetsLoading = true;
    presetsError = null;
    try {
      const res = await fetch('/api/hyperliquid/smart_selector_presets');
      if (!res.ok) throw new Error(`${res.status}`);
      const body = await res.json();
      presets = (body.presets ?? []) as Preset[];
    } catch (e) {
      presetsError = e instanceof Error ? e.message : String(e);
    } finally {
      presetsLoading = false;
    }
  }

  function applyPreset(name: string) {
    if (!name) return;
    const p = presets.find((x) => x.name === name);
    if (!p) return;
    try {
      const parsed = JSON.parse(p.config) as unknown;
      const cleaned = sanitizeSmartSelectorState(parsed);
      onChange(cleaned);
    } catch (e) {
      presetsError = `Failed to load "${name}": ${e instanceof Error ? e.message : String(e)}`;
    }
  }

  async function savePreset() {
    const name = saveName.trim();
    if (!name) return;
    saving = true;
    try {
      const res = await fetch('/api/hyperliquid/smart_selector_presets', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name, config: JSON.stringify(value) }),
      });
      if (!res.ok) throw new Error(await res.text());
      saveOpen = false;
      saveName = '';
      await loadPresetList();
    } catch (e) {
      presetsError = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  async function deletePreset(name: string) {
    if (!name) return;
    if (!confirm(`Delete preset "${name}"?`)) return;
    try {
      const res = await fetch(`/api/hyperliquid/smart_selector_presets/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error(await res.text());
      await loadPresetList();
    } catch (e) {
      presetsError = e instanceof Error ? e.message : String(e);
    }
  }

  // Lazy-load on first render. $effect runs after mount; the list is
  // small and the dropdown shows "Loading…" until it lands.
  $effect(() => {
    if (showPresets && presets.length === 0 && !presetsLoading && !presetsError) {
      loadPresetList();
    }
  });
</script>

<div class="rounded-md border border-zinc-700 bg-zinc-900/40 p-2.5 space-y-2 text-xs">
  <div class="flex items-center gap-2 flex-wrap">
    <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Wallet selection</span>
    <span class="w-px h-4 bg-zinc-800"></span>
    <span class="text-zinc-400">Lookback</span>
    <input
      type="number" min="1" max="180" step="1"
      value={value.lookback}
      onchange={(e) => set({ lookback: Math.round(Number((e.target as HTMLInputElement).value) || 7) })}
      class="w-14 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100"
    />
    <span class="text-zinc-500">d</span>
    <span class="text-zinc-400 ml-2">Top N</span>
    <input
      type="number" min="1" max="500" step="10"
      value={value.top_n}
      onchange={(e) => set({ top_n: Math.round(Number((e.target as HTMLInputElement).value) || 50) })}
      class="w-16 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100"
    />
    <span class="text-zinc-400 ml-2">Scope</span>
    <select
      value={value.scope}
      onchange={(e) => set({ scope: (e.target as HTMLSelectElement).value as 'global' | 'token' })}
      class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-zinc-100"
    >
      <option value="global">Global</option>
      <option value="token">{tokenLabel ? `Token (${tokenLabel})` : 'Token'}</option>
    </select>

    <!-- Saved presets — load + save + delete. Pushed to the right so
         the per-chart knobs stay grouped on the left. -->
    <span class="flex-1"></span>
    {#if showPresets}
    <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Preset</span>
    <select
      onchange={(e) => {
        const sel = (e.target as HTMLSelectElement);
        applyPreset(sel.value);
        sel.value = '';  // reset so re-picking the same preset re-fires
      }}
      class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 max-w-[10rem] truncate"
      title="Load a saved criteria group"
    >
      <option value="">{presetsLoading ? 'Loading…' : (presets.length === 0 ? '(no presets)' : 'Load…')}</option>
      {#each presets as p (p.name)}
        <option value={p.name}>{p.name}</option>
      {/each}
    </select>
    <button
      type="button"
      onclick={() => { saveOpen = !saveOpen; if (saveOpen) saveName = ''; }}
      class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-2 py-0.5 text-zinc-200 text-[11px]"
      title="Save the current criteria group as a named preset"
    >Save</button>
    {/if}
    <button
      type="button"
      onclick={() => {
        if (value.criteria.length === 0) return;
        if (!confirm(`Remove all ${value.criteria.length} criteria?`)) return;
        // Wipe criteria but keep lookback / top_n / scope / sort_by so
        // the user doesn't have to redo the surrounding knobs.
        onChange({ ...value, criteria: [] });
      }}
      disabled={value.criteria.length === 0}
      class="bg-zinc-800 hover:bg-zinc-700 disabled:bg-zinc-900 disabled:text-zinc-600 border border-zinc-700 rounded px-2 py-0.5 text-zinc-200 text-[11px]"
      title="Remove all criteria (lookback / scope / sort stay)"
    >Reset</button>
    {#if showPresets && presets.length > 0}
      <button
        type="button"
        onclick={() => (manageOpen = !manageOpen)}
        class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-2 py-0.5 text-zinc-200 text-[11px]"
        title="Show / hide the saved-preset list (for deleting)"
      >{manageOpen ? 'Hide' : 'Manage'}</button>
    {/if}
  </div>

  {#if saveOpen}
    <div class="flex items-center gap-2">
      <span class="text-zinc-500 text-[10px] uppercase tracking-widest w-12">Name</span>
      <input
        type="text"
        bind:value={saveName}
        placeholder="e.g. SOL whales ≥ $1M, 30d"
        maxlength="80"
        onkeydown={(e) => { if (e.key === 'Enter') savePreset(); if (e.key === 'Escape') saveOpen = false; }}
        class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100"
      />
      <button
        type="button"
        onclick={savePreset}
        disabled={saving || saveName.trim().length === 0}
        class="bg-emerald-700 hover:bg-emerald-600 disabled:bg-zinc-800 disabled:text-zinc-500 border border-zinc-700 rounded px-2 py-0.5 text-zinc-100 text-[11px]"
      >{saving ? 'Saving…' : 'Confirm'}</button>
      <button
        type="button"
        onclick={() => { saveOpen = false; saveName = ''; }}
        class="text-zinc-400 hover:text-zinc-100 px-1.5 py-0.5 text-[11px]"
      >Cancel</button>
      {#if presets.some((p) => p.name === saveName.trim())}
        <span class="text-amber-400 text-[10px]">overwrites existing</span>
      {/if}
    </div>
  {/if}

  {#if presetsError}
    <div class="text-red-400 text-[11px] flex items-center gap-2">
      <span>{presetsError}</span>
      <button type="button" class="text-zinc-500 hover:text-zinc-200" onclick={() => (presetsError = null)}>✕</button>
    </div>
  {/if}

  {#if manageOpen && presets.length > 0}
    <!-- Manage panel: toggled by the Manage button. Lists each saved
         preset with its own delete button. Hidden by default so a fresh
         chart isn't visually decorated with chips of other charts'
         saved presets (which can read as "applied state"). -->
    <div class="flex items-center gap-1.5 flex-wrap text-[10px] border-t border-zinc-800 pt-1.5">
      <span class="text-zinc-500 uppercase tracking-widest">Manage:</span>
      {#each presets as p (p.name)}
        <span class="inline-flex items-center gap-1 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5">
          <span class="text-zinc-300">{p.name}</span>
          <button
            type="button"
            class="text-zinc-500 hover:text-red-300 leading-none"
            title="Delete this preset"
            onclick={() => deletePreset(p.name)}
          >✕</button>
        </span>
      {/each}
    </div>
  {/if}

  <div class="space-y-1">
    {#each value.criteria as c, i (i)}
      <div class={'flex items-center gap-1.5 flex-wrap ' + ((c.disabled ?? false) ? 'opacity-50' : '')}>
        <input
          type="checkbox"
          checked={!(c.disabled ?? false)}
          onchange={(e) => setCriterion(i, { disabled: !(e.target as HTMLInputElement).checked })}
          class="accent-zinc-300"
          title="Toggle this criterion on/off without removing it"
          aria-label="Enable criterion"
        />
        <select
          value={c.metric}
          onchange={(e) => {
            const metric = (e.target as HTMLSelectElement).value as SmartMetricKey;
            // Token-only metrics (Sharpe) can't be global — snap scope to token
            // as the metric changes so the locked dropdown stays consistent.
            const patch: Partial<SmartCriterionState> = { metric };
            if (metricDef(metric)?.tokenOnly) patch.scope = 'token';
            setCriterion(i, patch);
          }}
          class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 w-44"
        >
          {#each METRIC_CATALOGUE as m (m.key)}
            <option value={m.key}>{m.label}</option>
          {/each}
        </select>
        <span class="text-zinc-500">min</span>
        <input
          type="number" step="any"
          value={c.min ?? ''}
          placeholder={placeholderFor(c.metric, 'min')}
          onchange={(e) => setCriterion(i, { min: numOrUndef((e.target as HTMLInputElement).value) })}
          class="w-24 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 text-right"
        />
        <span class="text-zinc-500">max</span>
        <input
          type="number" step="any"
          value={c.max ?? ''}
          placeholder={placeholderFor(c.metric, 'max')}
          onchange={(e) => setCriterion(i, { max: numOrUndef((e.target as HTMLInputElement).value) })}
          class="w-24 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 text-right"
        />
        {#if metricDef(c.metric)?.tokenOnly}
          <!-- Sharpe is token-only for now (global path times out); lock the
               scope picker to Token. -->
          <select
            value="token"
            disabled
            class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 ml-1 opacity-60 cursor-not-allowed"
            title="Sharpe runs in token scope only for now — global is temporarily disabled (it times out on wide windows)."
          >
            <option value="token">{tokenLabel ? `Token (${tokenLabel})` : 'Token'}</option>
          </select>
        {:else}
          <select
            value={c.scope ?? value.scope}
            onchange={(e) => setCriterion(i, { scope: (e.target as HTMLSelectElement).value as 'global' | 'token' })}
            class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 ml-1"
            title="Compute this metric across all HL tokens (global) or filtered to this chart's token only"
          >
            <option value="global">Global</option>
            <option value="token">{tokenLabel ? `Token (${tokenLabel})` : 'Token'}</option>
          </select>
        {/if}
        <input
          type="number" min="1" max="180" step="1"
          value={c.lookback ?? ''}
          placeholder={`${value.lookback}d`}
          onchange={(e) => {
            const n = numOrUndef((e.target as HTMLInputElement).value);
            setCriterion(i, { lookback: n === undefined ? undefined : Math.round(n) });
          }}
          class="w-12 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 text-right ml-1"
          title={`Lookback (days) for this criterion. Blank = inherit the selector's ${value.lookback}d.`}
        />
        <span class="text-zinc-500 text-[11px]">d</span>
        {#if metricDef(c.metric)?.usesMinDays}
          <span class="text-zinc-500 text-[11px] ml-1.5">min</span>
          <input
            type="number" min="2" max="180" step="1"
            value={c.min_days ?? ''}
            placeholder="2"
            onchange={(e) => {
              const n = numOrUndef((e.target as HTMLInputElement).value);
              setCriterion(i, { min_days: n === undefined ? undefined : Math.max(2, Math.round(n)) });
            }}
            class="w-12 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 text-right ml-1"
            title="Minimum invested (in-position) days required in the lookback before this Sharpe is scored — guards against tiny-sample blow-ups (a couple of near-identical daily returns → ~0 volatility → huge Sharpe). Below the threshold the metric is 0. Blank = 2 (no guard)."
          />
          <span class="text-zinc-500 text-[11px]">d invested</span>
        {/if}
        <label class="flex items-center gap-1 cursor-pointer ml-1">
          <input
            type="radio"
            name="smart-selector-sort"
            checked={value.sort_by === c.metric}
            onchange={() => setSort(c.metric)}
            class="accent-zinc-300"
          />
          <span class="text-zinc-400 text-[11px]">sort</span>
        </label>
        <button
          type="button"
          onclick={() => removeCriterion(i)}
          class="ml-1 px-1.5 py-0.5 rounded text-zinc-500 hover:text-zinc-100 hover:bg-zinc-800 leading-none"
          aria-label="Remove criterion"
        >✕</button>
      </div>
    {/each}
    <button
      type="button"
      onclick={addCriterion}
      class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-2 py-0.5 text-zinc-200 text-[11px]"
    >+ add criterion</button>
  </div>
</div>
