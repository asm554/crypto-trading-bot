import type { CSSProperties } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Trophy } from "lucide-react";
import type { BotSummary } from "@/lib/bots";
import { eur, signedEur, signedPct, pnlToneClass, relTime, runtime } from "@/lib/format";
import { cn } from "@/lib/utils";

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={cn("font-mono text-sm tabular-nums", tone)}>{value}</span>
    </div>
  );
}

export function BotCard({ bot, rank, isLeader = false }: { bot: BotSummary; rank?: number; isLeader?: boolean }) {
  const botColor = `var(--bot-${bot.key})`;
  const optimizationThreshold = 30;
  const isLongTermBenchmark = bot.key === "hodl";
  const optimizationReady = bot.closedTradeCount >= optimizationThreshold;
  const optimizationProgress = Math.min(100, (bot.closedTradeCount / optimizationThreshold) * 100);
  const tradesUntilOptimization = Math.max(0, optimizationThreshold - bot.closedTradeCount);

  return (
    <Card
      className={cn(
        "relative gap-0 overflow-hidden bg-card/85 transition-colors hover:border-foreground/20",
        isLeader && "border-primary/45"
      )}
      style={{ "--glow": botColor } as CSSProperties}
    >
      {/* Erkennungsfarbe des Bots als Leuchtkante oben */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-0.5"
        style={{ background: `linear-gradient(to right, ${botColor}, transparent 85%)` }}
      />
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: botColor, boxShadow: `0 0 8px ${botColor}` }}
              />
              <h3 className="font-heading text-base font-bold">{bot.nickname}</h3>
              <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                {bot.name}
              </Badge>
            </div>
            <p className="mt-1 max-w-[15rem] truncate text-xs text-muted-foreground" title={bot.tagline}>{bot.tagline}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {rank != null && (
              <Badge variant={isLeader ? "default" : "outline"} className="gap-1 font-mono text-xs tabular-nums">
                {isLeader && <Trophy aria-hidden className="size-3" />}
                Rang {rank}
              </Badge>
            )}
            <Badge variant="secondary" className="font-mono text-xs tabular-nums">
              {bot.openPositions} offen
            </Badge>
          </div>
        </div>

        <div className="mt-3 flex items-end justify-between">
          <div>
            <div className="font-mono text-2xl font-semibold tabular-nums">{eur(bot.equityEur)}</div>
            <div className={cn("font-mono text-sm font-medium tabular-nums", pnlToneClass(bot.totalPnlEur))} title={`gegen ${eur(bot.startingCapitalEur)} Startkapital`}>
              Netto-PnL {signedEur(bot.totalPnlEur)} ({signedPct(bot.pnlPct)})
            </div>
          </div>
          <div className="text-right text-xs text-muted-foreground">
            <div>{relTime(bot.lastActivity)}</div>
            <div className="mt-1 font-mono tabular-nums" title="Seit dem letzten Prozessstart">
              Läuft seit {runtime(bot.runtimeStartedAt)}
            </div>
          </div>
        </div>
      </CardHeader>

      <Separator />

      <CardContent className="grid grid-cols-2 gap-x-4 gap-y-3 py-3 sm:grid-cols-4">
        <Stat label="Bargeld" value={eur(bot.cashEur)} />
        <Stat label="Gewinn realisiert" value={signedEur(bot.realizedPnlEur)} tone={pnlToneClass(bot.realizedPnlEur)} />
        <Stat label="noch offen" value={signedEur(bot.unrealizedPnlEur)} tone={pnlToneClass(bot.unrealizedPnlEur)} />
        <Stat label="Trades gesamt" value={String(bot.tradeCount)} />

        {isLongTermBenchmark ? (
          <div className="col-span-full mt-1 flex items-center justify-between gap-3 border-t pt-3 text-xs text-muted-foreground">
            <span>Langfristiger Benchmark</span>
            <span className="font-mono">feste BTC · ETH · SOL-Verteilung</span>
          </div>
        ) : (
          <div
            className={cn(
              "col-span-full mt-1 border-t pt-3",
              optimizationReady
                ? "text-emerald-700 dark:text-emerald-300"
                : "text-amber-800 dark:text-amber-200"
            )}
          >
            <div className="flex items-center justify-between gap-3 text-xs font-semibold">
              <span>Abgeschlossene Trades</span>
              <span className="font-mono text-sm font-black tabular-nums">
                {bot.closedTradeCount}/{optimizationThreshold}
              </span>
            </div>
            <div
              role="progressbar"
              aria-label={`${bot.closedTradeCount} von ${optimizationThreshold} abgeschlossenen Trades bis zur nächsten Optimierung`}
              aria-valuemin={0}
              aria-valuemax={optimizationThreshold}
              aria-valuenow={Math.min(bot.closedTradeCount, optimizationThreshold)}
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-current/15"
            >
              <div
                className="h-full bg-current transition-[width]"
                style={{ width: `${optimizationProgress}%` }}
              />
            </div>
            <p className="mt-1.5 text-xs font-medium leading-tight">
              {optimizationReady
                ? "Optimierung kann jetzt geprüft werden"
                : `Noch ${tradesUntilOptimization} ${tradesUntilOptimization === 1 ? "Trade" : "Trades"} bis zur nächsten Optimierung`}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
