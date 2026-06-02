import { redirect } from '@sveltejs/kit';

// Root → /dashboard. Dashboard is the cross-cutting picker page and the
// first entry in the sidebar.
export const load = () => {
  throw redirect(307, '/dashboard');
};
