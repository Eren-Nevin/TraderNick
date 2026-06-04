<script lang="ts">
  import {
    METRIC_CATALOGUE,
    defaultSmartSelectorState,
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
    tokenLabel = ''
  }: {
    value?: SmartSelectorState;
    onChange: (v: SmartSelectorState) => void;
    tokenLabel?: string;
  } = $props();

  function set(patch: Partial<SmartSelectorState>) {
    onChange({ ...value, ...patch });
  }
  function setCriterion(i: number, patch: Partial<SmartCriterionState>) {
    const next = value.criteria.slice();
    next[i] = { ...next[i], ...patch };
    onChange({ ...value, criteria: next });
  }
  function removeCriterion(i: number) {
    onChange({ ...value, criteria: value.criteria.filter((_, j) => j !== i) });
  }
  function addCriterion() {
    // Pick a metric not already in use. Falls back to 'realized_pnl' if
    // every metric is taken (unlikely; the UI can show duplicates).
    const used = new Set(value.criteria.map((c) => c.metric));
    const next = METRIC_CATALOGUE.find((m) => !used.has(m.key)) ?? METRIC_CATALOGUE[0];
    // New criteria default to the overall selector scope — matches the
    // user's mental model ("set them the same as the overall scope by
    // default"). They can override per-row afterwards.
    const fresh: SmartCriterionState = { metric: next.key as SmartMetricKey, scope: value.scope };
    if (next.defaultMin !== undefined) fresh.min = next.defaultMin;
    onChange({ ...value, criteria: [...value.criteria, fresh] });
  }
  function setSort(metric: SmartMetricKey) {
    // Convenience: clicking the "sort" radio also makes sure that metric
    // is somewhere in the criteria list (with no min/max — it's just a
    // ranking hint). Otherwise picking, say, "sharpe" sort wouldn't show
    // anywhere in the criteria UI. The auto-added criterion inherits the
    // overall scope so the sort's effective scope is well-defined.
    let criteria = value.criteria;
    if (!criteria.some((c) => c.metric === metric)) {
      criteria = [...criteria, { metric, scope: value.scope }];
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
    if (def.kind === 'pct')   return which === 'min' ? '0.10 = 10%' : '';
    if (def.kind === 'ratio') return which === 'min' ? '0.50' : '';
    if (def.kind === 'count') return which === 'min' ? '# min' : '';
    return '';
  }
</script>

<div class="rounded-md border border-zinc-700 bg-zinc-900/40 p-2.5 space-y-2 text-xs">
  <div class="flex items-center gap-2 flex-wrap">
    <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Wallet selection</span>
    <span class="w-px h-4 bg-zinc-800"></span>
    <span class="text-zinc-400">Lookback</span>
    <input
      type="number" min="1" max="60" step="1"
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
  </div>

  <div class="space-y-1">
    {#each value.criteria as c, i (i)}
      <div class="flex items-center gap-1.5 flex-wrap">
        <select
          value={c.metric}
          onchange={(e) => setCriterion(i, { metric: (e.target as HTMLSelectElement).value as SmartMetricKey })}
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
        <select
          value={c.scope ?? value.scope}
          onchange={(e) => setCriterion(i, { scope: (e.target as HTMLSelectElement).value as 'global' | 'token' })}
          class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-zinc-100 ml-1"
          title="Compute this metric across all HL tokens (global) or filtered to this chart's token only"
        >
          <option value="global">Global</option>
          <option value="token">{tokenLabel ? `Token (${tokenLabel})` : 'Token'}</option>
        </select>
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
