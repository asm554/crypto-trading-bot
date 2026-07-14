from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from polybot.paper_db import DB_PATH

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
POWER_TRADER_DIR = BASE_DIR.parent / 'power_trader_kraken'


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _service_running(service: str) -> bool:
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() == 'active'
    except Exception:
        return False


def _fmt_eur(value: float) -> str:
    return f'{value:.2f}€'


def _dca_open_cost_basis(state: dict) -> float:
    portfolio = state.get('portfolio') or {}
    try:
        return sum(float((pos or {}).get('cost_basis', 0.0)) for pos in portfolio.values())
    except Exception:
        return float(state.get('total_invested', 0.0) or 0.0)


def _prefix_stats(prefixes: tuple[str, ...]) -> dict:
    stats = {
        'count': 0,
        'open_count': 0,
        'resolved_count': 0,
        'pnl': 0.0,
        'pnl_realized': 0.0,
        'pnl_unrealized': 0.0,
    }
    if not os.path.exists(DB_PATH):
        return stats
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cur = conn.cursor()
        clause = ' OR '.join(['market_question LIKE ?' for _ in prefixes])
        params = tuple(f'{p}%' for p in prefixes)

        cur.execute(f'SELECT COUNT(*), COALESCE(SUM(real_pnl),0) FROM paper_trades WHERE {clause}', params)
        count, pnl = cur.fetchone()
        stats['count'] = int(count or 0)
        stats['pnl'] = float(pnl or 0.0)

        cur.execute(f'SELECT COUNT(*) FROM paper_trades WHERE ({clause}) AND resolved_at IS NULL', params)
        stats['open_count'] = int(cur.fetchone()[0] or 0)
        stats['resolved_count'] = max(0, stats['count'] - stats['open_count'])

        cur.execute(f'SELECT COALESCE(SUM(real_pnl),0) FROM paper_trades WHERE ({clause}) AND resolved_at IS NOT NULL', params)
        stats['pnl_realized'] = float(cur.fetchone()[0] or 0.0)

        cur.execute(f'SELECT COALESCE(SUM(real_pnl),0) FROM paper_trades WHERE ({clause}) AND resolved_at IS NULL', params)
        stats['pnl_unrealized'] = float(cur.fetchone()[0] or 0.0)
        return stats
    finally:
        conn.close()


def _azuro_stats() -> dict:
    stats = {'count': 0, 'open_count': 0, 'resolved_count': 0, 'pnl': 0.0}
    if not os.path.exists(DB_PATH):
        return stats
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*), COALESCE(SUM(real_pnl),0) FROM azuro_bets')
        count, pnl = cur.fetchone()
        stats['count'] = int(count or 0)
        stats['pnl'] = float(pnl or 0.0)
        cur.execute('SELECT COUNT(*) FROM azuro_bets WHERE resolved_at IS NULL')
        stats['open_count'] = int(cur.fetchone()[0] or 0)
        stats['resolved_count'] = max(0, stats['count'] - stats['open_count'])
        return stats
    except Exception:
        return stats
    finally:
        conn.close()


def build_overview_snapshot() -> dict:
    dca_state = _load_json(DATA_DIR / 'dca_state.json')
    dca_stats = _prefix_stats(('DCA_',))

    bots = [
        {
            'name': 'DCA',
            'running': _service_running('polybot-dca.service'),
            'summary': (
                f"{_fmt_eur(float(dca_state.get('capital_remaining', 0.0)))} Cash | "
                f"{_fmt_eur(_dca_open_cost_basis(dca_state))} offen investiert | "
                f"{dca_stats['open_count']} offen | "
                f"PnL real. {_fmt_eur(float(dca_stats['pnl_realized']))} | "
                f"PnL unrel. {_fmt_eur(float(dca_stats['pnl_unrealized']))}"
            ),
        },
    ]

    return {
        'mode': 'PAPER',
        'generated_at': datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC'),
        'bots': bots,
    }


def format_overview_message(snapshot: dict) -> str:
    lines = [
        f"📊 *Bot-Überblick* — {snapshot.get('mode', 'PAPER')}",
        f"🕐 {snapshot.get('generated_at', '')}",
    ]
    for bot in snapshot.get('bots', []):
        emoji = '✅' if bot.get('running') else '⚪'
        lines.append(f"{emoji} *{bot.get('name', '?')}*: {bot.get('summary', '-')}")
    return '\n'.join(lines)


def build_overview_message() -> str:
    return format_overview_message(build_overview_snapshot())
