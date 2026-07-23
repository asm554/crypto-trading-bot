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
  pumpfun: { label: "Der PumpFun", color: "var(--bot-pumpfun)" },
  pumpfun_v2: { label: "Der PumpFun V2", color: "var(--bot-pumpfun-v2)" },
  surfer: { label: "Der Surfer", color: "var(--bot-surfer)" },
  scout: { label: "Der Spaeher", color: "var(--bot-scout)" },
  hodl: { label: "Der HODLer", color: "var(--bot-hodl)" },
  freqtrade: { label: "Freqtrade", color: "var(--bot-freqtrade)" },
  futures: { label: "Der Hebler", color: "var(--bot-futures)" },
} satisfies ChartConfig;

// Bei 6 Linien reicht Farbe allein nicht (Farbfehlsichtigkeit) — jede Linie
// bekommt zusätzlich ein eigenes Strich-Muster als zweites Unterscheidungsmerkmal.
const DASH: Record<keyof typeof config, string | undefined> = {
  dca: undefined,
  momentum: "6 3",
  meanrev: "2 2",
  arb: "8 3 2 3",
  daytrade: "1 3",
  memecoin: "10 2 2 2",
  pumpfun: "5 2",
  pumpfun_v2: "3 2 1 2",
  surfer: "12 3",
  scout: "4 2 1 2",
  hodl: "14 2",
  freqtrade: "6 2",
  futures: "10 2",
};

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
    <>
      <p className="sr-only">{describeLatest(data)}</p>
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
          {(Object.keys(config) as Array<keyof typeof config>).map((key) => (
            <Line
              key={key}
              dataKey={key}
              type="monotone"
              stroke={`var(--color-${key})`}
              strokeDasharray={DASH[key]}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ChartContainer>
    </>
  );
}

// Screenreader-Zusammenfassung: die Linien allein sind nicht vorlesbar.
function describeLatest(data: EquityPoint[]): string {
  const last = data[data.length - 1];
  const entries = (Object.keys(config) as Array<keyof typeof config>)
    .map((key) => ({ label: config[key].label as string, value: last[key] }))
    .filter((e): e is { label: string; value: number } => e.value != null)
    .sort((a, b) => b.value - a.value);
  if (entries.length === 0) return "Noch keine Wert-Verlaufsdaten.";
  const leader = entries[0];
  return `Wert-Verlauf, aktuell führt ${leader.label} mit ${leader.value.toFixed(2)} Euro. Alle Werte: ${entries
    .map((e) => `${e.label} ${e.value.toFixed(2)} Euro`)
    .join(", ")}.`;
}
