import { getSettings } from "@/lib/bots";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Info } from "lucide-react";

export const dynamic = "force-dynamic";

export const metadata = { title: "Trading-Bots · Einstellungen" };

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
            <CardContent className="flex flex-col gap-3 pt-4">
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
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
