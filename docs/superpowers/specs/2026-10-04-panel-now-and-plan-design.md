# Thermocast-Panel A: „Gestern · Heute · Morgen“ – Design

Status: zur Review · Datum: 2026-10-04 · Teilprojekt A von A/B/C

## 1. Ziel

Ein eigenes Seitenpanel in Home Assistant, das beantwortet:

> **Warum heizt Thermocast gerade (nicht) – und was hat es bis morgen Abend vor?**

Zielgruppe ist der Betreiber selbst, während der Beobachtungsphase (vor dem Aktivieren der Steuerung) und
danach zur Kontrolle. Reine Ansicht – keine Bedienelemente.

Erfolgskriterien:
- Auf einen Blick: aktuelle Entscheidung, nächster Block, Begründung in einem Satz.
- Der Plan bis morgen 24:00 ist als Blockfolge sichtbar und stimmt mit dem überein, was der Regler
  tatsächlich tun wird, wenn die Prognose eintrifft.
- Für jede Stunde der Zukunft lässt sich nachvollziehen, *woraus* sich die Temperaturänderung zusammensetzt.
- Es ist sichtbar, ob eine Entscheidung knapp ist, ob sie nur wegen der Unsicherheit fällt und warum
  sich der Plan seit der letzten Stunde geändert hat.
- Das Panel kann die Regelung nie stören.

### Abgrenzung

| Teilprojekt | Inhalt | Status |
|---|---|---|
| **A (dieses Dokument)** | Panel-Gerüst, 72-h-Zeitachse, Begründung, Kandidaten, Rollout, Ereignisse, Planänderung | dieses Design |
| B Modellgüte | Prognose vs. Messung über Tage, gelernte Parameter, Ursachen auch für die Vergangenheit, Plan von gestern vs. Ist, Warmwasser-Ladungen schraffieren, JSON-Export | später |
| C KPIs | Brennerstarts/Tag, Gas-kWh pro Heizgradtag, vorher/nachher | später |

## 2. Entscheidungen

| Thema | Entscheidung | Begründung |
|---|---|---|
| Form | Eigenes Seitenpanel („Thermocast“ in der Sidebar) | Platz für Zeitachse + Details |
| Frontend-Technik | Plain-JS-Module + vendortes Lit, selbstgezeichnetes SVG, **kein Build** | reines uv-Projekt, keine Node-Toolchain, Zeitachse ist ohnehin maßgeschneidert |
| Layout | „A“: Story oben → Tageskarten → Zeitachse → Details | klare Lesereihenfolge Was → Wann → Warum |
| Zeitfenster | fest **gestern 00:00 → morgen 24:00** (Ortszeit), Jetzt-Linie | wie ml-smart-budget (`HourlyChart`) |
| Zeitachse | Wetter-Bahn + Heizen-Bahn + je Zone eine Bahn, einklappbare Ursachen-Balken | Eingänge sichtbar, Zerlegung bei Bedarf |
| Mehrtagesplan | **Rollout** des echten Reglers (stündlich neu planen inkl. Aktor-Regeln) | zeigt genau das, was der Regler tun würde – kein zweiter Optimierer |
| Datenweg | WebSocket-Abo `thermocast/subscribe` | Attribute wären zu klein und landen im Recorder |
| Textbausteine | Backend liefert Codes + Zahlen, Frontend formuliert (de/en) | Sprache nach `hass.language`, Backend bleibt testbar |

## 3. Bedienoberfläche

Reihenfolge von oben nach unten:

1. **Story**
   - Große Zeile: `🔥 Heizen: AUS · nächster Block morgen 01:00–05:00`.
   - 1–2 Sätze Begründung aus `explanation` (z. B. „EG fällt ohne Heizen morgen um 06:00 unter 20,2 °C.
     Der Estrich braucht Vorlauf, deshalb Start 01:00.“).
   - Chips: Modus (Beobachtung/aktiv), Prognose-Alter, Wechsel heute x/y, Fail-safe (falls aktiv),
     **Robustheit** („klar“/„knapp“; „ohne Sicherheitsmarge kein Block nötig“, falls σ-getrieben).
   - **Planänderung** (falls vorhanden): „Block 1 h später als vor einer Stunde – Prognose für morgen früh 1,2 K wärmer.“
2. **Tageskarten** (3 Spalten; unter 700 px untereinander)
   - Gestern (Ist): Heizstunden, Anzahl Blöcke, Minimum der führenden Zonen in Komfortzeit,
     Anteil der Stunden, in denen die Freigabe dem Planer folgte.
   - Heute: Ist bis jetzt + erwartet bis 24:00.
   - Morgen (Plan): Heizstunden, Blöcke, minimale untere Grenze der führenden Zonen in Komfortzeit,
     Unsicherheit (σ) am Tagesende.
3. **Zeitachse** (SVG, 72 h ± Zeitumstellung, Tagestrenner, Vergangenheit grau hinterlegt, Jetzt-Linie rot)
   - **Wetter-Bahn:** Außentemperatur (Vergangenheit gemessen, sonst Open-Meteo; Zukunft gestrichelt),
     Einstrahlung je Flächenausrichtung als Flächen.
   - **Heizen-Bahn:** gelaufene Blöcke (Heizungspumpe, gefüllt), geplante Blöcke (Rollout, gestrichelt),
     Ereignis-Marker (Symbol + Tooltip).
   - **Je Zone eine Bahn** (führende zuerst, dann alphabetisch): gemessen (Vergangenheit), Plan mit
     σ-Band, „ohne Heizen“ gestrichelt, Komfortfenster + Untergrenze je Tag.
     Darunter einklappbar **„Warum?“**: Ursachen-Balken je Stunde (Zukunft), gestapelt positiv/negativ:
     Sonne je Fläche, Heizen, Nachbarn, Gewinne, Verlust, Basis. Zustand je Zone in `localStorage`.
   - **Fadenkreuz-Tooltip** über alle Bahnen: Uhrzeit, Wetter, je Zone Wert (± σ) und Ursachen der Stunde.
     Auf Touch: Tippen.
   - **Kandidaten-Overlay:** Hover über eine Zeile der Kandidatentabelle zeichnet deren Block und
     Verlauf der führenden Zonen über die Zeitachse.
   - Unter 700 px: Zeitachse horizontal scrollbar, Startposition „jetzt“.
4. **Details** (2 Spalten; unter 700 px untereinander)
   - **Kandidaten für den nächsten Block:** „kein Block“ + Top 5, Spalten Block, Komfortverletzung (K·h),
     Kostenanteile (Komfort/Start/Energie/Verzögerung), Summe; gewählter hervorgehoben.
   - **Aktor-Regeln:** Mindestblock, Mindestpause, Tagesbudget, Beobachtungsmodus, Fail-safe – je ✓/✗
     mit Wert; Freigabe-Entität und ihr aktueller Zustand.

Farben über HA-Theme-Variablen (`--primary-text-color`, `--card-background-color`, `--divider-color` …),
Reihen mit fester farbenblindtauglicher Palette, in hell und dunkel geprüft.

Zustände ohne Daten:
- Thermocast nicht geladen → Hinweis statt leerer Seite.
- Keine Prognose → Vergangenheit sichtbar, Zukunft leer, Story nennt den Fail-safe.
- Kein Recorder / keine Historie → Vergangenheit leer mit Hinweis.
- `view` konnte nicht gebaut werden → „Ansicht nicht verfügbar“ + Grund.

## 4. Architektur

```
custom_components/thermocast/
├── core/
│   ├── model.py      ~ Prediction.contrib, predict(var0=…)
│   ├── planner.py    ~ Kosten in Komponenten, Top-Kandidaten, „mit Block“ für alle Zonen
│   ├── rollout.py    + Regler-Rollout bis Fensterende
│   ├── explain.py    + Begründung, Robustheit, Planänderung (Codes + Zahlen)
│   └── view.py       + baut das JSON-„view“ aus reinen Eingaben (HA-frei, testbar)
├── actuator.py       ~ async_apply liefert (Zustand, Abweichungsgrund); reine Regel-Funktion
├── history.py        + Recorder → Stundenreihen (gestern 00:00 … jetzt), stündlich gecacht
├── events.py         + Ereignis-Ringpuffer (200), im Store persistiert
├── coordinator.py    ~ sammelt Eingaben, ruft Rollout/View im Executor, getrennt vom Regelpfad
├── websocket_api.py  + thermocast/subscribe
├── panel.py          + Static-Path + Panel-Registrierung/-Entfernung
└── frontend/
    ├── lit.js                 vendort (Lit 3, lit-core.min.js; Version + Quelle im Dateikopf)
    ├── thermocast-panel.js    Einstieg, Abo, Zustand (Hover-Stunde, Kandidat)
    ├── tc-summary.js  tc-days.js  tc-timeline.js  tc-details.js
    ├── i18n.js                de/en
    └── dev.html + sample-view.json   lokales Rendern ohne HA
scripts/sample_view.py        erzeugt sample-view.json aus tests/synthetic.py
```

`core/` bleibt HA-frei. Alles, was Zahlen erzeugt (Rollout, Ursachen, Begründung, Tageskarten, View-JSON),
liegt in `core/` und ist ohne HA testbar; die HA-Schicht sammelt nur Zustände, Historie und Ereignisse.

### 4.1 Kern-Änderungen

**`model.py`**
- `Prediction` erhält `contrib: list[dict[str, float]]` – je Stunde `phi_i · theta_i`, gruppiert:
  `base` (bias), `loss`, `sun:<surface_key>:<kind>` (alle Lags einer Fläche summiert), `heat`,
  `neighbor:<i>`, `gain:<i>`. Invariante: `Σ contrib[h] == mean[h] − mean[h−1]` (bzw. − `temp_now`).
- `predict(temp_now, future, var0=0.0)`: Startvarianz, damit der Rollout Unsicherheit über Neuplanungen
  weiterträgt.
- `predict(..., history=None)`: optional fremde Historie (Rollout schreibt sie fort, ohne das Modell zu verändern).
- `group_labels()`: Anzeigename je Gruppe (Flächenname aus `SurfaceSpec.name`, sonst „Fenster Ost“ o. ä.).

**`planner.py`**
- `CostFn = Callable[[Candidate, float], dict[str, float]]`, Gesamtkosten = Summe.
  `default_cost` liefert `{"comfort", "start", "energy", "delay"}` mit den bisherigen Gewichten.
- `PlanResult` zusätzlich: `ranked: list[ScoredCandidate]` (alle bewerteten Kandidaten sortiert;
  Ausgabe nimmt „kein Block“ + Top 5), je Kandidat `cost`, `parts`, `violation`, Vorhersagen der führenden Zonen.
- `planned` enthält Vorhersagen **aller** Zonen mit dem gewählten Block (nicht nur führende).
- `plan(..., z=…)` unverändert; Robustheit ruft zusätzlich `plan(..., z=0)` auf.

**`rollout.py`** (neu)
```
rollout(zones, inputs_until_end, rules, start_state, end_hour, lookahead=24) -> RolloutResult
```
- Stündlich ab der aktuellen Stunde bis Fensterende: `plan()` über `min(lookahead, verfügbare Prognose)`
  Stunden ab dem Simulationszeitpunkt, mit simulierter Temperatur (Mittelwert) und Startvarianz je Zone.
- Wunsch `heat_now` → reine Aktor-Regelfunktion (Mindestblock, Mindestpause, Tagesbudget je lokalem Tag)
  → simulierter Zustand an/aus → jede Zone einen Schritt fortschreiben (`q = q_on` bzw. `0`),
  Mittelwert, σ, Ursachen sammeln.
- Abkürzung: Ist im Ausschaltzustand über den Horizont keine führende Zone ohne Heizen verletzt,
  entfällt die Kandidatensuche (Ergebnis = kein Block); Ergebnis muss identisch zur vollen Suche sein.
- Ergebnis: `blocks: list[(start, end)]`, je Zone `mean`, `std`, `free_mean`, `free_std`, `contrib`,
  plus `first: PlanResult` (Schritt 0).
- **Invariante:** `first` entspricht exakt `plan()` mit denselben Eingaben → identisch zur Live-Entscheidung.

**`explain.py`** (neu)
- `explain(first: PlanResult, zones, times) -> dict`: Code + Zahlen, z. B.
  `{"code": "block_planned", "driver": "<zone_id>", "first_violation": "<iso>", "deficit_k": 0.8,
    "next_block": {"start", "end"}, "lead_h": 5}`.
  Codes: `heating_now`, `block_planned`, `no_need`, `no_need_sun`, `failsafe`, `no_forecast`.
  `no_need_sun` = kein Block nötig, aber mit Einstrahlung = 0 hätte eine führende Zone eine Verletzung
  (eine zusätzliche `predict()`-Rechnung je führender Zone).
- `robustness(plan_z, plan_z0) -> {"margin": Kostenabstand Bester↔Zweitbester, "level": "clear"|"close",
  "sigma_driven": bool}`; `close`, wenn Abstand < 1,0 (= ein Start); `sigma_driven`, wenn mit z=0 kein
  Block gewählt würde, mit z aber schon.
- `plan_change(prev_snapshot, current) -> dict | None`: nur wenn sich der nächste Block um ≥ 1 h
  verschoben hat bzw. entstanden/entfallen ist. Ursache = größte Abweichung im Zeitraum des Blocks:
  Außentemperatur-Prognose (K), Einstrahlung (W/m²) oder gemessene vs. erwartete Raumtemperatur jetzt (K).

**`view.py`** (neu): `build_view(...) -> dict` – siehe Vertrag 4.4.

### 4.2 HA-Schicht

**`actuator.py`**
- Reine Funktion `apply_rules(want, current, elapsed, switches_today, opts) -> (target, reason)` mit
  `reason ∈ {None, "min_block", "min_pause", "budget"}`; wird vom Actuator und vom Rollout benutzt.
- `async_apply` gibt `(release, reason)` zurück; im Beobachtungsmodus `reason = "observe"`.
- `release_state(value, domain, on_value, off_value) -> bool | None` als reine Funktion (auch für Historie).

**`history.py`**
- `async_load_history(hass, cfg, start, end) -> HistorySeries` über
  `recorder.get_instance(hass).async_add_executor_job(get_significant_states, …)`.
- Entitäten: Temperatursensoren je Zone, Außentemperatur, Heizungspumpe, Freigabe-Entität,
  eigener `binary_sensor` Heizfreigabe (Planerwunsch).
- Aggregation pro Stunde: numerisch zeitgewichtetes Mittel; binär An-Anteil (0…1); Freigabe über
  `release_state`. Fehlende Daten → `null`.
- Cache: komplette Stunden werden einmal geladen; nur die laufende Stunde wird je Update neu geladen.
  Recorder nicht geladen → leere Reihen + Fehlercode `no_recorder`.

**`events.py`**
- Ringpuffer (200 Einträge) `{time, type, zone?, detail?}` im bestehenden Store (Schlüssel `events`).
- Typen: `window_open` (Stunde nicht gelernt), `forecast_failed`, `failsafe_start`/`failsafe_end`,
  `control_on`/`control_off`, `model_reset` (Layout geändert), `release_written`.
- Erfasst an den bestehenden Stellen im Coordinator/Actuator.

**`coordinator.py`**
- Regelpfad bleibt unverändert in Reihenfolge und Fail-safe-Verhalten; die Live-Entscheidung kommt
  weiterhin aus `plan()` (= Rollout-Schritt 0).
- Danach, in eigenem `try/except`: Eingaben für die Ansicht sammeln, `rollout` + `explain` + `build_view`
  im Executor. Neu berechnet nur bei neuer Stunde, neuer Prognose oder Zonenänderung; sonst wird nur
  `decision`/`events` im gecachten `view` aktualisiert. Fehler → `view = {"error": "..."}`, Log-Eintrag,
  Regelung unberührt.
- Open-Meteo: `past_days=2`, `forecast_days=3` (deckt gestern 00:00 Ortszeit bis morgen 24:00 + Lookahead).
- Plan-Snapshot der vorigen Stunde im Speicher (nicht persistiert; nach Neustart keine Planänderung).
  Inhalt: Zeitstempel, nächster Block, Prognose-Eingaben (Außentemperatur, Einstrahlung je Fläche) und
  erwartete Temperatur je führender Zone für die nächste Stunde.

**`websocket_api.py`**
- `thermocast/subscribe` (ohne Parameter; Single-Instance): `send_result`, dann sofort das aktuelle
  `view` als Event, danach bei jedem Coordinator-Update (`async_add_listener`). Abmeldung beim Schließen.
  Kein Thermocast-Eintrag geladen → Fehler `not_loaded`.

**`panel.py`**
- Static-Path `/thermocast_static` → `frontend/` (einmalig pro HA-Lauf, Flag in `hass.data`).
- `panel_custom.async_register_panel(frontend_url_path="thermocast", webcomponent_name="thermocast-panel",
  module_url="/thermocast_static/thermocast-panel.js?v=<manifest-version>", sidebar_title="Thermocast",
  sidebar_icon="mdi:home-thermometer", require_admin=False)` beim Setup; `frontend.async_remove_panel` beim Entladen.
- Manifest: `dependencies` + `http`, `frontend`, `panel_custom`, `websocket_api`.

### 4.3 Frontend

- Einstieg `thermocast-panel` (LitElement) erhält `hass`, `narrow`, `panel`; abonniert via
  `hass.connection.subscribeMessage(cb, {type: "thermocast/subscribe"})`; hält `view`, `hoverHour`,
  `hoverCandidate`.
- Komponenten wie in 3. beschrieben; Zeitachse rendert reines SVG mit `viewBox`, Skalen selbst
  berechnet (x: Stunden-Index; y: je Bahn eigener Bereich aus Daten ± Rand).
- Zeiten nur über `Intl.DateTimeFormat` mit `hass.config.time_zone`, Sprache `hass.locale.language`.
- Keine externen Requests, keine CDN-Abhängigkeit (HA kann offline sein).

### 4.4 Vertrag: `view` (Version 1)

Alle Zeitreihen sind auf `hours` ausgerichtet (Länge = Stunden im Fenster); fehlende Werte `null`.

```jsonc
{
  "version": 1,
  "generated_at": "2026-10-04T18:05:00+00:00",
  "window": {"start": "…", "end": "…", "now_index": 44, "tz": "Europe/Berlin"},
  "hours": ["2026-10-02T22:00:00+00:00", "…"],            // Stundenanfänge (UTC)
  "weather": {
    "t_out": [..], "t_out_measured": [true, …],             // gemessen vs. Prognose
    "irr": [{"key": "40_180", "label": "Schräge Süd", "values": [..]}]
  },
  "heating": {
    "actual": [0.0, 0.5, …],                                // Anteil Pumpe an je Stunde (Vergangenheit)
    "release": [true, …], "planner": [true, …],             // Vergangenheit
    "past_blocks": [{"start": "…", "end": "…"}],
    "planned_blocks": [{"start": "…", "end": "…"}]          // Rollout
  },
  "zones": [{
    "id": "<subentry_id>", "name": "EG", "heat_type": "fbh", "leads": true,
    "measured": [..],                                       // Vergangenheit
    "comfort_low": [..],
    "plan": {"mean": [..], "std": [..]},                    // ab now_index
    "free": {"mean": [..], "std": [..]},                    // ab now_index
    "contrib": [{"loss": -0.09, "sun:40_180:roof": 0.31, "heat": 0.0}, …],
    "groups": [{"key": "sun:40_180:roof", "label": "Schräge Süd", "kind": "sun"}]
  }],
  "decision": {
    "planner_wants": false, "applied": true, "control_enabled": false,
    "override": "observe",                                  // null|min_block|min_pause|budget|observe|failsafe
    "failsafe_reason": null, "switches_today": 0, "max_switches": 12,
    "since_last_change_min": 312, "forecast_age_min": 12,
    "release_entity": "number.…", "release_state": true
  },
  "explanation": {"code": "block_planned", "driver": "<id>", "first_violation": "…",
                  "deficit_k": 0.8, "next_block": {"start": "…", "end": "…"}, "lead_h": 5},
  "robustness": {"margin": 8.0, "level": "clear", "sigma_driven": false},
  "plan_change": null,
  "candidates": [{"start": "…", "end": "…", "cost": 1.61, "violation_kh": 0.0,
                  "parts": {"comfort": 0, "start": 1, "energy": 0.6, "delay": 0.05},
                  "chosen": true, "trajectories": {"<zone_id>": [..]}}],
  "days": [{"date": "2026-10-03", "kind": "past", "heat_hours": 4.0, "blocks": 1,
            "min_leading": 20.3, "release_followed": 1.0},
           {"date": "2026-10-04", "kind": "today", "heat_hours": 4.0, "heat_hours_planned": 0.0,
            "blocks": 1, "blocks_planned": 0, "min_leading": 20.4, "min_leading_planned": 20.5},
           {"date": "2026-10-05", "kind": "future", "heat_hours_planned": 7.0, "blocks_planned": 2,
            "min_leading_planned": 20.2, "std_end": 0.4}],
  "events": [{"time": "…", "type": "window_open", "zone": "<id>"}],
  "errors": []                                              // z. B. "no_recorder", "no_forecast"
}
```

Erwartete Größe: < 100 KB je Nachricht bei 4 Zonen.

## 5. Fehlerfälle

| Fall | Verhalten |
|---|---|
| Ausnahme beim Bau des `view` | Log + `{"error": …}`, Regelung unberührt |
| Fail-safe aktiv | `view` wird gesendet, `decision.override = "failsafe"`, Story nennt Grund |
| Keine Prognose | Vergangenheit + Ereignisse sichtbar, Zukunft leer, `errors: ["no_forecast"]` |
| Kein Recorder | Vergangenheit `null`, `errors: ["no_recorder"]` |
| Zeitumstellung | Fensterlänge 71/73 h; `hours` explizit, Frontend rechnet nie mit 24 h/Tag |
| Zone ohne Messwerte | Bahn bleibt, Werte `null`, Hinweis im Tooltip |
| Panel ohne geladenen Eintrag | Abo-Fehler `not_loaded` → Hinweistext |

## 6. Performance

- Rollout + Kandidaten im Executor; nur bei neuer Stunde/Prognose/Zonenänderung.
- Budget: **< 2 s** auf dem Entwicklungs-Mac für 4 Zonen und 52 h (Test misst und schlägt fehl bei Überschreitung).
  Falls nötig: Kandidaten-Vorfilter (nur Starts vor der ersten Verletzung + Vorlauf).
- WebSocket-Nachricht nur bei Coordinator-Update (alle 15 min) bzw. bei Abo.

## 7. Tests

Kern (ohne HA):
- Ursachen summieren sich exakt zur vorhergesagten Änderung je Stunde.
- `predict(var0)` erhöht σ entsprechend; ohne `var0` unverändert.
- Kosten-Komponenten summieren zum bisherigen Skalar (Regression gegen alte Gewichte).
- Rollout: Schritt 0 == `plan()`; Mindestblock/-pause/Budget eingehalten; kalt → Blöcke, warm → keine;
  Abkürzung liefert identische Ergebnisse wie volle Suche.
- `explain`: Codes für kalt/warm/Sonne/Fail-safe; `robustness` (`close`, `sigma_driven`); `plan_change`.
- `build_view`: Längen aller Reihen == `len(hours)`, Zeitumstellungstag (71/73 h), Snapshot der Struktur.
- Performance-Budget.

HA (`pytest-homeassistant-custom-component`):
- Panel registriert beim Setup, entfernt beim Entladen; Static-Path liefert `thermocast-panel.js`.
- `thermocast/subscribe`: sofortiges `view`, Push nach Update, `not_loaded` ohne Eintrag.
- Historie mit Recorder-Fixture: Stundenmittel, An-Anteile, Freigabezustand.
- Ereignisse: `control_on/off`, `forecast_failed`, `failsafe_*` werden erfasst und persistiert.
- Fehler im `view`-Bau beeinflusst die Freigabe nicht.

Frontend:
- `frontend/dev.html` rendert `sample-view.json` (aus `scripts/sample_view.py`) ohne HA.
- Manuell in der Dev-Instanz: hell/dunkel, 375 px Breite, Hover/Tap, Kandidaten-Overlay, leere Zustände.

## 8. Risiken / offene Punkte

- **Rollout-Laufzeit** auf schwacher Hardware – abgesichert durch Budget-Test und Cache; Vorfilter als Plan B.
- **Lit vendoren:** Lizenz (BSD-3) im Dateikopf; Aktualisierung manuell.
- **`panel_custom`-API** kann sich ändern – durch HA-Test abgesichert, HA-Version ist gepinnt.
- **Planänderungs-Ursache** ist eine Heuristik (größte Eingangsabweichung), keine exakte Attribution;
  wird im UI als „vermutlich“ formuliert.
