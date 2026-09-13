import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import './index.css';
import App from './App.tsx';
// Dashboard charts are shadcn/Recharts and react-simple-maps now;
// the previous ECharts theme registration that lived here is gone.

// One QueryClient for the whole app. Defaults are tuned for our
// usage: data is considered fresh for 30s (so quick page hops don't
// re-fetch), retries are limited so a permanently-down endpoint
// doesn't hammer the server, and refetch-on-focus is OFF because our
// indexed-files / upload-status data should only refresh on explicit
// invalidation or polling intervals — not whenever the tab regains
// focus.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
