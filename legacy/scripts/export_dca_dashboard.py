#!/usr/bin/env python3
import json, sqlite3, os
from pathlib import Path
from datetime import datetime

DB=Path('/root/polyarbi/polybot/data/paper_trades.db')
OUT_JSON=Path('/root/polyarbi/polybot/data/dca_dashboard.json')
OUT_CSV=Path('/root/polyarbi/polybot/data/dca_closed_trades.csv')
STATE=Path('/root/polyarbi/polybot/data/dca_state.json')

conn=sqlite3.connect(DB)
conn.row_factory=sqlite3.Row
cur=conn.cursor()
cur.execute('''
SELECT id, timestamp, market_question, price as entry_price, exit_price, real_pnl, resolved_at
FROM paper_trades
WHERE market_question LIKE 'DCA_%'
ORDER BY id ASC
''')
rows=[dict(r) for r in cur.fetchall()]
closed=[r for r in rows if r['resolved_at'] is not None]
open_rows=[r for r in rows if r['resolved_at'] is None]

cum=0.0
series=[]
for r in closed:
    pnl=float(r['real_pnl'] or 0.0)
    cum += pnl
    series.append({
        'id': r['id'],
        'resolved_at': r['resolved_at'],
        'pair': (r['market_question'] or '').replace('DCA_',''),
        'net_pnl_eur': round(pnl, 6),
        'cum_net_pnl_eur': round(cum, 6),
    })

state={}
if STATE.exists():
    state=json.loads(STATE.read_text())

payload={
    'generated_at_utc': datetime.utcnow().isoformat() + 'Z',
    'config': {
        'tp_pct': float(os.getenv('DCA_TAKE_PROFIT_PCT', '0.01')),
        'sl_pct': float(os.getenv('DCA_STOP_LOSS_PCT', '0.03')),
        'min_net_profit_eur': float(os.getenv('DCA_MIN_NET_PROFIT_EUR', '0.15')),
    },
    'summary': {
        'total_dca_rows': len(rows),
        'open_positions': len(open_rows),
        'closed_positions': len(closed),
        'closed_net_pnl_eur': round(sum(float(r['real_pnl'] or 0.0) for r in closed), 6),
        'capital_remaining_eur': state.get('capital_remaining'),
        'total_invested_eur': state.get('total_invested'),
    },
    'series_closed_cum_net_pnl': series,
}

OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
with OUT_CSV.open('w', encoding='utf-8') as f:
    f.write('id,resolved_at,pair,net_pnl_eur,cum_net_pnl_eur\n')
    for r in series:
        f.write(f"{r['id']},{r['resolved_at']},{r['pair']},{r['net_pnl_eur']},{r['cum_net_pnl_eur']}\n")

print(OUT_JSON)
print(OUT_CSV)
