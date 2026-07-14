import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { BotSummary } from "@/lib/bots";
import { eur, signedEur, signedPct, pnlToneClass, relTime } from "@/lib/format";
import { cn } from "@/lib/utils";

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={cn("font-mono text-sm tabular-nums", tone)}>{value}</span>
    </div>
  );
}

export function BotCard({ bot }: { bot: BotSummary }) {
  return (
    <Card className="gap-0 overflow-hidden">
      <CardHeader className="pb-4">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{bot.nickname}</h3>
              <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                {bot.name}
              </Badge>
            </div>
            <p className="mt-1 max-w-[15rem] text-xs text-muted-foreground">{bot.tagline}</p>
          </div>
          <Badge variant="secondary" className="shrink-0 text-xs">
            {bot.openPositions} offen
          </Badge>
        </div>

        <div className="mt-4 flex items-end justify-between">
          <div>
            <div className="text-2xl font-semibold tabular-nums">{eur(bot.equityEur)}</div>
            <div className={cn("text-sm font-medium", pnlToneClass(bot.totalPnlEur))}>
              {signedEur(bot.totalPnlEur)} ({signedPct(bot.pnlPct)})
            </div>
          </div>
          <span className="text-xs text-muted-foreground">{relTime(bot.lastActivity)}</span>
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
