import { useMemo, useState } from 'react';
import { Cell, Label, Pie, PieChart } from 'recharts';
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart';
import { getEntityColor } from '@/lib/entityColorPalette';

/**
 * Interactive donut chart illustrating entity proportions, mapping each
 * category to its semantic color and displaying active counts in center.
 */

interface Props {
  data: { name: string; value: number }[];
  onSliceClick?: (name: string) => void;
}

// Normalizes entity category labels into slug keys for chartConfig.
function entityKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export function EntityDonutChart({ data, onSliceClick }: Props) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const total = useMemo(() => data.reduce((s, d) => s + d.value, 0), [data]);

  // Derives CSS color tokens for each entity category so slices and
  // tooltips share consistent semantic palettes.
  const chartConfig = useMemo<ChartConfig>(() => {
    const config: ChartConfig = {
      value: { label: 'Entities' },
    };
    data.forEach((d) => {
      config[entityKey(d.name)] = {
        label: d.name,
        color: getEntityColor(d.name).hex,
      };
    });
    return config;
  }, [data]);

  const dataWithFill = useMemo(
    () =>
      data.map((d) => ({
        ...d,
        key: entityKey(d.name),
        fill: getEntityColor(d.name).hex,
      })),
    [data],
  );

  const active = activeIndex != null ? dataWithFill[activeIndex] : null;
  const centerLine1 = active
    ? active.value.toLocaleString()
    : total.toLocaleString();
  const centerLine2 = active ? active.name : 'entities';

  return (
    <ChartContainer
      config={chartConfig}
      className="mx-auto aspect-square h-[300px] w-full"
    >
      <PieChart>
        <ChartTooltip
          cursor={false}
          content={
            <ChartTooltipContent
              hideLabel
              nameKey="name"
              formatter={(value, _name, item) => {
                const datum = item.payload as { name: string; value: number };
                return (
                  <div className="flex w-full justify-between gap-4">
                    <span className="text-muted-foreground">{datum.name}</span>
                    <span className="font-mono font-medium tabular-nums">
                      {Number(value).toLocaleString()}
                    </span>
                  </div>
                );
              }}
            />
          }
        />
        <Pie
          data={dataWithFill}
          dataKey="value"
          nameKey="name"
          innerRadius={96}
          outerRadius={138}
          strokeWidth={2}
          paddingAngle={2}
          onMouseEnter={(_, i) => setActiveIndex(i)}
          onMouseLeave={() => setActiveIndex(null)}
          onClick={(d: unknown) => {
            const payload = (d as { name?: string } | undefined)?.name;
            if (payload && onSliceClick) onSliceClick(payload);
          }}
        >
          {dataWithFill.map((d) => (
            <Cell key={d.key} fill={d.fill} className="cursor-pointer" />
          ))}

          {/* Displays aggregate total or hovered slice details in donut center. */}
          <Label
            content={({ viewBox }) => {
              if (!viewBox || !('cx' in viewBox) || !('cy' in viewBox))
                return null;
              const cx = viewBox.cx ?? 0;
              const cy = viewBox.cy ?? 0;
              return (
                <text
                  x={cx}
                  y={cy}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className="select-none"
                >
                  <tspan
                    x={cx}
                    y={cy - 8}
                    className="fill-foreground text-4xl font-bold tabular-nums tracking-tight"
                  >
                    {centerLine1}
                  </tspan>
                  <tspan
                    x={cx}
                    y={cy + 22}
                    className="fill-muted-foreground text-sm font-medium"
                  >
                    {centerLine2}
                  </tspan>
                </text>
              );
            }}
          />
        </Pie>
      </PieChart>
    </ChartContainer>
  );
}
