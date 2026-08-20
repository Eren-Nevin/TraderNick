import type { Handle } from '@sveltejs/kit';
import { timingSafeEqual } from 'node:crypto';
import { WEB_AUTH_USER, WEB_AUTH_PASSWORD } from '$lib/server/env';

// Constant-time string comparison. timingSafeEqual throws on length mismatch,
// so equalize first (still comparing to keep timing roughly uniform) then fail.
function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) {
    timingSafeEqual(ab, ab);
    return false;
  }
  return timingSafeEqual(ab, bb);
}

function unauthorized(): Response {
  return new Response('Authentication required.', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="TraderNick", charset="UTF-8"' }
  });
}

// Gate the whole SvelteKit app (dashboard, /admin pages, and all same-origin
// /api/* routes) behind HTTP basic auth, using WEB_AUTH_USER / WEB_AUTH_PASSWORD
// from the environment. This is the app-level equivalent of putting nginx basic
// auth in front, so the site is protected even with no reverse proxy.
//
// If either credential is unset the gate is disabled and the app runs open
// (prior behavior) — set BOTH in .env to enable.
export const handle: Handle = async ({ event, resolve }) => {
  if (WEB_AUTH_USER && WEB_AUTH_PASSWORD) {
    const header = event.request.headers.get('authorization') ?? '';
    if (!header.startsWith('Basic ')) return unauthorized();

    let user = '';
    let pass = '';
    try {
      const decoded = Buffer.from(header.slice(6), 'base64').toString('utf-8');
      const idx = decoded.indexOf(':');
      if (idx === -1) return unauthorized();
      user = decoded.slice(0, idx);
      pass = decoded.slice(idx + 1);
    } catch {
      return unauthorized();
    }

    if (!safeEqual(user, WEB_AUTH_USER) || !safeEqual(pass, WEB_AUTH_PASSWORD)) {
      return unauthorized();
    }
  }

  return resolve(event);
};
