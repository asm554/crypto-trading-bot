# Der Onchain — Strategie-Dokumentation

Bot: `polybot.main_memecoin` · Implementierung: [`polybot/memecoin_strategy.py`](../polybot/memecoin_strategy.py) · Prefix in der Trade-Ledger: `CHAIN_` · Stand: Redesign vom 2026-07-20 (live seit 20.07. 16:42 UTC).

## 1. Grundprinzip

Handelt Solana-Memecoins über die öffentliche DexScreener-REST-API — kein Wallet, kein API-Key, keine echte Order, reines Paper-Trading. `paper_mode=False` ist hart gesperrt (`NotImplementedError`).

Anders als die Kraken-Bots gibt es hier **kein Orderbuch** mit Bid/Ask, nur den aktuellen Pool-Preis (`priceUsd`). Fills simulieren deshalb zwei getrennte, unabhängig voneinander abziehende Kosten statt eines echten Spreads:

| Kosten-Komponente | Parameter | Default | Bedeutung |
|---|---|---|---|
| AMM-Preisimpact/Slippage | `slippage_pct` | 1.5 % | Der eigene Trade bewegt den Pool-Preis |
| DEX-/Bonding-Curve-Gebühr | `dex_fee_pct` | 1.0 % | Mechanische Swap-Gebühr (pump.fun-artige Bonding-Curve vor Raydium-Migration) |

Kauf: `fill_price = quote_price × (1 + slippage_pct/100) × (1 + dex_fee_pct/100)`
Verkauf: `fill_price = quote_price × (1 − slippage_pct/100) × (1 − dex_fee_pct/100)`

→ Realistischer Roundtrip-Cut: **~5 %** (2 × 1.5 % Slippage + 2 × 1 % Fee), bevor überhaupt ein Gewinn beginnt.

## 2. Universum: kuratiert + dynamisch (getrennte Gates)

### 2.1 Kuratierter Kern
12 einzeln live-verifizierte Mint-Adressen (`SYMBOL_TO_MINT`): `BONK`, `WIF`, `POPCAT`, `PNUT`, `GOAT`, `MEW`, `FARTCOIN`, `GIGA`, `MOODENG`, `FWOG`, `PENGU`, `SLERF`.

Solana ist permissionless — eine Ticker-Suche kann einen Klon statt des Originals treffen (live beobachtet: ein Homoglyph-Klon von „ai16z" mit griechischen/kyrillischen Look-alike-Zeichen und höherer Fake-Liquidität als das Original). Deshalb **Adresse = Identität**, keine Namenssuche.

### 2.2 Dynamisches Universum (optional, `dynamic_enabled=True`)
Zusätzliche Kandidaten aus DexScreeners öffentlichen Boost-/Profile-Feeds (`discover_dynamic_solana_tokens()`, max. `max_dynamic_tokens=15`). Diese Feeds sind **bezahlte Promotion**, kein organisches Signal — ein Discovery-Fehler liefert `[]` und blockiert nie den kuratierten Kern.

### 2.3 Getrennte Gates (Kernänderung im Redesign)

Ein erster Live-Tag zeigte: mit einem gemeinsamen Gate-Set dominierten dynamische Boost-Tokens das Trading komplett (5 von 6 Trades), weil das Volumen-Gate zu hoch für den eigentlich vertrauten kuratierten Kern war. Jetzt getrennt:

| Gate | Kuratiert | Dynamisch |
|---|---|---|
| Mindest-Liquidität | `min_liquidity_usd` = 50.000 $ | `min_liquidity_dynamic_usd` = 100.000 $ |
| Mindest-24h-Volumen | `min_volume_usd` = 100.000 $ | `min_volume_dynamic_usd` = 500.000 $ |
| Mindest-Pool-Alter | — (kein Gate) | `min_pair_age_hours` = 24 h (Rug-Bait-Schutz für frische Launches) |
| Max. gleichzeitige Positionen | — (nur globales `max_open_positions`) | `max_dynamic_positions` = 2 |

## 3. Entry: Momentum-Band auf nativem `priceChange`

Bis zum Redesign pflegte der Bot eine eigene rollierende Preis-Historie zur Momentum-Berechnung — das führte zu verzögerter Erkennung (ein Trade wurde 6 Minuten nach Entry mit -16 % ausgestoppt, weil der Bot den Pump erst spät erkannte). Jetzt: **DexScreeners natives `priceChange`-Feld** direkt aus dem Pair-Objekt (Fenster m5/h1/h6/h24, keine eigene Kerzenbildung nötig).

Ein Kandidat wird gekauft, wenn **alle** folgenden Bedingungen erfüllt sind:

1. **Momentum-Band**: `entry_change_pct` (8 %) ≤ `priceChange.h1` ≤ `entry_max_change_pct` (35 %) — früh genug, um am Momentum zu partizipieren, aber nicht in einen bereits auslaufenden Pump kaufen.
2. **m5-Frische-Gate**: `priceChange.m5 > 0` — der Pump läuft im Moment der Prüfung noch, nicht schon am Abklingen.
3. **h6-Blowoff-Gate**: `priceChange.h6 < max_h6_change_pct` (100 %) — kein Kauf am Ende eines bereits gelaufenen Tages-Pumps.
4. **Liquiditäts-Gate** (kuratiert/dynamisch getrennt, siehe 2.3).
5. **Volumen-Gate** (kuratiert/dynamisch getrennt, siehe 2.3).
6. **Mindest-Aktivität**: `buys + sells ≥ min_h1_txns` (50) in der letzten Stunde — verhindert, dass das Kaufdruck-Signal mit einer Handvoll Mini-Transaktionen erfüllbar ist.
7. **Kaufdruck-Ratio**: `buys / sells ≥ min_buy_sell_ratio` (1.2).
8. **Pool-Mindestalter** (nur dynamische Kandidaten, siehe 2.3).
9. **Cooldown**: Adresse darf nicht aktuell im Cooldown sein (siehe Abschnitt 5).
10. **Keine offene Position** auf dieser Adresse.

### Ranking & Sizing
Kandidaten werden nach volumen-gewichtetem Score sortiert: `score = priceChange.h1 × log10(max(volume_h1, 1))` — frisches Stundenvolumen statt 24h-Volumen, damit gerade laufende Bewegung stärker zählt als alter Umsatz. Pro Zyklus werden die besten Kandidaten gekauft, bis `max_open_positions` (3) erreicht ist oder das Cash nicht mehr für `position_eur` (8 €) reicht — unter Beachtung des `max_dynamic_positions`-Limits.

## 4. Exit: Hybrid aus Floor + Trailing-Stop

Der ursprüngliche fixe Take-Profit bei +15 % kappte den rechten Tail (Gewinner) während Verluste bis -10 % laufen durften — eine strukturelle Asymmetrie (Breakeven-Winrate rechnerisch ~43 %). Jetzt: **Trailing-Modus statt sofortigem Verkauf**, nach demselben `peak_price`-Muster wie beim Momentum-Bot (`momentum_strategy.py`).

### Ablauf pro Positions-Check (Priorität von oben nach unten):

1. **Harter Stop-Loss** (immer aktiv, unabhängig vom Trailing-Status): `change_pct ≤ −stop_loss_pct` (−10 %) → sofortiger Exit, Grund `stop_loss`.
2. **Max-Haltedauer** (immer aktiv): `age ≥ max_hold_sec` (24 h) → Exit, Grund `time_exit`.
3. **Falls bereits im Trailing-Modus**: Exit sobald `price ≤ max(floor_price, trail_price)`, wobei
   - `floor_price = entry × (1 + trail_floor_pct/100)` (5 % über Einstand — Mindestgewinn-Sicherung)
   - `trail_price = peak_price × (1 − trailing_stop_pct/100)` (12 % unter dem bisherigen Hoch)
   → Exit, Grund `trailing_stop`.
4. **Sonst, falls noch nicht im Trailing-Modus**: `change_pct ≥ take_profit_pct` (15 %) schaltet in den Trailing-Modus **ohne zu verkaufen** — die Position darf weiterlaufen, `peak_price` wird ab jetzt jeden Zyklus als Maximum fortgeschrieben.

**Effekt**: Ein Gewinner, der über 15 % hinausläuft, wird nicht sofort kassiert, sondern kann weiter mitlaufen — der bereits erreichte Gewinn ist aber durch den Floor (mindestens ~5 % über Einstand, abzüglich Slippage+Fee) gegen einen Rückfall auf Null abgesichert.

## 5. Cooldown (Anti-Revenge-Trading)

Nach jedem Exit wird die Adresse für eine gewisse Zeit gesperrt — die Dauer hängt vom Exit-Grund ab:

| Exit-Grund | Cooldown | Parameter |
|---|---|---|
| `stop_loss` | 24 h | `cooldown_after_stop_sec` |
| `take_profit` / `trailing_stop` / `time_exit` | 4 h | `cooldown_sec` |

Der längere Cooldown nach Stop-Loss verhindert, dass der Bot denselben Coin am selben Tag mehrfach gegen sich laufen lässt (beobachtet: 3× derselbe Coin an einem Tag unter dem alten 4h-Cooldown für alle Exit-Gründe).

## 6. Mark-to-Market-Bewertung (`equity()`)

Offene Positionen werden zum aktuellen Pool-Preis **abzüglich** Verkaufs-Slippage und DEX-Gebühr bewertet (simuliert, was ein sofortiger Verkauf tatsächlich realisieren würde — kein optimistischer Fake-Gewinn). Ist kein Live-Preis verfügbar, wird konservativ zum Einstandswert bewertet statt einen künstlichen Drawdown/Gewinn zu erzeugen.

`battle_report.equity_for_memecoin()` bewertet für den Dashboard-/Battle-Vergleich identisch (`DEFAULT_SLIPPAGE_PCT` + `DEFAULT_DEX_FEE_PCT`), da diese Funktion außerhalb der Bot-Instanz läuft und die Defaults separat anwendet.

## 7. Alle Parameter im Überblick

| Parameter | Default | Env-Var |
|---|---|---|
| `initial_capital_eur` | 100 € | `CHAIN_BUDGET` |
| `interval_sec` | 300 s | `CHAIN_INTERVAL_SEC` |
| `entry_change_pct` | 8 % | `CHAIN_ENTRY_CHANGE_PCT` |
| `entry_max_change_pct` | 35 % | `CHAIN_ENTRY_MAX_CHANGE_PCT` |
| `max_h6_change_pct` | 100 % | `CHAIN_MAX_H6_CHANGE_PCT` |
| `min_liquidity_usd` (kuratiert) | 50.000 $ | `CHAIN_MIN_LIQUIDITY_USD` |
| `min_liquidity_dynamic_usd` | 100.000 $ | `CHAIN_MIN_LIQUIDITY_DYNAMIC_USD` |
| `min_volume_usd` (kuratiert) | 100.000 $ | `CHAIN_MIN_VOLUME_USD` |
| `min_volume_dynamic_usd` | 500.000 $ | `CHAIN_MIN_VOLUME_DYNAMIC_USD` |
| `min_buy_sell_ratio` | 1.2 | `CHAIN_MIN_BUY_SELL_RATIO` |
| `min_h1_txns` | 50 | `CHAIN_MIN_H1_TXNS` |
| `dynamic_enabled` | true | `CHAIN_DYNAMIC_ENABLED` |
| `max_dynamic_tokens` | 15 | `CHAIN_MAX_DYNAMIC_TOKENS` |
| `max_dynamic_positions` | 2 | `CHAIN_MAX_DYNAMIC_POSITIONS` |
| `min_pair_age_hours` | 24 h | `CHAIN_MIN_PAIR_AGE_H` |
| `position_eur` | 8 € | `CHAIN_POSITION_EUR` |
| `max_open_positions` | 3 | `CHAIN_MAX_OPEN_POSITIONS` |
| `take_profit_pct` | 15 % | `CHAIN_TAKE_PROFIT_PCT` |
| `trailing_stop_pct` | 12 % | `CHAIN_TRAILING_STOP_PCT` |
| `trail_floor_pct` | 5 % | `CHAIN_TRAIL_FLOOR_PCT` |
| `stop_loss_pct` | 10 % | `CHAIN_STOP_LOSS_PCT` |
| `max_hold_sec` | 24 h | `CHAIN_MAX_HOLD_H` |
| `cooldown_sec` | 4 h | `CHAIN_COOLDOWN_H` |
| `cooldown_after_stop_sec` | 24 h | `CHAIN_COOLDOWN_AFTER_STOP_H` |
| `slippage_pct` | 1.5 % | `CHAIN_SLIPPAGE_PCT` |
| `dex_fee_pct` | 1.0 % | `CHAIN_DEX_FEE_PCT` |
| `paper_mode` | true (hart erzwungen) | `CHAIN_PAPER_MODE` |

Konfiguriert über `polybot/main_memecoin.py` (Env-Vars) bzw. `systemd/polybot-memecoin.service.example` fürs Server-Deployment.

## 8. Datenfluss & Identität

- **Keying**: nach Mint-Adresse, nicht nach Ticker (`portfolio`/`cooldowns` sind `dict[address, ...]`).
- **`market_question`-Format**: `CHAIN_{symbol}@{address}` — Symbol fürs Dashboard, Adresse für die eindeutige Preis-Auflösung. Parsen immer mit `.partition("@")`, nie annehmen, dass kein `@` im Rest vorkommt.
- **State-Persistenz**: `polybot/data/memecoin_state.json` (lokal, git-ignoriert) — Cash, offene Positionen (inkl. `peak_price`/`trailing_active`), Cooldowns.
- **Cloud-Sync**: `polybot.main_cloud_sync` spiegelt die lokale SQLite-DB unabhängig nach Supabase; das Dashboard liest von dort. Kein direkter Zugriff des Dashboards auf den Bot.

## 9. Bekannte Grenzen

- Solana-Netzwerk-/Priority-Fees werden nicht modelliert (vernachlässigbar klein, aber nicht null).
- `dex_fee_pct=1.0%` ist ein pauschaler Default — reale Gebühren variieren je nach Plattform (pump.fun-Bonding-Curve vs. migrierter Raydium-Pool ~0.25 %).
- Trailing-Exit prüft nur beim jeweiligen Scan-Intervall (nicht kontinuierlich) — ein Preis kann zwischen zwei Checks unter den Floor gappen, bevor der Bot reagiert (Gap-Risiko wie bei jedem nicht-kontinuierlich überwachten Stop).
