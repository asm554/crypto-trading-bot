import { BotCard } from "@/components/bot-card";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PauseCircle } from "lucide-react";
import { getBotSummaries, isActiveBotKey, isBotRunning } from "@/lib/bots";

export const dynamic = "force-dynamic";
export const metadata = { title: "Gestoppte Bots · Bot-Battle" };

export default async function StoppedBotsPage() {
  const bots = await getBotSummaries();
  // Ein Bot zählt als gestoppt, wenn er nicht zur aktuellen 500-€-Runde
  // gehört (ACTIVE_BOT_KEYS ist die feste Liste der wirklich laufenden
  // systemd-Services) ODER wenn er dazugehört, aber gerade kein frisches
  // Signal meldet. Nur auf isBotRunning zu prüfen reicht nicht: ein
  // manueller battle_report-Lauf schreibt Snapshots für ALLE Bots (auch
  // längst gestoppte), was sie für 8 Stunden fälschlich "aktiv" aussehen
  // ließe.
  const stopped = bots
    .filter((bot) => !isActiveBotKey(bot.key) || !isBotRunning(bot))
    .sort((a, b) => a.nickname.localeCompare(b.nickname));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          <PauseCircle aria-hidden className="size-3.5" />
          Archiv & Vergleichsvarianten
        </div>
        <h1 className="mt-1 text-2xl font-bold sm:text-3xl">Gestoppte Bots</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Diese Strategien laufen aktuell nicht mehr live auf dem Server (kein aktiver systemd-Prozess bzw. seit über 8 Stunden ohne Signal). Ihre bisherigen Ergebnisse bleiben zur Einordnung sichtbar, zählen aber nicht ins laufende 500-€-Battle.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {stopped.map((bot) => (
          <BotCard key={bot.key} bot={bot} />
        ))}
        {stopped.length === 0 && (
          <Card className="sm:col-span-2 xl:col-span-3">
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Aktuell ist kein Bot gestoppt — alle laufen.
            </CardContent>
          </Card>
        )}
      </div>

      <Badge variant="outline" className="w-fit text-xs font-normal text-muted-foreground">
        {stopped.length} {stopped.length === 1 ? "Bot" : "Bots"} gestoppt
      </Badge>
    </div>
  );
}
