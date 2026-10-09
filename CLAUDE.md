# CLAUDE.md – Thermocast

Projektkontext für Claude-Code-Sessions. Sprache mit dem Maintainer: **Deutsch**. Code, Kommentare,
Commit-Messages, Entitäts-/Config-Keys: **Englisch**. UI-Texte zweisprachig (en + de).

---

## 1. Ziel

Eigene Home-Assistant-Integration (Custom Component, Config Flow, später HACS), die die
**Raumheizung vorausschauend freigibt**:

- pro Zone vorhersagen, wie warm es **ohne Heizen** wird (Außentemperatur, Sonne auf Fenster
  *und* Dachflächen, Nachbarräume, innere Gewinne, Estrich-Trägheit),
- den Kessel nur dann und in **langen Blöcken** freigeben, wenn eine führende Zone sonst
  unter ihr Komfortband fällt,
- Modelle **online** lernen (RLS, kein periodisches Neutraining; RL bewusst verworfen –
  zu wenig Daten, Exploration = frieren),
- später dieselbe Logik für eine **Wärmepumpe** nutzen (Kostenfunktion austauschen: COP,
  EPEX-Strompreis, PV-Überschuss).

Hauptproblem heute: Gaskessel ist stark überdimensioniert und **taktet** (min. Leistung
≈ 7,3 kW vs. 1–2 kW Bedarf in der Übergangszeit). Blöcke über die Estrich-Speichermasse
sollen Starts von ~35/Tag auf 5–10/Tag senken.

Entscheidung: **Python direkt in HA**
(Rechenlast trivial, Recorder/Entitäten nativ, HACS-tauglich). Rust nicht für dieses Projekt.

---

## 2. Referenzinstallation (allgemein)

Hausspezifisches (Familie, Räume, Verbrauch, Ort, Zonenplan) steht in **`CLAUDE.local.md`** und
`docs/local/` – beides bewusst **nicht im Repo** (öffentlich auf GitHub). Für die Entwicklung reicht:

- Gedämmtes EFH (Bj. ~2000er), Gas-Brennwertkessel mit **Modulationsuntergrenze ≫ Bedarf** in der
  Übergangszeit (≈ 7 kW vs. 1–2 kW) → taktet; Regelung **Buderus RC310** witterungsgeführt,
  angebunden über **EMS-ESP** (MQTT). ⚠️ RC310-Einstellungen liegen im EEPROM → Schreibzugriffe begrenzen.
- **EG + Bad OG:** Fußbodenheizung ohne Verteiler/Stellantriebe (Rücklaufbegrenzer je Raum) →
  eine FBH-Zone ohne Stellglied, führt die Freigabe.
- **OG/Dachboden:** Heizkörper mit Zigbee-TRVs (Better Thermostat). Thermocast stellt TRVs **nie direkt**;
  optional (je Zone) setzt es den **BT-Sollwert** (Laden im Block, sonst Untergrenze, Ruhezeiten).
- **Warmwasser** über denselben Kessel: WW-Ladungen heben den Vorlauf → Heiz-Proxy nur bei laufender
  **Heizungspumpe**, nicht über den Vorlauf allein.
- **Recorder** ohne include-Filter (sonst bricht u. a. das Energie-Dashboard), Langzeitstatistiken
  werden für Warmstart und KPIs genutzt.
- Später evtl. Wärmepumpe → Kostenfunktion austauschbar halten.

---

## 3. Architektur

```
custom_components/thermocast/
├── core/                 # reines Python + numpy, KEIN HA-Import (Notebook/Test-tauglich)
│   ├── model.py          # OnlineZoneModel: ARX-Grey-Box + RLS, SurfaceSpec, ZoneSpec, HourRecord
│   ├── forecast.py       # Open-Meteo: temperature_2m + global_tilted_irradiance je (tilt, azimuth);
│   │                     #   nacheinander + 429-Retry (5/20 s) – parallel gab es alle paar Stunden 429
│   ├── planner.py        # Blockplaner: kein Block | Start 0..H × Länge {2,…,12} h, Ober-/Untergrenze,
│   │                     #   Lade-Deckel (charge_cap), CostFn(cand, K·h kalt, K·h warm, Zehrzeit, zu-Anteil) → Komponenten
│   ├── bt.py             # Better-Thermostat-Sollwert als reine Entscheidung (Laden/Untergrenze/Ruhezeit)
│   ├── rules.py          # Aktor-Regeln als reine Funktionen (Live-Aktor + Rollout)
│   ├── rollout.py        # Regler-Rollout bis morgen 24:00 (stündlich planen + Regeln anwenden)
│   ├── explain.py        # Begründungs-Codes, Robustheit (knapp/σ-getrieben), Planänderung
│   ├── series.py         # Stundenmittel / An-Anteil aus Zustandsfolgen (Recorder)
│   ├── view.py           # Panel-View-Vertrag v1 (Fenster, Tageskarten, Kandidaten)
│   ├── quality.py        # Stunden-/Prognose-Log, Hindcast, Fehlermaße, Parameter lesen, zone_quality()
│   └── kpi.py            # Tages-KPIs aus Stundenstatistiken (Starts, kWh/HGT, Komfort), vorher/nachher
├── coordinator.py        # 15-min-Loop: Sensoren → Stunden-Sample → RLS-Update → Prognose → Plan → Aktor
│                         #   + Stunden-Log, Param-Snapshots, control_since (alles im Store)
├── actuator.py           # Freigabe schalten: Mindestblock/-pause, Tagesbudget, Fail-safe
├── zone_actuator.py      # BT-Sollwerte je Zone (opt-in): schreiben, Handeingriff erkennen, Fail-safe
├── view_builder.py       # Panel-Daten neben dem Regelpfad (Historie, Rollout, Schatten-Aktor, Snapshots,
│                         #   Prognose-Log, Hindcast-Ursachen, WW-Anteil)
├── model_view.py, kpi_view.py  # Tab „Modell“ (+ JSON-Export), Tab „KPIs“ (Recorder-Langzeitstatistik)
├── history.py, events.py # Recorder-Abfragen (States + Statistiken); Ereignis-Ringpuffer (im Store)
├── websocket_api.py      # thermocast/subscribe (push), thermocast/model|kpis|export (on demand)
├── panel.py, frontend/   # Seitenpanel mit Tabs: Lit vendort (kein Build), tc-*.js, tc-chart.js, i18n.js, dev/
├── config_flow.py        # Haus (Main Entry), Zonen (Config Subentries), Options
├── entity.py, sensor.py, binary_sensor.py, switch.py
├── strings.json, translations/{en,de}.json
└── manifest.json         # numpy>=1.26; dependencies: frontend/http/panel_custom/websocket_api; after: recorder
tests/
├── synthetic.py          # Testhaus: Luft + versteckter Estrich, Ostfenster, Süddach (verzögert)
├── core_helpers.py       # trainiertes Modell, Zukunfts-Records (Kern-Tests importieren `core.*`)
├── test_core.py, test_model_contrib.py, test_planner.py, test_rules.py, test_rollout.py,
│   test_explain.py, test_series.py, test_view.py, test_quality.py, test_kpi.py   # Kern ohne HA
└── test_init.py, test_config_flow.py, test_events.py, test_view_builder.py, test_panel.py,
    test_view_history.py + test_kpi_ha.py (Recorder-Fixture)                    # HA
scripts/sample_view.py    # frontend/dev/sample-{view,model,kpis}.json aus synthetischen Daten
docs/superpowers/         # Specs + Pläne Panel A und B/C
```

### Modell (pro Zone, Zeitschritt 1 h)

```
T[t+1] − T[t] = b0 + a·(T_out − T)
              + Σ_s Σ_l b_{s,l} · I_s[t−l]     # Fenster l∈{0,1}, Dach {1,2,3,4,6}, Wand {2,4,6}
              + Σ_k c_k · Q[t−k]               # FBH k∈{0,1,2,3,4,6}, Heizkörper {0,1}
              + Σ_n d_n · (T_n − T)            # Nachbarzonen
              + Σ_g e_g · G_g                  # innere Gewinne
```

- `I_s`: Open-Meteo GTI für die Ausrichtung der Fläche (Kompass-Azimut → Open-Meteo: 0=S, −90=O).
  Fläche × g-Wert × Verschattung steckt im gelernten Koeffizienten.
- `Q` (Heiz-Proxy): `max(0, Vorlauf − T_raum)` solange Heizungspumpe läuft und keine WW-Ladung (Option);
  Heizkörper × Ventilanteil – bevorzugt `hvac_action` des TRV-Thermostats (heating/idle), denn der TRVZB
  meldet keine echte Ventilstellung (`valve_opening_degree` ist nur eine Grenze).
- `q_on` (Heiz-Proxy, den der Planer je Blockstunde annimmt): EMA (α 0,2) nur über Stunden mit Pumpe ≥ 75 % der
  Stunde, auf die Pumpenzeit normiert (`block_q`/`update_q_on`), live und im Warmstart (zeitlich geordnet).
  Vorher Mittel aller Stunden mit q > 1 → im Warmstart dominierte der alte Dauerbetrieb mit wenig Vorlauf
  (WZ 3,2 statt ~15 in Blöcken) und der Planer unterschätzte Blöcke um Faktor 4–5.
- RLS: Vergessensfaktor 0,996/h (~10 Tage), Vorzeichen-Projektion (alles außer Bias ≥ 0),
  Huber-Clipping (3σ), Kovarianz-Deckel (normierte Spur ≤ 50) gegen Wind-up im Sommer.
  Stunden mit offenem Fenster/Datenlücke: nicht lernen, nur Historie fortschreiben.
- Prognose liefert Mittelwert + σ; Planer nutzt `mean − z·σ` (z = Option, Standard 1).

### Planer / Kosten (Gaskessel)

Live und im Rollout: `charge_cost(w)` („Laden & Zehren“, Spec `docs/superpowers/specs/2026-10-06-charge-and-coast-design.md`),
`w` = Option „Wenige Brennerstarts ↔ wenig Gas“ / 100 (Standard 0,8):
`20·K·h unter Untergrenze (führende Zonen) + 4·K·h über Obergrenze (alle) + [(1+9w) + (0,5−0,35w)·Länge]·24/(Start+Länge+Zehrzeit)`.
- **Pro Tag ab jetzt** (v0.7.4): Nenner = Stunden *ab jetzt* bis zum nächsten Bedarf, nicht ab Blockbeginn – ein früher
  Block kauft keine Pause, die das Haus ohnehin hatte; bei gleichem nächsten Bedarf gewinnt der spätere Block (die
  alte Strafe `0,01·Start` bevorzugte frühe Starts, trotz Kommentar). Anlass: Block 17–21 Uhr für den Bedarf am
  nächsten Mittag, ein 2-h-Block am Vormittag reichte. Synthetisch: bei milden Tagen weniger Starts *und* Gas.
- Untergrenze = Komfort − Band in der Komfortzeit, sonst Grundwert; Obergrenze je Zone (Standard Komfort ± 1/2 K).
- **Zehrzeit** = Stunden nach Blockende bis zur nächsten Unterschreitung (darüber hinaus aus der Abkühlrate
  extrapoliert, ≤ 48 h). Auch 48 h erreicht ein langsam auskühlendes Wohnzimmer → alle Startzeiten kosten exakt
  gleich. **Gleichstand** (`_rank`): unter Blöcken, die bis Horizontende nichts übrig lassen (`covers`), gewinnt
  der spätere Start (v0.7.8; vorher 5 h Vorlauf für einen Bedarf, den 2 h ab 10 Uhr deckten); sonst bleibt die
  Suchreihenfolge (früh) – „später“ für Blöcke, die eine Unterschreitung an den nächsten Block weiterreichen, legte
  bei −10 °C den Block so spät, dass der nächste wegen der Mindestpause zu spät kam (1 K drunter).
  Unterschreitungen nach der Zehrzeit sind Sache des nächsten Blocks (außer Pause < 2 h).
  Ohne das sah der Ein-Block-Planer den nächsten Start nie, und der Regler wirkte nicht.
- Läuft ein Block schon (`running`), kostet Weiterheizen keinen Start (sonst bricht die Neuplanung Blöcke ab).
- BT-gesteuerte Zonen sind gedeckelt (`charge_cap`): q = 0, sobald die Zone ihr Ladeziel (Ruhezeit: Grundwert) erreicht.
  Ladeziel (`zone_charge_target`) = Obergrenze in der Komfortzeit oder wenn sie in ≤ 12 h beginnt (`PRECHARGE_H`), sonst
  Grundwert – Anlass: Büro wurde samstags in den Wohnzimmer-Blöcken auf 22,5 °C geladen, ohne Komfort bis Montag.
- **Wenig Abnehmer** (`cycling`): Blockstunden, in denen BT-Räume zu sind (Anteil an allen BT-Räumen), kosten
  `(1+9w)·24/(Start+Länge+Zehrzeit)·60/45` je Stunde – mit wenigen offenen Kreisen taktet der Brenner im Block
  (Taktsperre 45 min). Legt Blöcke in Stunden, in denen viele Räume Wärme nehmen (z. B. vor einer Ruhezeit).
`default_cost` (alt, ohne Obergrenze/Zehrzeit) bleibt für Tests/Vergleich. Die Kostenfunktion ist injizierbar –
für die Wärmepumpe später COP(T_out, Vorlauf), Strompreis, PV-Überschuss.

### Aktor-Regeln (Sicherheit zuerst)

- Standard: **Beobachtungsmodus** (`switch.…_steuerung_aktiv` aus) → nichts wird geschaltet.
- AN (Heizen erlaubt) ist immer erlaubt; AUS nur nach Mindestblock und im Tagesbudget
  (Standard ≤ 12 Wechsel/Tag); erneutes AN erst nach Mindestpause.
- Gleicher Zustand wird nicht erneut geschrieben (EEPROM).
- Fail-safe → AN: Update-Exception, Prognose > 2 h alt, fehlender Sensor einer führenden Zone,
  Integration entladen, Steuerung ausgeschaltet.
- Fail-safe ist ein erzwungenes AN: wirkt auch in der Mindestpause und hinterlässt keine Mindestblock-Pflicht.
- Anlaufphase: in den ersten 5 min nach dem Start halten fehlende Sensoren/Prognose den gespeicherten Zustand
  (Prüfung jede Minute, Override `startup`) – MQTT/Zigbee kommen nach einem HA-Neustart oft verzögert.
- Selbst-Neuladen nach Konfigurationsänderung (Update-Listener) gibt nicht frei; Deaktivieren/Entfernen schon.
- Empfohlene Freigabe-Entität: **RC310-Sommer/Winter-Modus (`select`)**, Winter = erlaubt, Auto = gesperrt,
  Sommerschwelle fest 10 °C → ein Block heizt sicher auch an milden Tagen, Pumpe steht zwischen den Blöcken,
  bei HA-Ausfall heizt das RC310 spätestens unter 10 °C. (Schwelle 16/10 allein wirkt nur bei Modus Auto
  und nur unter 16 °C draußen; „Sommer“ als Sperre nur mit Absicherung außerhalb von HA.)
- **Offenes Fenster** (Fenster-Entitäten der Zone): Zone führt nicht (löst keinen Block aus), ist im Planer auf den
  Grundwert gedeckelt und der BT-Aktor hält den Grundwert. Fensterwechsel per State-Change-Listener
  (`async_track_windows`); nach dem Schließen 1 h **Nachlauf** (`WINDOW_RECOVERY`): Planer startet ab
  `max(gemessen, Temperatur vor dem Öffnen)` (`window_recovery_temp`, Ereignis `window_recovery`), Stunden mit
  Lüften oder Nachlauf werden nicht gelernt (Warmstart: Stunde davor/danach auch nicht). Anlass: 10 min Stoßlüften
  −1 K, nach 40 min wieder +0,5 K – ohne Nachlauf plante die Zone sofort einen Block.
- **Warmstart-Halten:** Zone mit Modell auf dem Prior (neu/zurückgesetzt) → bis der Warmstart nach dem Setup fertig
  ist (≤ 10 min), hält der Aktor den gespeicherten Zustand (Override `warmstart`). Anlass: Flächen geändert →
  Reset → Prior-Plan schaltete den Kessel ein, 3 s bevor der Warmstart fertig war (dann 1 h Mindestblock).
- **Taktsperre** (Option `anti_cycle_minutes` + Sensor Brennerstarts): endet die Sperre vor dem nächsten Update und
  liefe der folgende Neustart < 15 min bis Blockende, endet der Block jetzt (`trim_tail_restart`, Ereignis
  `block_trimmed`) – spart den kurzen letzten Start.
- BT-Aktor (opt-in je Zone): im Block Ladeziel (Obergrenze, fern vom Komfort Grundwert), sonst Untergrenze; nur bei Änderung, 0,5-K-Schritte,
  15–24 °C, ≤ 24/Tag; Handeingriff → Zone ruht bis Blockende (≥ 3 h); Ruhezeit: 15 min vorher Grundwert,
  dann nichts (Ausnahme: führende Zone < Grundwert − 1 K); Steuerung aus/Entladen → Untergrenze.

### Entitäten

House: `switch` Steuerung aktiv, `binary_sensor` Heizfreigabe (Planerwunsch),
`sensor` Nächster Heizblock (Timestamp, Attr. Ende/Prognose-Alter/Fail-safe-Grund).
Zone (Subentry-Device): `sensor` Prognose-Minimum (Attr. `forecast`: [{time, mean, lower,
comfort_low}] für ApexCharts), `binary_sensor` Heizbedarf, `sensor` Modellfehler (Diagnose,
Attr. Parameter + Sonnenantwort je Fläche).

---

## 4. Status (v0.7.9)

- ✅ Kern getestet auf synthetischen Daten: 1-Schritt-MAE ≈ 0,03 K/h, 24-h-Prognose-MAE ≈ 0,1 K,
  Ostfenster und Süddach werden getrennt gelernt, Planer heizt bei −5 °C, nicht bei 18 °C.
- ✅ HA-Schicht getestet gegen HA 2026.9 (`tests/test_config_flow.py`, `tests/test_init.py`):
  Config Flow inkl. Release-Werte, Options, Zonen-Subentries (anlegen/rekonfigurieren + Reload),
  Entitäten an Subentry-Devices, Beobachtungsmodus, Aktor (sperren/freigeben), Fail-safes
  (Sensor fehlt, keine Prognose, Entladen, Steuerung aus), Stundenabschluss → RLS-Update + Store.
- ✅ Panel A „Gestern · Heute · Morgen“ (Spec/Plan in `docs/superpowers/`): Story mit Begründung,
  Robustheit, Planänderung; Tageskarten; 72-h-Zeitachse mit Ursachen-Zerlegung, Ereignissen,
  Kandidaten-Overlay; in der Dev-Instanz im echten HA-Frontend geprüft (hell/dunkel, Handybreite).
- ✅ Panel B „Modell“ + C „KPIs“ (v0.3.0, autonom umgesetzt, Spec `…-panel-model-and-kpis-design.md`
  mit Umsetzungsnotizen): Hindcast, Prognosehorizonte mit ±σ-Kalibrierung, Parameter + 30-Tage-Verlauf,
  JSON-Export; KPIs aus Recorder-Langzeitstatistiken (Options: Brennerstarts, Heizenergie, WW aktiv).
  Befund (synthetisch): ±σ-Abdeckung ab 3 h nur ~45 % → σ der Prognose zu klein (Wetterfehler fehlt).
- `recorder` ist nur `after_dependencies` (ein kaputter Recorder soll Thermocast nicht blockieren);
  das Panel nutzt ihn optional für die Vergangenheit (`errors: ["no_recorder"]` ohne).
- Läuft seit Oktober 2026 an der Referenzinstallation (echte EMS-ESP-Entitäten); Dev-Instanz: `./scripts/develop`.
- Panel-Interna, die man kennen muss:
  - Story/Zeitachse zeigen den **Rollout**-Block (was passieren wird), die Kandidatentabelle die
    aktuelle Einblock-Wahl des Planers (`explanation.planner_block`) – die können abweichen.
  - Im Beobachtungsmodus startet der Rollout vom **Schatten-Aktor** (`ViewBuilder._update_shadow`),
    sonst ignorierte er Mindestblock/-pause.
  - Periodische Coordinator-Refreshes sind Background-Tasks → in Tests
    `async_block_till_done(wait_background_tasks=True)`. Ebenso der Panel-View: **jedes** Coordinator-Update
    pusht sofort Entscheidung + Ereignisse und baut den View im Hintergrund neu (`ViewBuilder.async_schedule_refresh`,
    nur das jüngste wartende Update wird nach einem laufenden Bau gebaut – v0.7.5; vorher nur bei neuer Stunde/
    Prognose/Fail-safe, und ein Warmstart während eines Baus ging verloren). Prognose-Log nur beim ersten Bau der
    Stunde (`ForecastLog.has`). Setup/Reload warten nicht darauf; das Panel abonniert
    `ViewBuilder.async_add_listener`, nicht den Coordinator.
  - Planer: `ZonePlanInput.heating_batch` → `OnlineZoneModel.predict_batch` rechnet alle Kandidaten einer Zone als
    Matrix (Modell linear in T). Muss mit `predict` übereinstimmen (`tests/test_predict_batch.py`).
  - Reload übernimmt die Prognose (< 1 h, alle Flächen-Ausrichtungen enthalten) über `hass.data` (`FORECAST_CACHE`).
  - Recorder-Tests brauchen `recorder_mock` vor `hass` → eigenes Modul mit überschriebener
    autouse-Fixture (`tests/test_view_history.py`, `tests/test_kpi_ha.py`).
  - WebSocket-Tests nicht mit `freezer` in die Vergangenheit springen (Token wird ungültig) –
    Logs lieber direkt befüllen (siehe `tests/test_panel.py`).
  - Store-Inhalt je Zone: `model`, `q_on`, `log` (14 Tage), `flog` (Prognosen 1/3/6/12/24 h), `params`
    (30 Tages-Snapshots); HAs JSON-Store schreibt NaN als `null` → `HourRecord.from_dict` fängt das ab.
- ✅ v0.4.0 (autonom): EEPROM-Schutz im Aktor (Bestätigung, Backoff, 40 Schreibvorgänge/Tag, Repairs),
  Warmstart aus Recorder-Statistik + Open-Meteo-Vergangenheit (automatisch für neue Zonen, Button),
  σ-Kalibrierung je Horizont aus dem Prognose-Log (Option), laufende Stunde übersteht Neustarts,
  zwei Stores (kleiner Zustand debounced, Logs stündlich), Komfort aus `schedule`-Helfer,
  Diagnose-Download, Repair bei Fail-safe > 1 h, CI-Workflow (GitHub Actions: Tests, hassfest, HACS),
  `docs/INSTALLATION-CHECKLISTE.md`.
- ✅ v0.4.x (mit echter Anlage): Haus rekonfigurierbar, WW-Ladung aus dem Heiz-Proxy (Option), TRV-`hvac_action`
  als Ventilsignal, Gateway-Boolean-Formate (ON/true/an), Fail-safe ohne Blockpflicht, Ereignisse im Tooltip.
- ✅ v0.5.0 „Laden & Zehren“ Stufe 1 (Spec/Plan `…2026-10-06-charge-and-coast…`): Ober-/Untergrenze, Zehrzeit-
  Kosten mit Regler, BT-Aktor mit Ruhezeiten, Panel (Obergrenze, BT-Treppe, Ruhezeit, Starts je Block).
  Stufe 2 offen: Lade-Hebel (Vorlauf/Pumpe im Block) – erst nach Test des Hebels an der Anlage.
- ✅ v0.5.x–v0.7.1 (mit echter Anlage): Neuladen ohne Freigabe-Flip, 5-min-Anlaufphase, Panel-Pfad je Version,
  KPI Energie/Tag; v0.6: Kosten „wenig Abnehmer“; v0.6.1: Neulernen behält jüngste Live-Stunde (`merge_logs`);
  v0.7: fensterbewusst (Planer + BT), Blockende vor kurzem Taktsperre-Neustart; v0.7.1: Panel-Feinheiten;
  v0.7.2: Performance – Reload ohne View-Neubau/Prognose-Abruf, Planer vektorisiert (Rollout ~10× schneller);
  v0.7.3: Lüft-Nachlauf (1 h ab Temperatur vor dem Öffnen, Fenster per Listener), Warmstart-Halten nach Modell-Reset;
  v0.7.4: `q_on` nur aus Blockstunden (Pumpe ≥ 75 %), Kosten pro Tag ab jetzt → Blöcke so spät wie möglich;
  v0.7.5: Panel-View bei jedem Update neu (vorher stündlich; Warmstart während eines Baus ging verloren);
  v0.7.6: Zehrzeit hinter dem Horizont bis 48 h, Open-Meteo nacheinander mit 429-Retry;
  v0.7.7: Story – vom Rollout verhinderte Unterschreitung heißt „Block geplant“, nicht „in Kauf genommen“;
  v0.7.8: Gleichstand → späterer Start (nur Blöcke, die den Horizont abdecken);
  v0.7.9: Kandidaten-Tabelle – Summe vorn, Null-Spalten weg, für alle gleiche Anteile ausgegraut.
  Erkenntnis an der Anlage: an milden Tagen liefert die witterungsgeführte Kurve kaum Vorlauf → Fußpunkt anheben
  (README); längere Fenster-Verzögerungen (0–3 h) getestet und verworfen (MAE minimal schlechter).
- Bekannte Schwächen:
  - Prognose nutzt aktuelle Nachbartemperaturen/Gains als konstant über den Horizont.
  - Planer kennt nur *einen* Block im Horizont (Rollout plant stündlich neu; die Zehrzeit bewertet den nächsten Start).
  - Im Block taktet der Brenner weiter, wenn das Haus < Mindestleistung abnimmt (→ Stufe 2, „Starts je Block“).
  - Warmstart nutzt Stundenmittel (leicht geglättete ΔT) und für den Heiz-Proxy den Stundenmittel-Vorlauf
    × Pumpen-Anteil – grober als live; der Vergessensfaktor wäscht das in Tagen aus.
  - Keine Anwesenheit (`zone.home`) im Komfort; nur Zeitplan.
  - Sonnen-Zuordnung bei ähnlich verlaufenden Flächen (z. B. Westfenster vs. Norddach) unscharf – das Modell
    ordnet „tagsüber, verzögert“ der Fläche mit passenden Verzögerungen zu.

---

## 5. Nächste Schritte (Priorität)

1. ~~Installation an der Referenzanlage, Zonen, Steuerung aktiv~~ ✅ (Okt. 2026) – jetzt auswerten: Starts/Tag,
   kWh/Start, Komfort (KPI-Tab, Tageskarten „Starts je Block“), Modellgüte je Zone.
2. Zwei-Zustands-Modell (Luft + Speichermasse, Kalman) bzw. τ-geglättetes Sonnensignal, falls ARX nicht reicht.
3. Kurvenanhebung während eines Blocks (Estrich gezielt laden) – vorerst statisch über den Fußpunkt gelöst.
4. Anwesenheit (`zone.home`) im Komfort.
5. Nachbartemperaturen über den Horizont prognostizieren statt konstant.
6. Wärmepumpen-Kostenfunktion (EPEX, PV, COP) vorbereiten.
7. Gewinn-Prognose nach Tagesprofil (typischer Wert je Stunde, evtl. je Wochentag, aus dem Stunden-Log) statt
   konstant fortgeschrieben – erst danach ein Hausstrom-Signal (Gesamtleistung) als innerer Gewinn sinnvoll
   (sonst schreibt der Planer z. B. eine Ofen-Spitze für 72 h fort).

---

## 6. Konventionen

- `core/` bleibt HA-frei und vollständig per pytest testbar; HA-Code ist dünne Hülle.
- numpy-lastige Arbeit (Planer) via `hass.async_add_executor_job`; keine blockierenden Calls im Loop.
- Jede neue Steuerfunktion: Fail-safe-Richtung = „Heizen erlaubt“.
- Keine harten Entitäts-IDs im Code – alles über Config Flow.
- Umgebung: **uv** (`uv sync`, `uv run pytest`, `uv run ruff check`). Kein pip/requirements.txt.
  HA-Version über `homeassistant>=…,<…` in der dev-Gruppe auf stabile Releases pinnen
  (neueste `pytest-homeassistant-custom-component` zieht sonst Betas).
  `home-assistant-frontend` ist in der dev-Gruppe gepinnt (Version aus HAs frontend-Manifest): HA
  installiert Pakete zur Laufzeit nach (Dev-Instanz), `uv sync` entfernt sie wieder – ohne den Pin
  scheitern danach alle Tests, die `frontend`/`panel_custom` laden. Beim HA-Update mitziehen.
- Tests: `uv run pytest` (Kern + HA). Neue Modell-Features immer gegen `tests/synthetic.py` prüfen.
- Lokale HA-Instanz: `./scripts/develop` (Config in `config/configuration.yaml`, Fake-Sensoren).
- Panel-Frontend: Plain-JS-Module + `frontend/lit.js` (Lit 3.3.1 vendort), **kein Build, kein CDN**.
  Texte nur in `frontend/i18n.js` (de/en). Backend liefert Codes + Zahlen, nie fertige Sätze.
  Schnell iterieren über `frontend/dev/` (siehe README). Der View-Vertrag (`core/view.py`, Version 1)
  ist die Schnittstelle – Änderungen dort immer mit `tests/test_view.py`.
- Ruff, Zeilenlänge 120, Python ≥ 3.14.2 (wie aktuelles HA).
