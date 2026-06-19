// Shared helpers for the 1-day-grain "as of day" sliders (Smart Wallets table
// + the HL wallet detail page). A slider value runs oldest(left)→newest(right);
// we map between an ISO date (YYYY-MM-DD) and a whole-day offset back from today.

export const DAY_SLIDER_MAX_BACK = 540; // days (~18 months)
const DAY_MS = 86_400_000;

/** UTC midnight of today, in ms. */
export function startOfTodayMs(): number {
  const now = new Date();
  return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
}

/** ISO date → whole-day offset back from today (0 = today), clamped. */
export function isoToBack(iso: string, maxBack = DAY_SLIDER_MAX_BACK): number {
  const t = Date.parse(iso + 'T00:00:00Z');
  if (!isFinite(t)) return 0;
  const back = Math.round((startOfTodayMs() - t) / DAY_MS);
  return Math.max(0, Math.min(maxBack, back));
}

/** Whole-day offset back from today → ISO date (YYYY-MM-DD). */
export function backToIso(back: number): string {
  const ms = startOfTodayMs() - back * DAY_MS;
  return new Date(ms).toISOString().slice(0, 10);
}

/** ISO date (YYYY-MM-DD) → unix seconds at that UTC midnight. */
export function isoToUnix(iso: string): number {
  return Math.floor(Date.parse(iso + 'T00:00:00Z') / 1000);
}

/** True when the ISO date is today (UTC) or later. */
export function isToday(iso: string): boolean {
  return isoToBack(iso) <= 0;
}
