<script lang="ts">
  // Smart Wallets (Dynamic) "Token List" view. One row per token the filtered
  // wallet cohort holds: long/short OI summed across those wallets at the latest
  // position snapshot, the absolute change vs 24h-ago and 7d-ago, and HL-perp
  // 24h/7d price change. Served by /api/hyperliquid/smart_wallet_token_list.
  //
  // The server returns every held token; sorting is entirely client-side here —
  // click a column header to re-cut. `unit` ($/token, from the toolbar) flips the
  // OI + OI-change columns; price Δ columns are always %. Default: Long OI desc.

  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  type Row = {
    token: string;
    long_oi_token: number; long_oi_usd: number;
    short_oi_token: number; short_oi_usd: number;
    pct_24h: number | null; pct_7d: number | null;
    // per-side OI change vs each window (1h/4h/24/7d), token + usd.
    [k: string]: number | string | null;
  };

  let {
    rows = [],
    loading = false,
    error = null,
    unit = 'usd',
    onSelectToken = undefined
  }: {
    rows: Record<string, unknown>[] | Row[];
    loading?: boolean;
    error?: string | null;
    unit?: 'usd' | 'token';
    /** Click a token row → open the top-OI wallets dialog for that token. */
    onSelectToken?: (token: string) => void;
  } = $props();

  function fmtUsd(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtAmount(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(1) + 'K';
    return sign + abs.toFixed(2);
  }
  function fmtPct(n: number | null): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const sign = n > 0 ? '+' : '';
    return sign + n.toFixed(2) + '%';
  }

  type SortKey =
    | 'token'
    | 'long_oi_token' | 'long_oi_usd' | 'short_oi_token' | 'short_oi_usd'
    | 'net' | 'netchg1h' | 'netchg4h' | 'netchg24' | 'netchg7d' | 'lsnum'
    | 'long_chg24_token' | 'long_chg24_usd' | 'short_chg24_token' | 'short_chg24_usd'
    | 'pct_24h' | 'pct_7d';

  // Net OI = long − short (selected unit); its % = (long − short)/(long + short)
  // — unit-independent (price cancels), so it reads the same in $ or token.
  function netVal(r: Row): number {
    return unit === 'token' ? r.long_oi_token - r.short_oi_token : r.long_oi_usd - r.short_oi_usd;
  }
  function netPct(r: Row): number | null {
    const l = unit === 'token' ? r.long_oi_token : r.long_oi_usd;
    const s = unit === 'token' ? r.short_oi_token : r.short_oi_usd;
    const tot = l + s;
    return tot ? ((l - s) / tot) * 100 : null;
  }
  // Net OI delta over a window (sfx = '1h'|'4h'|'24'|'7d') = Δlong − Δshort =
  // ΔnetOI (selected unit). Field access is generic over the suffix.
  function netChg(r: Row, sfx: string): number {
    const un = unit === 'token' ? 'token' : 'usd';
    return (Number(r[`long_chg${sfx}_${un}`]) || 0) - (Number(r[`short_chg${sfx}_${un}`]) || 0);
  }
  // Net OI delta ÷ avg total OI (now & prior snapshot) → unit-independent %
  // (price cancels). avgTotal = ((long+short)_now + (long+short)_prior)/2, and
  // prior = now − change, so avgTotal = total_now − (Δlong + Δshort)/2 ≥ 0.
  function netChgPct(r: Row, sfx: string): number | null {
    const lc = Number(r[`long_chg${sfx}_usd`]) || 0;
    const sc = Number(r[`short_chg${sfx}_usd`]) || 0;
    const avgTotal = (r.long_oi_usd + r.short_oi_usd) - (lc + sc) / 2;
    return avgTotal ? ((lc - sc) / avgTotal) * 100 : null;
  }

  // 'oi' = OI magnitude (long green / short red); 'net' = long−short with %;
  // 'chg' = signed change; 'pct' = price %.
  type Col = { key: SortKey; label: string; kind: 'oi_long' | 'oi_short' | 'net' | 'netchg' | 'chg' | 'pct' | 'lsnum'; win?: string };
  let u = $derived(unit === 'token' ? 'token' : 'usd');
  let valueFmt = $derived(unit === 'token' ? fmtAmount : fmtUsd);
  let cols = $derived<Col[]>([
    { key: `long_oi_${u}` as SortKey, label: 'Long OI', kind: 'oi_long' },
    { key: `short_oi_${u}` as SortKey, label: 'Short OI', kind: 'oi_short' },
    { key: 'lsnum', label: 'L/S num', kind: 'lsnum' },
    { key: 'net', label: 'Net OI', kind: 'net' },
    { key: 'netchg1h', label: '1h Net OI', kind: 'netchg', win: '1h' },
    { key: 'netchg4h', label: '4h Net OI', kind: 'netchg', win: '4h' },
    { key: 'netchg24', label: '24h Net OI', kind: 'netchg', win: '24' },
    { key: 'netchg7d', label: '7d Net OI', kind: 'netchg', win: '7d' },
    { key: 'pct_24h', label: '24h Price Δ', kind: 'pct' },
    { key: 'pct_7d', label: '7d Price Δ', kind: 'pct' },
    { key: `long_chg24_${u}` as SortKey, label: '24h Long Δ', kind: 'chg' },
    { key: `short_chg24_${u}` as SortKey, label: '24h Short Δ', kind: 'chg' }
  ]);

  let sortKey = $state<SortKey>('long_oi_usd');
  let sortDir = $state<1 | -1>(-1);
  let search = $state('');

  // L/S long share = long ÷ (long + short), as a %. NaN for tokens with no
  // holders so they sort to the bottom (matches the displayed "—").
  function lsPct(r: Row): number {
    const lc = Number(r.long_count ?? 0);
    const sc = Number(r.short_count ?? 0);
    const tot = lc + sc;
    return tot > 0 ? (lc / tot) * 100 : NaN;
  }

  // Fuzzy token match: query chars appear in order in the token (case-insensitive).
  function fuzzy(q: string, t: string): boolean {
    q = q.trim().toLowerCase();
    if (!q) return true;
    t = t.toLowerCase();
    let i = 0;
    for (const ch of t) { if (ch === q[i]) i++; if (i === q.length) return true; }
    return false;
  }
  // Keep the sort on the displayed unit when sorting an OI/change column.
  let effSortKey = $derived<SortKey>(
    (sortKey.endsWith('_usd') || sortKey.endsWith('_token'))
      ? (sortKey.replace(/_(usd|token)$/, `_${u}`) as SortKey)
      : sortKey
  );

  function onSort(k: SortKey) {
    if (effSortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else {
      sortKey = k;
      sortDir = k === 'token' ? 1 : -1;
    }
  }
  function sortArrow(k: SortKey): string {
    if (effSortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }

  let sortedRows = $derived.by(() => {
    const arr = (rows as Row[]).filter((r) => fuzzy(search, String(r.token)));
    const dir = sortDir;
    const k = effSortKey;
    return [...arr].sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      // Net OI + its windowed changes sort by the PERCENTAGE (the parenthesised
      // value), not the raw amount — so ranking is comparable across tokens.
      const sv = (r: Row) =>
        k === 'net' ? netPct(r)
        : k === 'lsnum' ? lsPct(r)
        : k.startsWith('netchg') ? netChgPct(r, k.slice(6))
        : ((r as unknown as Record<string, number | null>)[k]);
      const av = sv(a);
      const bv = sv(b);
      const an = av === null || av === undefined || !isFinite(av as number);
      const bn = bv === null || bv === undefined || !isFinite(bv as number);
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      return ((av as number) - (bv as number)) * dir;
    });
  });

  function cellText(r: Row, c: Col): string {
    if (c.kind === 'net') {
      const n = netVal(r);
      return `${n > 0 ? '+' : ''}${valueFmt(n)} (${fmtPct(netPct(r))})`;
    }
    if (c.kind === 'netchg') {
      const n = netChg(r, c.win ?? '24');
      return `${n > 0 ? '+' : ''}${valueFmt(n)} (${fmtPct(netChgPct(r, c.win ?? '24'))})`;
    }
    if (c.kind === 'lsnum') {
      const lc = Math.round(Number(r.long_count ?? 0));
      const sc = Math.round(Number(r.short_count ?? 0));
      // Long share = long ÷ (long + short), bounded to 100% (e.g. 30/100 → 23%).
      const tot = lc + sc;
      const pct = tot > 0 ? `${Math.round((lc / tot) * 100)}%` : '—';
      return `${lc.toLocaleString()}/${sc.toLocaleString()} (${pct})`;
    }
    // (signClass handles netchg sign below)
    const v = (r as unknown as Record<string, number | null>)[c.key];
    return c.kind === 'pct' ? fmtPct(v) : valueFmt(Number(v));
  }
  function signClass(r: Row, c: Col): string {
    if (c.kind === 'oi_long') return 'text-emerald-300';
    if (c.kind === 'oi_short') return 'text-rose-300';
    if (c.kind === 'lsnum') return 'text-zinc-300';
    const v = c.kind === 'net' ? netVal(r)
      : c.kind === 'netchg' ? netChg(r, c.win ?? '24')
      : (r as unknown as Record<string, number | null>)[c.key];
    if (v === null || v === undefined || !isFinite(v) || v === 0) return 'text-zinc-500';
    return v > 0 ? 'text-emerald-400' : 'text-rose-400';
  }
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- Fuzzy token-name filter. -->
  <div class="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    <span class="text-[11px]">Token</span>
    <input
      class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 hover:border-zinc-600 focus:outline-none w-28"
      placeholder="Token…" bind:value={search} title="Fuzzy filter by token name" />
    <span class="ml-auto text-[11px] text-zinc-600">{(sortedRows as Row[]).length} shown</span>
  </div>
  <div class="flex-1 overflow-auto scrollbar-none">
  {#if error}
    <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
  {:else if loading && (rows as Row[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
  {:else if (rows as Row[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">No tokens held by this wallet set</div>
  {:else}
    <table class="w-full">
      <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
        <tr>
          <th class="text-left px-3 py-1.5 font-normal">#</th>
          <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
            onclick={() => onSort('token')}>Token{sortArrow('token')}</th>
          {#each cols as c (c.label)}
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              onclick={() => onSort(c.key)}>{c.label}{sortArrow(c.key)}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each sortedRows as r, idx (r.token)}
          <tr class="border-b border-zinc-900 hover:bg-zinc-900/40 {onSelectToken ? 'cursor-pointer' : ''}"
            onclick={onSelectToken ? () => onSelectToken(String(r.token)) : undefined}
            title={onSelectToken ? 'Show top wallets by OI for this token' : undefined}>
            <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
            <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
            {#each cols as c (c.label)}
              <td class="px-3 py-1 text-right font-mono {signClass(r, c)}">{cellText(r, c)}</td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
  </div>
</div>
