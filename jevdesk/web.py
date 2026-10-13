"""Lokale, nur lesende Status-Seite (http://127.0.0.1:8765). Kein Schreibzugriff auf die DB, keine Secrets,
nur localhost. Token-Namen kommen aus dem Netz und werden im Browser nur als Text eingesetzt (kein HTML)."""
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import book, record
from .config import PAPER


def _ro(path):
    p = Path(path)
    if not p.exists():
        return None
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _answer_digest(why: str | None) -> dict:
    try:
        a = json.loads(why or "{}")
    except ValueError:
        return {}
    out = {}
    if "shape" in a:
        out["shape"] = f"{a['shape'].get('choice')} ({a['shape'].get('probabilities', {}).get('crowd', 0):.2f} crowd)"
    for k in ("concentration_is_exit_risk", "momentum_already_spent", "liquidity_fits_ticket", "dev_still_loaded"):
        if k in a and "noul" in a[k]:
            out[k] = round(a[k]["noul"], 2)
    for k in ("authority_risk", "sell_side_risk", "data_coverage"):
        if k in a:
            out[k] = a[k].get("choice")
    return out


def _replay(db) -> list:
    has = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
    return record.summary(db) if has else []


def state(path=None) -> dict:
    db = _ro(path or book.DB_PATH)
    now = time.time()
    if db is None:
        return {"now": now, "empty": True, "bank": PAPER.bank_usd, "stats": {}, "position": None,
                "cycles": [], "trades": [], "replay": []}
    try:
        realized = db.execute("SELECT COALESCE(SUM(pnl_usd),0) s FROM trades WHERE status='CLOSED'").fetchone()["s"]
        rows = db.execute("SELECT pnl_usd, pnl_pct FROM trades WHERE status='CLOSED'").fetchall()
        n = len(rows)
        h = db.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY id LIMIT 1").fetchone()
        cycles = []
        for c in db.execute("SELECT ts, outcome, stats FROM cycles ORDER BY id DESC LIMIT 15"):
            try:
                st = json.loads(c["stats"] or "{}")
            except ValueError:
                st = {}
            cycles.append({"ts": c["ts"], "outcome": c["outcome"], "seen": st.get("seen"), "benched": st.get("benched"),
                           "free": st.get("free", {}), "chain": st.get("chain", {}), "soft": st.get("soft", {}),
                           "dossiers": st.get("dossiers")})
        trades = [{"id": t["id"], "ticker": t["ticker"], "chain": t["chain"], "status": t["status"],
                   "opened_ts": t["opened_ts"], "exit_ts": t["exit_ts"], "exit_reason": t["exit_reason"],
                   "pnl_usd": t["pnl_usd"], "pnl_pct": t["pnl_pct"], "ticket_usd": t["ticket_usd"],
                   "model": t["model"], "confidence": t["confidence"], "answers": _answer_digest(t["why"])}
                  for t in db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 25")]
        return {"now": now, "empty": False, "bank": PAPER.bank_usd + realized,
                "stats": {"closed": n, "win_rate": (sum(1 for r in rows if r["pnl_usd"] > 0) / n) if n else 0,
                          "avg_pct": (sum(r["pnl_pct"] for r in rows) / n) if n else 0, "total_usd": realized},
                "position": ({"ticker": h["ticker"], "chain": h["chain"], "opened_ts": h["opened_ts"],
                              "entry_price": h["entry_price"], "ticket_usd": h["ticket_usd"], "addr": h["addr"]}
                             if h else None),
                "cycles": cycles, "trades": trades, "replay": _replay(db)}
    finally:
        db.close()


PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>jevdesk</title><style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--mut:#667085;--ok:#0a7d4f;--bad:#b42318;--line:#e4e7ec}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--card:#171b22;--fg:#e8eaee;--mut:#98a2b3;--ok:#4cc38a;--bad:#f97066;--line:#262c36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:900px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.mut{color:var(--mut);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:22px}
h2{font-size:15px;margin:20px 0 8px}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
td,th{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}th{color:var(--mut);font-weight:500}
.ok{color:var(--ok)}.bad{color:var(--bad)}.tbl{overflow-x:auto}
</style></head><body><main>
<h1>jevdesk <span class="mut">Paper-Desk, keine echten Trades</span></h1><div class="mut" id="upd"></div>
<div class="grid" id="kpi"></div><h2>Offene Position</h2><div id="pos" class="card"></div>
<h2>Letzte Zyklen (warum kam nichts durch?)</h2><div class="tbl"><table id="cyc"></table></div>
<h2>Trades</h2><div class="tbl"><table id="trd"></table></div>
<h2>Aufzeichnung: was wurde aus den Token? (Kurs nach 1h / 6h / 24h)</h2>
<div class="mut">Median der Kursveraenderung, Anteil &gt;+50 %, Anteil &lt;-50 %, weg = kein Kurs mehr. Zeigt, ob Filter Gewinner aussortieren.</div>
<div class="tbl"><table id="rep"></table></div>
</main><script>
const $=id=>document.getElementById(id);
function el(tag,txt,cls){const e=document.createElement(tag);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e}
function row(t,cells,head){const tr=el('tr');cells.forEach(c=>{const td=el(head?'th':'td',c.t??c,c.c);tr.appendChild(td)});t.appendChild(tr)}
const ago=s=>{const m=Math.round((Date.now()/1000-s)/60);return m<60?m+' min':Math.round(m/6)/10+' h'};
const usd=v=>(v>=0?'+':'')+v.toFixed(2)+' $';
const kv=o=>Object.entries(o||{}).map(([k,v])=>k+' '+v).join(', ')||'-';
async function load(){
 let s;try{s=await (await fetch('/api/state')).json()}catch(e){$('upd').textContent='Keine Verbindung zum Server';return}
 $('upd').textContent=s.empty?'Noch keine Daten. Starte: python -m jevdesk run':'Stand '+new Date().toLocaleTimeString()+' (aktualisiert alle 30 s)';
 const k=$('kpi');k.replaceChildren();const st=s.stats||{};
 [['Konto (Paper)',s.bank.toFixed(2)+' $'],['Geschlossen',st.closed??0],['Win-Rate',((st.win_rate||0)*100).toFixed(0)+' %'],
  ['Ø Ergebnis',(st.avg_pct||0).toFixed(1)+' %'],['Summe',usd(st.total_usd||0)]].forEach(([a,b])=>{const c=el('div',undefined,'card');c.appendChild(el('span',a,'mut'));c.appendChild(el('b',String(b)));k.appendChild(c)});
 const p=$('pos');p.replaceChildren();
 if(s.position){const x=s.position;p.appendChild(el('b',x.ticker+' ('+x.chain+')'));p.appendChild(el('div','seit '+ago(x.opened_ts)+' | Ticket '+x.ticket_usd.toFixed(2)+' $ | Entry '+x.entry_price,'mut'));p.appendChild(el('div',x.addr,'mut'))}
 else p.appendChild(el('span','Keine offene Position. Der Bot sucht weiter.','mut'));
 const c=$('cyc');c.replaceChildren();row(c,['vor','Ergebnis','gesehen','Bench','Gratis-Filter','Fakten','Judge'],true);
 s.cycles.forEach(z=>row(c,[ago(z.ts),z.outcome,z.seen??'-',z.benched??'-',kv(z.free),kv(z.chain),kv(z.soft)]));
 const t=$('trd');t.replaceChildren();row(t,['#','Token','Status','Ergebnis','Grund','Modell','Antworten'],true);
 s.trades.forEach(x=>row(t,['#'+x.id,x.ticker+' ('+x.chain+')',x.status,
   x.pnl_pct==null?'-':{t:x.pnl_pct.toFixed(1)+' % / '+usd(x.pnl_usd),c:x.pnl_usd>=0?'ok':'bad'},x.exit_reason||'-',x.model+(x.confidence?' '+x.confidence:''),kv(x.answers)]));

 const r=$('rep');r.replaceChildren();row(r,['Gruppe','n','1h','6h','24h'],true);
 const cell=x=>!x||!x.measured?(x&&x.gone?'weg '+x.gone:'-'):(x.median_pct>=0?'+':'')+x.median_pct+' % | '+Math.round(x.share_up50*100)+' % | '+Math.round(x.share_down50*100)+' % | weg '+x.gone;
 (s.replay||[]).forEach(g=>row(r,[g.group,g.n,cell(g['1h']),cell(g['6h']),cell(g['24h'])]));
}
load();setInterval(load,30000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, json.dumps(state(self.db_path)).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass


def start_background(port: int = 8765, db_path=None):
    """Startet die Seite im Hintergrund-Thread (fuer `jevdesk run`). Port belegt -> still ueberspringen."""
    import threading
    Handler.db_path = db_path
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"Oberflaeche nicht gestartet (Port {port} belegt?): {e}")
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Oberflaeche: http://127.0.0.1:{port}")
    return srv


def serve(port: int = 8765, db_path=None):
    Handler.db_path = db_path
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)   # nur localhost
    print(f"jevdesk Oberflaeche: http://127.0.0.1:{port}  (Strg+C beendet)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
