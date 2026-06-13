import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for the ingestion service's token-batch list. Drives the batch
// selector on the admin backfill forms (select a batch of tokens to backfill
// instead of all-or-none). Batches are an ingestion concept — the trading
// dashboard never calls this.
export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/token_batches`, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
