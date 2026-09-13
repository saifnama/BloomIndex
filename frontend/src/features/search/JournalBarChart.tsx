import { useMemo } from 'react';
import { Bar, BarChart, Cell, LabelList, XAxis, YAxis } from 'recharts';
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart';

/**
 * Horizontal bar chart visualizing paper counts across journals with
 * distinct color allocations and value labels.
 */

interface Props {
  data: { name: string; value: number }[];
  onBarClick?: (name: string) => void;
}

const DIVERSE_PALETTE = [
  '#0EA5E9', // Sky blue
  '#10B981', // Emerald green
  '#F59E0B', // Amber gold
  '#8B5CF6', // Purple / Violet
  '#EC4899', // Pink
  '#06B6D4', // Cyan
  '#F97316', // Orange
  '#6366F1', // Indigo
  '#14B8A6', // Teal
  '#EF4444', // Coral Red
  '#84CC16', // Lime
  '#D946EF', // Fuchsia
  '#3B82F6', // Cobalt Blue
  '#E11D48', // Rose
  '#22C55E', // Bright Green
  '#A855F7', // Medium Purple
  '#38BDF8', // Light Sky
  '#FB923C', // Light Orange
  '#A78BFA', // Lavender
  '#4ADE80', // Mint
] as const;

function truncate(label: string, max = 26): string {
  return label.length > max ? label.slice(0, max - 1) + '…' : label;
}

export function JournalBarChart({ data, onBarClick }: Props) {
  // Shuffles vibrant colors per dataset change to visually distinguish
  // adjacent journal rows without requiring semantic category mapping.
  const dataWithFill = useMemo(() => {
    const pool = [...DIVERSE_PALETTE];
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const temp = pool[i];
      pool[i] = pool[j];
      pool[j] = temp;
    }
    return data.map((d, i) => ({
      ...d,
      fill: pool[i % pool.length],
    }));
  }, [data]);

  // Configures tooltip metric label; bar colors are injected per cell.
  const chartConfig = {
    value: {
      label: 'Papers',
      color: 'var(--chart-1)',
    },
  } satisfies ChartConfig;

  return (
    <ChartContainer config={chartConfig} className="w-full h-full">
      <BarChart
        accessibilityLayer
        data={dataWithFill}
        layout="vertical"
        margin={{ top: 4, right: 32, bottom: 4, left: 0 }}
      >
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="name"
          tickLine={false}
          axisLine={false}
          width={195}
          tick={{ fontSize: 11 }}
          tickFormatter={(v: string) => truncate(v, 26)}
        />

        <ChartTooltip
          cursor={false}
          content={
            <ChartTooltipContent
              hideLabel
              formatter={(value, _name, item) => {
                const datum = item.payload as { name: string; value: number };
                return (
                  <div className="flex w-full flex-col gap-1">
                    <span className="text-muted-foreground text-xs">
                      {datum.name}
                    </span>
                    <span className="font-mono font-medium tabular-nums">
                      {Number(value).toLocaleString()} papers
                    </span>
                  </div>
                );
              }}
            />
          }
        />

        <Bar
          dataKey="value"
          radius={[0, 4, 4, 0]}
          background={{
            fill: 'var(--muted)',
            opacity: 0.4,
            radius: 4,
          }}
          onClick={(d: unknown) => {
            const payload = (d as { name?: string } | undefined)?.name;
            if (payload && onBarClick) onBarClick(payload);
          }}
        >
          {dataWithFill.map((d, i) => (
            <Cell
              key={`${d.name}-${i}`}
              fill={d.fill}
              className="cursor-pointer"
            />
          ))}
          <LabelList
            dataKey="value"
            position="right"
            className="fill-muted-foreground"
            fontSize={10}
            formatter={(value) => Number(value ?? 0).toLocaleString()}
          />
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
