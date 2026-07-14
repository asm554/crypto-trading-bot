import "server-only";

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import type BetterSqlite3 from "better-sqlite3";

// better-sqlite3 ist ein natives Modul. Wir laden es erst dann, wenn die
// Datenbank-Datei wirklich existiert (lokal beim laufenden Bot). So läuft die
// Seite auch dort, wo es die Datei nicht gibt (z. B. Vercel), ohne Absturz.
const requireCjs = createRequire(import.meta.url);

// Die Bot-Daten liegen im übergeordneten Python-Projekt.
const DATA_DIR = path.join(process.cwd(), "..", "polybot", "data");
const DB_PATH = path.join(DATA_DIR, "paper_trades.db");
const CONFIG_PATH = path.join(process.cwd(), "..", "polybot", "config.json");

const START_CAPITAL = 100; // Startkapital pro Bot (€)

export type BotKey = "dca" | "momentum" | "meanrev";

type BotMeta = {
  key: BotKey;
  name: string;
  nickname: string;
  prefix: string;
  stateFile: string;
  tagline: string;
};

export const BOTS: BotMeta[] = [
  {
    key: "dca",
    name: "DCA",
    nickname: "Der Brave",
    prefix: "DCA_",
    stateFile: "dca_state.json",
    tagline: "Kauft regelmäßig kleine Beträge und sitzt Rücksetzer aus.",
  },
  {
    key: "momentum",
    name: "Momentum",
    nickname: "Der Zocker",
    prefix: "MOM_",
    stateFile: "momentum_state.json",
    tagline: "Springt auf Coins auf, die gerade stark steigen.",
  },
  {
    key: "meanrev",
    name: "Mean-Reversion",
    nickname: "Der Contrarian",
    prefix: "REV_",
    stateFile: "meanrev_state.json",
    tagline: "Kauft stark gefallene Coins in der Hoffnung auf Erholung.",
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
  lastActivity: number | null;
  hasData: boolean;
};

export type TradeRow = {
  id: number;
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

export type EquityPoint = { t: number; dca: number | null; momentum: number | null; meanrev: number | null };

function openDb(): BetterSqlite3.Database | null {
  if (!fs.existsSync(DB_PATH)) return null;
  try {
    const Database = requireCjs("better-sqlite3") as typeof BetterSqlite3;
    const db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 3000");
    return db;
  } catch {
    return null;
  }
}

function readState(file: string): Record<string, unknown> {
  try {
    const raw = fs.readFileSync(path.join(DATA_DIR, file), "utf8");
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function num(v: unknown, fallback = 0): number {
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? (n as number) : fallback;
}

export function getBotSummaries(): BotSummary[] {
  const db = openDb();
  const summaries: BotSummary[] = [];

  for (const bot of BOTS) {
    const state = readState(bot.stateFile);
    const cash = num(state.capital_remaining, START_CAPITAL);
    const portfolio = (state.portfolio as Record<string, unknown>) ?? {};
    let stateTrades = num(state.trade_count, 0);

    let openPositions = Object.keys(portfolio).length;
    let realized = 0;
    let unrealized = 0;
    let tradeCount = stateTrades;
    let equity = cash;
    let lastActivity: number | null = null;
    let hasData = Object.keys(state).length > 0;

    if (db) {
      const like = `${bot.prefix}%`;
      const open = db
        .prepare(
          "SELECT COUNT(*) c, COALESCE(SUM(unrealized_pnl),0) u FROM paper_trades WHERE market_question LIKE ? AND resolved_at IS NULL",
        )
        .get(like) as { c: number; u: number };
      const done = db
        .prepare(
          "SELECT COUNT(*) c, COALESCE(SUM(real_pnl),0) r FROM paper_trades WHERE market_question LIKE ? AND resolved_at IS NOT NULL",
        )
        .get(like) as { c: number; r: number };
      openPositions = open.c;
      unrealized = num(open.u);
      realized = num(done.r);
      tradeCount = open.c + done.c || stateTrades;

      const snap = db
        .prepare("SELECT equity_eur, ts FROM equity_snapshots WHERE bot = ? ORDER BY ts DESC LIMIT 1")
        .get(bot.key) as { equity_eur: number; ts: number } | undefined;
      if (snap) {
        equity = num(snap.equity_eur);
        lastActivity = num(snap.ts);
      }
      const lastTrade = db
        .prepare("SELECT MAX(timestamp) t FROM paper_trades WHERE market_question LIKE ?")
        .get(like) as { t: number | null };
      if (lastTrade.t) {
        lastActivity = Math.max(lastActivity ?? 0, num(lastTrade.t)) || lastActivity;
        hasData = true;
      }
    }

    const totalPnl = equity - START_CAPITAL;
    summaries.push({
      key: bot.key,
      name: bot.name,
      nickname: bot.nickname,
      tagline: bot.tagline,
      equityEur: round2(equity),
      cashEur: round2(cash),
      openPositions,
      realizedPnlEur: round2(realized),
      unrealizedPnlEur: round2(unrealized),
      totalPnlEur: round2(totalPnl),
      pnlPct: round2((totalPnl / START_CAPITAL) * 100),
      tradeCount,
      lastActivity,
      hasData,
    });
  }

  db?.close();
  return summaries;
}

export function getRecentTrades(limit = 25): TradeRow[] {
  const db = openDb();
  if (!db) return [];
  const prefixes = BOTS.map((b) => `market_question LIKE '${b.prefix}%'`).join(" OR ");
  const rows = db
    .prepare(
      `SELECT id, market_question, side, size, price, timestamp, status, resolved_at, real_pnl
       FROM paper_trades WHERE ${prefixes} ORDER BY timestamp DESC LIMIT ?`,
    )
    .all(limit) as Array<{
    id: number;
    market_question: string;
    side: string;
    size: number;
    price: number;
    timestamp: number;
    status: string;
    resolved_at: number | null;
    real_pnl: number | null;
  }>;
  db.close();

  return rows.map((r) => {
    const meta = BOTS.find((b) => r.market_question.startsWith(b.prefix));
    return {
      id: r.id,
      botKey: meta?.key ?? "?",
      bot: meta?.nickname ?? "?",
      pair: meta ? r.market_question.slice(meta.prefix.length) : r.market_question,
      side: r.side,
      sizeEur: round2(num(r.size) * num(r.price)),
      price: num(r.price),
      timestamp: num(r.timestamp),
      status: r.status,
      resolved: r.resolved_at != null,
      pnlEur: r.real_pnl == null ? null : round2(num(r.real_pnl)),
    };
  });
}

/** Alle Trades (mit Deckel), für die eigene Trades-Seite mit Filtern. */
export function getAllTrades(): TradeRow[] {
  return getRecentTrades(1000);
}

export function getEquitySeries(): EquityPoint[] {
  const db = openDb();
  if (!db) return [];
  const rows = db
    .prepare("SELECT bot, ts, equity_eur FROM equity_snapshots ORDER BY ts ASC")
    .all() as Array<{ bot: string; ts: number; equity_eur: number }>;
  db.close();

  const byTime = new Map<number, EquityPoint>();
  for (const r of rows) {
    const bucket = Math.round(num(r.ts) / 60) * 60; // auf Minute runden
    const point = byTime.get(bucket) ?? { t: bucket, dca: null, momentum: null, meanrev: null };
    if (r.bot === "dca" || r.bot === "momentum" || r.bot === "meanrev") {
      point[r.bot] = round2(num(r.equity_eur));
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
  dbExists: boolean;
};

export function getSettings(): SettingsView {
  let cfg: Record<string, unknown> = {};
  try {
    cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
  } catch {
    cfg = {};
  }
  const takerPct = (num(cfg.crypto_taker_fee_rate, 0.004) * 100).toFixed(2);
  const makerPct = (num(cfg.crypto_maker_fee_rate, 0.0016) * 100).toFixed(2);

  const fees: StrategyParam[] = [
    { label: "Gebühr pro Kauf/Verkauf", value: `${takerPct} %`, hint: "Wird bei jedem Trade abgezogen (Kraken Taker)." },
    { label: "Gebühr (Maker)", value: `${makerPct} %`, hint: "Falls als Maker gehandelt wird." },
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
  ];

  return { fees, strategies, dbExists: fs.existsSync(DB_PATH) };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
