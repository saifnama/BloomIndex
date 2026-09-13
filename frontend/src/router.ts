/**
 * Router singleton — exported so non-React modules (e.g. an axios interceptor
 * that needs to redirect on 401) can call `router.navigate(...)` without going
 * through a hook. The `Register` declaration below makes every `navigate`,
 * `<Link>`, `useSearch`, and `useParams` call across the app type-aware of
 * the route tree.
 */

import { createRouter } from '@tanstack/react-router';
import { routeTree } from './routeTree.gen';
import {
  DefaultPending,
  DefaultError,
  DefaultNotFound,
} from './components/ui/route-defaults';

export const router = createRouter({
  routeTree,

  // Auto-restore scroll position on back/forward navigation.
  scrollRestoration: true,

  // App-wide fallbacks. Individual routes can override via per-route
  // `pendingComponent` / `errorComponent` / `notFoundComponent`.
  defaultPendingComponent: DefaultPending,
  defaultErrorComponent: DefaultError,
  defaultNotFoundComponent: DefaultNotFound,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
