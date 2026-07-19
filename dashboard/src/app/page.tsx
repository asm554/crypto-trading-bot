import { getBotSummaries, getEquitySeries, getRecentTrades } from "@/lib/bots";
import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { AutoRefresh } from "@/components/auto-refresh";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { eur, signedEur, signedPct, pnlToneClass, clockTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [bots, equity, trades] = await Promise.all([
    getBotSummaries(),
    getEquitySeries(),
    getRecentTrades(20),
  ]);

  const totalEquity = bots.reduce((s, b) => s + b.equityEur, 0);
  const totalPnl = bots.reduce((s, b) => s + b.totalPnlEur, 0);
  const invested = bots.length * 100;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Übersicht</h1>
          <p className="text-sm text-muted-foreground">
            {bots.length} Bots handeln mit Spielgeld gegeneinander. Wer macht am meisten daraus?
          </p>
        </div>
        <AutoRefresh />
      </div>

      {/* Gesamt-Kachel */}
      <Card className="relative overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_120%_at_15%_0%,_oklch(0.86_0.155_92_/_9%),_transparent_60%)]"
        />
        <CardContent className="relative flex flex-wrap items-center justify-between gap-6 py-5">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Gesamtwert aller Bots
            </div>
            <div className="mt-1 font-mono text-4xl font-semibold tabular-nums">
              {eur(totalEquity)}
            </div>
          </div>
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Gewinn/Verlust gesamt
            </div>
            <div className={cn("mt-1 font-mono text-2xl font-semibold tabular-nums", pnlToneClass(totalPnl))}>
              {signedEur(totalPnl)}{" "}
              <span className="text-base">({signedPct((totalPnl / invested) * 100)})</span>
            </div>
          </div>
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Eingesetztes Startkapital
            </div>
            <div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{eur(invested)}</div>
          </div>
        </CardContent>
      </Card>

      {/* Bot-Karten */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {bots.map((bot) => (
          <BotCard key={bot.key} bot={bot} />
        ))}
      </div>

      {/* Verlauf */}
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base font-bold">Wert-Verlauf</CardTitle>
        </CardHeader>
        <CardContent>
          <EquityChart data={equity} />
        </CardContent>
      </Card>

      {/* Letzte Trades */}
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base font-bold">Letzte Trades</CardTitle>
        </CardHeader>
        <CardContent>
          {trades.length === 0 ? (
            <div className="flex flex-col items-center gap-1 py-10 text-center text-sm text-muted-foreground">
              <p>Noch keine Trades.</p>
              <p className="text-xs">
                Sobald ein Bot kauft oder verkauft, erscheinen die Trades hier.
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Zeit</TableHead>
                  <TableHead>Bot</TableHead>
                  <TableHead>Coin</TableHead>
                  <TableHead className="text-right">Betrag</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Ergebnis</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-mono text-muted-foreground">{clockTime(t.timestamp)}</TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1.5">
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ background: `var(--bot-${t.botKey})` }}
                        />
                        {t.bot}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono">{t.pair}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{eur(t.sizeEur)}</TableCell>
                    <TableCell>
                      <Badge variant={t.resolved ? "outline" : "secondary"} className="text-xs">
                        {t.resolved ? "geschlossen" : "offen"}
                      </Badge>
                    </TableCell>
                    <TableCell className={cn("text-right font-mono tabular-nums", t.pnlEur != null ? pnlToneClass(t.pnlEur) : "text-muted-foreground")}>
                      {t.pnlEur == null ? "—" : signedEur(t.pnlEur)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
