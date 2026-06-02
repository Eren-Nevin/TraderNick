import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type {
  ChainGroup,
  TokenGroup,
  TransferStream,
  UniswapStream
} from '$lib/api';
import type { PageServerLoad } from './$types';

export type LidoStream = { event: string; chain: string; rows: number };
export type GmxMarketRow = {
  event: string;
  chain: string;
  market: string;
  rows: number;
};

// Cross-cutting Dashboard page — surfaces every chart kind, so the loader
// is the union of what the per-category pages fetch (tokens, token/chain
// groups, transfer streams, uniswap pools, lido chains, gmx markets).
export const load: PageServerLoad = async ({ fetch }) => {
  const [
    tokensRes,
    streamsRes,
    uniRes,
    lidoRes,
    gmxRes,
    tokenGroupsRes,
    chainGroupsRes
  ] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/uniswap/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/lido/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/gmx/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/token-groups`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/chain-groups`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const streams: TransferStream[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  const uniPools: UniswapStream[] = uniRes.ok ? (await uniRes.json()).streams : [];
  const lidoChains: LidoStream[] = lidoRes.ok ? (await lidoRes.json()).streams : [];
  const gmxMarkets: GmxMarketRow[] = gmxRes.ok ? (await gmxRes.json()).streams : [];
  const tokenGroups: TokenGroup[] = tokenGroupsRes.ok ? (await tokenGroupsRes.json()).groups : [];
  const chainGroups: ChainGroup[] = chainGroupsRes.ok ? (await chainGroupsRes.json()).groups : [];
  return {
    tokens,
    streams,
    uniPools,
    lidoChains,
    gmxMarkets,
    tokenGroups,
    chainGroups
  };
};
