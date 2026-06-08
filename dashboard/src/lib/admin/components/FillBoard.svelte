<script lang="ts">
  // Per-event coverage visualization.
  //
  // Renders the last N days as a GitHub-contributions-style grid
  // (weeks as columns, days-of-week as rows) PLUS a 24-cell strip
  // showing today's hours. Colors: green = filled, red = partial-day
  // gap, gray = no data / future / intrinsically quiet.
  //
  // Tooltip on hover surfaces the day + hours_filled/hours_active.

  import { onMount } from 'svelte';

  type DayCell = {
    day: string;             // YYYY-MM-DD
    status: 'green' | 'red' | 'gray';
    hours_active: number;
    hours_filled: number;
  };
  type HourCell = { hour: number; status: 'green' | 'gray' };
  type CalendarPayload = {
    event: string;
    provider: string;
    label: string;
    table: string;
    mode: string;
    since: string;
    until: string;
    today_utc: string;
    first_data: string | null;
    last_data: string | null;
    days: DayCell[];
    today_hours: HourCell[];
  };

  type Props = {
    eventKey: string;
    label: string;
    days?: number;           // display window. Default 180.
    // Optional per-chain selector. When present (length >= 2) a
    // dropdown is rendered; selecting a chain refetches with
    // `&chain=<x>`. Default = 'ETH' if present, else chains[0].
    // No "All" view — multi-chain aggregation masked single-chain
    // blackouts and made the picture harder to read, not easier.
    chains?: string[];
  };
  let { eventKey, label, days: windowDays = 180, chains }: Props = $props();

  // Selected chain. Empty string only for single-chain events
  // (chains undefined) — those don't render a selector and the URL
  // omits `&chain=`.
  let chain = $state<string>(
    chains && chains.length > 0
      ? (chains.includes('ETH') ? 'ETH' : chains[0])
      : ''
  );

  let loading = $state(true);
  let err = $state<string | null>(null);
  let data = $state<CalendarPayload | null>(null);
  let hoverTip = $state<string | null>(null);

  async function load() {
    loading = true;
    err = null;
    try {
      let url = `/api/admin/gaps/calendar?event=${encodeURIComponent(eventKey)}`;
      if (chain) {
        url += `&chain=${encodeURIComponent(chain)}`;
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      data = (await res.json()) as CalendarPayload;
    } catch (e) {
      err = String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function onChainChange(e: Event) {
    chain = (e.currentTarget as HTMLSelectElement).value;
    load();
  }

  // Group days into weeks (Mon-Sun). Each "column" is a week. We pad
  // the start with empty cells so day-of-week aligns vertically.
  let weeks = $derived.by(() => {
    if (!data) return [];
    const cells = data.days.slice(-windowDays);
    if (cells.length === 0) return [];
    // What weekday is the first cell? Pad nulls before it so the
    // grid lines up.  Sunday = 0 in JS; we map Mon=0..Sun=6 so the
    // top row is Monday — easier to scan.
    const first = new Date(cells[0].day + 'T00:00:00Z');
    const dow = (first.getUTCDay() + 6) % 7;     // Mon=0..Sun=6
    const padded: (DayCell | null)[] = Array(dow).fill(null);
    padded.push(...cells);
    const cols: (DayCell | null)[][] = [];
    for (let i = 0; i < padded.length; i += 7) {
      const col = padded.slice(i, i + 7);
      while (col.length < 7) col.push(null);
      cols.push(col);
    }
    return cols;
  });

  // Color classes — small palette so the grid reads quickly.
  function cellClass(status: 'green' | 'red' | 'gray' | null): string {
    if (status === 'green') return 'bg-emerald-600 hover:ring-2 hover:ring-emerald-300';
    if (status === 'red')   return 'bg-rose-600 hover:ring-2 hover:ring-rose-300';
    return 'bg-zinc-700 hover:ring-2 hover:ring-zinc-500';   // gray or null
  }

  function tooltipFor(cell: DayCell | null): string {
    if (!cell) return '';
    return `${cell.day} · ${cell.hours_filled}/${cell.hours_active} hours covered · ${cell.status}`;
  }

  function tooltipForHour(h: HourCell): string {
    return `${data?.today_utc} ${String(h.hour).padStart(2, '0')}:00 UTC · ${h.status}`;
  }

  // Within window dimming: if a day is BEFORE first_data, it's outside
  // the event's recorded history. Draw it more faintly so the user
  // sees "no data history" not "we lost data".
  function isPreHistory(day: string): boolean {
    if (!data?.first_data) return false;
    return day < data.first_data;
  }
</script>

<div class="border border-zinc-800 rounded-md p-3 bg-zinc-950">
  <div class="flex items-baseline justify-between gap-2 mb-2">
    <div class="min-w-0">
      <h3 class="text-sm font-semibold text-zinc-200">
        {label}{chain ? ` · ${chain}` : ''}
      </h3>
      {#if data}
        <div class="text-[10px] text-zinc-500 uppercase tracking-widest">
          {data.mode === 'regular_cadence' ? 'regular cadence' : 'event driven'}
          {#if data.first_data}
            · first {data.first_data}
          {/if}
          {#if data.last_data}
            · last {data.last_data}
          {/if}
        </div>
      {/if}
    </div>
    <div class="flex items-center gap-2 shrink-0">
      {#if chains && chains.length >= 2}
        <select
          value={chain}
          onchange={onChainChange}
          disabled={loading}
          class="text-[11px] bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-zinc-200 disabled:opacity-50"
        >
          {#each chains as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
      {/if}
      {#if loading}
        <span class="text-[10px] text-zinc-500">loading…</span>
      {/if}
    </div>
  </div>

  {#if err}
    <div class="text-xs text-rose-300 bg-rose-950/30 p-2 rounded flex items-start gap-2">
      <span class="flex-1 min-w-0 break-words">{err}</span>
      <button
        type="button"
        onclick={load}
        disabled={loading}
        class="shrink-0 px-2 py-0.5 rounded border border-rose-700/50 text-rose-200 hover:bg-rose-900/40 disabled:opacity-50"
      >retry</button>
    </div>
  {:else if !data}
    <div class="text-xs text-zinc-500 py-4 text-center">no data</div>
  {:else}
    <!-- Past days grid — GitHub contributions style. -->
    <div class="flex gap-[2px]" onmouseleave={() => (hoverTip = null)}>
      {#each weeks as col, i (i)}
        <div class="flex flex-col gap-[2px]">
          {#each col as cell, j (j)}
            {#if cell}
              <div
                class="w-[10px] h-[10px] rounded-[1px] cursor-pointer {cellClass(cell.status)} {isPreHistory(cell.day) ? 'opacity-40' : ''}"
                onmouseenter={() => (hoverTip = tooltipFor(cell))}
                title={tooltipFor(cell)}
              ></div>
            {:else}
              <div class="w-[10px] h-[10px]"></div>
            {/if}
          {/each}
        </div>
      {/each}
    </div>

    <!-- Today strip — 24 hour cells separated from the past grid. -->
    <div class="mt-3 pt-3 border-t border-zinc-800">
      <div class="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">
        Today {data.today_utc} (24 hours UTC)
      </div>
      <div class="flex gap-[2px]" onmouseleave={() => (hoverTip = null)}>
        {#each data.today_hours as h (h.hour)}
          <div
            class="w-[10px] h-[10px] rounded-[1px] cursor-pointer {cellClass(h.status)}"
            onmouseenter={() => (hoverTip = tooltipForHour(h))}
            title={tooltipForHour(h)}
          ></div>
        {/each}
      </div>
    </div>

    {#if hoverTip}
      <div class="mt-2 text-[10px] text-zinc-400 font-mono">{hoverTip}</div>
    {/if}
  {/if}
</div>
