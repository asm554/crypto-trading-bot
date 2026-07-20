import { getBotSummaries, getEquitySeries, getRecentTrades } from "@/lib/bots";
import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { AutoRefresh } from "@/components/auto-refresh";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CalendarDays, Trophy } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { calendarDate, eur, signedEur, signedPct, pnlToneClass, clockTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [bots, equity, trades] = await Promise.all([
    getBotSummaries(),
    getEquitySeries(),
    getRecentTrades(20),
  ]);

  const rankedBots = [...bots].sort((a, b) => {
    if (a.hasData !== b.hasData) return a.hasData ? -1 : 1;
    return b.equityEur - a.equityEur;
  });
  const competingBots = rankedBots.filter((bot) => bot.hasData);
  const leader = competingBots[0];
  const runnerUp = competingBots[1];
  const lead = leader && runnerUp ? leader.equityEur - runnerUp.equityEur : null;

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

      <Card className="relative overflow-hidden border-primary/35">
        <CardContent className="grid gap-6 py-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.8fr)] lg:items-end">
          {leader ? (
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-primary">
                <Trophy aria-hidden className="size-4" />
                <span className="font-mono text-xs font-semibold uppercase tracking-[0.16em]">Aktueller Spitzenreiter</span>
              </div>
              <div className="mt-3 flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
                <h2 className="font-heading text-3xl font-bold">{leader.nickname}</h2>
                <span className="text-sm text-muted-foreground">{leader.name}</span>
              </div>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{leader.tagline}</p>
              {leader.startedAt && (
                <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
                  <CalendarDays aria-hidden className="size-4" />
                  <span>Start der erfassten Runde: {calendarDate(leader.startedAt)}</span>
                </div>
              )}
            </div>
          ) : (
            <div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Trophy aria-hidden className="size-4" />
                <span className="font-mono text-xs font-semibold uppercase tracking-[0.16em]">Rangliste bereit</span>
              </div>
              <h2 className="mt-3 font-heading text-2xl font-bold">Noch kein Spitzenreiter</h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">Sobald ein Bot einen Trade oder Equity-Snapshot liefert, erscheint er hier als Rang 1.</p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4 border-t pt-5 lg:border-t-0 lg:border-l lg:pl-6 lg:pt-0">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Netto-Equity</div>
              <div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{leader ? eur(leader.equityEur) : "—"}</div>
              {leader && <div className={cn("mt-1 font-mono text-sm tabular-nums", pnlToneClass(leader.totalPnlEur))}>{signedEur(leader.totalPnlEur)} ({signedPct(leader.pnlPct)})</div>}
            </div>
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">{runnerUp ? "Vorsprung auf Rang 2" : "Bots im Ranking"}</div>
              <div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{runnerUp && lead != null ? signedEur(lead) : `${competingBots.length} aktiv`}</div>
              <div className="mt-1 text-sm text-muted-foreground">{runnerUp ? runnerUp.nickname : "Netto-Equity entscheidet die Platzierung."}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h2 className="font-heading text-lg font-bold">Rangliste</h2>
          <p className="text-sm text-muted-foreground">Sortiert nach aktueller Netto-Equity.</p>
        </div>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">{competingBots.length} mit Daten</span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {rankedBots.map((bot, index) => (
          <BotCard key={bot.key} bot={bot} rank={bot.hasData ? index + 1 : undefined} isLeader={bot.key === leader?.key} />
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
