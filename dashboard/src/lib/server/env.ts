import { env } from '$env/dynamic/private';

export const INTERNAL_DATA_SERVER_URL =
  env.INTERNAL_DATA_SERVER_URL ?? 'http://localhost:8002';

// Ingestion admin service. Carries /streams, /jobs, and /jobs/backfill/* used
// by the admin panel. Basic auth credentials live server-side so the browser
// never sees them — the proxy adds the Authorization header.
export const INTERNAL_INGESTION_URL =
  env.INTERNAL_INGESTION_URL ?? 'http://ingestion:8000';

export const INGESTION_ADMIN_USER = env.INGESTION_ADMIN_USER ?? 'admin';
export const INGESTION_ADMIN_PASSWORD = env.INGESTION_ADMIN_PASSWORD ?? 'change_me';

export function ingestionAuthHeader(): string {
  const raw = `${INGESTION_ADMIN_USER}:${INGESTION_ADMIN_PASSWORD}`;
  return 'Basic ' + Buffer.from(raw).toString('base64');
}
