/**
 * Displays prominent dominant journal metrics in a spotlight card alongside
 * ranked distribution bars for remaining journals.
 */

import React from 'react';
import { JournalBarChart } from './JournalBarChart';

interface JournalEntry {
  name: string;
  value: number;
}

interface Props {
  // Sorted descending by publication volume, with dominant journal first.
  journals: JournalEntry[];
  totalPapers: number;
  height?: number;
  onJournalClick?: (name: string) => void;
}

const JournalDistributionCard: React.FC<Props> = ({
  journals,
  totalPapers,
  height = 260,
  onJournalClick,
}) => {
  if (!journals.length) {
    return (
      <div
        className="flex items-center justify-center text-xs text-muted-foreground"
        style={{ height }}
      >
        No journal data
      </div>
    );
  }

  const dominant = journals[0];
  const ranked = journals.slice(1, 9);
  const pct =
    totalPapers > 0 ? Math.round((dominant.value / totalPapers) * 100) : 0;

  return (
    <div className="flex items-stretch gap-4 w-full" style={{ height }}>
      {/* Dominant publication spotlight card. */}
      <div
        className="flex-1 min-w-0 rounded-2xl flex flex-col justify-between py-6 px-6"
        style={{
          fontFamily: 'var(--font-google-sans)',
          background: '#F2FBFC',
          border: 'none',
          outline: 'none',
          boxShadow: 'none',
        }}
      >
        <div>
          <div
            className="text-[17.5px] font-semibold line-clamp-2 leading-snug tracking-tight"
            style={{ color: '#2AACBF', fontFamily: 'var(--font-google-sans)' }}
          >
            {dominant.name}
          </div>
        </div>

        <div>
          <div className="flex items-baseline gap-2">
            <span
              className="text-6xl font-semibold tabular-nums leading-none"
              style={{
                color: '#2AACBF',
                fontFamily: 'var(--font-google-sans)',
              }}
            >
              {dominant.value.toLocaleString()}
            </span>
            <span className="text-sm font-medium" style={{ color: '#5BBCC8' }}>
              papers
            </span>
          </div>
        </div>

        <div className="flex flex-col items-stretch gap-2">
          <div className="flex w-full items-baseline justify-between">
            <span
              className="text-2xl font-medium tabular-nums"
              style={{
                color: '#2AACBF',
                fontFamily: 'var(--font-google-sans)',
              }}
            >
              {pct}%
            </span>
            <span className="text-xs font-medium" style={{ color: '#5BBCC8' }}>
              of corpus
            </span>
          </div>
          <div
            className="h-1.5 w-full overflow-hidden rounded-full"
            style={{ backgroundColor: 'rgba(160,228,241,0.3)' }}
          >
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${pct}%`,
                backgroundColor: '#A0E4F1',
              }}
            />
          </div>
        </div>
      </div>

      {/* Ranked comparative distribution bars. */}
      <div className="flex-1 min-w-0 h-full">
        <JournalBarChart data={ranked} onBarClick={onJournalClick} />
      </div>
    </div>
  );
};

export default JournalDistributionCard;
