import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { TradesView } from "@/components/trades-view";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ACTIVE_BOTS, ACTIVE_BOT_KEYS, getAllTrades, getBotSummaries, getEquitySeries, isActiveBotKey } from "@/lib/bots";
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
  const selected = bots.filter((bot) => isActiveBotKey(bot.key));
  const filtered = trades.filter((trade) => isActiveBotKey(trade.botKey));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            Geprüft und überarbeitet
          </div>
          <h1 className="mt-1 text-2xl font-bold">Aktive Bots</h1>
          <p className="text-sm text-muted-foreground">
            Nur die beiden verbesserten Strategien nehmen noch an der aktuellen Runde teil.
          </p>
        </div>
        <Badge variant="outline" className="gap-1.5 border-emerald-500/35 text-emerald-300">
          <CheckCircle2 className="size-3.5" />
          2 Strategien aktiv
        </Badge>
      </div>

      <Card className="border-primary/25 bg-card/85">
        <CardContent className="flex items-start gap-3 py-4">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
          <div>
            <p className="text-sm font-semibold">Alte Varianten sind aus der aktiven Ansicht entfernt.</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Der Turbo und Pump.fun V2 dienen nicht mehr als laufende Kandidaten. Historische Daten bleiben im Hintergrund erhalten.
            </p>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        {selected.map((bot) => <BotCard key={bot.key} bot={bot} />)}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {ACTIVE_BOT_KEYS.map((key) => {
          const bot = ACTIVE_BOTS.find((item) => item.key === key);
          return (
            <Card key={key}>
              <CardHeader>
                <CardTitle className="text-base">{bot?.nickname ?? key} · Wert-Verlauf</CardTitle>
              </CardHeader>
              <CardContent><EquityChart data={equity} includeKeys={[key]} /></CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Trades der aktiven Bots</CardTitle></CardHeader>
        <CardContent>
          <TradesView
            trades={filtered}
            bots={ACTIVE_BOTS.map((bot) => ({ key: bot.key, nickname: bot.nickname }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}
