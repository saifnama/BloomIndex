/**
 * App — TanStack Router shell. The router instance + type registration live
 * in `src/router.ts`. Routes themselves live in `src/routes/`.
 */

import { RouterProvider } from '@tanstack/react-router';
import { router } from './router';

function App() {
  return <RouterProvider router={router} />;
}

export default App;
