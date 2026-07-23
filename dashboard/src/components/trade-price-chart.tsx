"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type { PricePoint } from "@/lib/bots";

const config = {
  price: { label: "Kurs", color: "var(--primary)" },
} satisfies ChartConfig;

export function TradePriceChart({
  data,
  entryPrice,
  exitPrice,
  entryTs,
  exitTs,
  breakEvenPrice,
}: {
  data: PricePoint[];
  entryPrice: number;
  exitPrice: number | null;
  entryTs: number;
  exitTs: number | null;
  breakEvenPrice: number | null;
}) {
  const visibleEntry = nearestPoint(data, entryTs);
  const visibleExit = exitTs ? nearestPoint(data, exitTs) : null;
  const spanSeconds = Math.max(0, (data.at(-1)?.t ?? 0) - (data[0]?.t ?? 0));

  return (
    <ChartContainer config={config} className="h-[320px] w-full">
      <LineChart data={data} margin={{ top: 18, right: 18, bottom: 4, left: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          tickLine={false}
          axisLine={false}
          minTickGap={45}
          tickFormatter={(value) =>
            spanSeconds <= 48 * 3600
              ? new Date(Number(value) * 1000).toLocaleTimeString("de-DE", {
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : new Date(Number(value) * 1000).toLocaleDateString("de-DE", {
                  day: "2-digit",
                  month: "2-digit",
                })
          }
        />
        <YAxis
          domain={["dataMin - (dataMax-dataMin)*0.08", "dataMax + (dataMax-dataMin)*0.08"]}
          tickLine={false}
          axisLine={false}
          width={70}
          tickFormatter={(value) => formatAxisPrice(Number(value))}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(value) =>
                new Date(Number(value) * 1000).toLocaleString("de-DE")
              }
            />
          }
        />
        <ReferenceLine
          y={entryPrice}
          stroke="var(--bot-momentum)"
          strokeDasharray="5 4"
          label={{ value: "Entry", fill: "var(--bot-momentum)", position: "insideTopLeft" }}
        />
        {exitPrice != null && (
          <ReferenceLine
            y={exitPrice}
            stroke="var(--bot-meanrev)"
            strokeDasharray="5 4"
            label={{ value: "Exit", fill: "var(--bot-meanrev)", position: "insideBottomLeft" }}
          />
        )}
        {breakEvenPrice != null && (
          <ReferenceLine
            y={breakEvenPrice}
            stroke="var(--muted-foreground)"
            strokeDasharray="2 4"
            ifOverflow="extendDomain"
            label={{ value: "Gebühren-Break-even", fill: "var(--muted-foreground)", position: "insideTopRight" }}
          />
        )}
        <Line
          dataKey="price"
          type="monotone"
          stroke="var(--color-price)"
          strokeWidth={2.5}
          dot={false}
          activeDot={{ r: 5 }}
        />
        {visibleEntry && (
          <ReferenceDot
            x={visibleEntry.t}
            y={visibleEntry.price}
            r={5}
            fill="var(--bot-momentum)"
            stroke="var(--background)"
            strokeWidth={2}
          />
        )}
        {visibleExit && (
          <ReferenceDot
            x={visibleExit.t}
            y={visibleExit.price}
            r={5}
            fill="var(--bot-meanrev)"
            stroke="var(--background)"
            strokeWidth={2}
          />
        )}
      </LineChart>
    </ChartContainer>
  );
}

function formatAxisPrice(value: number): string {
  const digits = value < 1 ? 5 : value < 10 ? 4 : value < 100 ? 3 : 2;
  return value.toLocaleString("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function nearestPoint(data: PricePoint[], timestamp: number): PricePoint | null {
  return data.reduce<PricePoint | null>((best, point) => {
    if (!best) return point;
    return Math.abs(point.t - timestamp) < Math.abs(best.t - timestamp) ? point : best;
  }, null);
}
