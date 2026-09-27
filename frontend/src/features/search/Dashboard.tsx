/**
 * Dashboard visualization aggregating key database metrics, journal
 * shares, entity taxonomy, publication timelines, and geographic sites.
 */

import React, { useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { dashboardApi } from '../../lib/api/dashboard';
import JournalDistributionCard from './JournalDistributionCard';
import DatabaseExplorerDrawer, {
  type DrawerTab,
  type DrawerFilter,
} from './DatabaseExplorerDrawer';
import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { PublicationTimelineChart } from './PublicationTimelineChart';
import { EntityDonutChart } from './EntityDonutChart';
import { PlantOriginMap } from './PlantOriginMap';
import { useDatabaseDrawerStore } from '../../stores/databaseDrawerStore';

interface DashboardMetrics {
  kpis: {
    total_papers: number;
    total_entities: number;
    total_journals: number;
    top_journals: string;
  };
  charts: {
    papers_by_journal: { name: string; value: number }[];
    entity_distribution: { name: string; value: number }[];
    papers_by_year: { name: string; value: number }[];
    geo_distribution: { name: string; value: number }[];
  };
}

// Supplies CSS custom properties (--stat-accent and --stat-divider)
// consumed via inline style to preserve declarative color mapping.
const statCardVariants = cva(
  'p-6 transition-all duration-200 justify-center gap-0',
  {
    variants: {
      accent: {
        papers:
          '[--stat-accent:var(--role-papers)]   [--stat-divider:var(--role-papers-under)]',
        entities:
          '[--stat-accent:var(--role-entities)] [--stat-divider:var(--role-entities-under)]',
        journals:
          '[--stat-accent:var(--role-journals)] [--stat-divider:var(--role-journals-under)]',
      },
    },
    defaultVariants: { accent: 'papers' },
  },
);

interface StatCardProps extends VariantProps<typeof statCardVariants> {
  label: string;
  value: number;
  onClick?: () => void;
  isActive?: boolean;
}

function StatCard({ accent, label, value, onClick, isActive }: StatCardProps) {
  const clickable = !!onClick;
  return (
    <Card
      className={cn(
        statCardVariants({ accent }),
        clickable &&
          'cursor-pointer hover:-translate-y-0.5 hover:[border-color:var(--stat-accent)] hover:shadow-[0_1px_2px_rgba(0,0,0,0.06),0_2px_6px_rgba(0,0,0,0.04)]',
        isActive &&
          'border-[var(--stat-accent)] shadow-[0_1px_2px_rgba(0,0,0,0.06),0_2px_6px_rgba(0,0,0,0.04)]',
      )}
      onClick={onClick}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
    >
      <div className="flex flex-col gap-3.5">
        <span
          className="text-[12px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: 'var(--stat-accent)' }}
        >
          {label}
        </span>
        <div className="text-[54px] lg:text-[60px] font-bold leading-none tracking-[-0.02em] text-on-surface">
          {value.toLocaleString()}
        </div>
      </div>
    </Card>
  );
}

// Standardizes card padding and typography across dashboard widgets.
function ChartCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[18px] font-semibold text-on-surface">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>('papers');
  const [drawerFilter, setDrawerFilter] = useState<DrawerFilter | null>(null);

  const openDrawer = (opts?: {
    tab?: DrawerTab;
    filter?: DrawerFilter | null;
  }) => {
    if (opts?.tab) setDrawerTab(opts.tab);
    setDrawerFilter(opts?.filter ?? null);
    setDrawerOpen(true);
  };

  // Opens the explorer drawer when an explore search specifies database mode.
  const pendingOpenQuery = useDatabaseDrawerStore((s) => s.pendingOpenQuery);
  const clearPendingOpenQuery = useDatabaseDrawerStore(
    (s) => s.clearPendingOpenQuery,
  );

  // Consume cross-page search triggers as a one-shot event.
  useEffect(() => {
    if (!pendingOpenQuery) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- consume-once signal
    openDrawer({
      tab: 'papers',
      filter: {
        kind: 'papers',
        label: `Search: ${pendingOpenQuery}`,
        value: pendingOpenQuery,
      },
    });
    clearPendingOpenQuery();
    // Only re-run when a new query value arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingOpenQuery]);

  // Fetches aggregated dashboard metrics on initial component mount.
  useEffect(() => {
    dashboardApi
      .getMetrics()
      .then(setMetrics)
      .catch((err) => console.error('Failed to fetch dashboard metrics:', err))
      .finally(() => setIsLoading(false));
  }, []);

  const isReady = !!metrics;

  if (isLoading || !isReady) {
    return (
      <div
        className="w-full max-w-6xl mx-auto px-4 py-8 space-y-6"
        aria-label="Loading dashboard"
      >
        {/* Dashboard loading skeleton. */}
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-3 gap-3">
          <Skeleton className="h-32 rounded-2xl" />
          <Skeleton className="h-32 rounded-2xl" />
          <Skeleton className="h-32 rounded-2xl" />
        </div>
        <Skeleton className="h-72 rounded-2xl" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Skeleton className="h-80 rounded-2xl" />
          <Skeleton className="h-80 rounded-2xl" />
        </div>
        <Skeleton className="h-[520px] rounded-2xl" />
      </div>
    );
  }

  const papersTotal = metrics.kpis.total_papers;
  const entitiesTotal = metrics.kpis.total_entities;
  const journalsTotal = metrics.kpis.total_journals;

  return (
    <div
      className="mx-auto"
      style={{
        maxWidth: 'var(--content-max)',
        fontFamily: 'var(--font-google-sans)',
      }}
    >
      <div className="px-12 pt-2 pb-20 animate-in fade-in slide-in-from-bottom-4 duration-700">
        {/* Summary metric KPI cards. */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-8 mt-8">
          <StatCard
            accent="papers"
            label="Papers indexed"
            value={papersTotal}
            onClick={() => {
              if (drawerOpen && drawerTab === 'papers') {
                setDrawerOpen(false);
              } else {
                openDrawer({ tab: 'papers', filter: null });
              }
            }}
          />
          <StatCard
            accent="entities"
            label="Entities extracted"
            value={entitiesTotal}
            onClick={() => {
              if (drawerOpen && drawerTab === 'entities') {
                setDrawerOpen(false);
              } else {
                openDrawer({ tab: 'entities', filter: null });
              }
            }}
          />
          <StatCard
            accent="journals"
            label="Journals indexed"
            value={journalsTotal}
            onClick={() => {
              if (drawerOpen && drawerTab === 'journals') {
                setDrawerOpen(false);
              } else {
                openDrawer({ tab: 'journals', filter: null });
              }
            }}
          />
        </div>

        <div className="mt-6">
          {/* Top journals distribution widget. */}
          <ChartCard title="Top Journals">
            <JournalDistributionCard
              journals={metrics.charts.papers_by_journal}
              totalPapers={metrics.kpis.total_papers}
              onJournalClick={() =>
                openDrawer({
                  tab: 'journals',
                  filter: null,
                })
              }
            />
          </ChartCard>

          {/* Entity classification and publication year timeline. */}
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.2fr] gap-6 mt-6">
            <ChartCard title="Entity Distribution">
              <EntityDonutChart
                data={metrics.charts.entity_distribution}
                onSliceClick={(name) =>
                  openDrawer({
                    tab: 'entities',
                    filter: {
                      kind: 'entity',
                      label: `Type: ${name}`,
                      value: name,
                    },
                  })
                }
              />
            </ChartCard>

            <ChartCard title="Publication Timeline">
              <PublicationTimelineChart
                data={metrics.charts.papers_by_year}
                onYearClick={(year) =>
                  openDrawer({
                    tab: 'papers',
                    filter: {
                      kind: 'year',
                      label: `Year: ${year}`,
                      value: year,
                    },
                  })
                }
              />
            </ChartCard>
          </div>

          {/* Geographic distribution of sample collection sites. */}
          <div className="mt-6">
            <ChartCard title="Geographic distribution of bioactive species collection sites">
              <PlantOriginMap
                data={metrics.charts.geo_distribution}
                onCountryClick={(name, raws) =>
                  openDrawer({
                    tab: 'papers',
                    filter: {
                      kind: 'country',
                      label: `Origin: ${name}`,
                      value: raws.length ? raws : [name],
                    },
                  })
                }
              />
            </ChartCard>
          </div>
        </div>
      </div>

      <DatabaseExplorerDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        tab={drawerTab}
        filter={drawerFilter}
        entities={metrics.charts.entity_distribution}
        journals={metrics.charts.papers_by_journal}
        onOpenPaper={(doi) => {
          setDrawerOpen(false);
          navigate({
            to: '/paper/$doi',
            params: { doi },
            search: { src: 'database' },
          });
        }}
      />
    </div>
  );
};

export default Dashboard;
