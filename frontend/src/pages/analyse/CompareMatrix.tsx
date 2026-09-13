import { useMemo } from 'react';
import type { UploadedPaper } from '../../stores/analyseStore';

const ENTITY_GROUP_ORDER = [
  'CHEMICAL',
  'SPECIES',
  'PLANT PART',
  'DEVELOPMENT STAGE',
  'EXTRACTION METHOD',
  'ANALYTICAL TECHNIQUE',
  'BIOACTIVITY',
  'DISEASE',
  'SEASON',
  'LOCATION',
] as const;

const LABEL_NAMES: Record<string, string> = {
  CHEMICAL: 'Chemical',
  SPECIES: 'Species',
  'PLANT PART': 'Plant Part',
  'ANALYTICAL TECHNIQUE': 'Analytical Technique',
  'EXTRACTION METHOD': 'Extraction Method',
  BIOACTIVITY: 'Bioactivity',
  'DEVELOPMENT STAGE': 'Development Stage',
  SEASON: 'Season',
  DISEASE: 'Disease',
  LOCATION: 'Location',
};

const getEntityAccentVar = (label: string) =>
  `--entity-${label.toLowerCase().replace(/[\s_]+/g, '-')}`;

export const CompareMatrix = ({ papers }: { papers: UploadedPaper[] }) => {
  // Aggregate data
  const matrixData = useMemo(() => {
    // 1. Collect all unique entity names per category
    const categoryEntities: Record<string, Set<string>> = {};

    // 2. Map of (entityName + category) -> paperId -> count
    const entityCountsMap: Record<string, Record<string, number>> = {};

    // 3. Track universal max count across the dataset for universal opacity scaling
    let globalMaxCount = 1;

    // 4. Track total mentions per paper
    const paperTotals: Record<string, number> = {};
    papers.forEach((p) => (paperTotals[p.id] = 0));

    papers.forEach((paper) => {
      if (!paper.entity_counts) return;

      Object.entries(paper.entity_counts).forEach(([category, entities]) => {
        if (!categoryEntities[category]) categoryEntities[category] = new Set();

        entities.forEach((entity) => {
          const name = entity.canonical || entity.text;
          categoryEntities[category].add(name);

          const key = `${category}::${name}`;
          if (!entityCountsMap[key]) entityCountsMap[key] = {};

          const existingCount = entityCountsMap[key][paper.id] || 0;
          const totalCount = existingCount + entity.count;
          entityCountsMap[key][paper.id] = totalCount;

          if (totalCount > globalMaxCount) {
            globalMaxCount = totalCount;
          }

          paperTotals[paper.id] += entity.count;
        });
      });
    });

    // Prepare rows based on defined group order
    type RowType =
      | { type: 'group'; category: string }
      | {
          type: 'entity';
          category: string;
          name: string;
          counts: Record<string, number>;
          numPapersWithMention: number;
        };

    const aggregatedRows: RowType[] = [];

    for (const category of ENTITY_GROUP_ORDER) {
      const entities = categoryEntities[category];
      if (!entities || entities.size === 0) continue;

      aggregatedRows.push({ type: 'group', category });

      // Sort entities alphabetically for consistency
      const sortedEntities = Array.from(entities).sort((a, b) =>
        a.localeCompare(b),
      );

      sortedEntities.forEach((name) => {
        const key = `${category}::${name}`;
        const counts = entityCountsMap[key];
        const numPapersWithMention = Object.keys(counts).length;

        aggregatedRows.push({
          type: 'entity',
          category,
          name,
          counts,
          numPapersWithMention,
        });
      });
    }

    return { rows: aggregatedRows, globalMaxCount, paperTotals };
  }, [papers]);

  if (papers.length === 0) return null;

  return (
    <div
      className="flex-1 w-full flex flex-col min-h-0 h-full overflow-hidden"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {/* Table container: borderless on left and right sides */}
      <div className="bg-white border-y border-outline-variant/40 border-x-0 overflow-hidden flex-1 flex flex-col min-h-0">
        <div className="overflow-auto thin-scrollbar flex-1 relative">
          <table className="w-full text-left border-collapse table-fixed min-w-[1100px]">
            <thead className="sticky top-0 z-40">
              <tr className="bg-white border-b border-outline-variant/50 shadow-[0_1px_3px_rgba(0,0,0,0.03)]">
                <th className="px-5 py-3.5 text-[11px] font-bold uppercase tracking-[0.08em] text-on-surface-variant w-72 min-w-[240px] max-w-[340px] sticky left-0 bg-white z-50 border-r border-outline-variant/40 shadow-[2px_0_6px_rgba(0,0,0,0.03)]">
                  Entity Index
                </th>
                {papers.map((p) => (
                  <th
                    key={p.id}
                    className="px-4 py-3 text-center border-r last:border-r-0 border-outline-variant/35 bg-white min-w-[170px]"
                  >
                    <div className="flex flex-col items-center gap-0.5">
                      <span
                        className="font-semibold text-on-surface text-[13px] line-clamp-2 leading-snug px-1"
                        title={p.name}
                      >
                        {p.name}
                      </span>
                      {p.doi && (
                        <span
                          className="font-mono text-blue-600/80 font-medium text-[10.5px] mt-0.5 block truncate max-w-[210px] mx-auto hover:text-blue-700"
                          title={p.doi}
                        >
                          {p.doi}
                        </span>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="text-[13px] text-on-surface-variant">
              {matrixData.rows.map((row) => {
                if (row.type === 'group') {
                  const accentColor = `var(${getEntityAccentVar(row.category)}, var(--primary))`;
                  return (
                    <tr
                      key={`group-${row.category}`}
                      style={{
                        backgroundColor: `color-mix(in srgb, ${accentColor} 8%, #ffffff)`,
                      }}
                    >
                      <td
                        className="px-5 py-2 font-bold text-[11px] uppercase tracking-[0.08em] border-b border-r border-outline-variant/40 sticky left-0 z-30 shadow-[2px_0_6px_rgba(0,0,0,0.03)] whitespace-normal break-words"
                        style={{
                          backgroundColor: `color-mix(in srgb, ${accentColor} 12%, #ffffff)`,
                          color: accentColor,
                        }}
                      >
                        {LABEL_NAMES[row.category] || row.category}
                      </td>
                      {papers.map((p) => (
                        <td
                          key={`group-spacer-${p.id}`}
                          className="border-b border-r last:border-r-0 border-outline-variant/35"
                          style={{ backgroundColor: 'inherit' }}
                        ></td>
                      ))}
                    </tr>
                  );
                }

                // Entity Row
                const entityRow = row as {
                  type: 'entity';
                  category: string;
                  name: string;
                  counts: Record<string, number>;
                  numPapersWithMention: number;
                };
                const accentColor = `var(${getEntityAccentVar(entityRow.category)}, var(--primary))`;
                const maxCount = matrixData.globalMaxCount || 1;

                return (
                  <tr
                    key={`entity-${entityRow.category}-${entityRow.name}`}
                    className="border-b border-outline-variant/25 hover:bg-slate-50/70 transition-colors group"
                  >
                    <td className="px-5 py-2.5 font-medium border-r border-outline-variant/40 sticky left-0 bg-white group-hover:bg-slate-50/70 transition-colors z-20 shadow-[2px_0_6px_rgba(0,0,0,0.03)] align-middle">
                      <span
                        className="text-on-surface text-[13px] leading-snug whitespace-normal break-words block pr-2"
                        style={{
                          fontStyle:
                            entityRow.category === 'SPECIES'
                              ? 'italic'
                              : 'normal',
                        }}
                        title={entityRow.name}
                      >
                        {entityRow.name}
                      </span>
                    </td>
                    {papers.map((p) => {
                      const count = entityRow.counts[p.id];
                      if (!count) {
                        return (
                          <td
                            key={`cell-${entityRow.name}-${p.id}`}
                            className="py-2 px-3 border-r last:border-r-0 border-outline-variant/25 text-center align-middle"
                          >
                            <span className="text-slate-300 text-[13px] font-light select-none">
                              –
                            </span>
                          </td>
                        );
                      }

                      // Universal dynamic opacity percentage (18% to 85%) calculated from entity mention number
                      const norm = maxCount > 1 ? count / maxCount : 0.5;
                      const opacityPercent = Math.min(
                        85,
                        Math.max(18, Math.round(18 + 67 * Math.pow(norm, 0.5))),
                      );
                      // High-contrast text color on tinted background
                      const textColor =
                        opacityPercent > 55
                          ? '#ffffff'
                          : `color-mix(in srgb, ${accentColor} 88%, #0f172a)`;

                      return (
                        <td
                          key={`cell-${entityRow.name}-${p.id}`}
                          className="py-2 px-3 border-r last:border-r-0 border-outline-variant/25 text-center align-middle"
                        >
                          <div
                            className="inline-flex items-center justify-center min-w-[38px] px-3 py-0.5 h-[25px] rounded-full font-semibold text-[12.5px] tabular-nums transition-all hover:scale-105"
                            style={{
                              backgroundColor: `color-mix(in srgb, ${accentColor} ${opacityPercent}%, transparent)`,
                              color: textColor,
                            }}
                            title={String(count)}
                          >
                            {count}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
