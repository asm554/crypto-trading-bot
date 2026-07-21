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
  return (
    <Card
      className={cn(
        "relative gap-0 overflow-hidden transition-shadow hover:shadow-[0_0_28px_-10px_var(--glow)]",
        isLeader && "border-primary/50 shadow-[0_0_30px_-16px_var(--glow)]"
      )}
      style={{ "--glow": botColor } as CSSProperties}
    >
      {/* Erkennungsfarbe des Bots als Leuchtkante oben */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-0.5"
        style={{ background: `linear-gradient(to right, ${botColor}, transparent 85%)` }}
      />
      <CardHeader className="pb-4">
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
            <p className="mt-1 max-w-[15rem] text-xs text-muted-foreground">{bot.tagline}</p>
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

        <div className="mt-4 flex items-end justify-between">
          <div>
            <div className="font-mono text-2xl font-semibold tabular-nums">{eur(bot.equityEur)}</div>
            <div className={cn("font-mono text-sm font-medium tabular-nums", pnlToneClass(bot.totalPnlEur))}>
              {signedEur(bot.totalPnlEur)} ({signedPct(bot.pnlPct)})
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

      <CardContent className="grid grid-cols-2 gap-x-4 gap-y-3 py-4 sm:grid-cols-4">
        <Stat label="Bargeld" value={eur(bot.cashEur)} />
        <Stat label="Gewinn realisiert" value={signedEur(bot.realizedPnlEur)} tone={pnlToneClass(bot.realizedPnlEur)} />
        <Stat label="noch offen" value={signedEur(bot.unrealizedPnlEur)} tone={pnlToneClass(bot.unrealizedPnlEur)} />
        <Stat label="Trades gesamt" value={String(bot.tradeCount)} />
      </CardContent>
    </Card>
  );
}
