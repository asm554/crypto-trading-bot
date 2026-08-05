import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { BotKey } from "@/lib/bots";
import { getBotResearchProfile, RESEARCH_STATUS_CLASS } from "@/lib/research-status";

export function BacktestStatusBadge({ botKey }: { botKey: BotKey }) {
  const profile = getBotResearchProfile(botKey);
  return (
    <Badge
      variant="outline"
      className={cn("whitespace-nowrap text-[11px] font-semibold", RESEARCH_STATUS_CLASS[profile.status])}
    >
      {profile.label}
    </Badge>
  );
}

export function BacktestStatusPanel({ botKey }: { botKey: BotKey }) {
  const profile = getBotResearchProfile(botKey);
  return (
    <div className={cn("col-span-full mt-1 rounded-lg border px-3 py-2.5", RESEARCH_STATUS_CLASS[profile.status])}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-bold uppercase tracking-[0.12em]">Backtest: {profile.label}</span>
        <span className="text-xs opacity-80">Bull · Bear · Current</span>
      </div>
      <p className="mt-1.5 text-sm leading-5 text-foreground/90">{profile.evidence}</p>
      <p className="mt-1 text-xs leading-5 opacity-85">Nächster Schritt: {profile.nextStep}</p>
    </div>
  );
}
