import "server-only";

// Liest die Bot-Daten aus Supabase (Cloud-Datenbank), damit das Dashboard sowohl
// lokal als auch online (z.B. Vercel) dieselben Live-Daten zeigt. Der Bot selbst
// schreibt dorthin über polybot/cloud_sync.py.

const SUPABASE_URL = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY ?? "";

export type BotKey = "dca" | "dca_core" | "momentum" | "meanrev" | "arb" | "daytrade" | "memecoin" | "pumpfun" | "pumpfun_v2" | "surfer" | "candlestick" | "ultimate" | "scout" | "hodl" | "freqtrade" | "futures" | "futures_grid" | "futures_grid_signal";

type BotMeta = {
  key: BotKey;
  name: string;
  nickname: string;
  prefix: string;
  tagline: string;
  startingCapitalEur: number;
};

export type PositionOverview = {
  pair: string;
  buyPrice: number;
  currentPrice: number | null;
  breakEvenPrice: number | null;
  exitPrice: number | null;
  exitPlan: string;
};

export const BOTS: BotMeta[] = [
  {
    key: "dca",
    name: "DCA",
    nickname: "Der Stapler",
    prefix: "DCA_",
    tagline: "Kauft regelmäßig kleine Beträge und sitzt Rücksetzer aus.",
    startingCapitalEur: 500,
  },
  {
    key: "dca_core",
    name: "Core-DCA (Pilot)",
    nickname: "Der Kern",
    prefix: "DCACORE_",
    tagline: "Kauft nur BTC/ETH im bestätigten Bull-Regime, verkauft alles im Bär und stoppt dauerhaft bei -10 % Drawdown.",
    startingCapitalEur: 500,
  },
  {
    key: "momentum",
    name: "Momentum",
    nickname: "Der Zocker",
    prefix: "MOM_",
    tagline: "Springt auf Coins auf, die gerade stark steigen.",
    startingCapitalEur: 500,
  },
  {
    key: "meanrev",
    name: "Mean-Reversion",
    nickname: "Der Contrarian",
    prefix: "REV_",
    tagline: "Kauft stark gefallene Coins in der Hoffnung auf Erholung.",
    startingCapitalEur: 500,
  },
  {
    key: "arb",
    name: "Triangular-Arb",
    nickname: "Der Pedant",
    prefix: "ARB_",
    tagline: "Sucht risikofreie Rundungsgewinne im EUR-BTC-ETH-Dreieck.",
    startingCapitalEur: 500,
  },
  {
    key: "daytrade",
    name: "Daytrade",
    nickname: "Der Zappler",
    prefix: "DAY_",
    tagline: "Handelt kurzfristige Kursausschläge, nie länger als ein paar Stunden.",
    startingCapitalEur: 500,
  },
  {
    key: "memecoin",
    name: "Onchain-Memecoin",
    nickname: "Der Onchain",
    prefix: "CHAIN_",
    tagline: "Springt früh auf stark steigende Solana-Memecoins auf und nimmt den Gewinn bei rund +15 % mit.",
    startingCapitalEur: 500,
  },
  {
    key: "pumpfun",
    name: "Pullback/Reclaim",
    nickname: "Der PumpFun Reclaim",
    prefix: "PUMP_",
    tagline: "Kauft nicht mehr in den Pump, sondern erst nach einem kontrollierten Rücksetzer und bestätigter Erholung.",
    startingCapitalEur: 500,
  },
  {
    key: "pumpfun_v2",
    name: "Pump.fun V2",
    nickname: "Der PumpFun V2",
    prefix: "PUMP2_",
    tagline: "Chainstack-inspirierte, aktivere Pump.fun-Paper-Strategie ohne Wallet oder Live-Orders.",
    startingCapitalEur: 500,
  },
  {
    key: "surfer",
    name: "Trend/Breakout",
    nickname: "Der Surfer",
    prefix: "SURF_",
    tagline: "Reitet bestätigte SOL/EUR-Trends: 4h-Aufwärtstrend, EMA20 über EMA50 und ein 20h-Ausbruch müssen zusammenkommen.",
    startingCapitalEur: 500,
  },
  {
    key: "candlestick",
    name: "Candlestick-Scoring",
    nickname: "Der Kerzenreiter",
    prefix: "CND_",
    tagline: "Kombiniert Kerzenmuster, Multi-Timeframe-Trend und Momentum mit realistischen Jupiter-Paper-Quotes.",
    startingCapitalEur: 500,
  },
  {
    key: "ultimate",
    name: "Adaptive Multi-Strategie",
    nickname: "Der Ultimative",
    prefix: "ULT_",
    tagline: "Wechselt je nach Marktphase zwischen Trend, Breakout, Pullback und kontrollierter Mean-Reversion.",
    startingCapitalEur: 500,
  },
  {
    key: "scout",
    name: "New-Pool Scout",
    nickname: "Der Spaeher",
    prefix: "SCOUT_",
    tagline: "Beobachtet neue Solana-Pools 20 Minuten und handelt nur nach harten Sicherheits-, Aktivitaets- und Route-Checks.",
    startingCapitalEur: 500,
  },
  { key: "hodl", name: "Long-Term Allocation", nickname: "Der HODLer", prefix: "HODL_", tagline: "Investiert woechentlich regelbasiert in BTC, ETH und SOL und behaelt einen dauerhaften Kern.", startingCapitalEur: 500 },
  // Startkapital kommt aus der dry_run_wallet-Config der Freqtrade-Instanz —
  // hier nur der Anzeige-Wert, beim Skalieren dort auch diesen Wert anpassen.
  { key: "freqtrade", name: "Freqtrade", nickname: "Freqtrade", prefix: "FT_", tagline: "Read-only Paper-Trading-Daten aus der separaten Freqtrade-Instanz.", startingCapitalEur: 1000 },
  { key: "futures", name: "Futures", nickname: "Der Hebler", prefix: "FUT_", tagline: "Paper-Trading mit Kraken Futures und begrenztem Hebel.", startingCapitalEur: 500 },
  {
    key: "futures_grid",
    name: "2× Futures Grid",
    nickname: "Der Treppensteiger Turbo",
    prefix: "GRIDFUT_",
    tagline: "Kauft ETH in 0,8-%-Stufen mit 2× Paper-Hebel und fest begrenzter isolierter Margin.",
    startingCapitalEur: 500,
  },
  {
    key: "futures_grid_signal",
    name: "2× Signal Grid",
    nickname: "Der Treppensteiger Signal",
    prefix: "GRIDSIG_",
    tagline: "Handelt nur bestätigte Aufwärtstrends, setzt 12,50 € Margin je Stufe ein und pausiert nach einem Verlust 30 Tage.",
    startingCapitalEur: 500,
  },
];

// Nur diese überarbeiteten Strategien gehören noch zur aktiven Runde.
// Die übrigen Metadaten bleiben erhalten, damit alte Trade-Detailseiten weiterhin
// verständliche Bot-Namen und Farben anzeigen können.
export const STANDARD_ACTIVE_BOT_KEYS = [
  "dca",
  "dca_core",
  "momentum",
  "meanrev",
  "daytrade",
  "memecoin",
  "surfer",
  "ultimate",
  "pumpfun",
] as const satisfies readonly BotKey[];
export const LEVERAGED_ACTIVE_BOT_KEYS = [
  "futures_grid_signal",
] as const satisfies readonly BotKey[];
export const ACTIVE_BOT_KEYS = [
  ...STANDARD_ACTIVE_BOT_KEYS,
  ...LEVERAGED_ACTIVE_BOT_KEYS,
] as const satisfies readonly BotKey[];
export const ACTIVE_ROUND_STARTED_AT = 1785354759;
export const ACTIVE_BOTS = BOTS.filter((bot) =>
  (ACTIVE_BOT_KEYS as readonly BotKey[]).includes(bot.key),
);

export function isActiveBotKey(key: BotKey | "?"): key is (typeof ACTIVE_BOT_KEYS)[number] {
  return (ACTIVE_BOT_KEYS as readonly string[]).includes(key);
}

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
  tradeUnitLabel: string;
  tradeUnitSingular: string;
  startedAt: number | null;
  lastActivity: number | null;
  runtimeStartedAt: number | null;
  runtimeStatus: string | null;
  hasData: boolean;
  startingCapitalEur: number;
  activePosition: PositionOverview | null;
};

const RUNNING_ACTIVITY_MAX_AGE_SEC = 8 * 3600;

export function isBotRunning(
  bot: BotSummary,
  nowSeconds = Date.now() / 1000,
): boolean {
  if (bot.runtimeStatus !== "running") return false;
  const latestSignal = Math.max(bot.runtimeStartedAt ?? 0, bot.lastActivity ?? 0);
  return latestSignal > 0 && nowSeconds - latestSignal <= RUNNING_ACTIVITY_MAX_AGE_SEC;
}

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

export function isCurrentRoundTrade(trade: TradeRow): boolean {
  return isActiveBotKey(trade.botKey) && trade.timestamp >= ACTIVE_ROUND_STARTED_AT;
}

export type PricePoint = { t: number; price: number };

export type TradeDetail = TradeRow & {
  marketQuestion: string;
  priceSeries: PricePoint[];
  priceSource: string;
  latestPrice: number;
  currentPrice: number | null;
  currentPriceSource: string | null;
  highPrice: number;
  lowPrice: number;
  targetPrice: number | null;
  breakEvenPrice: number | null;
  exitPlan: string;
};

export type EquityPoint = {
  t: number;
  dca: number | null;
  dca_core: number | null;
  momentum: number | null;
  meanrev: number | null;
  arb: number | null;
  daytrade: number | null;
  memecoin: number | null;
  pumpfun: number | null;
  pumpfun_v2: number | null;
  surfer: number | null;
  candlestick: number | null;
  ultimate: number | null;
  scout: number | null;
  hodl: number | null;
  freqtrade: number | null;
  futures: number | null;
  futures_grid: number | null;
  futures_grid_signal: number | null;
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

function normalizeSnapshotCapital(snapshot: RawSnapshot, targetCapitalEur: number) {
  const equity = num(snapshot.equity_eur);
  const cash = num(snapshot.cash_eur);
  const pnl = num(snapshot.realized_pnl_eur) + num(snapshot.unrealized_pnl_eur);
  const inferredCapital = equity - pnl;
  const adjustment = inferredCapital > 0 ? targetCapitalEur - inferredCapital : 0;
  return {
    equity: equity + adjustment,
    cash: cash + adjustment,
  };
}

export async function getBotSummaries(): Promise<BotSummary[]> {
  const [trades, snapshots] = await Promise.all([fetchAllTrades(), fetchAllSnapshots()]);

  return Promise.all(BOTS.map(async (bot) => {
    const roundStartedAt = isActiveBotKey(bot.key) ? ACTIVE_ROUND_STARTED_AT : 0;
    const botTrades = trades.filter(
      (t) => t.market_question.startsWith(bot.prefix) && num(t.timestamp) >= roundStartedAt,
    );
    const openTrades = botTrades.filter((t) => t.resolved_at == null);
    const doneTrades = botTrades.filter((t) => t.resolved_at != null);
    const displayTrades = displayTradesForBot(bot, botTrades);
    const displayDoneTrades = displayTrades.filter((t) => t.resolved_at != null);

    const botSnaps = snapshots.filter(
      (s) => s.bot === bot.key && num(s.ts) >= roundStartedAt,
    );
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
    const normalizedSnapshot = latestSnap
      ? normalizeSnapshotCapital(latestSnap, startingCapitalEur)
      : null;
    const equity = normalizedSnapshot?.equity ?? startingCapitalEur;
    const cash = normalizedSnapshot?.cash ?? startingCapitalEur;

    const lastTradeTs = botTrades.reduce((max, t) => Math.max(max, num(t.timestamp)), 0);
    const firstTradeTs = botTrades.reduce((min, t) => Math.min(min, num(t.timestamp)), Infinity);
    const firstSnapshotTs = botSnaps.reduce((min, s) => Math.min(min, num(s.ts)), Infinity);
    const startedAt = Math.min(firstTradeTs, firstSnapshotTs);
    const lastActivity = Math.max(lastTradeTs, latestSnap ? num(latestSnap.ts) : 0) || null;
    const runtimeStartedKey = `__runtime_${bot.key}`;
    const runtimeStoppedKey = `__runtime_stopped_${bot.key}`;
    const runtimeEvents = snapshots.filter(
      (s) =>
        s.bot === runtimeStartedKey || s.bot === runtimeStoppedKey,
    );
    const latestRuntimeEvent = runtimeEvents[runtimeEvents.length - 1];
    const runtimeStarts = runtimeEvents.filter((event) => event.bot === runtimeStartedKey);
    const runtimeStart = runtimeStarts[runtimeStarts.length - 1];

    const totalPnl = equity - startingCapitalEur;
    const activePosition = await buildPositionOverview(bot, openTrades);
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
      tradeCount: displayTrades.length,
      closedTradeCount: displayDoneTrades.length,
      tradeUnitLabel: bot.key === "ultimate" ? "Positionen" : "Trades",
      tradeUnitSingular: bot.key === "ultimate" ? "Position" : "Trade",
      startedAt: Number.isFinite(startedAt) ? startedAt : null,
      lastActivity,
      runtimeStartedAt: runtimeStart ? num(runtimeStart.ts) : null,
      runtimeStatus: latestRuntimeEvent
        ? latestRuntimeEvent.bot === runtimeStartedKey ? "running" : "stopped"
        : latestSnap ? "running" : null,
      hasData: botTrades.length > 0 || botSnaps.length > 0,
      startingCapitalEur,
      activePosition,
    };
  }));
}

function displayTradesForBot(bot: BotMeta, trades: RawTrade[]): RawTrade[] {
  if (bot.key !== "ultimate") return trades;
  return trades.filter((trade) => trade.status !== "paper_runner");
}

async function buildPositionOverview(bot: BotMeta, openTrades: RawTrade[]): Promise<PositionOverview | null> {
  if (openTrades.length === 0) return null;
  const primary = openTrades[0];
  const pair = toTradeRow(primary).pair;
  const samePair = openTrades.filter((trade) => toTradeRow(trade).pair === pair);
  const totalShares = samePair.reduce((sum, trade) => sum + num(trade.size), 0);
  const buyPrice = totalShares > 0
    ? samePair.reduce((sum, trade) => sum + num(trade.size) * num(trade.price), 0) / totalShares
    : num(primary.price);
  const rule = exitRuleFor(bot.key);
  const currentPrice = await fetchCurrentPriceForBot(bot, primary, pair);
  return {
    pair,
    buyPrice,
    currentPrice,
    breakEvenPrice: rule.feeRate == null ? null : buyPrice * (1 + rule.feeRate) / (1 - rule.feeRate),
    exitPrice: rule.targetPct == null ? null : buyPrice * (1 + rule.targetPct),
    exitPlan: rule.label,
  };
}

function exitRuleFor(key: BotKey): { feeRate: number | null; targetPct: number | null; label: string } {
  const spotFee = 0.004;
  switch (key) {
    case "dca": return { feeRate: spotFee, targetPct: 0.03, label: "+3 % Gewinnziel" };
    case "dca_core": return { feeRate: spotFee, targetPct: null, label: "Verkauf bei Bär-Regime oder -10 % Drawdown" };
    case "meanrev": return { feeRate: spotFee, targetPct: 0.04, label: "+4 % Gewinnziel" };
    case "futures_grid":
      return { feeRate: 0.0005, targetPct: 0.011, label: "+1,1 % über Durchschnitt" };
    case "futures_grid_signal":
      return { feeRate: 0.0005, targetPct: 0.012, label: "+1,2 % über Durchschnitt" };
    case "freqtrade": return { feeRate: 0.0025, targetPct: 0.06, label: "+6 % ROI-Regel" };
    case "memecoin": return { feeRate: null, targetPct: 0.15, label: "+15 % Ziel, dann Trailing" };
    case "pumpfun": return { feeRate: null, targetPct: 0.20, label: "+20 % Ziel, dann Trailing" };
    case "pumpfun_v2": return { feeRate: null, targetPct: 0.25, label: "+25 % Ziel, dann Trailing" };
    case "scout": return { feeRate: null, targetPct: 0.25, label: "+25 % Gewinnziel" };
    case "momentum": return { feeRate: spotFee, targetPct: null, label: "Trailing-Stop −2,5 % vom Hoch" };
    case "daytrade": return { feeRate: spotFee, targetPct: null, label: "Trailing-Stop −1,5 % vom Hoch" };
    case "surfer": return { feeRate: spotFee, targetPct: null, label: "Trailing-Stop −3 % vom Hoch" };
    case "candlestick": return { feeRate: null, targetPct: null, label: "ATR-Trailing · kein fixer Exit" };
    case "ultimate": return { feeRate: 0.008, targetPct: null, label: "Netto-2R-Teilgewinn · ATR-Trailing" };
    case "hodl": return { feeRate: spotFee, targetPct: null, label: "Langfristig halten · kein Exit" };
    case "futures": return { feeRate: null, targetPct: null, label: "Exit gemäß Futures-Regel" };
    case "arb": return { feeRate: null, targetPct: null, label: "Atomarer Zyklus · kein offener Exit" };
  }
}

async function fetchCurrentPriceForBot(bot: BotMeta, trade: RawTrade, pair: string): Promise<number | null> {
  if (bot.key === "futures") {
    const rest = trade.market_question.slice(bot.prefix.length);
    return fetchFuturesCurrentPrice(rest.split("_").slice(0, 2).join("_"));
  }
  if (["memecoin", "pumpfun", "pumpfun_v2", "scout"].includes(bot.key)) return null;
  if (bot.key === "candlestick") return fetchCandlestickCurrentPrice();
  return fetchSpotCurrentPrice(pair);
}

async function fetchCandlestickCurrentPrice(): Promise<number | null> {
  const [solUsdc, eurUsd] = await Promise.all([
    fetchSpotCurrentPrice("SOLUSDC"),
    fetchSpotCurrentPrice("EURUSD"),
  ]);
  return solUsdc != null && eurUsd != null && eurUsd > 0 ? solUsdc / eurUsd : null;
}

function toTradeRow(r: RawTrade): TradeRow {
  const meta = BOTS.find((b) => r.market_question.startsWith(b.prefix));
  // "Der Onchain" kodiert CHAIN_{symbol}@{address} (Adresse für die Preis-
  // Auflösung, da zwei dynamisch entdeckte Solana-Tokens denselben Namen
  // tragen können) — im Dashboard reicht das Symbol vor dem "@".
  const rest = meta ? r.market_question.slice(meta.prefix.length) : r.market_question;
  const pair = meta?.key === "memecoin" || meta?.key === "pumpfun" || meta?.key === "pumpfun_v2" || meta?.key === "scout"
    ? rest.split("@")[0]
    : meta?.key === "hodl" || meta?.key === "futures" || meta?.key === "futures_grid" || meta?.key === "futures_grid_signal"
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
  const normalizedPair = pair.replaceAll("/", "").replaceAll("-", "");
  const requested = KRAKEN_PAIR_MAP[normalizedPair] ?? normalizedPair;
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

async function fetchSpotCurrentPrice(pair: string): Promise<number | null> {
  const normalizedPair = pair.replaceAll("/", "").replaceAll("-", "");
  const requested = KRAKEN_PAIR_MAP[normalizedPair] ?? normalizedPair;
  try {
    const res = await fetch(`https://api.kraken.com/0/public/Ticker?pair=${encodeURIComponent(requested)}`, {
      next: { revalidate: 30 },
    });
    if (!res.ok) return null;
    const payload = await res.json() as { result?: Record<string, { c?: unknown[] }> };
    const ticker = Object.values(payload.result ?? {})[0];
    const price = num(ticker?.c?.[0]);
    return price > 0 ? price : null;
  } catch {
    return null;
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

async function fetchFuturesCurrentPrice(symbol: string): Promise<number | null> {
  try {
    const res = await fetch("https://futures.kraken.com/derivatives/api/v3/tickers", {
      next: { revalidate: 30 },
    });
    if (!res.ok) return null;
    const payload = await res.json() as {
      tickers?: Array<{ symbol?: string; markPrice?: number | string; last?: number | string }>;
    };
    const ticker = (payload.tickers ?? []).find((candidate) => candidate.symbol === symbol);
    const price = num(ticker?.markPrice ?? ticker?.last);
    return price > 0 ? price : null;
  } catch {
    return null;
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
  let currentPrice: number | null = null;
  let currentPriceSource: string | null = null;

  if (meta?.key === "futures") {
    const symbol = rest.split("_").slice(0, 2).join("_");
    [priceSeries, currentPrice] = await Promise.all([
      fetchFuturesPriceSeries(symbol),
      fetchFuturesCurrentPrice(symbol),
    ]);
    priceSource = "Kraken Futures · Mark Price · 1h";
    currentPriceSource = currentPrice == null ? null : "Kraken Futures · Live Mark Price";
  } else if (!["memecoin", "pumpfun", "pumpfun_v2", "scout"].includes(meta?.key ?? "")) {
    [priceSeries, currentPrice] = await Promise.all([
      fetchSpotPriceSeries(row.pair, raw.timestamp - 6 * 3600),
      fetchSpotCurrentPrice(row.pair),
    ]);
    priceSource = "Kraken Spot · OHLC · 1h";
    currentPriceSource = currentPrice == null ? null : "Kraken Spot · Live Ticker";
    if (meta?.key === "candlestick") {
      const eurUsd = await fetchSpotCurrentPrice("EURUSD");
      if (eurUsd != null && eurUsd > 0) priceSeries = priceSeries.map((point) => ({ ...point, price: point.price / eurUsd }));
      currentPrice = await fetchCandlestickCurrentPrice();
      priceSource = "Kraken SOL/USDC · in EUR · OHLC 1h";
      currentPriceSource = currentPrice == null ? null : "Kraken SOL/USDC · live in EUR";
    }
  }

  const endTs = row.resolvedAt ?? Math.floor(Date.now() / 1000);
  priceSeries = priceSeries.filter((point) => point.t >= raw.timestamp - 6 * 3600 && point.t <= endTs + 6 * 3600);
  if (priceSeries.length < 2) {
    priceSeries = [
      { t: raw.timestamp, price: row.entryPrice },
      { t: endTs, price: row.exitPrice ?? row.entryPrice },
    ];
  } else {
    priceSeries.push({ t: raw.timestamp, price: row.entryPrice });
    if (row.resolvedAt && row.exitPrice) {
      priceSeries.push({ t: row.resolvedAt, price: row.exitPrice });
    }
    priceSeries.sort((a, b) => a.t - b.t);
  }
  const prices = priceSeries.map((point) => point.price);
  const latestPrice = row.exitPrice ?? prices[prices.length - 1] ?? row.entryPrice;
  const exitRule = meta
    ? exitRuleFor(meta.key)
    : { feeRate: null, targetPct: null, label: "Kein Exit-Plan verfügbar" };
  return {
    ...row,
    marketQuestion: raw.market_question,
    priceSeries,
    priceSource,
    latestPrice,
    currentPrice,
    currentPriceSource,
    highPrice: Math.max(...prices, row.entryPrice, latestPrice),
    lowPrice: Math.min(...prices, row.entryPrice, latestPrice),
    targetPrice: exitRule.targetPct == null
      ? null
      : row.entryPrice * (1 + exitRule.targetPct),
    breakEvenPrice: exitRule.feeRate == null
      ? null
      : row.entryPrice * (1 + exitRule.feeRate) / (1 - exitRule.feeRate),
    exitPlan: exitRule.label,
  };
}

export async function getEquitySeries(): Promise<EquityPoint[]> {
  const snapshots = await fetchAllSnapshots();
  const byTime = new Map<number, EquityPoint>();
  for (const r of snapshots) {
    if (
      (ACTIVE_BOT_KEYS as readonly string[]).includes(r.bot)
      && num(r.ts) < ACTIVE_ROUND_STARTED_AT
    ) {
      continue;
    }
    const bucket = Math.round(num(r.ts) / 60) * 60; // auf Minute runden
    const point =
      byTime.get(bucket) ??
      { t: bucket, dca: null, dca_core: null, momentum: null, meanrev: null, arb: null, daytrade: null, memecoin: null, pumpfun: null, pumpfun_v2: null, surfer: null, candlestick: null, ultimate: null, scout: null, hodl: null, freqtrade: null, futures: null, futures_grid: null, futures_grid_signal: null };
    if (BOTS.some((b) => b.key === r.bot)) {
      const bot = BOTS.find((candidate) => candidate.key === r.bot);
      point[r.bot as BotKey] = round2(
        normalizeSnapshotCapital(r, bot?.startingCapitalEur ?? 500).equity,
      );
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
    { label: "Kraken Spot-Gebühr", value: "0,40 %", hint: "Im Paper-Modell für Kauf und Verkauf berücksichtigt." },
    { label: "Treppensteiger-Gebühr je Seite", value: "0,05 %", hint: "Für den separaten 2×-Signal-Bot." },
    { label: "Pump.fun Ausführung", value: "Curve + simulierte Gebühr", hint: "Kein echter Wallet-Handel." },
    { label: "Modus", value: "Papierhandel", hint: "Es wird kein echtes Geld eingesetzt." },
    { label: "Startkapital", value: "500 € je laufendem Bot; historische, nicht laufende Instanzen bleiben separat" },
  ];

  const strategies: StrategyGroup[] = [
    {
      key: "futures_grid",
      name: "2× Futures Grid",
      nickname: "Der Treppensteiger Turbo",
      purpose: "Testet die ETH-Nachkaufstrategie aus dem Video mit Hebel, aber ohne echtes Geld und ohne nachträgliches Margin-Nachschießen.",
      currentBehavior: "Startet sofort long, legt je 0,8 % Rückgang eine gleich große 2×-Position nach und schließt den Zyklus bei 1,1 % über dem Durchschnitt oder vor der Liquidationszone.",
      params: [
        { label: "Startkapital", value: "500 €" },
        { label: "Hebel", value: "2× isoliert" },
        { label: "Margin je Stufe", value: "7,50 €", hint: "Entspricht 15 € Positionswert." },
        { label: "Raster", value: "−0,8 %" },
        { label: "Max. Nachkäufe", value: "50" },
        { label: "Gewinnmitnahme", value: "+1,1 %" },
        { label: "Margin-Wächter", value: "1,25× Maintenance", hint: "Schließt vor der simulierten Liquidation." },
      ],
    },
    {
      key: "futures_grid_signal",
      name: "2× Signal Grid",
      nickname: "Der Treppensteiger Signal",
      purpose: "Prüft, ob Rücksetzer-, Trend- und Erholungssignale den ursprünglichen Treppensteiger stabiler machen.",
      currentBehavior: "Startet nur in einem klaren Aufwärtstrend mit positiver 12-Stunden-Bewegung. Neue Preisstufen werden zunächst vorgemerkt und erst nach sichtbarer Erholung gekauft. Nach einem Verlust bleibt der Bot 30 Tage an der Seitenlinie.",
      params: [
        { label: "Startkapital", value: "500 €" },
        { label: "Hebel", value: "2× isoliert" },
        { label: "Margin je Stufe", value: "12,50 €", hint: "Entspricht 25 € Positionswert." },
        { label: "Stufenabstand", value: "dynamisch 0,8–1,6 %" },
        { label: "Max. Stufen gesamt", value: "8" },
        { label: "Pause nach Gewinn", value: "12 Std." },
        { label: "Pause nach Verlust", value: "30 Tage" },
        { label: "Gewinnmitnahme", value: "+1,2 % über Durchschnitt" },
        { label: "Zyklus-Verlustgrenze", value: "−3 % vom Zyklus-Startkapital" },
        { label: "Spätestes Ende", value: "21 Tage" },
      ],
    },
    {
      key: "dca",
      name: "DCA",
      nickname: "Der Brave",
      purpose: "Kauft regelmäßig kleine Beträge in gefallene Coins und wartet geduldig auf eine Erholung.",
      currentBehavior: "Kauft höchstens zwei Positionen, hält 50 € zurück und verkauft ab rund 3 % Gewinn oder spätestens nach 14 Tagen.",
      params: [
        { label: "Kauf-Intervall", value: "alle 4 Std." },
        { label: "Kapital verteilt auf", value: "5 Runden" },
        { label: "Gewinnmitnahme", value: "+3 %", hint: "Position wird mit +3 % Gewinn verkauft." },
        { label: "Notverkauf nach Zeit", value: "nach 14 Tagen", hint: "Verlust-Bremse: alte Positionen werden zwangsweise geschlossen." },
        { label: "Max. offene Positionen", value: "2" },
        { label: "Max. pro Coin", value: "100 €" },
        { label: "Bar-Reserve", value: "50 €", hint: "Wird nie investiert." },
      ],
    },
    {
      key: "dca_core",
      name: "Core-DCA (Pilot)",
      nickname: "Der Kern",
      purpose: "Testet einen viel selteneren, regime-gefilterten DCA-Ansatz als Ersatzkandidat für Der Brave -- Vorwärts-Pilot nach einem Backtest-Holdout ohne Trades.",
      currentBehavior: "Kauft montags je 25 € BTC und ETH, aber nur wenn BTC über EMA200 und EMA50 über EMA200 liegt. Verkauft alles bei bestätigtem Bär-Regime oder stoppt dauerhaft bei -10 % Drawdown vom Hoch.",
      params: [
        { label: "Kauf-Intervall", value: "montags, max. 1× pro Woche" },
        { label: "Wochenbudget", value: "50 €", hint: "25 € BTC + 25 € ETH." },
        { label: "Bar-Reserve", value: "50 €", hint: "Wird nie investiert." },
        { label: "Regime-Filter", value: "BTC Close & EMA50 über EMA200 (Bull)", hint: "Kein Kauf in neutraler oder Bär-Phase." },
        { label: "Bär-Exit", value: "alles verkaufen bei Close & EMA50 unter EMA200" },
        { label: "Kontoverlust-Sperre", value: "-10 % vom Equity-Hoch -> dauerhafter Stopp", hint: "Kein automatischer Neustart -- entspricht exakt dem getesteten Backtest-Verhalten." },
      ],
    },
    {
      key: "momentum",
      name: "Momentum",
      nickname: "Der Zocker",
      purpose: "Sucht Coins mit starkem Tagestrend und versucht, auf eine laufende Aufwärtsbewegung aufzuspringen.",
      currentBehavior: "Steigt bei 3–25 % Tagesanstieg mit 60 € ein, hält maximal vier Positionen und beendet Trades spätestens nach 48 Stunden.",
      params: [
        { label: "Prüf-Intervall", value: "jede Std." },
        { label: "Einstieg bei Anstieg", value: "+3 % bis +25 % (24 Std.)" },
        { label: "Nachlaufende Stop-Bremse", value: "2,5 %", hint: "Verkauft, wenn der Kurs 2,5 % vom Höchststand fällt." },
        { label: "Harte Verlust-Bremse", value: "4 %" },
        { label: "Positionsgröße", value: "60 €" },
        { label: "Max. offene Positionen", value: "4" },
        { label: "Max. Haltedauer", value: "48 Std." },
        { label: "Cooldown nach Verlust", value: "24 Std.", hint: "Nach einem Verlust-Exit wird dasselbe Paar 24 Std. gesperrt (statt 6 Std. nach Gewinn) — verhindert wiederholtes Nachkaufen derselben fallenden Rally." },
      ],
    },
    {
      key: "meanrev",
      name: "Mean-Reversion",
      nickname: "Der Contrarian",
      purpose: "Kauft stark gefallene Coins, wenn sie überverkauft wirken und eine Gegenbewegung beginnen könnte.",
      currentBehavior: "Wartet auf mindestens 8 % Tagesverlust und RSI unter 30; pro Position setzt er 75 € mit 4 % Ziel und 5 % Stop ein.",
      params: [
        { label: "Prüf-Intervall", value: "jede Std." },
        { label: "Einstieg bei Absturz", value: "ab −8 % (24 Std.)" },
        { label: "Zusatz-Bedingung", value: "RSI unter 30", hint: "Coin gilt als überverkauft." },
        { label: "Gewinnmitnahme", value: "+4 %" },
        { label: "Verlust-Bremse", value: "−5 %" },
        { label: "Positionsgröße", value: "75 €" },
        { label: "Max. offene Positionen", value: "3" },
      ],
    },
    {
      key: "arb",
      name: "Triangular-Arb",
      nickname: "Der Pedant",
      purpose: "Prüft, ob ein schneller Währungskreislauf über BTC und ETH nach allen Gebühren einen kleinen Gewinn ergibt.",
      currentBehavior: "Scannt alle 45 Sekunden beide Richtungen und handelt mit 125 € nur, wenn mindestens 0,25 € Nettogewinn übrig bleiben.",
      params: [
        { label: "Prüf-Intervall", value: "alle 45 Sek." },
        { label: "Dreieck", value: "EUR → BTC → ETH → EUR", hint: "Beide Richtungen werden geprüft." },
        { label: "Ticket-Größe", value: "125 €" },
        { label: "Mindestgewinn", value: "0,25 €", hint: "Nach allen drei Gebühren-Legs." },
        { label: "Max. Trades/Std.", value: "6", hint: "Sicherheits-Deckel gegen Fehlkonfiguration." },
      ],
    },
    {
      key: "daytrade",
      name: "Daytrade",
      nickname: "Der Zappler",
      purpose: "Handelt kurzfristige Kursstärke und schließt Positionen noch am selben Handelstag wieder.",
      currentBehavior: "Prüft alle fünf Minuten den 4-Stunden-Trend, setzt 50 € pro Trade und hält höchstens sechs Stunden.",
      params: [
        { label: "Prüf-Intervall", value: "alle 5 Min." },
        { label: "Einstieg bei Anstieg", value: "+3 % bis +25 % (4 Std.)", hint: "Kurzfristiges Momentum statt 24h-Trend." },
        { label: "Nachlaufende Stop-Bremse", value: "1,5 %" },
        { label: "Harte Verlust-Bremse", value: "3 %" },
        { label: "Positionsgröße", value: "50 €" },
        { label: "Max. offene Positionen", value: "4" },
        { label: "Max. Haltedauer", value: "6 Std." },
      ],
    },
    {
      key: "memecoin",
      name: "Onchain-Memecoin",
      nickname: "Der Onchain",
      purpose: "Sucht Solana-Memecoins mit frischem Momentum, ausreichender Liquidität und echtem Kaufdruck.",
      currentBehavior: "Setzt 40 € pro Position, filtert extreme Kurzzeit-Pumps und sichert Trades mit Gewinnziel, Trailing-Stop und Verlustgrenze ab.",
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
        { label: "Positionsgröße", value: "40 €" },
        { label: "Max. offene Positionen", value: "3" },
        { label: "Max. Haltedauer", value: "24 Std." },
        { label: "Swap-Slippage", value: "1,5 %", hint: "On-chain gibt es kein Bid/Ask – simuliert den AMM-Preisimpact." },
      ],
    },
    {
      key: "pumpfun",
      name: "Pump.fun",
      nickname: "Der PumpFun",
      purpose: "Beobachtet neue Pump.fun-Token und wartet nach dem ersten Anstieg auf einen kontrollierten Rücksetzer mit bestätigter Erholung.",
      currentBehavior: "Handelt rein simuliert mit 10 € und höchstens einer Position. Ein gerader Pump wird nicht mehr gekauft: Erst Anstieg, dann 7–25 % Rücksetzer und anschließend neuer Kaufdruck lösen einen Einstieg aus.",
      params: [
        { label: "Datenquelle", value: "PumpPortal WebSocket", hint: "Neue Token und Trades; keine Wallet und keine Orders." },
        { label: "Modus", value: "100 % Paper-Trading" },
        { label: "Positionsgröße", value: "10 €" },
        { label: "Phasen", value: "Neue Entries nur Early Bonding Curve", hint: "Bereits offene Positionen werden auch nach einer Migration weiter überwacht." },
        { label: "Vorheriger Anstieg", value: "mindestens +18 %" },
        { label: "Kontrollierter Rücksetzer", value: "−7 % bis −25 % vom Hoch" },
        { label: "Bestätigte Erholung", value: "+2 % bis +10 % in 30 Sek.", hint: "Mindestens 6 neue Trades und Buy/Sell ≥ 1,2 in der letzten Minute." },
        { label: "Mindestaktivität", value: "30 Trades · 12 Trader · Buy/Sell ≥ 1,4" },
        { label: "Curve-Fill", value: "virtuelle Reserven + simulierte Gebühr" },
        { label: "Verlust-Bremse", value: "−12 %" },
        { label: "Gewinnsicherung", value: "+20 %, Trailing 8 %, Floor +5 %" },
        { label: "Max. Haltedauer Early", value: "20 Min." },
        { label: "Max. offene Positionen", value: "1" },
      ],
    },
    {
      key: "pumpfun_v2",
      name: "Pump.fun V2",
      nickname: "Der PumpFun V2",
      purpose: "Beobachtet neue Pump.fun-Token aggressiver als V1 und sucht frühere Momentum-Einstiege.",
      currentBehavior: "Handelt rein simuliert mit 50 €, verlangt mindestens fünf Trades und drei eindeutige Trader und hält frühe Positionen maximal 30 Minuten.",
      params: [
        { label: "Datenquelle", value: "PumpPortal WebSocket" },
        { label: "Modus", value: "100 % Paper-Trading" },
        { label: "Positionsgröße", value: "50 €" },
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
        { label: "Risiko pro Trade", value: "max. 2,50 €", hint: "Bestimmt die Positionsgröße über den ATR-Stop-Abstand." },
        { label: "Max. Positionsgröße", value: "125 €" },
        { label: "Verlustpause", value: "24 Std. nach 3 Verlusten in Folge" },
        { label: "Kontoverlust-Sperre", value: "−10 %", hint: "Ab dieser Verlustgrenze keine neuen Einstiege, offene Positionen laufen weiter." },
      ],
    },
    {
      key: "candlestick",
      name: "Candlestick-Scoring",
      nickname: "Der Kerzenreiter",
      purpose: "Handelt SOL/USDC nur bei gemeinsam bestätigtem Trend, Momentum, Volumen und bullischem Kerzenmuster.",
      currentBehavior: "Bewertet abgeschlossene 1h- und 15m-Kerzen mit bis zu 100 Punkten und nutzt Jupiter ausschließlich für realistische Paper-Fills.",
      params: [
        { label: "Handelspaar", value: "SOL/USDC", hint: "PnL und Equity werden für das Battle in EUR umgerechnet." },
        { label: "Mindestscore", value: "75/100" },
        { label: "Trendfilter", value: "EMA50 > EMA200 (1h)" },
        { label: "Momentum", value: "EMA20 > EMA50, RSI 52–70, MACD positiv (15m)" },
        { label: "Kerzenmuster V1", value: "Bullish Engulfing, Hammer oder bullische Inside-Bar" },
        { label: "Volumen", value: "mindestens 130 % des 20-Kerzen-Mittels" },
        { label: "Paper-Fills", value: "Jupiter Quote API", hint: "Keine Wallet, keine Signatur und keine Transaktion." },
        { label: "Risiko pro Trade", value: "max. 2,50 €" },
        { label: "Max. Position", value: "125 €" },
        { label: "Chance/Risiko", value: "mindestens 1,8 : 1 nach Rundreisekosten" },
        { label: "Exit", value: "2× ATR-Trailing, EMA-/Momentum-/Strukturbruch" },
        { label: "Max. Haltedauer", value: "48 Std." },
        { label: "Verlustpause", value: "24 Std. nach 3 Verlusten" },
        { label: "Kontoverlust-Sperre", value: "−10 %" },
      ],
    },
    {
      key: "ultimate",
      name: "Adaptive Multi-Strategie",
      nickname: "Der Ultimative",
      purpose: "Wählt abhängig von der Marktphase die passende Long-Strategie für BTC/EUR, ETH/EUR oder SOL/EUR.",
      currentBehavior: "Handelt nur neue, klar bestätigte Signale. Gebühren, Mindesthaltezeit, Wiederholungssperren und Verlustpausen verhindern die früheren schnellen Minus-Trades.",
      params: [
        { label: "Märkte", value: "BTC/EUR, ETH/EUR, SOL/EUR" },
        { label: "Marktphasen", value: "Aufwärtstrend, seitwärts, abwärts, unklar" },
        { label: "Mindestscore", value: "85/100" },
        { label: "Indikatoren", value: "EMA20/50/200, RSI, MACD, ATR und Volumen" },
        { label: "Setups", value: "Breakout, Pullback oder kontrollierte Mean-Reversion" },
        { label: "Kerzenmuster", value: "Engulfing, Hammer, Inside-Bar, Morning Star, Three White Soldiers, Tweezer Bottom, Piercing" },
        { label: "Risiko pro Position", value: "max. 2,50 €" },
        { label: "Positionsgröße", value: "max. 125 €" },
        { label: "Nachkauf", value: "maximal 1, nur bei bestätigtem Trend-Pullback" },
        { label: "Netto-CRV", value: "mindestens 2 : 1 nach allen Gebühren" },
        { label: "Gewinnsicherung", value: "50 % Teilgewinn bei 2R, Rest per ATR-Trailing" },
        { label: "Mindesthaltezeit", value: "60 Min.", hint: "Ein normaler Signalausstieg darf nicht mehr direkt nach dem Kauf auslösen. Der Schutzstopp bleibt immer aktiv." },
        { label: "Wiederholungssperre", value: "gleiches Signal nie doppelt · 12 Std. Pause je Markt" },
        { label: "Tageslimit", value: "maximal 3 neue Positionen" },
        { label: "Verlustpause", value: "12 Std. nach 2 Verlustpositionen" },
        { label: "Weitere Exits", value: "Break-even, bestätigter Regime-/Momentumbruch, 72-Std.-Zeitlimit" },
        { label: "Kontoverlust-Sperre", value: "−10 %" },
        { label: "Modus", value: "100 % Paper-Trading" },
      ],
    },
    {
      key: "scout",
      name: "New-Pool Scout",
      nickname: "Der Spaeher",
      purpose: "Beobachtet neue Solana-Pools und handelt nur Kandidaten, die Sicherheits- und Liquiditätsprüfungen bestehen.",
      currentBehavior: "Lässt Pools erst 20 Minuten reifen, setzt 25 € pro Trade und hält mindestens 425 € als Barreserve zurück.",
      params: [
        { label: "Pruef-Intervall", value: "alle 30 Sek." },
        { label: "Reifezeit", value: "20 Min.", hint: "Neue Pools werden vor jeder Bewertung beobachtet." },
        { label: "Sicherheits-Gates", value: "Mint + Freeze deaktiviert, Audit/Shield sauber" },
        { label: "Markt-Gates", value: "ab 40.000 $ Liquiditaet, 150 Holdern und Score 60/100" },
        { label: "Route-Gate", value: "max. 1,5 % Preiswirkung, 8 % Rundreisekosten" },
        { label: "Positionsgroesse", value: "25 €", hint: "Maximal zwei Positionen; 425 € bleiben Barreserve." },
        { label: "Verlust-Bremse", value: "-12 %" },
        { label: "Gewinnmitnahme", value: "+25 %; Trailing ab +10 %" },
        { label: "Max. Haltedauer", value: "6 Std." },
        { label: "Risk-off", value: "12 Std. nach 2 Verlusten; Kontolimit -8 %" },
      ],
    },
    { key: "hodl", name: "Long-Term Allocation", nickname: "Der HODLer",
      purpose: "Baut langfristig eine feste Mischung aus Bitcoin, Ethereum und Solana auf.",
      currentBehavior: "Investiert wöchentlich bis zu 100 €, reduziert Käufe im Bärenmarkt und behält immer einen langfristigen Kern.",
      params: [
      { label: "Wochenbudget", value: "max. 100 €", hint: "100 € Barreserve bleiben unangetastet." },
      { label: "Basisverteilung", value: "50 % BTC, 30 % ETH, 20 % SOL" },
      { label: "Marktphase", value: "EMA50/EMA200 + 90-Tage-Momentum" },
      { label: "Baerenmarkt", value: "nur 35 % der Rate in BTC" },
      { label: "Gewinnmitnahme", value: "25 % bei +100 %, 25 % bei +200 %; Kern bleibt" },
      { label: "Stops", value: "kein normaler Stop-Loss" },
    ] },
  ];

  return {
    fees,
    strategies: strategies.filter((strategy) =>
      (ACTIVE_BOT_KEYS as readonly BotKey[]).includes(strategy.key),
    ),
  };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
