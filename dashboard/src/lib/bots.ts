import "server-only";

// Liest die Bot-Daten aus Supabase (Cloud-Datenbank), damit das Dashboard sowohl
// lokal als auch online (z.B. Vercel) dieselben Live-Daten zeigt. Der Bot selbst
// schreibt dorthin über polybot/cloud_sync.py.

const SUPABASE_URL = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY ?? "";

export type BotKey = "dca" | "momentum" | "meanrev" | "arb" | "daytrade" | "memecoin" | "pumpfun" | "pumpfun_v2" | "surfer" | "scout" | "hodl" | "freqtrade" | "futures" | "futures_grid";

type BotMeta = {
  key: BotKey;
  name: string;
  nickname: string;
  prefix: string;
  tagline: string;
  startingCapitalEur: number;
};

export const BOTS: BotMeta[] = [
  {
    key: "dca",
    name: "DCA",
    nickname: "Der Brave",
    prefix: "DCA_",
    tagline: "Kauft regelmäßig kleine Beträge und sitzt Rücksetzer aus.",
    startingCapitalEur: 100,
  },
  {
    key: "momentum",
    name: "Momentum",
    nickname: "Der Zocker",
    prefix: "MOM_",
    tagline: "Springt auf Coins auf, die gerade stark steigen.",
    startingCapitalEur: 100,
  },
  {
    key: "meanrev",
    name: "Mean-Reversion",
    nickname: "Der Contrarian",
    prefix: "REV_",
    tagline: "Kauft stark gefallene Coins in der Hoffnung auf Erholung.",
    startingCapitalEur: 100,
  },
  {
    key: "arb",
    name: "Triangular-Arb",
    nickname: "Der Pedant",
    prefix: "ARB_",
    tagline: "Sucht risikofreie Rundungsgewinne im EUR-BTC-ETH-Dreieck.",
    startingCapitalEur: 100,
  },
  {
    key: "daytrade",
    name: "Daytrade",
    nickname: "Der Zappler",
    prefix: "DAY_",
    tagline: "Handelt kurzfristige Kursausschläge, nie länger als ein paar Stunden.",
    startingCapitalEur: 100,
  },
  {
    key: "memecoin",
    name: "Onchain-Memecoin",
    nickname: "Der Onchain",
    prefix: "CHAIN_",
    tagline: "Springt früh auf stark steigende Solana-Memecoins auf und nimmt den Gewinn bei rund +15 % mit.",
    startingCapitalEur: 100,
  },
  {
    key: "pumpfun",
    name: "Pump.fun",
    nickname: "Der PumpFun",
    prefix: "PUMP_",
    tagline: "Verfolgt Pump.fun-Bonding-Curve-Events als separaten Paper-Trading-Bot.",
    startingCapitalEur: 100,
  },
  {
    key: "pumpfun_v2",
    name: "Pump.fun V2",
    nickname: "Der PumpFun V2",
    prefix: "PUMP2_",
    tagline: "Chainstack-inspirierte, aktivere Pump.fun-Paper-Strategie ohne Wallet oder Live-Orders.",
    startingCapitalEur: 100,
  },
  {
    key: "surfer",
    name: "Trend/Breakout",
    nickname: "Der Surfer",
    prefix: "SURF_",
    tagline: "Reitet bestätigte SOL/EUR-Trends: 4h-Aufwärtstrend, EMA20 über EMA50 und ein 20h-Ausbruch müssen zusammenkommen.",
    startingCapitalEur: 100,
  },
  {
    key: "scout",
    name: "New-Pool Scout",
    nickname: "Der Spaeher",
    prefix: "SCOUT_",
    tagline: "Beobachtet neue Solana-Pools 20 Minuten und handelt nur nach harten Sicherheits-, Aktivitaets- und Route-Checks.",
    startingCapitalEur: 100,
  },
  { key: "hodl", name: "Long-Term Allocation", nickname: "Der HODLer", prefix: "HODL_", tagline: "Investiert woechentlich regelbasiert in BTC, ETH und SOL und behaelt einen dauerhaften Kern.", startingCapitalEur: 100 },
  { key: "freqtrade", name: "Freqtrade", nickname: "Freqtrade", prefix: "FT_", tagline: "Read-only Paper-Trading-Daten aus der separaten Freqtrade-Instanz.", startingCapitalEur: 1000 },
  { key: "futures", name: "Futures", nickname: "Der Hebler", prefix: "FUT_", tagline: "Paper-Trading mit Kraken Futures und begrenztem Hebel.", startingCapitalEur: 100 },
  {
    key: "futures_grid",
    name: "2× Futures Grid",
    nickname: "Der Treppensteiger Turbo",
    prefix: "GRIDFUT_",
    tagline: "Kauft ETH in 0,8-%-Stufen mit 2× Paper-Hebel und fest begrenzter isolierter Margin.",
    startingCapitalEur: 1000,
  },
];

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
  closedTradeCount: number;
  startedAt: number | null;
  lastActivity: number | null;
  runtimeStartedAt: number | null;
  runtimeStatus: string | null;
  hasData: boolean;
  startingCapitalEur: number;
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
  entryPrice: number;
  exitPrice: number | null;
  resolvedAt: number | null;
};

export type PricePoint = { t: number; price: number };

export type TradeDetail = TradeRow & {
  marketQuestion: string;
  priceSeries: PricePoint[];
  priceSource: string;
  latestPrice: number;
  highPrice: number;
  lowPrice: number;
  targetPrice: number | null;
  breakEvenPrice: number | null;
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
  surfer: number | null;
  scout: number | null;
  hodl: number | null;
  freqtrade: number | null;
  futures: number | null;
  futures_grid: number | null;
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
  exit_price: number | null;
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
  const newestFirst = await fetchTable<RawSnapshot>("equity_snapshots", "select=*&order=ts.desc&limit=20000");
  return newestFirst.reverse();
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

    const startingCapitalEur = bot.startingCapitalEur;
    const equity = latestSnap ? num(latestSnap.equity_eur) : startingCapitalEur;
    const cash = latestSnap ? num(latestSnap.cash_eur) : startingCapitalEur;

    const lastTradeTs = botTrades.reduce((max, t) => Math.max(max, num(t.timestamp)), 0);
    const firstTradeTs = botTrades.reduce((min, t) => Math.min(min, num(t.timestamp)), Infinity);
    const firstSnapshotTs = botSnaps.reduce((min, s) => Math.min(min, num(s.ts)), Infinity);
    const startedAt = Math.min(firstTradeTs, firstSnapshotTs);
    const lastActivity = Math.max(lastTradeTs, latestSnap ? num(latestSnap.ts) : 0) || null;
    const runtimeSnapshots = snapshots.filter((s) => s.bot === `__runtime_${bot.key}`);
    const runtime = runtimeSnapshots[runtimeSnapshots.length - 1];

    const totalPnl = equity - startingCapitalEur;
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
      pnlPct: round2((totalPnl / startingCapitalEur) * 100),
      tradeCount: botTrades.length,
      closedTradeCount: doneTrades.length,
      startedAt: Number.isFinite(startedAt) ? startedAt : null,
      lastActivity,
      runtimeStartedAt: runtime ? num(runtime.ts) : null,
      runtimeStatus: runtime ? "running" : null,
      hasData: botTrades.length > 0 || botSnaps.length > 0,
      startingCapitalEur,
    };
  });
}

function toTradeRow(r: RawTrade): TradeRow {
  const meta = BOTS.find((b) => r.market_question.startsWith(b.prefix));
  // "Der Onchain" kodiert CHAIN_{symbol}@{address} (Adresse für die Preis-
  // Auflösung, da zwei dynamisch entdeckte Solana-Tokens denselben Namen
  // tragen können) — im Dashboard reicht das Symbol vor dem "@".
  const rest = meta ? r.market_question.slice(meta.prefix.length) : r.market_question;
  const pair = meta?.key === "memecoin" || meta?.key === "pumpfun" || meta?.key === "pumpfun_v2" || meta?.key === "scout"
    ? rest.split("@")[0]
    : meta?.key === "hodl" || meta?.key === "futures" || meta?.key === "futures_grid"
      ? rest.split("_")[0]
      : rest;
  return {
    id: r.id,
    botKey: meta?.key ?? "?",
    bot: meta?.nickname ?? "?",
    pair,
    side: r.side,
    sizeEur: round2(num(r.size) * num(r.price)),
    price: num(r.price),
    timestamp: num(r.timestamp),
    status: r.status,
    resolved: r.resolved_at != null,
    pnlEur: r.real_pnl == null ? null : round2(num(r.real_pnl)),
    entryPrice: num(r.price),
    exitPrice: r.exit_price == null ? null : num(r.exit_price),
    resolvedAt: r.resolved_at == null ? null : num(r.resolved_at),
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

const KRAKEN_PAIR_MAP: Record<string, string> = {
  XBTEUR: "XXBTZEUR",
  ETHEUR: "XETHZEUR",
  LTCEUR: "XLTCZEUR",
  XRPEUR: "XXRPZEUR",
  XLMEUR: "XXLMZEUR",
};

async function fetchSpotPriceSeries(pair: string, since: number): Promise<PricePoint[]> {
  const requested = KRAKEN_PAIR_MAP[pair] ?? pair;
  try {
    const params = new URLSearchParams({
      pair: requested,
      interval: "60",
      since: String(Math.max(0, Math.floor(since))),
    });
    const res = await fetch(`https://api.kraken.com/0/public/OHLC?${params}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return [];
    const payload = await res.json() as {
      result?: Record<string, unknown>;
    };
    const rows = Object.entries(payload.result ?? {}).find(([key, value]) => key !== "last" && Array.isArray(value))?.[1];
    if (!Array.isArray(rows)) return [];
    return rows.flatMap((row) => {
      if (!Array.isArray(row)) return [];
      const t = num(row[0]);
      const price = num(row[4]);
      return t > 0 && price > 0 ? [{ t, price }] : [];
    });
  } catch {
    return [];
  }
}

async function fetchFuturesPriceSeries(symbol: string): Promise<PricePoint[]> {
  try {
    const safeSymbol = encodeURIComponent(symbol);
    const res = await fetch(
      `https://futures.kraken.com/api/charts/v1/mark/${safeSymbol}/1h?count=720`,
      { next: { revalidate: 300 } },
    );
    if (!res.ok) return [];
    const payload = await res.json() as { candles?: Array<{ time?: number; close?: number | string }> };
    return (payload.candles ?? []).flatMap((candle) => {
      const rawTime = num(candle.time);
      const t = rawTime > 10_000_000_000 ? rawTime / 1000 : rawTime;
      const price = num(candle.close);
      return t > 0 && price > 0 ? [{ t, price }] : [];
    });
  } catch {
    return [];
  }
}

export async function getTradeDetail(id: number): Promise<TradeDetail | null> {
  const trades = await fetchAllTrades();
  const raw = trades.find((trade) => trade.id === id);
  if (!raw) return null;
  const row = toTradeRow(raw);
  const meta = BOTS.find((bot) => raw.market_question.startsWith(bot.prefix));
  const rest = meta ? raw.market_question.slice(meta.prefix.length) : raw.market_question;
  let priceSeries: PricePoint[] = [];
  let priceSource = "Entry-/Exit-Daten";

  if (meta?.key === "futures") {
    const symbol = rest.split("_").slice(0, 2).join("_");
    priceSeries = await fetchFuturesPriceSeries(symbol);
    priceSource = "Kraken Futures · Mark Price · 1h";
  } else if (!["memecoin", "pumpfun", "pumpfun_v2", "scout", "freqtrade"].includes(meta?.key ?? "")) {
    priceSeries = await fetchSpotPriceSeries(row.pair, raw.timestamp - 6 * 3600);
    priceSource = "Kraken Spot · OHLC · 1h";
  }

  const endTs = row.resolvedAt ?? Math.floor(Date.now() / 1000);
  priceSeries = priceSeries.filter((point) => point.t >= raw.timestamp - 6 * 3600 && point.t <= endTs + 6 * 3600);
  if (priceSeries.length < 2) {
    priceSeries = [
      { t: raw.timestamp, price: row.entryPrice },
      { t: endTs, price: row.exitPrice ?? row.entryPrice },
    ];
  }
  const prices = priceSeries.map((point) => point.price);
  const latestPrice = row.exitPrice ?? prices[prices.length - 1] ?? row.entryPrice;
  const targetPct = meta?.key === "freqtrade" ? 0.06 : meta?.key === "futures_grid" ? 0.011 : null;
  const roundTripFee = meta?.key === "freqtrade" ? 0.0025 : null;
  return {
    ...row,
    marketQuestion: raw.market_question,
    priceSeries,
    priceSource,
    latestPrice,
    highPrice: Math.max(...prices, row.entryPrice, latestPrice),
    lowPrice: Math.min(...prices, row.entryPrice, latestPrice),
    targetPrice: targetPct == null ? null : row.entryPrice * (1 + targetPct),
    breakEvenPrice: roundTripFee == null
      ? null
      : row.entryPrice * (1 + roundTripFee) / (1 - roundTripFee),
  };
}

export async function getEquitySeries(): Promise<EquityPoint[]> {
  const snapshots = await fetchAllSnapshots();
  const byTime = new Map<number, EquityPoint>();
  for (const r of snapshots) {
    const bucket = Math.round(num(r.ts) / 60) * 60; // auf Minute runden
    const point =
      byTime.get(bucket) ??
      { t: bucket, dca: null, momentum: null, meanrev: null, arb: null, daytrade: null, memecoin: null, pumpfun: null, pumpfun_v2: null, surfer: null, scout: null, hodl: null, freqtrade: null, futures: null, futures_grid: null };
    if (BOTS.some((b) => b.key === r.bot)) {
      point[r.bot as BotKey] = round2(num(r.equity_eur));
    }
    byTime.set(bucket, point);
  }
  return Array.from(byTime.values()).sort((a, b) => a.t - b.t);
}

export type StrategyParam = { label: string; value: string; hint?: string };
export type StrategyGroup = {
  key: BotKey;
  name: string;
  nickname: string;
  purpose: string;
  currentBehavior: string;
  params: StrategyParam[];
};
export type SettingsView = {
  fees: StrategyParam[];
  strategies: StrategyGroup[];
};

export function getSettings(): SettingsView {
  const fees: StrategyParam[] = [
    { label: "Gebühr pro Kauf/Verkauf", value: "0.40 %", hint: "Wird bei jedem Trade abgezogen (Kraken Taker)." },
    { label: "Gebühr (Maker)", value: "0.16 %", hint: "Falls als Maker gehandelt wird." },
    { label: "Modus", value: "Papierhandel", hint: "Es wird kein echtes Geld eingesetzt." },
    { label: "Startkapital", value: "100 € Standard-Battle/Hebler; 1.000 € Treppensteiger/Freqtrade" },
  ];

  const strategies: StrategyGroup[] = [
    {
      key: "futures_grid",
      name: "2× Futures Grid",
      nickname: "Der Treppensteiger Turbo",
      purpose: "Testet die ETH-Nachkaufstrategie aus dem Video mit Hebel, aber ohne echtes Geld und ohne nachträgliches Margin-Nachschießen.",
      currentBehavior: "Startet sofort long, legt je 0,8 % Rückgang eine gleich große 2×-Position nach und schließt den Zyklus bei 1,1 % über dem Durchschnitt oder vor der Liquidationszone.",
      params: [
        { label: "Startkapital", value: "1.000 €" },
        { label: "Hebel", value: "2× isoliert" },
        { label: "Margin je Stufe", value: "15 €", hint: "Entspricht 30 € Positionswert." },
        { label: "Raster", value: "−0,8 %" },
        { label: "Max. Nachkäufe", value: "50" },
        { label: "Gewinnmitnahme", value: "+1,1 %" },
        { label: "Margin-Wächter", value: "1,25× Maintenance", hint: "Schließt vor der simulierten Liquidation." },
      ],
    },
    {
      key: "dca",
      name: "DCA",
      nickname: "Der Brave",
      purpose: "Kauft regelmäßig kleine Beträge in gefallene Coins und wartet geduldig auf eine Erholung.",
      currentBehavior: "Kauft höchstens zwei Positionen, hält 10 € zurück und verkauft ab rund 3 % Gewinn oder spätestens nach 14 Tagen.",
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
      purpose: "Sucht Coins mit starkem Tagestrend und versucht, auf eine laufende Aufwärtsbewegung aufzuspringen.",
      currentBehavior: "Steigt bei 3–25 % Tagesanstieg mit 12 € ein, hält maximal vier Positionen und beendet Trades spätestens nach 48 Stunden.",
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
      purpose: "Kauft stark gefallene Coins, wenn sie überverkauft wirken und eine Gegenbewegung beginnen könnte.",
      currentBehavior: "Wartet auf mindestens 8 % Tagesverlust und RSI unter 30; pro Position setzt er 15 € mit 4 % Ziel und 5 % Stop ein.",
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
      purpose: "Prüft, ob ein schneller Währungskreislauf über BTC und ETH nach allen Gebühren einen kleinen Gewinn ergibt.",
      currentBehavior: "Scannt alle 45 Sekunden beide Richtungen und handelt mit 25 € nur, wenn mindestens 0,05 € Nettogewinn übrig bleiben.",
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
      purpose: "Handelt kurzfristige Kursstärke und schließt Positionen noch am selben Handelstag wieder.",
      currentBehavior: "Prüft alle fünf Minuten den 4-Stunden-Trend, setzt 10 € pro Trade und hält höchstens sechs Stunden.",
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
      purpose: "Sucht Solana-Memecoins mit frischem Momentum, ausreichender Liquidität und echtem Kaufdruck.",
      currentBehavior: "Setzt 8 € pro Position, filtert extreme Kurzzeit-Pumps und sichert Trades mit Gewinnziel, Trailing-Stop und Verlustgrenze ab.",
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
      purpose: "Beobachtet neue Pump.fun-Token während der Bonding Curve und nach ihrer Migration.",
      currentBehavior: "Handelt rein simuliert mit 5 €, verlangt Momentum und Kaufdruck und hält frühe Positionen maximal 45 Minuten.",
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
      purpose: "Beobachtet neue Pump.fun-Token aggressiver als V1 und sucht frühere Momentum-Einstiege.",
      currentBehavior: "Handelt rein simuliert mit 10 €, verlangt mindestens fünf Trades und drei eindeutige Trader und hält frühe Positionen maximal 30 Minuten.",
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
      key: "surfer",
      name: "Trend/Breakout",
      nickname: "Der Surfer",
      purpose: "Versucht einen bestätigten SOL/EUR-Aufwärtstrend möglichst lange mitzunehmen.",
      currentBehavior: "Steigt nur bei Trend, EMA-Bestätigung, Ausbruch und erhöhtem Volumen ein; der Stop passt sich der Volatilität an.",
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
      purpose: "Beobachtet neue Solana-Pools und handelt nur Kandidaten, die Sicherheits- und Liquiditätsprüfungen bestehen.",
      currentBehavior: "Lässt Pools erst 20 Minuten reifen, setzt 5 € pro Trade und hält mindestens 85 € als Barreserve zurück.",
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
    { key: "hodl", name: "Long-Term Allocation", nickname: "Der HODLer",
      purpose: "Baut langfristig eine feste Mischung aus Bitcoin, Ethereum und Solana auf.",
      currentBehavior: "Investiert wöchentlich bis zu 20 €, reduziert Käufe im Bärenmarkt und behält immer einen langfristigen Kern.",
      params: [
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
