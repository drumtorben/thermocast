<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/thermocast/brand/dark_logo@2x.png">
    <img alt="Thermocast" src="custom_components/thermocast/brand/logo@2x.png" width="420">
  </picture>
</p>

# Thermocast

Vorausschauende Heizfreigabe für Home Assistant – mit **online lernenden Raummodellen**,
**Einstrahlung pro Fenster-/Dachfläche** (Open-Meteo) und einem **Blockplaner**, der
überdimensionierte Kessel lange und selten statt kurz und oft laufen lässt.
Ausgelegt für Fußbodenheizung (träger Estrich) + Heizkörper, später Wärmepumpe.

> Status: v0.4 – getestet mit synthetischen Daten und einer lokalen HA-Instanz (120 Tests); noch nicht im
> Dauerbetrieb an einer echten Heizung. Feedback und Issues willkommen.
> **Startet im Beobachtungsmodus** und schaltet nichts, bis du `Steuerung aktiv` einschaltest.

## Wie es funktioniert

```
alle 15 min:  Sensoren lesen ──► Stunde abgeschlossen? ──► RLS-Update pro Zone (online, kein Neutraining)
              Open-Meteo (stündlich, je Flächenausrichtung) ──► 24-h-Simulation ohne Heizung
              Planer: kein Block | Start 0..23 h × Länge 2..8 h ──► Kosten (Komfort, Starts, Energie)
              Aktor: Mindestblock, Mindestpause, Tagesbudget, Fail-safe ──► Freigabe-Entität
```

**Modell pro Zone** (stündlich, linear in den Parametern):

```
ΔT = b0 + a·(T_außen − T) + Σ b_s,l · I_s[t−l] + Σ c_k · Q[t−k] + Σ d_n · (T_n − T) + Σ e_g · G_g
```

| Term | Bedeutung | Verzögerungen |
|---|---|---|
| `I_s` Fenster | Einstrahlung auf die Fensterfläche | 0–1 h |
| `I_s` Dach | Einstrahlung auf die Dachfläche (wirkt durch Dämmung verzögert) | 1–6 h |
| `Q` FBH | Heiz-Proxy (Vorlauf − Raum, wenn Heizung aktiv) | 0–6 h (Estrich) |
| `Q` Heizkörper | Proxy × Ventilöffnung | 0–1 h |
| `T_n` | Nachbarräume | – |
| `G_g` | innere Gewinne (W-Sensoren, Anwesenheit) | – |

**Online-Lernen:** Recursive Least Squares mit Vergessensfaktor (Standard 0,996/h ≈ 10 Tage
Gedächtnis), Vorzeichen-Projektion (Sonne/Heizen/Verluste ≥ 0), Huber-Clipping gegen Ausreißer,
Kovarianz-Deckel gegen „Wind-up“ im Sommer. Stunden mit offenem Fenster oder Datenlücken werden
nicht gelernt. Der Planer rechnet mit `Mittelwert − z·σ` → unsicheres Modell heizt eher.

**Warum kein Reinforcement Learning?** Ein Haus liefert ein paar hundert Entscheidungen pro
Saison; RL bräuchte Größenordnungen mehr und müsste dafür „ausprobieren“ (= frieren).
Modellbasiert + online identifiziert ist die dateneffiziente, sichere Variante.

## Installation

**HACS (empfohlen):** HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/drumtorben/thermocast`,
Typ *Integration* → „Thermocast“ herunterladen → HA neu starten.
**Manuell:** `custom_components/thermocast` nach `config/custom_components/` kopieren, HA neu starten.
Voraussetzung: Home Assistant ≥ 2026.9. Schritt für Schritt: [`docs/INSTALLATION-CHECKLISTE.md`](docs/INSTALLATION-CHECKLISTE.md).

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Thermocast*
2. Haus: Außentemperatur, Vorlauf, „Heizungspumpe läuft“ (empfohlen!), Freigabe-Entität.
3. Zonen über **„Zone hinzufügen“** am Integrationseintrag anlegen.

### Freigabe-Entität – Empfehlung für Buderus RC310 via EMS-ESP

| Variante | Entität | Wert „erlaubt“ | Wert „gesperrt“ | Verhalten bei HA-Ausfall |
|---|---|---|---|---|
| **A (empfohlen)** | Sommer-/Winter-Schwelle (`number`) | `16` | `10` | Heizung springt bei Kälte (< 10 °C gedämpft) selbst wieder an |
| B | Sommerbetrieb (`select`) | `winter`/`auto` | `summer` | bleibt gesperrt, bis HA wieder läuft |

Variante A „degradiert sanft“: Fällt HA aus, während gesperrt ist, heizt das RC310 trotzdem,
sobald es draußen kalt wird. Schreibzugriffe sind begrenzt (Standard ≤ 12/Tag), identische
Werte werden nicht erneut geschrieben (EEPROM).

### Beispiel-Zonen (Flächen als YAML im Feld „Sonnenbeschienene Flächen“)

Schlafzimmer – Fenster Ost, Dachschräge Süd:
```yaml
- {kind: window, azimuth: 90, tilt: 90, name: Fenster Ost}
- {kind: roof, azimuth: 180, tilt: 40, name: Schräge Süd}
```
Kinderzimmer – Fenster Ost, Dachschräge Nord:
```yaml
- {kind: window, azimuth: 90, tilt: 90}
- {kind: roof, azimuth: 0, tilt: 40}
```
Azimut: 0 = N, 90 = O, 180 = S, 270 = W. Neigung: 90 = senkrecht. Die Größe muss nicht angegeben
werden – die wirksame Fläche × g-Wert × Verschattung lernt das Modell.

EG mit Fußbodenheizung ohne Stellantriebe: **eine Zone** (`fbh`) mit den Sensoren Wohnzimmer +
Küche, `Führt die Freigabe` = an.

## Entitäten

| Entität | Bedeutung |
|---|---|
| `switch.…_steuerung_aktiv` | aus = Beobachtungsmodus (Standard), Freigabe bleibt „erlaubt“ |
| `binary_sensor.…_heizfreigabe` | Entscheidung des Planers (auch im Beobachtungsmodus) |
| `sensor.…_nachster_heizblock` | Start (Attribut: Ende, Prognose-Alter, Fail-safe-Grund) |
| `sensor.<zone>_prognose_minimum` | tiefste erwartete Temperatur (untere Grenze) ohne Heizen, Attribut `forecast` = Verlauf |
| `binary_sensor.<zone>_heizbedarf` | Zone fällt ohne Heizen unter ihr Komfortband |
| `sensor.<zone>_modellfehler` | laufender MAE (K/h), Attribute: Parameter, Sonnenantwort je Fläche |

## Panel „Thermocast“

Die Integration bringt ein eigenes Seitenpanel mit (Sidebar → *Thermocast*), das beantwortet:
**Warum heizt Thermocast gerade (nicht) – und was hat es bis morgen Abend vor?**

- **Story:** Planerwunsch bzw. Freigabe, nächster Block, Begründung in einem Satz, Robustheit
  („knapp“, „nur wegen der Sicherheitsmarge σ“), Planänderung seit der letzten Stunde (mit vermuteter Ursache),
  Hinweis, wenn eine Aktor-Regel (Mindestblock/-pause, Budget) den Planer überstimmt.
- **Gestern · Heute · Morgen:** Heizstunden, Blöcke, Minimum der führenden Zonen, Freigabe vs. Planer, Unsicherheit.
- **Zeitachse** (fest gestern 00:00 → morgen 24:00): Wetter (gemessen/Prognose, Sonne je Fläche),
  gelaufene und geplante Blöcke, Ereignisse (Fenster offen, Fail-safe, Prognosefehler, Steuerung an/aus …),
  je Zone gemessen / Plan ± σ / ohne Heizen / Komfortfenster. **„Warum?“** zerlegt jede künftige Stunde
  exakt in Sonne je Fläche, Heizen, Verlust, Nachbarn, Gewinne (das Modell ist linear). Fadenkreuz-Tooltip über alle Bahnen.
- **Kandidaten** für den nächsten Block mit Kostenaufschlüsselung – Hover zeichnet den Verlauf in die Zeitachse –
  und **Aktor-Regeln**.

Tab **„Modell“** (je Zone): Wie gut beschreibt das Modell die Zone?
- **Modellfehler (Hindcast):** das aktuelle Modell simuliert die letzten 7 Tage mit den *gemessenen* Eingängen
  (Neustart jede Mitternacht) – reiner Modellfehler, unabhängig von Wetterprognose und Plan.
- **Prognosegüte je Horizont** (1/3/6/12/24 h): MAE, Bias und Kalibrierung (Anteil der Messungen innerhalb ±σ;
  ≈ 68 % wäre richtig kalibriert – deutlich weniger heißt: die Sicherheitsmarge ist zu knapp).
- **Gelernte Parameter** physikalisch gelesen: Zeitkonstante τ (h), Sonne je Fläche (K/h je kW/m²),
  Heizen (K/h im typischen Block), Nachbarkopplung, Gewinne, Grunddrift – mit ±1σ und 30-Tage-Verlauf;
  Estrich-Verzögerungsprofil. **JSON-Export** aller Modellzustände und Logs (z. B. für polars).

Tab **„KPIs“** (14/30/90 Tage, aus den Recorder-Langzeitstatistiken): Brennerstarts/Tag, kWh pro Heizgradtag
(Heizgrenze 15 °C), Minimum der führenden Zonen in Komfortzeit, Unterschreitungsstunden – jeweils
**vor** und **seit** dem ersten Einschalten der Steuerung. Dafür in den Optionen der Integration
**Brennerstarts** und **Heizenergie** (beide `total_increasing`, z. B. von EMS-ESP) eintragen; optional
**Warmwasser aktiv** für die Schraffur in der Zeitachse.

Der Plan bis morgen entsteht durch einen **Rollout des echten Reglers**: stündlich neu planen, Mindestblock,
Mindestpause und Tagesbudget anwenden, Zustand fortschreiben. Im Beobachtungsmodus führt ein Schatten-Aktor
die Regeln virtuell mit. Die Vergangenheit kommt aus dem Recorder; ohne Recorder bleibt sie leer.
Das Panel ist reine Ansicht und läuft getrennt vom Regelpfad – ein Fehler dort ändert nie die Freigabe.

### ApexCharts-Karte (Prognose vs. Komfort)

```yaml
type: custom:apexcharts-card
graph_span: 24h
span: {start: hour}
header: {show: true, title: Schlafzimmer – Prognose ohne Heizen}
series:
  - entity: sensor.schlafzimmer_prognose_minimum
    name: Erwartet
    data_generator: |
      return entity.attributes.forecast.map(p => [new Date(p.time).getTime(), p.mean]);
  - entity: sensor.schlafzimmer_prognose_minimum
    name: Untere Grenze
    data_generator: |
      return entity.attributes.forecast.map(p => [new Date(p.time).getTime(), p.lower]);
  - entity: sensor.schlafzimmer_prognose_minimum
    name: Komfort
    data_generator: |
      return entity.attributes.forecast.map(p => [new Date(p.time).getTime(), p.comfort_low]);
```

## Sicherheit

* Beobachtungsmodus ist Standard.
* Einschalten der Heizung ist immer erlaubt; Ausschalten nur nach Mindestblock und im Tagesbudget.
* Fehler im Update, Prognose älter als 2 h, fehlender Sensor einer führenden Zone → Freigabe **an**.
  Dauert ein Fail-safe länger als 1 h, erscheint ein Reparaturhinweis.
* Entladen/Entfernen der Integration oder Ausschalten der Steuerung → Freigabe **an**.
* **EEPROM-Schutz:** Jeder Schreibvorgang muss von der Entität bestätigt werden. Unbestätigt → neuer Versuch
  erst nach 10 min, dann mit wachsender Pause (30 min … 6 h), Ereignis + Reparaturhinweis. Nie mehr als
  **40 Schreibvorgänge pro Tag**; bei nicht verfügbarer Entität wird gar nicht geschrieben.

## Lernen, Kalibrierung, Diagnose

* **Warmstart:** Neue Zonen lernen beim Einrichten aus den letzten **30 Tagen** Recorder-Langzeitstatistik
  (+ Einstrahlung aus Open-Meteo-Vergangenheitsdaten) statt mit Standardwerten zu starten. Der Button
  *„Modelle aus Historie neu lernen“* wiederholt das für alle Zonen.
* **σ-Kalibrierung** (Option, Standard an): Aus den beobachteten Prognosefehlern der letzten 14 Tage wird je
  Horizont ein Faktor gelernt, um den der Planer die Unsicherheit weitet (nie verengt). Sichtbar im Modell-Tab („σ ×“).
* **Komfort-Zeitplan:** Je Zone optional ein Zeitplan-Helfer (`schedule.*`), z. B. Büro werktags 8–17 Uhr.
* **Diagnose herunterladen** (⋮ an der Integration): alle Modelle, Logs, Ereignisse, Aktorzustand.
* Installation in einer echten HA: siehe [`docs/INSTALLATION-CHECKLISTE.md`](docs/INSTALLATION-CHECKLISTE.md).

## Entwicklung

```
custom_components/thermocast/
├── core/            reines Python + numpy (Notebook- & Test-tauglich, kein HA-Import)
│   ├── model.py     OnlineZoneModel (RLS), Ursachen-Zerlegung
│   ├── forecast.py  Open-Meteo, Einstrahlung je Ausrichtung
│   ├── planner.py   Blockplaner, austauschbare Kostenfunktion (Komponenten)
│   ├── rules.py     Aktor-Regeln (rein)
│   ├── rollout.py   Regler-Rollout bis morgen 24:00
│   ├── explain.py   Begründungs-Codes, Robustheit, Planänderung
│   ├── series.py    Stundenaggregation von Zustandsfolgen
│   └── view.py      Panel-View-Vertrag v1
├── coordinator.py   Sensoren → Stunden-Samples → Modell → Planer → Aktor
├── actuator.py      Freigabe schalten (Mindestzeiten, Budget, Fail-safe)
├── view_builder.py  Panel-Daten (Historie, Rollout, Ereignisse) – getrennt vom Regelpfad
├── history.py, events.py, websocket_api.py, panel.py
├── frontend/        Panel (Lit, ohne Build): thermocast-panel.js, tc-*.js, i18n.js, dev/
└── config_flow.py   Haus, Zonen (Subentries), Optionen
```

```bash
uv sync                        # .venv mit Home Assistant + pytest-homeassistant-custom-component
uv run pytest                  # Kern- und HA-Tests
uv run pytest tests/test_core.py   # nur Kern, ohne HA
./scripts/develop              # lokale HA-Instanz mit Fake-Sensoren → http://localhost:8123
```

Die Dev-Instanz (`config/configuration.yaml`) bringt Schieberegler für Außen-, Vorlauf- und
Raumtemperaturen, einen Heizungspumpen-Schalter und `input_number.summer_threshold` als Freigabe.

Panel-Entwicklung ohne HA:

```bash
uv run python scripts/sample_view.py      # Beispiel-Antworten (View, Modell, KPIs) aus synthetischen Daten
uv run python -m http.server -d custom_components/thermocast/frontend 8765
# → http://localhost:8765/dev/   (?tab=now|model|kpis, ?lang=en, ?dark=1, ?hover=50, ?cand=1, ?why=1, ?src=<datei.json>)
```

Im Notebook: `sys.path.insert(0, "custom_components/thermocast")`, dann `from core import OnlineZoneModel`.

## Roadmap

- [x] HA-Tests mit `pytest-homeassistant-custom-component` (Config Flow, Coordinator)
- [x] Panel „Gestern · Heute · Morgen“ (Begründung, Plan, Ursachen, Kandidaten)
- [x] Panel B: Modellgüte (Hindcast, Prognosehorizonte, Kalibrierung, Parameter, JSON-Export, WW-Ladungen)
- [x] Panel C: KPIs (Brennerstarts/Tag, kWh pro Heizgradtag, Komfort, vorher/nachher)
- [ ] Warmstart aus der Recorder-Historie beim Einrichten (statt Prior)
- [ ] Zwei-Zustands-Modell (Luft + Estrich) mit Kalman-Filter für FBH
- [ ] Zonen-Komfort aus `schedule`-Entität, Anwesenheit (`zone.home`)
- [ ] Kurvenanhebung während eines Blocks (Estrich gezielt laden)
- [ ] Rollladen-Zustand als Verschattungsfaktor
- [ ] Kostenfunktion Wärmepumpe: COP(T_außen, Vorlauf), EPEX-Preis, PV-Überschuss
- [ ] Diagnostics-Download, Repairs bei fehlenden Sensoren
