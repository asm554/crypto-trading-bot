"use client";

import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type { EquityPoint } from "@/lib/bots";

// Farben kommen aus globals.css (--bot-*), damit Karten und Chart identisch sind.
const config = {
  dca: { label: "Der Brave", color: "var(--bot-dca)" },
  momentum: { label: "Der Zocker", color: "var(--bot-momentum)" },
  meanrev: { label: "Der Contrarian", color: "var(--bot-meanrev)" },
  arb: { label: "Der Pedant", color: "var(--bot-arb)" },
  daytrade: { label: "Der Zappler", color: "var(--bot-daytrade)" },
  memecoin: { label: "Der Onchain", color: "var(--bot-memecoin)" },
} satisfies ChartConfig;

export function EquityChart({ data }: { data: EquityPoint[] }) {
  if (data.length < 2) {
    return (
      <div className="flex h-[260px] flex-col items-center justify-center gap-1 text-center text-sm text-muted-foreground">
        <p>Noch kein Verlauf vorhanden.</p>
        <p className="text-xs">Sobald die Bots laufen, erscheint hier der Wert-Verlauf.</p>
      </div>
    );
  }

  return (
    <ChartContainer config={config} className="h-[260px] w-full">
      <LineChart data={data} margin={{ left: 4, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          minTickGap={40}
          tickFormatter={(t) =>
            new Date(t * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })
          }
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={44}
          tickFormatter={(v) => `${v} €`}
          domain={["dataMin - 2", "dataMax + 2"]}
        />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        {(["dca", "momentum", "meanrev", "arb", "daytrade", "memecoin"] as const).map((key) => (
          <Line
            key={key}
            dataKey={key}
            type="monotone"
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            dot={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}
