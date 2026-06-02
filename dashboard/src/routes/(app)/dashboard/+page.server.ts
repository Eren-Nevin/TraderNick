import { redirect } from '@sveltejs/kit';

// Bare /dashboard always lands on the default page. User-created pages
// live at /dashboard/{pageId} via the [pageId] dynamic route.
export const load = () => {
  throw redirect(307, '/dashboard/default');
};
