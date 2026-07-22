import "server-only";

const SUPABASE_URL = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY ?? "";
const START_CAPITAL_EUR = 1_000;
const STALE_AFTER_SEC = 120;

type RawSnapshot = {
  ts: number;
  equity_eur: number;
  cash_eur: number;
  open_positions: number;
  unrealized_pnl_eur: number;
  realized_pnl_eur: number;
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
};

export type FreqtradeTrade = {
  id: number;
  timestamp: number;
  pair: string;
  sizeEur: number;
  resolved: boolean;
  pnlEur: number | null;
};

export type FreqtradeStatus = {
  state: "running" | "stale" | "waiting" | "unconfigured";
  dryRun: true;
  connected: boolean;
  equityEur: number;
  cashEur: number;
  openPositions: number;
  realizedPnlEur: number;
  unrealizedPnlEur: number;
  totalPnlEur: number;
  pnlPct: number;
  lastSync: number | null;
  trades: FreqtradeTrade[];
};

function number(value: unknown, fallback = 0): number {
  const parsed = typeof value === "string" ? Number.parseFloat(value) : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

async function fetchTable<T>(table: string, params: Record<string, string>): Promise<T[]> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return [];
  const query = new URLSearchParams(params);
  try {
    const response = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${query}`, {
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
      },
      cache: "no-store",
    });
    if (!response.ok) return [];
    return (await response.json()) as T[];
  } catch {
    return [];
  }
}

export async function getFreqtradeStatus(): Promise<FreqtradeStatus> {
  const configured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
  const [snapshots, rawTrades] = await Promise.all([
    fetchTable<RawSnapshot>("equity_snapshots", {
      select: "ts,equity_eur,cash_eur,open_positions,unrealized_pnl_eur,realized_pnl_eur",
      bot: "eq.freqtrade",
      order: "ts.desc",
      limit: "1",
    }),
    fetchTable<RawTrade>("paper_trades", {
      select: "id,timestamp,market_question,side,size,price,status,resolved_at,real_pnl",
      market_question: "like.FT_*",
      order: "timestamp.desc",
      limit: "50",
    }),
  ]);

  const latest = snapshots[0];
  const lastSync = latest ? number(latest.ts) : null;
  const age = lastSync == null ? Number.POSITIVE_INFINITY : Date.now() / 1000 - lastSync;
  const state: FreqtradeStatus["state"] = !configured
    ? "unconfigured"
    : !latest
      ? "waiting"
      : age > STALE_AFTER_SEC
        ? "stale"
        : "running";
  const equity = latest ? number(latest.equity_eur, START_CAPITAL_EUR) : START_CAPITAL_EUR;
  const totalPnl = equity - START_CAPITAL_EUR;
  const trades = rawTrades.map((trade) => ({
    id: number(trade.id),
    timestamp: number(trade.timestamp),
    pair: trade.market_question.replace(/^FT_/, ""),
    sizeEur: round2(number(trade.size) * number(trade.price)),
    resolved: trade.resolved_at != null,
    pnlEur: trade.real_pnl == null ? null : round2(number(trade.real_pnl)),
  }));

  return {
    state,
    dryRun: true,
    connected: Boolean(latest),
    equityEur: round2(equity),
    cashEur: round2(latest ? number(latest.cash_eur) : START_CAPITAL_EUR),
    openPositions: latest ? number(latest.open_positions) : 0,
    realizedPnlEur: round2(latest ? number(latest.realized_pnl_eur) : 0),
    unrealizedPnlEur: round2(latest ? number(latest.unrealized_pnl_eur) : 0),
    totalPnlEur: round2(totalPnl),
    pnlPct: round2((totalPnl / START_CAPITAL_EUR) * 100),
    lastSync,
    trades,
  };
}
