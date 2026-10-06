# Laden & Zehren: wenige lange Brennerläufe – Design

Status: Design abgestimmt (Abschnitte 1–4 einzeln freigegeben) · Datum: 2026-10-06
Baut auf v0.4.9 auf. Stufe 1 dieses Dokuments wird umgesetzt; Stufe 2 (Lade-Hebel) folgt nach einem
Test an der Referenzanlage.

## 1. Ziel und Ausgangslage

**Ziel:** Der überdimensionierte Gaskessel (Modulationsuntergrenze ≈ 7 kW ≫ Bedarf in der Übergangszeit)
startet möglichst **1–2-mal pro Tag** und läuft dann lange am Stück; mehr Starts nur, wenn es sich nicht
vermeiden lässt. Die führenden Zonen bleiben dabei über ihrer Untergrenze.

**Beobachtungen an der Referenzanlage, die das Design prägen:**

- Taktsperre am Kessel ist bereits am Maximum (45 min). Kürzere Brennerläufe als eine Minute treten auf –
  Ursache ist zu wenig Durchfluss/Wärmeabnahme beim Start (Rücklaufbegrenzer der FBH, geschlossene TRVs,
  kleine Pumpenstufe), nicht nur zu geringer Wärmebedarf.
- Die bisherige Freigabe über die Sommerschwelle wirkt nur, wenn der Sommer/Winter-Modus des RC310 auf
  „Auto“ steht; bei warmer Außenluft bringt eine Freigabe keine Wärme. Im Winterbetrieb läuft die
  Heizungspumpe auch ohne Brenner periodisch.
- Thermocast hat bisher nur eine **Untergrenze** je Zone; Laden auf Vorrat braucht eine **Obergrenze**.
- Heizkörperräume werden von Better Thermostat (BT) geregelt. Weichen BT-Sollwert und Thermocast-Komfort
  voneinander ab, wartet der Planer vergeblich auf Wärme.

**Nicht-Ziele (Stufe 1):** kein Modell der abnehmbaren Leistung über dem Vorlauf, keine kontinuierliche
Vorlaufregelung (MPC), keine Warmwasser-Steuerung, keine direkte TRV-Ansteuerung (nur über BT).

## 2. Entscheidungen

| Thema | Entscheidung | Begründung |
|---|---|---|
| Ansatz | **A „Laden & Zehren“** in zwei Stufen: neuer Planer + gekoppelte Aktoren; Lade-Hebel später | B (nur Aktoren) lädt nicht auf Vorrat; C (MPC) braucht zu viele EEPROM-Schreibvorgänge und Daten, die fehlen |
| Kesselfreigabe | Empfehlung: `select …_summersetmode` **Winter** = erlaubt, **Auto** (Schwelle fest 10 °C) = gesperrt; Code generisch wie bisher | Block heizt sicher auch an milden Tagen; Pumpe steht zwischen Blöcken; bei HA-Ausfall heizt das RC310 spätestens unter 10 °C |
| Sperre „Sommer“ | später, nur mit Totmannschalter außerhalb von HA (z. B. EMS-ESP-Scheduler) | sonst keine Heizung bei HA-Ausfall |
| Zonen-Steuerung | Thermocast setzt **BT-Sollwerte**, **pro Zone einschaltbar** | Wärme dorthin lenken, wo geladen werden soll; BT bleibt Feinregler |
| Obergrenze | **pro Zone einstellbar**, Standard Komfort + 1 K | Speicher für lange Pausen; Räume unterschiedlich empfindlich |
| Starts vs. Verbrauch | **Regler 0–100** in den Optionen, Standard **80** (Starts zuerst) | Abwägung ist hausabhängig; KPI-Tab zeigt die Wirkung |

## 3. Planer (Abschnitt 1)

Bleibt: Brute-Force-Suche Start × Länge, stündliche Neuplanung im Rollout, Prognose mit σ, injizierbare
Kostenfunktion.

Neu:

1. **Band je Zone und Stunde:** `comfort_low` (in der Komfortzeit Komfort − Band, sonst **Grundwert**,
   Standard Komfort − 2 K) und `comfort_high` (Obergrenze, gilt immer).
2. **Gedeckelte Heizwirkung:** Für Zonen mit BT-Steuerung nimmt die Prognose an, dass der TRV im Block offen
   ist, bis die Zone ihre Obergrenze erreicht, danach geschlossen (Stunden, deren Prognose die Obergrenze
   erreicht, bekommen `q = 0`; iterativ, max. 3 Durchläufe). In Ruhezeiten (Abschnitt 5) lädt die Zone nicht.
   Zonen ohne BT-Steuerung: gelernte typische Heizwirkung (`q_on`) wie bisher. FBH ohne Stellglied: ungedeckelt.
3. **Blocklängen** 2, 3, 4, 6, 8, 10, 12 h (nicht kürzer als der Mindestblock); Horizont je Planung 24 h.
4. **Kostenfunktion** mit Regler `s ∈ [0, 100]` (`w = s/100`):

   | Teil | Formel |
   |---|---|
   | Unterschreitung (führende Zonen) | 20 · K·h unter `comfort_low` (untere Prognosegrenze `mean − z·σ`) |
   | Überschreitung (alle Zonen) | 4 · K·h über `comfort_high` (Mittelwert) |
   | Start | 1 + 9·w |
   | Blockstunden (Gas) | (0,5 − 0,35·w) je Stunde |
   | Startverzögerung | 0,01 je Stunde |

   Die bisherige `default_cost` entspricht ungefähr `s = 0` ohne Überschreitungsterm und bleibt als
   Funktion erhalten (Tests, Wärmepumpe später).
5. Zwei Blöcke in einer Suche: **nein** – die stündliche Neuplanung erzeugt Folgeblöcke.

## 4. Aktoren (Abschnitt 2)

### 4.1 Kesselfreigabe

Unverändert (Mindestblock/-pause, Wechselbudget, EEPROM-Schutz: Bestätigung, Backoff, 40 Schreibvorgänge/Tag,
Fail-safe ohne Blockpflicht). Doku/Checkliste empfehlen Winter/Auto + Schwelle 10 °C.

### 4.2 BT-Sollwerte (`zone_actuator.py`, neu)

- Zonenfelder: `bt_control` (bool, Standard aus), `bt_entity` (`climate` von BT), `comfort_high`
  (Obergrenze), `base_temp` (Grundwert), `quiet_from`/`quiet_to` (Ruhezeit, optional).
- Sollwert: **im Block** = Obergrenze; **außerhalb** = aktuelle Untergrenze (`comfort_low` der Stunde).
- Schreiben nur bei Änderung, gerundet auf 0,5 K; harte Grenzen 15–24 °C; max. 24 Änderungen je Zone und Tag.
- **Manuelle Übersteuerung:** Weicht der BT-Sollwert vom zuletzt geschriebenen ab (nicht von Thermocast),
  ruht die Zone bis zum Ende des nächsten Blocks, mindestens 3 h. Ereignis + Anzeige im Panel.
- Bestehende BT-Zeitpläne/Automationen für gesteuerte Zonen müssen deaktiviert werden (Doku).

### 4.3 Lade-Hebel (Stufe 2, nur Gerüst in Stufe 1 vorbereitet)

Hausoption „Lade-Entität“ (`number`) mit Wert „im Block“/„normal“ (Kandidaten: temporäre Raumsolltemperatur
im Automatikbetrieb, Parallelverschiebung der Kurve, Pumpen-Mindestmodulation). 2 Schreibvorgänge je Block,
gleiches EEPROM-Tagesbudget wie die Freigabe. Auswahl nach einem Test an der Referenzanlage
(Vorlauf-Soll vs. Wert) und Nachtdaten (Brenner, Vorlauf, Soll-Vorlauf, Pumpenmodulation).

### 4.4 Beobachtungsmodus

Alle Aktoren berechnen und zeigen ihre Sollzustände, schreiben aber nicht. BT-Steuerung zusätzlich pro Zone
schaltbar.

## 5. Sicherheit und Ruhezeiten (Abschnitt 3)

- **Ruhezeit je Zone:** 15 min vor Beginn einmal Sollwert = Grundwert; während der Ruhezeit keine
  Schreibvorgänge, auch nicht bei Blockbeginn. Ausnahme: führende Zone fällt unter Grundwert − 1 K.
- **Fail-safe:**

  | Situation | Freigabe | BT-Sollwert |
  |---|---|---|
  | Update-Fehler, Prognose zu alt, Sensor fehlt | sofort an | aktuelle Untergrenze |
  | Steuerung aus / Integration entladen | an | einmal aktuelle Untergrenze, danach nichts |
  | Zonentemperatur unbekannt | – | Zone nicht anfassen |
  | Ruhezeit | – | Regeln der Ruhezeit gelten auch im Fail-safe |

- Grenze außerhalb von Thermocast: Fällt HA aus, steht auch BT; TRVs behalten den letzten BT-Sollwert.

## 6. Panel, Migration, Tests (Abschnitt 4)

**Panel „Jetzt & Plan“** (View-Vertrag bleibt v1, nur additive Felder):
Obergrenze als Linie + eingefärbtes Band; BT-Sollwert als Treppenlinie (Ist/Plan); Ruhezeiten schraffiert;
neue Begründungen (Laden bis X °C / danach N h Pause, manuell übersteuert bis …, Ruhezeit lädt nicht);
Tageskarten mit Starts (gemessen/geplant) und „Max. Zone“; Tooltip mit BT-Sollwerten.

**Migration:** bestehende Zonen ohne Änderung lauffähig (Obergrenze = Komfort + 1 K, Grundwert = Komfort − 2 K,
`bt_control` aus, keine Ruhezeit); Regler in den Optionen Standard 80; Modelle werden nicht zurückgesetzt.
Validierung: Grundwert ≤ Komfort − Band < Komfort < Obergrenze.

**Tests:**

- Kern/Planer (synthetisches Haus): Deckelung an der Obergrenze; Regler 80 vs. 0 → weniger, längere Blöcke;
  Ziel bei 8 °C Außentemperatur ≤ 2 Blöcke/Tag ohne Unterschreitung; keine Ladung in Ruhezeiten;
  Rollout-Laufzeit mit Längen bis 12 h < 2 s (CI < 5 s).
- BT-Aktor (HA): Schreiben nur bei Änderung/0,5 K; Übersteuerung erkannt und respektiert; Ruhezeit inkl.
  15-min-Vorlauf und Unterkühlungs-Ausnahme; Fail-safe, Entladen, Beobachtungsmodus; Grenzen 15–24 °C;
  Tageslimit.
- Config Flow: neue Zonenfelder, Validierung, Migration.
- Panel: Beispieldaten + `test_view`, Sichtprüfung über `frontend/dev/`.

**Reihenfolge Stufe 1:** Planer → Zonenfelder/Migration → BT-Aktor → Panel → Doku/Checkliste.

## 7. Erfolgskriterien

- Im KPI-Tab (vorher/nachher ab Aktivierung der Steuerung): Brennerstarts/Tag deutlich gesenkt, Ziel 1–2
  in der Übergangszeit, ohne mehr Unterschreitungsstunden der führenden Zonen.
- Keine Stellgeräusche in Ruhezeiten (keine BT-Schreibvorgänge im Ereignislog).
- Gasverbrauch je Heizgradtag nicht mehr als ~5 % über dem Vorher-Wert bei Regler 80.
