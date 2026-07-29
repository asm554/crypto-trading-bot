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
  currentPrice,
  plannedExitPrice,
  exitPlan,
}: {
  data: PricePoint[];
  entryPrice: number;
  exitPrice: number | null;
  entryTs: number;
  exitTs: number | null;
  breakEvenPrice: number | null;
  currentPrice: number | null;
  plannedExitPrice: number | null;
  exitPlan: string;
}) {
  const visibleEntry = nearestPoint(data, entryTs);
  const visibleExit = exitTs ? nearestPoint(data, exitTs) : null;
  const spanSeconds = Math.max(0, (data.at(-1)?.t ?? 0) - (data[0]?.t ?? 0));

  return (
    <div className="space-y-4">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <ChartMarker
          color="var(--bot-momentum)"
          label="Kauf / Entry"
          value={formatPrice(entryPrice)}
        />
        <ChartMarker
          color="var(--muted-foreground)"
          label="Break-even"
          value={breakEvenPrice == null ? "modellabhängig" : formatPrice(breakEvenPrice)}
        />
        <ChartMarker
          color="var(--bot-futures-grid-signal)"
          label="Geplanter Exit"
          value={plannedExitPrice == null ? exitPlan : formatPrice(plannedExitPrice)}
        />
        <ChartMarker
          color="var(--bot-memecoin)"
          label="Aktueller Kurs"
          value={currentPrice == null ? "nicht verfügbar" : formatPrice(currentPrice)}
        />
      </div>

      <ChartContainer config={config} className="h-[340px] w-full">
        <LineChart data={data} margin={{ top: 30, right: 22, bottom: 4, left: 8 }}>
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
          ifOverflow="extendDomain"
          label={{ value: "Kauf", fill: "var(--bot-momentum)", position: "insideTopLeft" }}
        />
        {exitPrice != null && (
          <ReferenceLine
            y={exitPrice}
            stroke="var(--bot-meanrev)"
            strokeDasharray="5 4"
            ifOverflow="extendDomain"
            label={{ value: "Verkauft", fill: "var(--bot-meanrev)", position: "insideBottomLeft" }}
          />
        )}
        {breakEvenPrice != null && (
          <ReferenceLine
            y={breakEvenPrice}
            stroke="var(--muted-foreground)"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            ifOverflow="extendDomain"
            label={{ value: `Break-even · ${formatAxisPrice(breakEvenPrice)} €`, fill: "var(--muted-foreground)", position: "insideBottomRight" }}
          />
        )}
        {plannedExitPrice != null && (
          <ReferenceLine
            y={plannedExitPrice}
            stroke="var(--bot-futures-grid-signal)"
            strokeWidth={2}
            strokeDasharray="7 4"
            ifOverflow="extendDomain"
            label={{
              value: `Geplanter Exit · ${formatAxisPrice(plannedExitPrice)} €`,
              fill: "var(--bot-futures-grid-signal)",
              position: "insideTopRight",
            }}
          />
        )}
        {currentPrice != null && (
          <ReferenceLine
            y={currentPrice}
            stroke="var(--bot-memecoin)"
            strokeWidth={1.5}
            strokeDasharray="6 3"
            ifOverflow="extendDomain"
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
    </div>
  );
}

function ChartMarker({
  color,
  label,
  value,
}: {
  color: string;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span
          className="h-0.5 w-5 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden="true"
        />
        {label}
      </div>
      <div className="mt-1 font-mono text-sm font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function formatAxisPrice(value: number): string {
  const digits = value < 1 ? 5 : value < 10 ? 4 : value < 100 ? 3 : 2;
  return value.toLocaleString("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatPrice(value: number): string {
  return `${formatAxisPrice(value)} €`;
}

function nearestPoint(data: PricePoint[], timestamp: number): PricePoint | null {
  return data.reduce<PricePoint | null>((best, point) => {
    if (!best) return point;
    return Math.abs(point.t - timestamp) < Math.abs(best.t - timestamp) ? point : best;
  }, null);
}
