# Trading-Bots Dashboard

Ein Überwachungs-Dashboard für die drei Paper-Trading-Bots (DCA / Momentum / Mean-Reversion).
Es liest **direkt** die echten Bot-Daten aus dem übergeordneten Projekt:

- Datenbank: `../polybot/data/paper_trades.db`
- Zustandsdateien: `../polybot/data/*_state.json`
- Gebühren: `../polybot/config.json`

Das Dashboard zeigt Daten nur an und ändert nichts an den Bots.

## Starten

```bash
cd dashboard
npm install        # nur beim ersten Mal
npm run dev        # Entwicklungs-Modus, http://localhost:3000
```

Für den Dauerbetrieb:

```bash
npm run build
npm run start      # http://localhost:3000
```

## Seiten

- **Übersicht** (`/`): Gesamtwert, die drei Bots mit Wert/Gewinn/Verlust, Wert-Verlauf als
  Diagramm und die letzten Trades. Aktualisiert sich automatisch alle 10 Sekunden.
- **Einstellungen** (`/settings`): Gebühren und die Regeln jedes Bots zum Ansehen.

## Hinweis zu leeren Werten

Solange die Bots noch nicht gelaufen sind und keine Trades erzeugt haben, zeigt das Dashboard
je Bot 100&nbsp;€ Startkapital und „noch keine Aktivität". Sobald die Bots handeln, füllen sich
Tabelle und Diagramm von selbst.
