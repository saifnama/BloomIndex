/**
 * `/` — Search page with typed search params.
 * Pairs with `pages/explore/ExplorePage.tsx`, which reads/writes these via
 * the typed `getRouteApi('/').useSearch()` helper.
 */

import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';
import ExplorePage from '../pages/explore/ExplorePage';

export const nerSearchSchema = z.object({
  q: z.string().optional(),
  oa: z.enum(['1']).optional(), // present-or-absent boolean
  ft: z.enum(['1']).optional(),
  type: z.string().optional(),
  sort: z.string().optional(),
  // `src` is a free-form string (can contain spaces, e.g. "europe pmc")
  src: z.string().optional(),
});

export type NerSearch = z.infer<typeof nerSearchSchema>;

export const Route = createFileRoute('/')({
  validateSearch: nerSearchSchema,
  component: ExplorePage,
});
