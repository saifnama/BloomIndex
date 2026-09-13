/**
 * `/paper/$doi` — paper reader, with typed `:doi` param and optional `?src=`.
 */

import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';
import PaperPage from '../pages/paper/PaperPage';

export const paperSearchSchema = z.object({
  src: z.string().optional(),
});

export type PaperSearch = z.infer<typeof paperSearchSchema>;

export const Route = createFileRoute('/paper/$doi')({
  validateSearch: paperSearchSchema,
  component: PaperPage,
});
