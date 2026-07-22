import { getFreqtradeEquitySeries, getFreqtradeSummary, getFreqtradeTrades } from "@/lib/bots";
import { EquityChart } from "@/components/equity-chart";
import { AutoRefresh } from "@/components/auto-refresh";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { eur, signedEur, signedPct, pnlToneClass, clockTime, runtime } from "@/lib/format";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Trading-Bots · Freqtrade" };

export default async function FreqtradePage() {
  const [bot, equity, trades] = await Promise.all([getFreqtradeSummary(), getFreqtradeEquitySeries(), getFreqtradeTrades()]);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Freqtrade</h1>
          <p className="text-sm text-muted-foreground">Eigenständige Paper-Trading-Ansicht — nicht Teil des Bot-Battles.</p>
        </div>
        <AutoRefresh />
      </div>

      <Card>
        <CardContent className="pt-5">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <div className="text-sm font-medium">30-Trades-Fortschritt</div>
              <div className="mt-1 text-xs text-muted-foreground">Eigene Freqtrade-Runde — getrennt vom Bot-Battle.</div>
            </div>
            <div className="font-mono text-sm font-semibold tabular-nums">{Math.min(bot.tradeCount, 30)} / 30</div>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-secondary" aria-label={`${Math.min(bot.tradeCount, 30)} von 30 Trades`} role="progressbar" aria-valuemin={0} aria-valuemax={30} aria-valuenow={Math.min(bot.tradeCount, 30)}>
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.min((bot.tradeCount / 30) * 100, 100)}%` }} />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["Equity", eur(bot.equityEur)],
          ["Bargeld", eur(bot.cashEur)],
          ["Gesamt-PnL", `${signedEur(bot.totalPnlEur)} (${signedPct(bot.pnlPct)})`],
          ["Offene Trades", String(bot.openPositions)],
        ].map(([label, value]) => (
          <Card key={label as string}><CardContent className="pt-5"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{value}</div></CardContent></Card>
        ))}
      </div>

      <Card>
        <CardHeader><CardTitle className="font-heading text-base font-bold">Status</CardTitle></CardHeader>
        <CardContent className="grid gap-4 text-sm sm:grid-cols-3">
          <div><div className="text-muted-foreground">Betriebsmodus</div><div className="mt-1 font-medium">Dry-Run / Paper-Trading</div></div>
          <div><div className="text-muted-foreground">Laufzeit seit</div><div className="mt-1 font-mono">{runtime(bot.runtimeStartedAt)}</div></div>
          <div><div className="text-muted-foreground">Dashboard-Sync</div><div className="mt-1 flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-emerald-500" /> Read-only, ca. 30 Sekunden</div></div>
        </CardContent>
      </Card>

      <Card><CardHeader><CardTitle className="font-heading text-base font-bold">Equity-Verlauf</CardTitle></CardHeader><CardContent><EquityChart data={equity} /></CardContent></Card>

      <Card>
        <CardHeader><CardTitle className="font-heading text-base font-bold">Freqtrade-Trades</CardTitle></CardHeader>
        <CardContent>
          {trades.length === 0 ? <div className="py-10 text-center text-sm text-muted-foreground">Noch keine Freqtrade-Trades.</div> : (
            <Table><TableHeader><TableRow><TableHead>Zeit</TableHead><TableHead>Paar</TableHead><TableHead>Seite</TableHead><TableHead className="text-right">Betrag</TableHead><TableHead>Status</TableHead><TableHead className="text-right">PnL</TableHead></TableRow></TableHeader><TableBody>
              {trades.map((t) => <TableRow key={t.id}><TableCell className="font-mono text-muted-foreground">{clockTime(t.timestamp)}</TableCell><TableCell className="font-mono">{t.pair}</TableCell><TableCell>{t.side}</TableCell><TableCell className="text-right font-mono tabular-nums">{eur(t.sizeEur)}</TableCell><TableCell><Badge variant={t.resolved ? "outline" : "secondary"}>{t.resolved ? "geschlossen" : "offen"}</Badge></TableCell><TableCell className={cn("text-right font-mono tabular-nums", t.pnlEur != null ? pnlToneClass(t.pnlEur) : "text-muted-foreground")}>{t.pnlEur == null ? "—" : signedEur(t.pnlEur)}</TableCell></TableRow>)}
            </TableBody></Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
