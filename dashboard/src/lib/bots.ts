import "server-only";

// Liest die Bot-Daten aus Supabase (Cloud-Datenbank), damit das Dashboard sowohl
// lokal als auch online (z.B. Vercel) dieselben Live-Daten zeigt. Der Bot selbst
// schreibt dorthin über polybot/cloud_sync.py.

const SUPABASE_URL = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY ?? "";

const START_CAPITAL = 100; // Startkapital pro Bot (€)

export type BotKey = "dca" | "momentum" | "meanrev" | "arb" | "daytrade" | "memecoin" | "pumpfun" | "pumpfun_v2" | "freqtrade" | "surfer" | "scout" | "hodl";

type BotMeta = {
  key: BotKey;
  name: string;
  nickname: string;
  prefix: string;
  tagline: string;
  startCapital?: number;
};

export const BOTS: BotMeta[] = [
  {
    key: "dca",
    name: "DCA",
    nickname: "Der Brave",
    prefix: "DCA_",
    tagline: "Kauft regelmäßig kleine Beträge und sitzt Rücksetzer aus.",
  },
  {
    key: "momentum",
    name: "Momentum",
    nickname: "Der Zocker",
    prefix: "MOM_",
    tagline: "Springt auf Coins auf, die gerade stark steigen.",
  },
  {
    key: "meanrev",
    name: "Mean-Reversion",
    nickname: "Der Contrarian",
    prefix: "REV_",
    tagline: "Kauft stark gefallene Coins in der Hoffnung auf Erholung.",
  },
  {
    key: "arb",
    name: "Triangular-Arb",
    nickname: "Der Pedant",
    prefix: "ARB_",
    tagline: "Sucht risikofreie Rundungsgewinne im EUR-BTC-ETH-Dreieck.",
  },
  {
    key: "daytrade",
    name: "Daytrade",
    nickname: "Der Zappler",
    prefix: "DAY_",
    tagline: "Handelt kurzfristige Kursausschläge, nie länger als ein paar Stunden.",
  },
  {
    key: "memecoin",
    name: "Onchain-Memecoin",
    nickname: "Der Onchain",
    prefix: "CHAIN_",
    tagline: "Springt früh auf stark steigende Solana-Memecoins auf und nimmt den Gewinn bei rund +15 % mit.",
  },
  {
    key: "pumpfun",
    name: "Pump.fun",
    nickname: "Der PumpFun",
    prefix: "PUMP_",
    tagline: "Verfolgt Pump.fun-Bonding-Curve-Events als separaten Paper-Trading-Bot.",
  },
  {
    key: "pumpfun_v2",
    name: "Pump.fun V2",
    nickname: "Der PumpFun V2",
    prefix: "PUMP2_",
    tagline: "Chainstack-inspirierte, aktivere Pump.fun-Paper-Strategie ohne Wallet oder Live-Orders.",
  },
  {
    key: "surfer",
    name: "Trend/Breakout",
    nickname: "Der Surfer",
    prefix: "SURF_",
    tagline: "Reitet bestätigte SOL/EUR-Trends: 4h-Aufwärtstrend, EMA20 über EMA50 und ein 20h-Ausbruch müssen zusammenkommen.",
  },
  {
    key: "scout",
    name: "New-Pool Scout",
    nickname: "Der Spaeher",
    prefix: "SCOUT_",
    tagline: "Beobachtet neue Solana-Pools 20 Minuten und handelt nur nach harten Sicherheits-, Aktivitaets- und Route-Checks.",
  },
  { key: "hodl", name: "Long-Term Allocation", nickname: "Der HODLer", prefix: "HODL_", tagline: "Investiert woechentlich regelbasiert in BTC, ETH und SOL und behaelt einen dauerhaften Kern." },
];

export const FREQTRADE_META = {
  key: "freqtrade" as BotKey,
  name: "Freqtrade",
  nickname: "Der Freqtrade",
  prefix: "FT_",
  startCapital: 1000,
  tagline: "Freqtrade Paper-Trading, read-only aus der lokalen API gespiegelt.",
};

export type BotSummary = {
  key: BotKey;
  name: string;
  nickname: string;
  tagline: string;
  equityEur: number;
  cashEur: number;
  openPositions: number;
  realizedPnlEur: number;
  unrealizedPnlEur: number;
  totalPnlEur: number;
  pnlPct: number;
  tradeCount: number;
  startedAt: number | null;
  lastActivity: number | null;
  runtimeStartedAt: number | null;
  runtimeStatus: string | null;
  hasData: boolean;
};

export type TradeRow = {
  id: number;
  botKey: BotKey | "?";
  bot: string;
  pair: string;
  side: string;
  sizeEur: number;
  price: number;
  timestamp: number;
  status: string;
  resolved: boolean;
  pnlEur: number | null;
};

export type EquityPoint = {
  t: number;
  dca: number | null;
  momentum: number | null;
  meanrev: number | null;
  arb: number | null;
  daytrade: number | null;
  memecoin: number | null;
  pumpfun: number | null;
  pumpfun_v2: number | null;
  freqtrade: number | null;
  surfer: number | null;
  scout: number | null;
  hodl: number | null;
};

type RawTrade = {
  id: number;
  timestamp: number;
  market_question: string;
  side: string;
  size: number;
  price: number;
  status: string;
  resolved_at: number | null;
  real_pnl: number | null;
  unrealized_pnl: number | null;
};

type RawSnapshot = {
  id: number;
  bot: string;
  ts: number;
  equity_eur: number;
  cash_eur: number;
  open_positions: number;
  unrealized_pnl_eur: number;
  realized_pnl_eur: number;
};


export function isCloudConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

async function fetchTable<T>(table: string, query: string): Promise<T[]> {
  if (!isCloudConfigured()) return [];
  try {
    const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${query}`, {
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
      },
      cache: "no-store",
    });
    if (!res.ok) return [];
    return (await res.json()) as T[];
  } catch {
    return [];
  }
}

async function fetchAllTrades(): Promise<RawTrade[]> {
  return fetchTable<RawTrade>("paper_trades", "select=*&order=timestamp.desc&limit=5000");
}

async function fetchAllSnapshots(): Promise<RawSnapshot[]> {
  return fetchTable<RawSnapshot>("equity_snapshots", "select=*&order=ts.asc&limit=20000");
}


function num(v: unknown, fallback = 0): number {
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? (n as number) : fallback;
}

export async function getBotSummaries(): Promise<BotSummary[]> {
  const [trades, snapshots] = await Promise.all([fetchAllTrades(), fetchAllSnapshots()]);

  return BOTS.map((bot) => {
    const botTrades = trades.filter((t) => t.market_question.startsWith(bot.prefix));
    const openTrades = botTrades.filter((t) => t.resolved_at == null);
    const doneTrades = botTrades.filter((t) => t.resolved_at != null);

    const botSnaps = snapshots.filter((s) => s.bot === bot.key);
    const latestSnap = botSnaps[botSnaps.length - 1];

    // Der neueste Snapshot ist die maßgebliche, zeitgleiche Bewertung. Die
    // Trade-Summen bleiben der Fallback für Bots ohne Snapshot-Historie.
    const unrealized = latestSnap
      ? num(latestSnap.unrealized_pnl_eur)
      : openTrades.reduce((s, t) => s + num(t.unrealized_pnl), 0);
    const realized = latestSnap
      ? num(latestSnap.realized_pnl_eur)
      : doneTrades.reduce((s, t) => s + num(t.real_pnl), 0);

    const equity = latestSnap ? num(latestSnap.equity_eur) : START_CAPITAL;
    const cash = latestSnap ? num(latestSnap.cash_eur) : START_CAPITAL;

    const lastTradeTs = botTrades.reduce((max, t) => Math.max(max, num(t.timestamp)), 0);
    const firstTradeTs = botTrades.reduce((min, t) => Math.min(min, num(t.timestamp)), Infinity);
    const firstSnapshotTs = botSnaps.reduce((min, s) => Math.min(min, num(s.ts)), Infinity);
    const startedAt = Math.min(firstTradeTs, firstSnapshotTs);
    const lastActivity = Math.max(lastTradeTs, latestSnap ? num(latestSnap.ts) : 0) || null;
    const runtimeSnapshots = snapshots.filter((s) => s.bot === `__runtime_${bot.key}`);
    const runtime = runtimeSnapshots[runtimeSnapshots.length - 1];

    const startCapital = bot.startCapital ?? START_CAPITAL;
    const totalPnl = equity - startCapital;
    return {
      key: bot.key,
      name: bot.name,
      nickname: bot.nickname,
      tagline: bot.tagline,
      equityEur: round2(equity),
      cashEur: round2(cash),
      openPositions: latestSnap ? num(latestSnap.open_positions) : openTrades.length,
      realizedPnlEur: round2(realized),
      unrealizedPnlEur: round2(unrealized),
      totalPnlEur: round2(totalPnl),
      pnlPct: round2((totalPnl / startCapital) * 100),
      tradeCount: botTrades.length,
      startedAt: Number.isFinite(startedAt) ? startedAt : null,
      lastActivity,
      runtimeStartedAt: runtime ? num(runtime.ts) : null,
      runtimeStatus: runtime ? "running" : null,
      hasData: botTrades.length > 0 || botSnaps.length > 0,
    };
  });
}

function toTradeRow(r: RawTrade): TradeRow {
  const isFreqtrade = r.market_question.startsWith("FT_");
  const meta = BOTS.find((b) => r.market_question.startsWith(b.prefix));
  // "Der Onchain" kodiert CHAIN_{symbol}@{address} (Adresse für die Preis-
  // Auflösung, da zwei dynamisch entdeckte Solana-Tokens denselben Namen
  // tragen können) — im Dashboard reicht das Symbol vor dem "@".
  const rest = meta ? r.market_question.slice(meta.prefix.length) : r.market_question;
  const pair = isFreqtrade
    ? r.market_question.slice(3)
    : meta?.key === "memecoin" || meta?.key === "pumpfun" || meta?.key === "pumpfun_v2" || meta?.key === "scout" ? rest.split("@")[0] : meta?.key === "hodl" ? rest.split("_")[0] : rest;
  return {
    id: r.id,
    botKey: isFreqtrade ? "freqtrade" : meta?.key ?? "?",
    bot: isFreqtrade ? "Der Freqtrade" : meta?.nickname ?? "?",
    pair,
    side: r.side,
    sizeEur: round2(num(r.size) * num(r.price)),
    price: num(r.price),
    timestamp: num(r.timestamp),
    status: r.status,
    resolved: r.resolved_at != null,
    pnlEur: r.resolved_at == null
      ? (r.unrealized_pnl == null ? null : round2(num(r.unrealized_pnl)))
      : (r.real_pnl == null ? null : round2(num(r.real_pnl))),
  };
}

export async function getRecentTrades(limit = 25): Promise<TradeRow[]> {
  const trades = await fetchAllTrades();
  return trades.slice(0, limit).map(toTradeRow);
}

/** Alle Trades (mit Deckel), für die eigene Trades-Seite mit Filtern. */
export async function getAllTrades(): Promise<TradeRow[]> {
  const trades = await fetchAllTrades();
  return trades.map(toTradeRow);
}

export async function getEquitySeries(): Promise<EquityPoint[]> {
  const snapshots = await fetchAllSnapshots();
  const byTime = new Map<number, EquityPoint>();
  for (const r of snapshots) {
    const bucket = Math.round(num(r.ts) / 60) * 60; // auf Minute runden
    const point =
      byTime.get(bucket) ??
      { t: bucket, dca: null, momentum: null, meanrev: null, arb: null, daytrade: null, memecoin: null, pumpfun: null, pumpfun_v2: null, freqtrade: null, surfer: null, scout: null, hodl: null };
    if (BOTS.some((b) => b.key === r.bot)) {
      point[r.bot as BotKey] = round2(num(r.equity_eur));
    }
    byTime.set(bucket, point);
  }
  return Array.from(byTime.values()).sort((a, b) => a.t - b.t);
}

export type StrategyParam = { label: string; value: string; hint?: string };
export type StrategyGroup = { key: BotKey; name: string; nickname: string; params: StrategyParam[] };
export type SettingsView = {
  fees: StrategyParam[];
  strategies: StrategyGroup[];
};

export function getSettings(): SettingsView {
  const fees: StrategyParam[] = [
    { label: "Gebühr pro Kauf/Verkauf", value: "0.40 %", hint: "Wird bei jedem Trade abgezogen (Kraken Taker)." },
    { label: "Gebühr (Maker)", value: "0.16 %", hint: "Falls als Maker gehandelt wird." },
    { label: "Modus", value: "Papierhandel", hint: "Es wird kein echtes Geld eingesetzt." },
    { label: "Startkapital je Bot", value: `${START_CAPITAL} €` },
  ];

  const strategies: StrategyGroup[] = [
    {
      key: "dca",
      name: "DCA",
      nickname: "Der Brave",
      params: [
        { label: "Kauf-Intervall", value: "alle 4 Std." },
        { label: "Kapital verteilt auf", value: "5 Runden" },
        { label: "Gewinnmitnahme", value: "+3 %", hint: "Position wird mit +3 % Gewinn verkauft." },
        { label: "Notverkauf nach Zeit", value: "nach 14 Tagen", hint: "Verlust-Bremse: alte Positionen werden zwangsweise geschlossen." },
        { label: "Max. offene Positionen", value: "2" },
        { label: "Max. pro Coin", value: "20 €" },
        { label: "Bar-Reserve", value: "10 €", hint: "Wird nie investiert." },
      ],
    },
    {
      key: "momentum",
      name: "Momentum",
      nickname: "Der Zocker",
      params: [
        { label: "Prüf-Intervall", value: "jede Std." },
        { label: "Einstieg bei Anstieg", value: "+3 % bis +25 % (24 Std.)" },
        { label: "Nachlaufende Stop-Bremse", value: "2,5 %", hint: "Verkauft, wenn der Kurs 2,5 % vom Höchststand fällt." },
        { label: "Harte Verlust-Bremse", value: "4 %" },
        { label: "Positionsgröße", value: "12 €" },
        { label: "Max. offene Positionen", value: "4" },
        { label: "Max. Haltedauer", value: "48 Std." },
      ],
    },
    {
      key: "meanrev",
      name: "Mean-Reversion",
      nickname: "Der Contrarian",
      params: [
        { label: "Prüf-Intervall", value: "jede Std." },
        { label: "Einstieg bei Absturz", value: "ab −8 % (24 Std.)" },
        { label: "Zusatz-Bedingung", value: "RSI unter 30", hint: "Coin gilt als überverkauft." },
        { label: "Gewinnmitnahme", value: "+4 %" },
        { label: "Verlust-Bremse", value: "−5 %" },
        { label: "Positionsgröße", value: "15 €" },
        { label: "Max. offene Positionen", value: "3" },
      ],
    },
    {
      key: "arb",
      name: "Triangular-Arb",
      nickname: "Der Pedant",
      params: [
        { label: "Prüf-Intervall", value: "alle 45 Sek." },
        { label: "Dreieck", value: "EUR → BTC → ETH → EUR", hint: "Beide Richtungen werden geprüft." },
        { label: "Ticket-Größe", value: "25 €" },
        { label: "Mindestgewinn", value: "0,05 €", hint: "Nach allen drei Gebühren-Legs." },
        { label: "Max. Trades/Std.", value: "6", hint: "Sicherheits-Deckel gegen Fehlkonfiguration." },
      ],
    },
    {
      key: "daytrade",
      name: "Daytrade",
      nickname: "Der Zappler",
      params: [
        { label: "Prüf-Intervall", value: "alle 5 Min." },
        { label: "Einstieg bei Anstieg", value: "+3 % bis +25 % (4 Std.)", hint: "Kurzfristiges Momentum statt 24h-Trend." },
        { label: "Nachlaufende Stop-Bremse", value: "1,5 %" },
        { label: "Harte Verlust-Bremse", value: "3 %" },
        { label: "Positionsgröße", value: "10 €" },
        { label: "Max. offene Positionen", value: "4" },
        { label: "Max. Haltedauer", value: "6 Std." },
      ],
    },
    {
      key: "memecoin",
      name: "Onchain-Memecoin",
      nickname: "Der Onchain",
      params: [
        { label: "Prüf-Intervall", value: "alle 5 Min." },
        { label: "Coin-Universum", value: "12 kuratierte + bis zu 15 dynamische", hint: "Kern: BONK, WIF, POPCAT, PNUT, GOAT, MEW, FARTCOIN, GIGA, MOODENG, FWOG, PENGU, SLERF. Dazu aktuell beworbene Solana-Token aus DexScreeners Boost-/Profile-Feeds, scharf gefiltert." },
        { label: "Einstieg bei Momentum", value: "+8 % bis +60 % (letzte Stunde)", hint: "Springt früh auf einen frischen Anstieg auf; die Obergrenze vermeidet den Kauf in einen schon auslaufenden Pump." },
        { label: "Mindest-Liquidität", value: "50.000 $", hint: "Filtert dünne/riskante Pools raus." },
        { label: "Mindest-Volumen (24h)", value: "250.000 $", hint: "Filtert Anstiege raus, die kaum echtes Handelsvolumen hinter sich haben." },
        { label: "Kaufdruck", value: "Käufe ≥ 1,2× Verkäufe (letzte Stunde)", hint: "Lehnt Anstiege ab, die schon ins Verkaufen kippen." },
        { label: "Mindestalter (dynamisch)", value: "6 Std.", hint: "Nur für neu entdeckte Token, nicht für den kuratierten Kern – schützt vor frischen Rug-Bait-Launches." },
        { label: "Gewinnmitnahme", value: "+15 %" },
        { label: "Verlust-Bremse", value: "−10 %", hint: "Zwingend, da nur Take-Profit unbegrenzte Verluste zuließe." },
        { label: "Positionsgröße", value: "8 €" },
        { label: "Max. offene Positionen", value: "3" },
        { label: "Max. Haltedauer", value: "24 Std." },
        { label: "Swap-Slippage", value: "1,5 %", hint: "On-chain gibt es kein Bid/Ask – simuliert den AMM-Preisimpact." },
      ],
    },
    {
      key: "pumpfun",
      name: "Pump.fun",
      nickname: "Der PumpFun",
      params: [
        { label: "Datenquelle", value: "PumpPortal WebSocket", hint: "Neue Token und Trades; keine Wallet und keine Orders." },
        { label: "Modus", value: "100 % Paper-Trading" },
        { label: "Positionsgröße", value: "5 €" },
        { label: "Phasen", value: "Early Bonding Curve + Migration/Post-Migration" },
        { label: "Entry Early", value: "+10 % bis +35 % Market-Cap-Momentum", hint: "Mindestens 20 Trades, 8 eindeutige Trader und positiver 30-Sekunden-Impuls." },
        { label: "Kaufdruck", value: "mindestens 1,4× Buy/Sell" },
        { label: "Curve-Fill", value: "virtuelle Reserven + simulierte Gebühr" },
        { label: "Verlust-Bremse", value: "−20 %" },
        { label: "Gewinnsicherung", value: "+30 %, Trailing-Floor +15 %" },
        { label: "Max. Haltedauer Early", value: "45 Min." },
        { label: "Max. Haltedauer migriert", value: "6 Std." },
        { label: "Max. offene Positionen", value: "2" },
      ],
    },
    {
      key: "pumpfun_v2",
      name: "Pump.fun V2",
      nickname: "Der PumpFun V2",
      params: [
        { label: "Datenquelle", value: "PumpPortal WebSocket" },
        { label: "Modus", value: "100 % Paper-Trading" },
        { label: "Positionsgröße", value: "10 €" },
        { label: "Entry", value: "+3 % bis +60 % Momentum" },
        { label: "Kaufdruck", value: "mindestens 1,05× Buy/Sell" },
        { label: "Verlust-Bremse", value: "−18 %" },
        { label: "Gewinnsicherung", value: "+25 %, Trailing 12 %" },
        { label: "Max. offene Positionen", value: "3" },
      ],
    },
    {
      key: "freqtrade",
      name: "Freqtrade",
      nickname: "Der Freqtrade",
      params: [
        { label: "Quelle", value: "Lokale Freqtrade-API" },
        { label: "Modus", value: "Dry-Run / Paper-Trading" },
        { label: "Paper-Kapital", value: "1.000 €" },
        { label: "Stake-Größe", value: "100 €" },
        { label: "Max. offene Trades", value: "3" },
        { label: "Dashboard-Sync", value: "alle 30 Sek.", hint: "Read-only: keine Steuerbefehle aus dem Dashboard." },
      ],
    },
    {
      key: "surfer",
      name: "Trend/Breakout",
      nickname: "Der Surfer",
      params: [
        { label: "Handelspaar", value: "SOL/EUR", hint: "Einziges gehandeltes Paar, maximal 1 offene Position." },
        { label: "Einstiegsbedingungen", value: "4h-Aufwärtstrend + EMA20 > EMA50 + 20h-Ausbruch + erhöhtes Volumen", hint: "Alle vier müssen gleichzeitig erfüllt sein – bewusst selten." },
        { label: "Initialer Stop", value: "ATR-basiert (2× ATR14)", hint: "Passt sich der aktuellen Volatilität an." },
        { label: "Gewinnsicherung", value: "Trailing-Stop 3 %", hint: "Kein fester Take-Profit, Gewinne laufen mit dem Trend." },
        { label: "Trend-Exit", value: "EMA20 kreuzt unter EMA50" },
        { label: "Max. Haltedauer", value: "7 Tage" },
        { label: "Risiko pro Trade", value: "max. 0,50 €", hint: "Bestimmt die Positionsgröße über den ATR-Stop-Abstand." },
        { label: "Max. Positionsgröße", value: "25 €" },
        { label: "Verlustpause", value: "24 Std. nach 3 Verlusten in Folge" },
        { label: "Kontoverlust-Sperre", value: "−10 %", hint: "Ab dieser Verlustgrenze keine neuen Einstiege, offene Positionen laufen weiter." },
      ],
    },
    {
      key: "scout",
      name: "New-Pool Scout",
      nickname: "Der Spaeher",
      params: [
        { label: "Pruef-Intervall", value: "alle 30 Sek." },
        { label: "Reifezeit", value: "20 Min.", hint: "Neue Pools werden vor jeder Bewertung beobachtet." },
        { label: "Sicherheits-Gates", value: "Mint + Freeze deaktiviert, Audit/Shield sauber" },
        { label: "Markt-Gates", value: "ab 40.000 $ Liquiditaet, 150 Holdern und Score 60/100" },
        { label: "Route-Gate", value: "max. 1,5 % Preiswirkung, 8 % Rundreisekosten" },
        { label: "Positionsgroesse", value: "5 €", hint: "Maximal zwei Positionen; 85 € bleiben Barreserve." },
        { label: "Verlust-Bremse", value: "-12 %" },
        { label: "Gewinnmitnahme", value: "+25 %; Trailing ab +10 %" },
        { label: "Max. Haltedauer", value: "6 Std." },
        { label: "Risk-off", value: "12 Std. nach 2 Verlusten; Kontolimit -8 %" },
      ],
    },
    { key: "hodl", name: "Long-Term Allocation", nickname: "Der HODLer", params: [
      { label: "Wochenbudget", value: "max. 20 €", hint: "20 € Barreserve bleiben unangetastet." },
      { label: "Basisverteilung", value: "50 % BTC, 30 % ETH, 20 % SOL" },
      { label: "Marktphase", value: "EMA50/EMA200 + 90-Tage-Momentum" },
      { label: "Baerenmarkt", value: "nur 35 % der Rate in BTC" },
      { label: "Gewinnmitnahme", value: "25 % bei +100 %, 25 % bei +200 %; Kern bleibt" },
      { label: "Stops", value: "kein normaler Stop-Loss" },
    ] },
  ];

  return { fees, strategies };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}


export async function getFreqtradeSummary(): Promise<BotSummary> {
  const [trades, snapshots] = await Promise.all([fetchAllTrades(), fetchAllSnapshots()]);
  const bot = FREQTRADE_META;
  const botTrades = trades.filter((t) => t.market_question.startsWith(bot.prefix));
  const openTrades = botTrades.filter((t) => t.resolved_at == null);
  const doneTrades = botTrades.filter((t) => t.resolved_at != null);
  const botSnaps = snapshots.filter((s) => s.bot === bot.key);
  const latestSnap = botSnaps[botSnaps.length - 1];
  const unrealized = latestSnap ? num(latestSnap.unrealized_pnl_eur) : openTrades.reduce((sum, t) => sum + num(t.unrealized_pnl), 0);
  const realized = latestSnap ? num(latestSnap.realized_pnl_eur) : doneTrades.reduce((sum, t) => sum + num(t.real_pnl), 0);
  const startCapital = bot.startCapital;
  const equity = latestSnap ? num(latestSnap.equity_eur) : startCapital;
  const cash = latestSnap ? num(latestSnap.cash_eur) : startCapital;
  const lastTradeTs = botTrades.reduce((max, t) => Math.max(max, num(t.timestamp)), 0);
  const lastActivity = Math.max(lastTradeTs, latestSnap ? num(latestSnap.ts) : 0) || null;
  const firstTradeTs = botTrades.reduce((min, t) => Math.min(min, num(t.timestamp)), Infinity);
  const firstSnapshotTs = botSnaps.reduce((min, s) => Math.min(min, num(s.ts)), Infinity);
  const startedAt = Math.min(firstTradeTs, firstSnapshotTs);
  const runtimeSnapshots = snapshots.filter((s) => s.bot === "__runtime_freqtrade");
  const runtime = runtimeSnapshots[runtimeSnapshots.length - 1];
  const totalPnl = equity - startCapital;
  return {
    key: bot.key, name: bot.name, nickname: bot.nickname, tagline: bot.tagline,
    equityEur: round2(equity), cashEur: round2(cash),
    openPositions: latestSnap ? num(latestSnap.open_positions) : openTrades.length,
    realizedPnlEur: round2(realized), unrealizedPnlEur: round2(unrealized),
    totalPnlEur: round2(totalPnl), pnlPct: round2((totalPnl / startCapital) * 100),
    tradeCount: botTrades.length,
    startedAt: Number.isFinite(startedAt) ? startedAt : null,
    lastActivity, runtimeStartedAt: runtime ? num(runtime.ts) : null,
    runtimeStatus: runtime ? "running" : null,
    hasData: botTrades.length > 0 || botSnaps.length > 0,
  };
}

export async function getFreqtradeTrades(): Promise<TradeRow[]> {
  return (await fetchAllTrades()).filter((t) => t.market_question.startsWith("FT_")).map(toTradeRow);
}

export async function getFreqtradeEquitySeries(): Promise<EquityPoint[]> {
  const snapshots = await fetchAllSnapshots();
  const byTime = new Map<number, EquityPoint>();
  for (const r of snapshots.filter((s) => s.bot === "freqtrade")) {
    const bucket = Math.round(num(r.ts) / 60) * 60;
    byTime.set(bucket, { t: bucket, dca: null, momentum: null, meanrev: null, arb: null, daytrade: null, memecoin: null, pumpfun: null, pumpfun_v2: null, freqtrade: num(r.equity_eur), surfer: null, scout: null, hodl: null });
  }
  return Array.from(byTime.values()).sort((a, b) => a.t - b.t);
}
