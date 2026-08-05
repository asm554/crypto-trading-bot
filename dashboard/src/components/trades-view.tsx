"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChartNoAxesCombined } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { eur, signedEur, pnlToneClass, clockTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { BotKey, TradeRow } from "@/lib/bots";

type BotFilter = BotKey | "all";
type StatusFilter = "all" | "open" | "resolved";

const SIDE_LABEL: Record<string, string> = { buy: "Kauf", sell: "Verkauf" };

export function TradesView({
  trades,
  bots,
}: {
  trades: TradeRow[];
  bots: { key: BotKey; nickname: string }[];
}) {
  const [botFilter, setBotFilter] = useState<BotFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const botItems = useMemo(
    () => ({ all: "Alle Bots", ...Object.fromEntries(bots.map((b) => [b.key, b.nickname])) }),
    [bots],
  );

  const filtered = useMemo(() => {
    return trades.filter((t) => {
      if (botFilter !== "all" && t.botKey !== botFilter) return false;
      if (statusFilter === "open" && t.resolved) return false;
      if (statusFilter === "resolved" && !t.resolved) return false;
      return true;
    });
  }, [trades, botFilter, statusFilter]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
          <TabsList>
            <TabsTrigger value="all">Alle</TabsTrigger>
            <TabsTrigger value="open">Offen</TabsTrigger>
            <TabsTrigger value="resolved">Geschlossen</TabsTrigger>
          </TabsList>
        </Tabs>

        <Select items={botItems} value={botFilter} onValueChange={(v) => setBotFilter(v as BotFilter)}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Alle Bots" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Alle Bots</SelectItem>
            {bots.map((b) => (
              <SelectItem key={b.key} value={b.key}>
                {b.nickname}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-1 py-10 text-center text-sm text-muted-foreground">
          <p>Keine Trades für diese Auswahl.</p>
          <p className="text-xs">Filter anpassen oder abwarten, bis ein Bot wieder handelt.</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Zeit</TableHead>
              <TableHead>Bot</TableHead>
              <TableHead>Coin</TableHead>
              <TableHead>Seite</TableHead>
              <TableHead className="text-right">Betrag</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Ergebnis</TableHead>
              <TableHead className="text-right">Verlauf</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((t) => (
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
                <TableCell className="text-muted-foreground">
                  {SIDE_LABEL[t.side] ?? t.side}
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">{eur(t.sizeEur)}</TableCell>
                <TableCell>
                  <Badge variant={t.resolved ? "outline" : "secondary"} className="text-xs">
                    {t.resolved ? "geschlossen" : "offen"}
                  </Badge>
                </TableCell>
                <TableCell
                  className={cn(
                    "text-right font-mono tabular-nums",
                    t.pnlEur != null ? pnlToneClass(t.pnlEur) : "text-muted-foreground",
                  )}
                >
                  {t.pnlEur == null ? "—" : signedEur(t.pnlEur)}
                </TableCell>
                <TableCell className="text-right">
                  <Link
                    href={`/trades/${t.id}`}
                    aria-label={`Kursverlauf für Trade ${t.id} öffnen`}
                    className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:bg-primary/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <ChartNoAxesCombined className="size-3.5" />
                    <span className="hidden sm:inline">Details</span>
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <p className="text-xs text-muted-foreground">
        {filtered.length} von {trades.length} Trades angezeigt.
      </p>
    </div>
  );
}
