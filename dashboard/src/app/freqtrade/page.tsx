import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { FlaskConical, ShieldCheck } from "lucide-react";
import { getFreqtradeStatus } from "@/lib/freqtrade";
import { clockTime, eur, pnlToneClass, signedEur, signedPct } from "@/lib/format";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function FreqtradePage() {
  const status = await getFreqtradeStatus();
  const stateLabel = {
    running: "RUNNING",
    stale: "SYNC VERALTET",
    waiting: "WARTE AUF DATEN",
    unconfigured: "NICHT KONFIGURIERT",
  }[status.state];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="flex items-center gap-2 text-primary">
          <FlaskConical aria-hidden className="size-5" />
          <span className="font-mono text-xs font-bold uppercase tracking-[0.16em]">Separater Paper-Bot</span>
        </div>
        <h1 className="mt-2 text-2xl font-bold">Freqtrade</h1>
        <p className="mt-1 text-sm text-muted-foreground">Eigenständige Kraken-Simulation neben dem Bot-Battle.</p>
      </div>

      <Card className="border-emerald-500/35">
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-5">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 size-5 text-emerald-500" aria-hidden />
            <div>
              <p className="font-semibold">Paper-Modus aktiv</p>
              <p className="text-sm text-muted-foreground">Dry-Run auf Kraken; Live-Orders sind deaktiviert.</p>
            </div>
          </div>
          <Badge variant={status.state === "running" ? "default" : "secondary"}>
            {status.state === "running" ? "Alle 30 Sek. synchronisiert" : stateLabel}
          </Badge>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">Status</CardTitle></CardHeader>
          <CardContent>
            <div className="font-mono text-sm">{stateLabel}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {status.lastSync ? `Letzter Sync ${clockTime(status.lastSync)}` : "Noch kein Snapshot"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">Netto-Equity</CardTitle></CardHeader>
          <CardContent>
            <div className="font-mono text-2xl tabular-nums">{eur(status.equityEur)}</div>
            <div className={cn("font-mono text-sm tabular-nums", pnlToneClass(status.totalPnlEur))}>
              {signedEur(status.totalPnlEur)} ({signedPct(status.pnlPct)})
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">Kapital</CardTitle></CardHeader>
          <CardContent>
            <div className="font-mono text-sm tabular-nums">{eur(status.cashEur)} frei</div>
            <div className="mt-1 text-xs text-muted-foreground">Startkapital 1.000 €</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">Positionen</CardTitle></CardHeader>
          <CardContent>
            <div className="font-mono text-2xl tabular-nums">{status.openPositions}</div>
            <div className="mt-1 text-xs text-muted-foreground">aktuell offen</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="font-heading text-base">Performance</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <div><div className="text-xs text-muted-foreground">Gesamt</div><div className={cn("font-mono tabular-nums", pnlToneClass(status.totalPnlEur))}>{signedEur(status.totalPnlEur)}</div></div>
          <div><div className="text-xs text-muted-foreground">Realisiert</div><div className={cn("font-mono tabular-nums", pnlToneClass(status.realizedPnlEur))}>{signedEur(status.realizedPnlEur)}</div></div>
          <div><div className="text-xs text-muted-foreground">Offen</div><div className={cn("font-mono tabular-nums", pnlToneClass(status.unrealizedPnlEur))}>{signedEur(status.unrealizedPnlEur)}</div></div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-heading text-base">Letzte Trades</CardTitle></CardHeader>
        <CardContent>
          {status.trades.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              Noch keine Trades — die Strategie wartet auf ein gültiges Einstiegssignal.
            </div>
          ) : (
            <Table>
              <TableHeader><TableRow><TableHead>Zeit</TableHead><TableHead>Paar</TableHead><TableHead className="text-right">Betrag</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Ergebnis</TableHead></TableRow></TableHeader>
              <TableBody>
                {status.trades.slice(0, 20).map((trade) => (
                  <TableRow key={trade.id}>
                    <TableCell className="font-mono text-muted-foreground">{clockTime(trade.timestamp)}</TableCell>
                    <TableCell className="font-mono">{trade.pair}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{eur(trade.sizeEur)}</TableCell>
                    <TableCell><Badge variant={trade.resolved ? "outline" : "secondary"}>{trade.resolved ? "geschlossen" : "offen"}</Badge></TableCell>
                    <TableCell className={cn("text-right font-mono tabular-nums", trade.pnlEur == null ? "text-muted-foreground" : pnlToneClass(trade.pnlEur))}>{trade.pnlEur == null ? "—" : signedEur(trade.pnlEur)}</TableCell>
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
