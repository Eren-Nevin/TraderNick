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

// HTTP basic-auth guarding the whole app (dashboard + admin pages), enforced in
// hooks.server.ts. Empty = auth disabled (app runs open). Set both to enable.
export const WEB_AUTH_USER = env.WEB_AUTH_USER ?? '';
export const WEB_AUTH_PASSWORD = env.WEB_AUTH_PASSWORD ?? '';

export function ingestionAuthHeader(): string {
  const raw = `${INGESTION_ADMIN_USER}:${INGESTION_ADMIN_PASSWORD}`;
  return 'Basic ' + Buffer.from(raw).toString('base64');
}
