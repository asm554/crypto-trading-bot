import { getSettings } from "@/lib/bots";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Info } from "lucide-react";

export const dynamic = "force-dynamic";

export const metadata = { title: "Trading-Bots · Einstellungen" };

const BOT_SUMMARIES: Record<string, { what: string; how: string; risk: string }> = {
  dca: {
    what: "Baut Positionen schrittweise auf, statt alles auf einmal zu investieren.",
    how: "Kauft regelmäßig in mehreren Runden und nimmt Gewinne mit, sobald sich der Coin ausreichend erholt hat.",
    risk: "Eher ruhig, aber längere Verlustphasen sind möglich, wenn ein Markt weiter fällt.",
  },
  momentum: {
    what: "Versucht, bereits laufende Aufwärtstrends mit einem kontrollierten Einstieg zu handeln.",
    how: "Wartet nach einem Impuls auf einen bestätigten Rücksetzer und schützt Gewinne mit einem nachlaufenden Stop.",
    risk: "Höheres Risiko: Ein Trend kann schnell drehen oder der Rücksetzer bleibt aus.",
  },
  meanrev: {
    what: "Sucht überverkaufte Coins, die sich nach einem starken Rückgang wieder stabilisieren könnten.",
    how: "Kauft nicht blind in fallende Kurse, sondern wartet auf RSI- und Stabilitäts-Bestätigung.",
    risk: "Contrarian-Risiko: Ein scheinbar günstiger Coin kann weiter fallen.",
  },
  arb: {
    what: "Sucht kleine Preisunterschiede zwischen drei Währungspaaren aus.",
    how: "Prüft EUR → BTC → ETH → EUR und handelt nur, wenn nach Gebühren ein ausreichender Vorteil bleibt.",
    risk: "Viele kleine Chancen, aber empfindlich gegenüber Gebühren, Verzögerungen und Preisbewegungen.",
  },
  daytrade: {
    what: "Handelt kurzfristige Bewegungen innerhalb weniger Stunden.",
    how: "Nutzt einen kurzen Impuls, wartet auf einen Rücksetzer und beendet Trades spätestens nach wenigen Stunden.",
    risk: "Hohes kurzfristiges Risiko durch schnelle Richtungswechsel und normale Marktschwankungen.",
  },
  memecoin: {
    what: "Sucht handelbare Solana-Memecoins mit Momentum, Liquidität und echtem Handelsvolumen.",
    how: "Filtert dünne oder auslaufende Pumps heraus und berücksichtigt den erwarteten Preisimpact beim Kauf.",
    risk: "Sehr hoch: Memecoins können trotz aller Filter schnell und stark fallen.",
  },
  pumpfun: {
    what: "Beobachtet neue Pump.fun-Token und testet frühe Momentum-Signale im Paper-Modus.",
    how: "Wartet auf Mindestaktivität, Kaufdruck und eine bestätigte Bewegung, bevor eine kleine Position simuliert wird.",
    risk: "Sehr hoch und spekulativ; kleine Positionsgrößen und harte Verlustgrenzen sind entscheidend.",
  },
  pumpfun_v2: {
    what: "Ist eine zweite, etwas flexiblere Paper-Strategie für frühe Pump.fun-Bewegungen.",
    how: "Bewertet Momentum und Kaufdruck, sichert Gewinne stufenweise und begrenzt die Zahl offener Positionen.",
    risk: "Sehr hoch; die Strategie kann durch schnelle Token-Bewegungen und Slippage verlieren.",
  },
  freqtrade: {
    what: "Eine getrennte Freqtrade-Paper-Runde zum Vergleich mit dem Bot-Battle.",
    how: "Läuft eigenständig im Dry-Run und wird vom Dashboard nur lesend übernommen.",
    risk: "Paper-only; die Ergebnisse werden separat bewertet und nicht mit den Polybot-Bots vermischt.",
  },
  surfer: {
    what: "Versucht, länger laufende Trends und Ausbrüche bei SOL/EUR zu reiten.",
    how: "Kombiniert Trendrichtung, gleitende Durchschnitte, Ausbruch und Volumen mit einem dynamischen Stop.",
    risk: "Geduldig, aber ein Trendbruch kann einen Teil der offenen Gewinne wieder abgeben.",
  },
  scout: {
    what: "Prüft neue Liquiditätspools auf technische und marktseitige Sicherheitsmerkmale.",
    how: "Wartet auf eine Reifezeit, kontrolliert Liquidität, Token-Risiken und erwartete Rundreisekosten.",
    risk: "Sehr spekulativ; neue Pools bleiben trotz Sicherheitsfiltern besonders ausfallgefährdet.",
  },
  hodl: {
    what: "Baut langfristig kleine Kernpositionen in BTC, ETH und SOL auf.",
    how: "Investiert regelmäßig, passt die Rate an die Marktphase an und verkauft nur Teile bei sehr starken Gewinnen.",
    risk: "Langfristiges Marktrisiko; kein kurzfristiger Stop bedeutet mögliche längere Rückgänge.",
  },
};

export default function SettingsPage() {
  const { fees, strategies } = getSettings();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">Einstellungen</h1>
        <p className="text-sm text-muted-foreground">
          Zum Ansehen. Geändert werden die Werte in der Bot-Konfiguration.
        </p>
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-border bg-secondary/40 p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>
          Alle Bots handeln im <span className="font-medium text-foreground">Papier-Modus</span> — es
          wird kein echtes Geld eingesetzt. Jeder startet mit 100&nbsp;€ Spielgeld.
        </p>
      </div>

      {/* Gebühren */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Allgemein &amp; Gebühren</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
          {fees.map((p) => (
            <div key={p.label} className="flex flex-col gap-0.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-muted-foreground">{p.label}</span>
                <span className="font-mono text-sm font-medium tabular-nums">{p.value}</span>
              </div>
              {p.hint && <span className="text-xs text-muted-foreground/70">{p.hint}</span>}
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Strategien */}
      <div className="grid gap-4 lg:grid-cols-3">
        {strategies.map((s) => (
          <Card key={s.key} className="relative overflow-hidden">
            <div
              aria-hidden
              className="absolute inset-x-0 top-0 h-0.5"
              style={{
                background: `linear-gradient(to right, var(--bot-${s.key}), transparent 85%)`,
              }}
            />
            <CardHeader className="pb-3">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: `var(--bot-${s.key})` }}
                />
                <CardTitle className="font-heading text-base font-bold">{s.nickname}</CardTitle>
                <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                  {s.name}
                </Badge>
              </div>
            </CardHeader>
            <Separator />
            <CardContent className="flex flex-col gap-4 pt-4">
              {BOT_SUMMARIES[s.key] && (
                <div className="rounded-lg bg-secondary/50 p-3 text-sm leading-6">
                  <div className="font-medium text-foreground">Kurz erklärt</div>
                  <dl className="mt-2 space-y-2 text-muted-foreground">
                    <div>
                      <dt className="inline font-medium text-foreground">Was:</dt>{" "}
                      <dd className="inline">{BOT_SUMMARIES[s.key].what}</dd>
                    </div>
                    <div>
                      <dt className="inline font-medium text-foreground">Wie:</dt>{" "}
                      <dd className="inline">{BOT_SUMMARIES[s.key].how}</dd>
                    </div>
                    <div>
                      <dt className="inline font-medium text-foreground">Risiko:</dt>{" "}
                      <dd className="inline">{BOT_SUMMARIES[s.key].risk}</dd>
                    </div>
                  </dl>
                </div>
              )}
              <div className="flex flex-col gap-3">
                {s.params.map((p) => (
                  <div key={p.label} className="flex flex-col gap-0.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm text-muted-foreground">{p.label}</span>
                      <span className="text-right font-mono text-sm font-medium tabular-nums">
                        {p.value}
                      </span>
                    </div>
                    {p.hint && <span className="text-xs text-muted-foreground/70">{p.hint}</span>}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
