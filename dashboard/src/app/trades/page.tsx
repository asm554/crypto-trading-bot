import { BOTS, getAllTrades } from "@/lib/bots";
import { TradesView } from "@/components/trades-view";
import { Card, CardContent } from "@/components/ui/card";

export const dynamic = "force-dynamic";

export const metadata = { title: "Trading-Bots · Trades" };

export default async function TradesPage() {
  const trades = await getAllTrades();
  const bots = BOTS.map((b) => ({ key: b.key, nickname: b.nickname }));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">Trades</h1>
        <p className="text-sm text-muted-foreground">
          Alle Trades der fünf Bots, nach Status und Bot filterbar.
        </p>
      </div>

      <Card>
        <CardContent className="pt-5">
          <TradesView trades={trades} bots={bots} />
        </CardContent>
      </Card>
    </div>
  );
}
