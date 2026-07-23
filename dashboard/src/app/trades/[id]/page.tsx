import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertTriangle, ArrowLeft, Clock3, Database, Target, TrendingDown, TrendingUp } from "lucide-react";
import { getTradeDetail } from "@/lib/bots";
import { TradePriceChart } from "@/components/trade-price-chart";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { calendarDate, eur, signedEur, signedPct } from "@/lib/format";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function TradeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const trade = await getTradeDetail(Number(id));
  if (!trade) notFound();

  const changePct = trade.entryPrice > 0
    ? ((trade.latestPrice - trade.entryPrice) / trade.entryPrice) * 100
    : 0;
  const durationEnd = trade.resolvedAt ?? trade.priceSeries.at(-1)?.t ?? trade.timestamp;
  const violatesCurrentExitRule = trade.resolved
    && trade.exitPrice != null
    && trade.breakEvenPrice != null
    && trade.exitPrice < trade.breakEvenPrice;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          href="/trades"
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
          Zurück zu allen Trades
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-heading text-2xl font-bold">{trade.pair}</h1>
          <Badge variant={trade.resolved ? "outline" : "secondary"}>
            {trade.resolved ? "geschlossen" : "offen"}
          </Badge>
          <span className="font-mono text-xs text-muted-foreground">Trade #{trade.id}</span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {trade.bot} · {trade.side.toUpperCase()} · eröffnet am {calendarDate(trade.timestamp)}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Entry" value={formatPrice(trade.entryPrice)} />
        <Metric label={trade.resolved ? "Exit" : "Letzter Kurs"} value={formatPrice(trade.latestPrice)} />
        <Metric
          label="Kursbewegung"
          value={signedPct(changePct)}
          tone={changePct >= 0 ? "up" : "down"}
        />
        <Metric
          label="Netto-PnL"
          value={trade.pnlEur == null ? "offen" : signedEur(trade.pnlEur)}
          tone={trade.pnlEur == null ? undefined : trade.pnlEur >= 0 ? "up" : "down"}
        />
      </div>

      {trade.targetPrice != null && trade.breakEvenPrice != null && (
        <Card className={cn("overflow-hidden", violatesCurrentExitRule && "border-amber-500/40")}>
          <CardContent className="grid gap-4 py-4 md:grid-cols-[auto_1fr_1fr_1fr] md:items-center">
            <div className={cn(
              "flex size-10 items-center justify-center rounded-full",
              violatesCurrentExitRule ? "bg-amber-500/15 text-amber-400" : "bg-emerald-500/15 text-emerald-400",
            )}>
              {violatesCurrentExitRule ? <AlertTriangle className="size-5" /> : <Target className="size-5" />}
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Gebühren-Break-even</div>
              <div className="mt-1 font-mono font-semibold">{formatPrice(trade.breakEvenPrice)}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Aktuelle Exit-Regel</div>
              <div className="mt-1 font-mono font-semibold text-emerald-400">
                {formatPrice(trade.targetPrice)} ({signedPct((trade.targetPrice / trade.entryPrice - 1) * 100)})
              </div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Bewertung dieses Exits</div>
              <div className={cn(
                "mt-1 text-sm font-semibold",
                violatesCurrentExitRule ? "text-amber-400" : "text-emerald-400",
              )}>
                {violatesCurrentExitRule
                  ? "Alte Regel · heute nicht mehr zulässig"
                  : "Entspricht der aktuellen Regel"}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader className="border-b">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="font-heading text-base font-bold">Preisentwicklung</CardTitle>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Database className="size-3.5" />
              {trade.priceSource}
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-5">
          <TradePriceChart
            data={trade.priceSeries}
            entryPrice={trade.entryPrice}
            exitPrice={trade.exitPrice}
            entryTs={trade.timestamp}
            exitTs={trade.resolvedAt}
            breakEvenPrice={trade.breakEvenPrice}
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-base font-bold">Trade-Daten</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            <DetailRow label="Bot" value={trade.bot} />
            <DetailRow label="Positionswert" value={eur(trade.sizeEur)} mono />
            <DetailRow label="Höchster Kurs" value={formatPrice(trade.highPrice)} mono />
            <DetailRow label="Tiefster Kurs" value={formatPrice(trade.lowPrice)} mono />
            {trade.breakEvenPrice != null && (
              <DetailRow label="Gebühren-Break-even" value={formatPrice(trade.breakEvenPrice)} mono />
            )}
            {trade.targetPrice != null && (
              <DetailRow label="Aktueller Ziel-Exit" value={formatPrice(trade.targetPrice)} mono />
            )}
            <DetailRow label="Haltedauer" value={formatDuration(durationEnd - trade.timestamp)} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-base font-bold">Lesart</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
            <p>
              Die durchgezogene Linie zeigt den Marktpreis. Die gestrichelten Linien markieren
              tatsächlichen Entry und – falls vorhanden – Exit.
            </p>
            <p>
              Das Trade-Ergebnis berücksichtigt die simulierten Gebühren. Die reine Kursbewegung
              kann deshalb vom Netto-PnL abweichen.
            </p>
            {violatesCurrentExitRule && (
              <p className="font-medium text-amber-400">
                Dieser Trade stammt aus der früheren Konfiguration. Die heute aktive +6-%-Regel
                hätte diesen Exit unterhalb des Break-even blockiert.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  const Icon = tone === "up" ? TrendingUp : tone === "down" ? TrendingDown : Clock3;
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-3 py-4">
        <div>
          <div className="text-xs text-muted-foreground">{label}</div>
          <div className={cn(
            "mt-1 font-mono text-lg font-semibold tabular-nums",
            tone === "up" && "text-emerald-400",
            tone === "down" && "text-red-400",
          )}>
            {value}
          </div>
        </div>
        <Icon className={cn(
          "size-5 text-muted-foreground",
          tone === "up" && "text-emerald-400",
          tone === "down" && "text-red-400",
        )} />
      </CardContent>
    </Card>
  );
}

function DetailRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border/60 pb-2 last:border-0 last:pb-0">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("text-right", mono && "font-mono tabular-nums")}>{value}</span>
    </div>
  );
}

function formatPrice(value: number): string {
  return `${value.toLocaleString("de-DE", { maximumFractionDigits: 8 })} €`;
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, seconds);
  const days = Math.floor(safe / 86400);
  const hours = Math.floor((safe % 86400) / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  if (days > 0) return `${days} T ${hours} Std`;
  if (hours > 0) return `${hours} Std ${minutes} Min`;
  return `${minutes} Min`;
}
