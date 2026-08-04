import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { TradesView } from "@/components/trades-view";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ACTIVE_BOTS,
  LEVERAGED_ACTIVE_BOT_KEYS,
  STANDARD_ACTIVE_BOT_KEYS,
  getAllTrades,
  getBotSummaries,
  getEquitySeries,
  isActiveBotKey,
  isBotRunning,
  isCurrentRoundTrade,
} from "@/lib/bots";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, ShieldCheck } from "lucide-react";

export const dynamic = "force-dynamic";
export const metadata = { title: "Aktive Bots · Bot-Battle" };

export default async function VideoBotsPage() {
  const [bots, equity, trades] = await Promise.all([
    getBotSummaries(),
    getEquitySeries(),
    getAllTrades(),
  ]);
  const selected = bots.filter((bot) => isActiveBotKey(bot.key) && isBotRunning(bot));
  const selectedKeys = new Set<string>(selected.map((bot) => bot.key));
  const filtered = trades.filter(
    (trade) => isCurrentRoundTrade(trade) && selectedKeys.has(trade.botKey),
  );
  const standardKeys = selected
    .filter((bot) => (STANDARD_ACTIVE_BOT_KEYS as readonly string[]).includes(bot.key))
    .map((bot) => bot.key);
  const leveragedKeys = selected
    .filter((bot) => (LEVERAGED_ACTIVE_BOT_KEYS as readonly string[]).includes(bot.key))
    .map((bot) => bot.key);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            Live und historisch eingeordnet
          </div>
          <h1 className="mt-1 text-2xl font-bold">Aktive Bots</h1>
          <p className="text-sm text-muted-foreground">
            Die vorhandenen Paper-Bots zeigen jetzt zusätzlich ihren belastbaren Backtest-Status.
          </p>
        </div>
        <Badge variant="outline" className="gap-1.5 border-emerald-500/35 text-emerald-300">
          <CheckCircle2 className="size-3.5" />
          {selected.length} Strategien sichtbar
        </Badge>
      </div>

      <Card className="border-primary/25 bg-card/85">
        <CardContent className="flex items-start gap-3 py-4">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
          <div>
            <p className="text-sm font-semibold">Live-Ergebnis und Forschungsstatus sind getrennte Kennzahlen.</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Eine grüne Live-Position beweist noch keine robuste Strategie. Jede Karte erklärt deshalb den Stand aus Bull-, Bear- und Current-Tests.
            </p>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {selected.map((bot) => <BotCard key={bot.key} bot={bot} />)}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(22rem,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">500-€-Paper-Battle · Wert-Verlauf</CardTitle>
          </CardHeader>
          <CardContent>
            <EquityChart data={equity} includeKeys={standardKeys} />
          </CardContent>
        </Card>
        {leveragedKeys.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">500-€-Signal-Klasse · Wert-Verlauf</CardTitle>
            </CardHeader>
            <CardContent>
              <EquityChart data={equity} includeKeys={leveragedKeys} />
            </CardContent>
          </Card>
        )}
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Trades der aktiven Bots</CardTitle></CardHeader>
        <CardContent>
          <TradesView
            trades={filtered}
            bots={ACTIVE_BOTS
              .filter((bot) => selectedKeys.has(bot.key))
              .map((bot) => ({ key: bot.key, nickname: bot.nickname }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}
