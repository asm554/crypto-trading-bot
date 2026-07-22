# Analyse: Krypto-Futures für das Bot Battle Center

Stand: 22. Juli 2026. Diese Analyse beschreibt die technische Umsetzung und ist keine Anlageberatung. Der implementierte Bot sendet keine echten Orders.

## Ergebnis

Für dieses Projekt sind lineare Kraken Multi-Collateral Perpetual Futures der sinnvollste erste Markt. Kraken ist bereits die zentrale Kursquelle der Spot-Bots, die öffentlichen Futures-Endpunkte benötigen im Paper-Modus keinen Schlüssel und liefern genau die Daten, die eine belastbare Simulation braucht: Mark Price, Index, Bid/Ask, Volumen, Open Interest und Funding.

Der neue Bot **Der Hebler** handelt deshalb ausschließlich:

- `PF_XBTUSD` — BTC/USD Perpetual
- `PF_ETHUSD` — ETH/USD Perpetual
- `PF_SOLUSD` — SOL/USD Perpetual

Die drei Märkte sind gegenüber kleinen Altcoin-Perpetuals liquider und weisen typischerweise engere Spreads auf. Der Bot prüft zusätzlich mindestens 10 Mio. USD 24h-Quote-Volumen und höchstens 0,10 % Spread. Diese Filter sind wichtiger als eine möglichst große Coin-Liste: Bei Futures wirken Spread, Slippage und Funding auf den gesamten Nominalwert.

## Handelbarkeit in Deutschland/EWR

Kraken beschreibt Deutschland nicht als ausgeschlossene Region. Für EWR-Kunden sind jedoch vollständige Verifizierung, Steuer-ID, Kundeneinstufung und ein Eignungs-/Kenntnistest erforderlich. Kraken nennt für Privatkunden bis zu 10× Leverage; die tatsächliche Freischaltung hängt immer vom individuellen Konto und den aktuellen Produktbedingungen ab.

Quellen:

- [Kraken Derivatives: EWR-Berechtigung](https://support.kraken.com/articles/derivatives-eligibility-requirements-eea)
- [Kraken: Kundeneinstufung im EWR](https://support.kraken.com/de/articles/how-does-classification-work)
- [EWR-Kontraktspezifikationen](https://support.kraken.com/articles/perpetual-contract-specifications-for-clients-in-the-eea)
- [EWR-Marginplan](https://support.kraken.com/articles/derivatives-margin-schedule-and-maximum-leverage-eea)

## Warum Perpetuals und keine Futures mit Verfall

Perpetuals besitzen kein festes Verfallsdatum. Damit entfallen Roll-Termine und unterschiedliche Monatskontrakte, was die Battle-Auswertung wesentlich sauberer macht. Der Preis wird über Funding an den Spot-Index gekoppelt. Kraken berechnet Funding kontinuierlich und realisiert es stündlich. Positive Funding-Raten bedeuten in der üblichen Darstellung, dass Long-Positionen zahlen und Short-Positionen erhalten; bei negativen Raten ist es umgekehrt.

Die Simulation nutzt die absolute Funding-Rate aus dem öffentlichen Ticker pro Basiseinheit und Stunde. Nach längeren Ausfällen wird höchstens eine Stunde mit dem aktuellen Satz nachberechnet, weil ein heutiger Satz nicht seriös auf eine mehrstündige Vergangenheit übertragen werden kann.

Quellen:

- [Kraken Linear Multi-Collateral Perpetual Specifications](https://support.kraken.com/articles/4844359082772-linear-multi-collateral-derivatives-contract-specifications)
- [Kraken Futures Ticker API](https://docs-legacy.kraken.com/api/docs/futures-api/trading/get-tickers/)
- [Kraken Futures Candle API](https://docs.kraken.com/api/docs/futures-api/charts/candles)

## Kostenmodell

Der Bot simuliert ausschließlich Taker-Ausführungen:

- 0,05 % des Nominalwerts beim Öffnen
- 0,05 % des Nominalwerts beim Schließen
- echten Futures-Spread über Bid/Ask
- kontinuierliches Funding
- bei simulierter Voll-Liquidation zusätzlich 50 % der Maintenance Margin

Kraken berechnet Derivategebühren auf den Nominalwert, nicht nur auf die hinterlegte Margin. Bei 20 € Margin und 2× Hebel beträgt der Nominalwert 40 €; eine 0,05-%-Gebühr sind damit 0,02 € je Seite. Das wirkt klein, wird bei häufigem Handel aber relevant.

Quelle: [Kraken Derivatives Fee Schedule](https://support.kraken.com/articles/360048917612-fee-schedule)

## Strategie und Risiko

Der Hebler ist ein symmetrischer Trendfolger:

1. Er verwendet abgeschlossene 1h-Mark-Price-Kerzen, nicht den leichter beweglichen letzten Trade.
2. EMA 9 muss oberhalb/unterhalb EMA 21 liegen.
3. Das 6h-Momentum muss mindestens +0,8 % für Long oder −0,8 % für Short betragen.
4. Eine Position wird nur eröffnet, wenn Spread, Volumen und Funding-Filter bestehen.
5. Pro Position werden 20 € isolierte Paper-Margin bei 2× Hebel reserviert; maximal zwei Positionen laufen gleichzeitig.

Risikogrenzen:

- harter Stop bei 2 % ungünstiger Kursbewegung
- Trailing-Sicherung ab 2 % günstiger Kursbewegung
- festes Gewinnziel bei 5 % Kursbewegung
- maximal 24 Stunden Haltedauer
- vier Stunden Cooldown je Markt nach einem Exit
- keine neuen Einstiege ab 5 % Verlust der Netto-Equity
- Konstruktor begrenzt den Paper-Hebel hart auf maximal 3×
- Liquidationsprüfung auf Basis von Mark Price, isolierter Equity und 5 % Maintenance Margin

Der Stop liegt bei 2× Hebel weit vor der rechnerischen Liquidation. Trotzdem bleibt die Liquidationslogik nötig, weil Kurslücken, API-Ausfälle oder extremes Funding einen Stop überspringen können.

## Battle-Center-Bewertung

Futures dürfen nicht wie Spot bewertet werden. Die Netto-Equity lautet:

```text
freie EUR-Cash
+ reservierte isolierte Margin
+ Long/Short-PnL zum Mark Price
+ aufgelaufenes Funding
- geschätzte Taker-Gebühr zum sofortigen Schließen
```

Die Einstiegsgebühr wird beim Öffnen direkt vom Cash abgezogen. Beim Schließen speichert `real_pnl` Preis-PnL plus Funding minus Einstiegs-, Ausstiegs- und gegebenenfalls Liquidationsgebühr. Trades verwenden den Prefix `FUT_`; Cloud-Sync und Supabase brauchen dadurch keine Schemaänderung.

## Plug-and-play

Lokal:

```bash
python -m polybot.main_futures
python -m polybot.battle_report
```

Server:

```bash
sudo cp systemd/polybot-futures.service.example /etc/systemd/system/polybot-futures.service
sudo systemctl daemon-reload
sudo systemctl enable --now polybot-futures.service
```

Der Cloud-Sync übernimmt `FUT_`-Trades und `futures`-Equity-Snapshots automatisch. Das Dashboard kennt Bot, Farbe, Equity-Linie, Long/Short-Anzeige und Strategieparameter.

## Was für echten Handel noch fehlen würde

Live-Trading ist absichtlich nicht enthalten. Vor einer separaten Freigabe wären mindestens Kraken-Demo/Testnet, authentifizierte Order- und Positionsabgleiche, Reduce-only Stops auf Börsenseite, Idempotenz, Reconnect-/Partial-Fill-Logik, Subaccount-Isolation, API-Key ohne Withdrawal-Recht, Alarmierung und ein mehrwöchiger Shadow-Vergleich zwischen simulierten und echten Fills erforderlich. Außerdem müssten aktuelle Vertragsbedingungen, persönliche Eignung und steuerliche Behandlung separat geprüft werden.
