import type { PageLoad } from './$types';

// HL wallet detail page. The address is the only route input; all wallet data
// is fetched client-side from the /api/hyperliquid/* proxies so the date
// slider can refetch positions without a full navigation.
export const load: PageLoad = ({ params }) => {
  return { address: (params.address ?? '').toLowerCase() };
};
