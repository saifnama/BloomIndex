/**
 * Root route — hosts the app shell that used to live in App.tsx.
 * AppHeader, ErrorBoundary, UploadStatusListener, and the beforeunload cleanup
 * are mounted here so they survive every route transition.
 */

import { createRootRouteWithContext, Outlet } from '@tanstack/react-router';
import { TanStackRouterDevtools } from '@tanstack/react-router-devtools';
import { useEffect } from 'react';
import AppHeader from '../layout/AppHeader';
import { ErrorBoundary } from '../components/ui/error-boundary';
import { UploadStatusListener } from '../components/UploadStatusListener';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/sonner';
import { ragApi } from '../lib/api/chat';

// Empty context now; leaves room to inject queryClient or auth state later
// without forcing a refactor of every route.
// eslint-disable-next-line react-refresh/only-export-components -- TanStack requires the Route export in this file.
export const Route = createRootRouteWithContext<Record<string, never>>()({
  component: RootLayout,
});

// eslint-disable-next-line react-refresh/only-export-components -- co-located with its Route by design.
function RootLayout() {
  useEffect(() => {
    // Fire-and-forget async fetch() never completes in beforeunload —
    // use sendBeacon (guaranteed delivery attempt) with a fetch fallback.
    const handleBeforeUnload = () => {
      try {
        const blob = new Blob([JSON.stringify({})], {
          type: 'application/json',
        });
        if (!navigator.sendBeacon('/api/chat/cleanup', blob)) {
          void ragApi.cleanupUserData();
        }
      } catch {
        // Best-effort only.
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, []);

  return (
    <TooltipProvider delayDuration={250}>
      <UploadStatusListener />
      <div className="bg-background flex h-screen overflow-hidden">
        <main className="flex-1 flex flex-col min-w-0 relative z-10">
          <AppHeader />
          <div
            className="flex-1 overflow-y-auto relative h-full nice-scroll"
            id="main-content-display"
          >
            <ErrorBoundary>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
      </div>
      <Toaster richColors closeButton position="bottom-right" />
      {import.meta.env.DEV && (
        <TanStackRouterDevtools position="bottom-right" />
      )}
    </TooltipProvider>
  );
}
