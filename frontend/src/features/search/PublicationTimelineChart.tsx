import { useEffect, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  Brush,
  CartesianGrid,
  ReferenceDot,
  XAxis,
  YAxis,
} from 'recharts';
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart';

/**
 * Area chart visualizing annual publication trends, featuring zoomable
 * range brushes, cursor-anchored wheel scaling, and peak value markers.
 */

const chartConfig = {
  count: {
    label: 'Papers',
    color: '#00ACC1',
  },
} satisfies ChartConfig;

interface Props {
  data: { name: string; value: number }[];
  onYearClick?: (year: string) => void;
}

export function PublicationTimelineChart({ data, onYearClick }: Props) {
  const [hover, setHover] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Controlled range window synchronized between wheel zoom and brush drag.
  const lastIdx = Math.max(0, data.length - 1);
  const [range, setRange] = useState({ startIndex: 0, endIndex: lastIdx });

  // Resets viewport boundaries if the underlying dataset length changes.
  const [prevDataLength, setPrevDataLength] = useState(data.length);
  if (prevDataLength !== data.length) {
    setPrevDataLength(data.length);
    setRange({ startIndex: 0, endIndex: Math.max(0, data.length - 1) });
  }

  // Intercepts wheel events non-passively to prevent container page scroll.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || data.length < 2) return;

    const handler = (e: WheelEvent) => {
      // Require modifier key to avoid trapping regular page scroll.
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const ratio = Math.max(
        0,
        Math.min(1, (e.clientX - rect.left) / rect.width),
      );

      setRange((prev) => {
        const span = prev.endIndex - prev.startIndex;
        // Scale window proportionally according to wheel scroll direction.
        const factor = e.deltaY < 0 ? 0.85 : 1.18;
        const maxSpan = data.length - 1;
        const newSpan = Math.max(
          1,
          Math.min(maxSpan, Math.round(span * factor)),
        );
        if (newSpan === span) return prev;
        // Anchor zoom centering around data point under cursor.
        const anchorIdx = prev.startIndex + span * ratio;
        let startIndex = Math.round(anchorIdx - newSpan * ratio);
        startIndex = Math.max(0, Math.min(maxSpan - newSpan, startIndex));
        const endIndex = startIndex + newSpan;
        return { startIndex, endIndex };
      });
    };

    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, [data.length]);

  const peak = data.reduce(
    (p, c) => (c.value > p.value ? c : p),
    data[0] ?? { name: '', value: 0 },
  );

  return (
    <div
      ref={containerRef}
      className="h-[300px] w-full"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <ChartContainer config={chartConfig} className="h-full w-full">
        <AreaChart
          accessibilityLayer
          data={data}
          margin={{ left: 12, right: 12, top: 18, bottom: 4 }}
          onClick={(e: unknown) => {
            const chart = e as {
              activePayload?: Array<{ payload?: { name?: unknown } }>;
              activeLabel?: unknown;
            } | null;
            if (chart?.activePayload && chart.activePayload.length > 0) {
              const yearName = chart.activePayload[0]?.payload?.name;
              if (yearName) onYearClick?.(String(yearName));
            } else if (chart?.activeLabel) {
              onYearClick?.(String(chart.activeLabel));
            }
          }}
          style={{ cursor: onYearClick ? 'pointer' : 'default' }}
        >
          <defs>
            <linearGradient id="fillTimeline" x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="5%"
                stopColor="var(--color-count)"
                stopOpacity={0.5}
              />
              <stop
                offset="95%"
                stopColor="var(--color-count)"
                stopOpacity={0.02}
              />
            </linearGradient>
          </defs>

          <CartesianGrid vertical={false} strokeDasharray="3 3" />

          <XAxis
            dataKey="name"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            interval={4}
          />
          <YAxis tickLine={false} axisLine={false} width={32} />

          <ChartTooltip
            cursor={{ strokeDasharray: '3 3' }}
            content={<ChartTooltipContent indicator="line" />}
          />

          <Area
            dataKey="value"
            name="Papers"
            type="monotone"
            fill="url(#fillTimeline)"
            stroke="var(--color-count)"
            strokeWidth={2}
            dot={false}
            activeDot={{
              r: 5,
              stroke: 'var(--color-count)',
              strokeWidth: 2,
              fill: '#FFFFFF',
              cursor: onYearClick ? 'pointer' : 'default',
            }}
          />

          {/* Highlight dot identifying the peak publication year. */}
          {peak.value > 0 && (
            <ReferenceDot
              x={peak.name}
              y={peak.value}
              r={5}
              fill="var(--color-count)"
              stroke="var(--background)"
              strokeWidth={2}
              ifOverflow="extendDomain"
            />
          )}

          {/* Maintained in DOM to preserve range indices during hover state. */}
          <Brush
            dataKey="name"
            height={22}
            stroke="var(--color-count)"
            fill="rgba(0, 172, 193, 0.08)"
            travellerWidth={8}
            startIndex={range.startIndex}
            endIndex={range.endIndex}
            onChange={(r) => {
              if (
                typeof r?.startIndex === 'number' &&
                typeof r?.endIndex === 'number' &&
                (r.startIndex !== range.startIndex ||
                  r.endIndex !== range.endIndex)
              ) {
                setRange({ startIndex: r.startIndex, endIndex: r.endIndex });
              }
            }}
            className={
              'transition-opacity duration-200 ' +
              (hover ? 'opacity-100' : 'opacity-0 pointer-events-none')
            }
          />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}
