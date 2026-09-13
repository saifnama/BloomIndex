import { createFileRoute } from '@tanstack/react-router';
import AnalysePage from '../pages/analyse/AnalysePage';

export const Route = createFileRoute('/analyse')({
  component: AnalysePage,
});
