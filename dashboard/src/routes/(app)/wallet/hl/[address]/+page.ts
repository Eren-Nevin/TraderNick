import type { PageLoad } from './$types';

// HL wallet detail page. The address is the only route input; all wallet data
// is fetched client-side from the /api/hyperliquid/* proxies so the date
// slider can refetch positions without a full navigation.
export const load: PageLoad = ({ params, url }) => {
  // Optional ?snapshot=YYYY-MM-DD pre-selects the "as of" day (e.g. opening a
  // smart wallet at the lookback period's end). Only a well-formed date is
  // honoured; anything else falls through to the default (today / live).
  const snap = url.searchParams.get('snapshot');
  const initialSnapshot = snap && /^\d{4}-\d{2}-\d{2}$/.test(snap) ? snap : null;
  // Optional ?token=<SYM> pre-selects that token in the positions table (e.g. opened
  // from the Backtracker Leaderboard dialog for a specific token).
  const tok = url.searchParams.get('token');
  const initialToken = tok && /^[A-Za-z0-9._-]{1,20}$/.test(tok) ? tok.toUpperCase() : null;
  return { address: (params.address ?? '').toLowerCase(), initialSnapshot, initialToken };
};
