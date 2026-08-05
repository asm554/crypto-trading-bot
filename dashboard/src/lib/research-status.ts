import type { BotKey } from "@/lib/bots";

export type ResearchStatus = "priority" | "research" | "benchmark" | "stop" | "open";

export type BotResearchProfile = {
  status: ResearchStatus;
  label: string;
  evidence: string;
  nextStep: string;
};

const PROFILES: Partial<Record<BotKey, BotResearchProfile>> = {
  dca: {
    status: "research",
    label: "Umbau-Kandidat",
    evidence: "Legacy-Modell fällt korrigiert durch. Core BTC/ETH war in 3/5 Entwicklungsphasen positiv; der 2026-Holdout blieb im Bear-Markt bei 0,00 %.",
    nextStep: "Core-Modell separat vorwärts im Paper-Modus testen; keine Echtgeldfreigabe ohne echte Bull-Trades.",
  },
  momentum: {
    status: "stop",
    label: "Backtest-Stop",
    evidence: "In Bull, Bear und Current klar negativ; Current -97,03 %.",
    nextStep: "Nicht weiter optimieren ohne grundlegend neue Signalregel.",
  },
  meanrev: {
    status: "stop",
    label: "Backtest-Stop",
    evidence: "In allen drei Marktphasen negative Brutto-Edge.",
    nextStep: "Bestehende Signalklasse nicht weiter tunen.",
  },
  daytrade: {
    status: "stop",
    label: "Backtest-Stop",
    evidence: "In jeder Phase negativ; Bear-Signal statistisch negativ.",
    nextStep: "Keine Weiterentwicklung ohne neue mechanische Hypothese.",
  },
  surfer: {
    status: "research",
    label: "Forschung",
    evidence: "Schwache Brutto-Edge, aber Gebührenhürde nicht erreicht.",
    nextStep: "Nur Kosten- oder Ausführungshypothesen weiter prüfen.",
  },
  ultimate: {
    status: "research",
    label: "Forschung",
    evidence: "Messbare Richtungssignale, wirtschaftlich noch zu schwach.",
    nextStep: "Weniger Round-Trips und günstigere Ausführung untersuchen.",
  },
  hodl: {
    status: "benchmark",
    label: "Benchmark",
    evidence: "Bull stark, aber Bear -46,11 % und Drawdown bis rund -53 %.",
    nextStep: "Als Marktvergleich behalten, nicht als Trading-Sieger werten.",
  },
  candlestick: {
    status: "stop",
    label: "Backtest-Stop",
    evidence: "Vor Ausführungskosten praktisch keine belastbare Edge.",
    nextStep: "Erst mit echten Jupiter-Ausführungsdaten neu bewerten.",
  },
  memecoin: {
    status: "open",
    label: "Noch offen",
    evidence: "Mit Bitvavo-Kerzen nicht seriös validierbar.",
    nextStep: "Historische DEX-, Liquiditäts- und Token-Daten beschaffen.",
  },
  pumpfun: {
    status: "open",
    label: "Noch offen",
    evidence: "Keine belastbare historische On-Chain-Ausführungssimulation.",
    nextStep: "Launches, Slippage, Rugs und verschwundene Tokens abbilden.",
  },
  pumpfun_v2: {
    status: "open",
    label: "Noch offen",
    evidence: "On-Chain-Datenbasis für eine faire Prüfung fehlt.",
    nextStep: "Nach Aufbau des PumpFun-Harness separat validieren.",
  },
  arb: {
    status: "open",
    label: "Noch offen",
    evidence: "Kerzen bilden zeitgleiche Quotes und Fills nicht ab.",
    nextStep: "Historische Orderbücher, Latenzen und Fill-Risiko modellieren.",
  },
  scout: {
    status: "open",
    label: "Noch offen",
    evidence: "Historische On-Chain-Signale fehlen.",
    nextStep: "Damals verfügbare Pool- und Sicherheitsdaten rekonstruieren.",
  },
  futures: {
    status: "open",
    label: "Noch offen",
    evidence: "Spot-Kerzen reichen für gehebelte Futures nicht aus.",
    nextStep: "Funding, Margin, Shorts und Liquidationen simulieren.",
  },
  futures_grid: {
    status: "open",
    label: "Noch offen",
    evidence: "Grid-Ergebnis hängt von Intrabar-Pfad und Liquidation ab.",
    nextStep: "Realistischen Futures-Grid-Harness aufbauen.",
  },
  futures_grid_signal: {
    status: "open",
    label: "Noch offen",
    evidence: "Signal-Grid ist noch nicht mit Futures-Marktdaten validiert.",
    nextStep: "Nach Fertigstellung des Futures-Harness separat testen.",
  },
  freqtrade: {
    status: "open",
    label: "Noch offen",
    evidence: "Separate Freqtrade-Instanz ohne vergleichbaren Phasentest.",
    nextStep: "Mit identischen Gebühren und Marktfenstern vergleichen.",
  },
};

const FALLBACK: BotResearchProfile = {
  status: "open",
  label: "Noch offen",
  evidence: "Noch keine belastbare Bull-, Bear- und Current-Auswertung.",
  nextStep: "Geeignete Datenbasis und Testmethodik festlegen.",
};

export function getBotResearchProfile(key: BotKey): BotResearchProfile {
  return PROFILES[key] ?? FALLBACK;
}

export const RESEARCH_STATUS_CLASS: Record<ResearchStatus, string> = {
  priority: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  research: "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  benchmark: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200",
  stop: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  open: "border-slate-500/35 bg-slate-500/10 text-slate-700 dark:text-slate-300",
};
