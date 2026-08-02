import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { TradesView } from "@/components/trades-view";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BOTS, getAllTrades, getBotSummaries, getEquitySeries } from "@/lib/bots";

export const dynamic = "force-dynamic";
export const metadata = { title: "Der HODLer · Bot-Battle" };

export default async function HodlPage() {
  const [bots, equity, trades] = await Promise.all([
    getBotSummaries(),
    getEquitySeries(),
    getAllTrades(),
  ]);
  const bot = bots.find((item) => item.key === "hodl");
  const meta = BOTS.find((item) => item.key === "hodl");
  const filtered = trades.filter((trade) => trade.botKey === "hodl");

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          Langfristiger Benchmark
        </div>
        <h1 className="mt-1 text-2xl font-bold">Der HODLer</h1>
        <p className="text-sm text-muted-foreground">
          Langfristige BTC-, ETH- und SOL-Allokation außerhalb des aktiven Rankings.
        </p>
      </div>
      {bot && <div className="max-w-md"><BotCard bot={bot} /></div>}
      <Card>
        <CardHeader><CardTitle className="text-base">HODL-Equity</CardTitle></CardHeader>
        <CardContent><EquityChart data={equity} includeKeys={["hodl"]} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle className="text-base">HODL-Trades</CardTitle></CardHeader>
        <CardContent>
          <TradesView
            trades={filtered}
            bots={meta ? [{ key: meta.key, nickname: meta.nickname }] : []}
          />
        </CardContent>
      </Card>
    </div>
  );
}
