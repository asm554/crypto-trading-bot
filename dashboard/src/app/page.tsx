import {
  LEVERAGED_ACTIVE_BOT_KEYS,
  STANDARD_ACTIVE_BOT_KEYS,
  getAllTrades,
  getBotSummaries,
  getEquitySeries,
  isActiveBotKey,
} from "@/lib/bots";
import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import { AutoRefresh } from "@/components/auto-refresh";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Activity, CheckCircle2, ShieldCheck, WalletCards } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { clockTime, eur, pnlToneClass, signedEur, signedPct } from "@/lib/format";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [bots, equity, allTrades] = await Promise.all([
    getBotSummaries(),
    getEquitySeries(),
    getAllTrades(),
  ]);

  const activeBots = bots.filter((bot) => isActiveBotKey(bot.key));
  const standardBots = activeBots
    .filter((bot) => (STANDARD_ACTIVE_BOT_KEYS as readonly string[]).includes(bot.key))
    .sort((a, b) => {
      if (a.hasData !== b.hasData) return a.hasData ? -1 : 1;
      return b.equityEur - a.equityEur;
    });
  const leveragedBots = activeBots.filter((bot) =>
    (LEVERAGED_ACTIVE_BOT_KEYS as readonly string[]).includes(bot.key),
  );
  const activeTrades = allTrades.filter((trade) => isActiveBotKey(trade.botKey));
  const recentTrades = activeTrades.slice(0, 12);
  const totalStartingCapital = activeBots.reduce((sum, bot) => sum + bot.startingCapitalEur, 0);
  const totalEquity = activeBots.reduce((sum, bot) => sum + bot.equityEur, 0);
  const totalPnl = totalEquity - totalStartingCapital;
  const totalPnlPct = totalStartingCapital > 0 ? (totalPnl / totalStartingCapital) * 100 : 0;
  const openPositions = activeBots.reduce((sum, bot) => sum + bot.openPositions, 0);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-primary">
            Optimierte Paper-Trading-Runde
          </div>
          <h1 className="mt-1 text-2xl font-bold sm:text-3xl">Verbesserte Bots. Klare Gruppen.</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Im Dashboard laufen nur Strategien, deren Einstieg, Ausstieg oder Risikoschutz
            bereits gezielt überarbeitet wurde.
          </p>
        </div>
        <AutoRefresh />
      </div>

      <Card className="overflow-hidden border-primary/30 bg-card/90">
        <div className="h-0.5 bg-gradient-to-r from-primary via-emerald-400 to-fuchsia-400" />
        <CardContent className="grid gap-5 py-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(22rem,1fr)] lg:items-center">
          <div>
            <div className="flex items-center gap-2 text-emerald-300">
              <CheckCircle2 aria-hidden className="size-4" />
              <span className="font-mono text-xs font-semibold uppercase tracking-[0.16em]">
                Aktive Auswahl
              </span>
            </div>
            <h2 className="mt-2 font-heading text-xl font-bold">Nur die verbesserte Auswahl läuft</h2>
            <p className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground">
              Turbo, Pump.fun V2 und ungeprüfte Experimente beeinflussen diese Übersicht nicht
              mehr. Der 2×-Signal-Bot wird wegen seines höheren Startkapitals separat gezeigt.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-border">
            <Metric
              icon={Activity}
              label="Aktive Strategien"
              value={`${activeBots.length}`}
              hint={`${standardBots.length} Standard + ${leveragedBots.length} Signal`}
            />
            <Metric
              icon={WalletCards}
              label="Papierkapital"
              value={eur(totalEquity)}
              hint={`${eur(totalStartingCapital)} Start`}
            />
            <Metric
              icon={ShieldCheck}
              label="Offene Positionen"
              value={`${openPositions}`}
              hint={openPositions === 0 ? "Warten auf Signal" : "Werden überwacht"}
            />
            <Metric
              icon={Activity}
              label="Gemeinsames Ergebnis"
              value={signedEur(totalPnl)}
              hint={signedPct(totalPnlPct)}
              tone={pnlToneClass(totalPnl)}
            />
          </div>
        </CardContent>
      </Card>

      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="font-heading text-lg font-bold">100-€-Battle</h2>
            <p className="text-sm text-muted-foreground">
              Acht verbesserte Strategien mit demselben Startkapital, fair nach Netto-Equity sortiert.
            </p>
          </div>
          <Badge variant="outline" className="border-emerald-500/30 text-emerald-300">
            Nur verbesserte Versionen
          </Badge>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {standardBots.map((bot, index) => (
            <BotCard
              key={bot.key}
              bot={bot}
              rank={bot.hasData ? index + 1 : undefined}
              isLeader={bot.hasData && index === 0}
            />
          ))}
        </div>
      </section>

      <section className="border-t pt-6">
        <div className="mb-3">
          <h2 className="font-heading text-lg font-bold">1.000-€-Signal-Klasse</h2>
          <p className="text-sm text-muted-foreground">
            Separat geführt, weil Hebel und Startkapital nicht mit dem 100-€-Battle vergleichbar sind.
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          {leveragedBots.map((bot) => (
            <BotCard key={bot.key} bot={bot} />
          ))}
        </div>
      </section>

      <section>
        <div className="mb-3">
          <h2 className="font-heading text-lg font-bold">Wert-Entwicklung</h2>
          <p className="text-sm text-muted-foreground">
            Standard-Battle und Signal-Klasse bleiben wegen des unterschiedlichen Kapitals getrennt.
          </p>
        </div>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(22rem,1fr)]">
          <Card className="bg-card/85">
            <CardHeader className="pb-2">
              <CardTitle className="font-heading text-base font-bold">100-€-Battle</CardTitle>
            </CardHeader>
            <CardContent>
              <EquityChart data={equity} includeKeys={[...STANDARD_ACTIVE_BOT_KEYS]} />
            </CardContent>
          </Card>
          <Card className="bg-card/85">
            <CardHeader className="pb-2">
              <CardTitle className="font-heading text-base font-bold">Treppensteiger Signal</CardTitle>
            </CardHeader>
            <CardContent>
              <EquityChart data={equity} includeKeys={[...LEVERAGED_ACTIVE_BOT_KEYS]} />
            </CardContent>
          </Card>
        </div>
      </section>

      <Card className="bg-card/85">
        <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
          <div>
            <CardTitle className="font-heading text-base font-bold">Letzte aktive Trades</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Alte Bots und Vergleichsvarianten sind hier ausgeblendet.
            </p>
          </div>
          <Badge variant="secondary">{activeTrades.length} insgesamt</Badge>
        </CardHeader>
        <CardContent>
          {recentTrades.length === 0 ? (
            <div className="flex flex-col items-center gap-1 py-10 text-center text-sm text-muted-foreground">
              <p>Noch keine Trades der neuen Runde.</p>
              <p className="text-xs">Die Bots warten auf ein passendes Einstiegssignal.</p>
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
                {recentTrades.map((trade) => (
                  <TableRow key={trade.id}>
                    <TableCell className="font-mono text-muted-foreground">
                      {clockTime(trade.timestamp)}
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1.5">
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ background: `var(--bot-${trade.botKey})` }}
                        />
                        {trade.bot}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono">{trade.pair}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {eur(trade.sizeEur)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={trade.resolved ? "outline" : "secondary"} className="text-xs">
                        {trade.resolved ? "geschlossen" : "offen"}
                      </Badge>
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right font-mono tabular-nums",
                        trade.pnlEur != null ? pnlToneClass(trade.pnlEur) : "text-muted-foreground",
                      )}
                    >
                      {trade.pnlEur == null ? "—" : signedEur(trade.pnlEur)}
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

function Metric({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  hint: string;
  tone?: string;
}) {
  return (
    <div className="bg-card px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon aria-hidden className="size-3.5" />
        <span>{label}</span>
      </div>
      <div className={cn("mt-1 font-mono text-lg font-semibold tabular-nums", tone)}>{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}
