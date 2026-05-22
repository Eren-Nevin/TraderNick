import { env } from '$env/dynamic/private';

export const INTERNAL_DATA_SERVER_URL =
  env.INTERNAL_DATA_SERVER_URL ?? 'http://localhost:8002';
