import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { TradesView } from "@/components/trades-view";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BOTS, getAllTrades, getBotSummaries, getEquitySeries } from "@/lib/bots";

export const dynamic = "force-dynamic";
export const metadata = { title: "Video-Bots · Bot-Battle" };

const VIDEO_KEYS = ["futures", "futures_grid"] as const;

export default async function VideoBotsPage() {
  const [bots, equity, trades] = await Promise.all([
    getBotSummaries(),
    getEquitySeries(),
    getAllTrades(),
  ]);
  const selected = bots.filter((bot) => VIDEO_KEYS.includes(bot.key as typeof VIDEO_KEYS[number]));
  const meta = BOTS.filter((bot) => VIDEO_KEYS.includes(bot.key as typeof VIDEO_KEYS[number]));
  const filtered = trades.filter((trade) => VIDEO_KEYS.includes(trade.botKey as typeof VIDEO_KEYS[number]));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-orange-400">
          Strategien aus dem Video-Experiment
        </div>
        <h1 className="mt-1 text-2xl font-bold">Video-Bots</h1>
        <p className="text-sm text-muted-foreground">
          Hebelstrategien werden wegen abweichendem Kapital und Risiko separat dargestellt.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {selected.map((bot) => <BotCard key={bot.key} bot={bot} />)}
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">Hebel-Equity</CardTitle></CardHeader>
        <CardContent><EquityChart data={equity} includeKeys={["futures", "futures_grid"]} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle className="text-base">Trades der Video-Bots</CardTitle></CardHeader>
        <CardContent>
          <TradesView
            trades={filtered}
            bots={meta.map((bot) => ({ key: bot.key, nickname: bot.nickname }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}
