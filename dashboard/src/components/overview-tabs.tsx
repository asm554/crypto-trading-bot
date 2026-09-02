"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BotCard } from "@/components/bot-card";
import { EquityChart } from "@/components/equity-chart";
import type { BotSummary, EquityPoint } from "@/lib/bots";

export function OverviewTabs({
  surferBot,
  otherBots,
  equity,
}: {
  surferBot: BotSummary | null;
  otherBots: BotSummary[];
  equity: EquityPoint[];
}) {
  return (
    <Tabs defaultValue="surfer">
      <TabsList>
        <TabsTrigger value="surfer">Der Surfer</TabsTrigger>
        <TabsTrigger value="others">Alle anderen Bots</TabsTrigger>
      </TabsList>

      <TabsContent value="surfer" className="mt-4 flex flex-col gap-4">
        {surferBot ? (
          <>
            <div className="mx-auto w-full max-w-xl">
              <BotCard bot={surferBot} />
            </div>
            <Card className="bg-card/85">
              <CardHeader className="pb-2">
                <CardTitle className="font-heading text-base font-bold">Wert-Entwicklung</CardTitle>
              </CardHeader>
              <CardContent>
                <EquityChart data={equity} includeKeys={["surfer"]} />
              </CardContent>
            </Card>
          </>
        ) : (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Der Surfer meldet sich derzeit nicht als laufend.
            </CardContent>
          </Card>
        )}
      </TabsContent>

      <TabsContent value="others" className="mt-4 flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {otherBots.map((bot, index) => (
            <BotCard
              key={bot.key}
              bot={bot}
              rank={bot.hasData ? index + 1 : undefined}
              isLeader={bot.hasData && index === 0}
            />
          ))}
          {otherBots.length === 0 && (
            <Card className="sm:col-span-2 xl:col-span-3">
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                Derzeit meldet sich kein weiterer Bot als laufend.
              </CardContent>
            </Card>
          )}
        </div>
        {otherBots.length > 0 && (
          <Card className="bg-card/85">
            <CardHeader className="pb-2">
              <CardTitle className="font-heading text-base font-bold">Wert-Entwicklung</CardTitle>
            </CardHeader>
            <CardContent>
              <EquityChart data={equity} includeKeys={otherBots.map((bot) => bot.key)} />
            </CardContent>
          </Card>
        )}
      </TabsContent>
    </Tabs>
  );
}
