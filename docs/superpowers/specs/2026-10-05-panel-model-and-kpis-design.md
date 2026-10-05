# Thermocast-Panel B + C: Modellgüte und KPIs – Design

Status: autonom umgesetzt (Nutzer hat B + C freigegeben und war offline) · Datum: 2026-10-05
Baut auf Panel A auf (`2026-10-04-panel-now-and-plan-design.md`).

> **Hinweis:** Die Entscheidungen in Abschnitt 2 hat Claude ohne Rückfrage getroffen. Sie sind so
> gewählt, dass sie später leicht änderbar sind; bitte beim Review gezielt prüfen.

## 1. Ziel

- **B – Taugt das Modell?** Pro Zone sichtbar machen, wie gut das gelernte Modell die Raumtemperatur
  beschreibt (unabhängig von Wetterprognose und Plan) und wie gut die operative Prognose war
  („Plan von gestern vs. Ist“). Gelernte Parameter physikalisch interpretieren und ihre Entwicklung zeigen.
  Ursachen-Zerlegung auch für die Vergangenheit. Warmwasser-Ladungen in der Zeitachse schraffieren.
  Alles als JSON exportierbar (Notebook/polars).
- **C – Bringt es was?** Tages-KPIs über Wochen: Brennerstarts/Tag, Heizenergie (kWh), Heizgradtage,
  kWh pro Heizgradtag, Minimum der führenden Zonen in Komfortzeit, Komfort-Unterschreitungsstunden –
  und ein Vorher/Nachher-Vergleich ab dem ersten Einschalten der Steuerung.

Nicht-Ziele: keine Bedienung, keine Modelländerung aus dem Panel, keine Gas-Kosten (€), kein Warmstart
(eigenes Thema, Schritt 4 in CLAUDE.md).

## 2. Entscheidungen

| Thema | Entscheidung | Begründung |
|---|---|---|
| Navigation | Panel bekommt **Tabs**: „Jetzt & Plan“ · „Modell“ · „KPIs“ | ein Panel, eine Sidebar-Zeile |
| Datenweg B/C | **On-Demand-WebSocket-Befehle** `thermocast/model`, `thermocast/kpis` (Parameter `days`), `thermocast/export` | teure Rechnungen/Abfragen nur, wenn der Tab offen ist; kein Push nötig |
| Modellgüte-Datenbasis | Eigenes **Stunden-Log je Zone** (Eingänge + gemessene Temperatur + a-priori-Fehler), 14 Tage Ringpuffer, im Store | unabhängig vom Recorder (120 Tage Purge, Filterprobleme); exakt die Daten, mit denen gelernt wurde |
| „Modell“ vs. „Prognose“ | Zwei getrennte Gütemaße: **Hindcast** (aktuelles Modell simuliert die Vergangenheit mit *gemessenen* Eingängen, Neustart jede Mitternacht) und **operative Prognose** (gespeicherte Rollout-Vorhersagen für Horizonte 1/3/6/12/24 h vs. Messung) | trennt Modellfehler von Wetter-/Planfehlern |
| Kalibrierung | Anteil der Messungen innerhalb ±1σ der operativen Prognose (Soll ≈ 68 %) | prüft, ob die Sicherheitsmarge stimmt |
| Parameter-Historie | Tägliche Snapshots von θ und Diagonale von P je Zone, 30 Tage | Konvergenz sichtbar |
| Vergangene Ursachen | aus dem Hindcast (Zerlegung mit gemessenen Eingängen) | gleiche Mathematik wie in A |
| „Plan von gestern vs. Ist“ | in der Zeitachse (Tab A) gepunktete Linie „Prognose von vor 6 h“ für vergangene Stunden | direkte Sicht, wo die Prognose daneben lag |
| Warmwasser | optionale Entität „Warmwasser aktiv“ im Options-Flow; Schraffur in der Heizen-Bahn | erklärt Vorlaufspitzen |
| KPI-Quelle | **Recorder-Langzeitstatistiken** (stündlich, bleiben über `purge_keep_days` hinaus) | 90 Tage ohne riesige State-Abfragen |
| KPI-Entitäten | optional im Options-Flow: **Brennerstarts** (Zähler, `total_increasing`), **Heizenergie** (kWh, `total_increasing`) | EMS-ESP liefert beide; ohne sie entfallen die jeweiligen KPIs |
| Heizgradtage | Basis **15 °C** (Heizgrenze, wie VDI 3807 „Gradtagzahl 20/15“ vereinfacht): HGT = Σ max(0, 15 − T̄ₜₐ𝑔) | Option später möglich, YAGNI |
| Vorher/Nachher | Stichtag = erstes Einschalten der Steuerung (`control_since`, persistiert) | einfach, nachvollziehbar |
| Export | JSON mit Konfiguration, Modellzuständen, Logs, Ereignissen, aktuellem `view`, Modellgüte | Notebook-tauglich |

## 3. Bedienoberfläche

### Tab „Modell“ (je Zone eine Karte, führende zuerst)
- **Kennzahlen (7 Tage):** 1-Schritt-MAE (K/h) und Bias, Hindcast-MAE (K), Prognose-MAE nach Horizont
  (1/3/6/12/24 h) mit Kalibrierung (±σ-Treffer %), Anzahl gelernter Stunden.
- **Diagramm 7 Tage:** gemessen, Hindcast (Neustart je Mitternacht), Prognose von vor 6 h;
  darunter Fehlerbalken Hindcast − Messung.
- **Parameter:** Zeitkonstante τ (h), Sonne je Fläche (K/h je kW/m²), Heizen (K/h bei typischem Block),
  Estrich-Verzögerung (Balken je Lag), Nachbarkopplung, innere Gewinne, Basis – jeweils mit ±1σ;
  Sparkline der letzten 30 Tage.
- **Export-Button** (oben im Tab): lädt `thermocast-export-<datum>.json` herunter.

### Tab „KPIs“
- **Kacheln:** Ø Brennerstarts/Tag, Ø kWh/HGT, Min. führende Zonen, Komfort-Unterschreitung (h) –
  jeweils für den gewählten Zeitraum und, falls vorhanden, *vorher* vs. *seit Steuerung*.
- **Zeitraum:** 14 / 30 / 90 Tage.
- **Diagramme:** Starts/Tag (Balken), kWh/HGT (Balken) + Außentemperatur (Linie), Min. Temperatur (Linie)
  mit Komfortgrenze; senkrechte Linie am Stichtag „Steuerung seit“.
- Fehlende Quellen → Hinweis, welche Entität im Options-Flow fehlt oder keine Statistik hat.

### Tab „Jetzt & Plan“ (Ergänzungen)
- Zonen-Bahn: gepunktete Linie „Prognose von vor 6 h“ in der Vergangenheit.
- „Warum?“: Ursachen-Balken auch für vergangene Stunden (Hindcast).
- Heizen-Bahn: Schraffur für Warmwasser-Ladungen.

## 4. Architektur

```
core/quality.py   HourLog (Ringpuffer), ForecastLog, ParamHistory, hindcast(), quality_metrics(), interpret()
core/kpi.py       daily_kpis(hourly stats → lokale Tage), summary(before/after)
history.py        + async_fetch_statistics() (Recorder-Langzeitstatistik, stündlich)
coordinator.py    loggt je Zone Stunde (in _close_hour), Param-Snapshot täglich, control_since, Logs im Store
view_builder.py   loggt operative Prognose (aus dem Rollout) je Stunde; hindcast/„vor 6 h“ in den view
model_view.py     baut Antwort für thermocast/model und thermocast/export
kpi_view.py       baut Antwort für thermocast/kpis
websocket_api.py  + thermocast/model, thermocast/kpis, thermocast/export
config_flow.py    Options: dhw_active_entity, burner_starts_entity, heat_energy_entity
frontend/         Tabs, tc-model.js, tc-kpis.js, tc-chart.js (kleine SVG-Linien/Balken)
```

### 4.1 Datenformate

**HourLog-Eintrag** (je Zone, je abgeschlossener Stunde):
`{"t": iso Stundenbeginn, "rec": HourRecord.to_dict(), "temp_next": float, "err": float|null}`
`rec.temp` = gemessen zu Stundenbeginn, `temp_next` = gemessen zu Stundenende, `err` = a-priori-Fehler
des RLS-Schritts (null, wenn nicht gelernt). Maximal 14·24 Einträge.

**ForecastLog** (je Zone): `{iso Ausgabestunde: {"1": [mean, std], "3": …, "6": …, "12": …, "24": …}}`,
Werte = Rollout-Vorhersage (mit geplanten Blöcken) für das **Ende** der Stunde `Ausgabe + k − 1`,
d. h. die Temperatur zu Beginn von Stunde `Ausgabe + k`. Maximal 14·24 Ausgabestunden.

**ParamHistory** (je Zone): `[{"date": iso, "theta": {name: v}, "std": {name: v}}]`, max. 30.

### 4.2 Hindcast
Für jedes zusammenhängende Stück des Logs (keine Lücke, Stunden lückenlos) wird ab jeder lokalen
Mitternacht (bzw. Stückanfang) mit der gemessenen Temperatur neu gestartet und mit den geloggten
Eingängen (`t_out`, `irr`, `q`, Nachbarn, Gewinne) simuliert; die Lag-Historie sind die vorherigen
Log-Einträge. Ergebnis je Stunde: simulierte Temperatur am Stundenende + Ursachen. Ungültige Stunden
(Fenster offen) werden simuliert, aber nicht in die Fehlermaße gezählt.

### 4.3 KPI-Aggregation
Stündliche Statistiken (`change` für Zähler, `mean`/`min` für Temperaturen) werden lokalen Tagen
zugeordnet (Zeitzone von HA, 23/25-h-Tage korrekt). Tagesmittel außen nur bei ≥ 18 Stunden Daten.
Min. führende Zonen = Minimum der stündlichen `min` der Temperatursensoren führender Zonen in deren
Komfortfenster. Unterschreitungsstunden = Stunden, deren Zonen-`mean` unter der Komfortuntergrenze liegt.
Der laufende Tag ist als „unvollständig“ markiert und fließt nicht in Mittelwerte ein.

### 4.4 Verträge

`thermocast/model` → `{"version": 1, "generated_at", "zones": [{"id","name","leads","heat_type",
"hours": [iso…], "measured": […], "hindcast": […], "forecast6": […], "error": […],
"metrics": {"n_learned","one_step_mae","one_step_bias","hindcast_mae","horizons": {"1": {"mae","bias","coverage","n"}, …}},
"params": [{"key","label","kind","value","std","unit","history": [[date, value], …]}],
"heat_lags": [{"lag","value"}], "contrib_past": […] }]}`

`thermocast/kpis` (`days`) → `{"version": 1, "days": [{"date","complete","starts","energy_kwh","t_out_mean","hdd",
"kwh_per_hdd","min_leading","below_comfort_h"}], "control_since": iso|null,
"summary": {"all": {...}, "before": {...}|null, "after": {...}|null}, "missing": ["burner_starts", …]}`

`thermocast/export` → `{"version": 1, "exported_at", "config", "options", "zones": {id: {"model", "log",
"forecast_log", "params"}}, "events", "view", "model_view"}`

`view` (A) Ergänzungen: `zones[].forecast6` (Prognose von vor 6 h, Vergangenheit), `zones[].contrib`
auch für Vergangenheit (Hindcast), `heating.dhw` (Anteil Warmwasser je Stunde).

## 5. Fehlerfälle
- Kein Log (frische Installation) → Modell-Tab zeigt „noch keine Daten“, Parameter trotzdem.
- Keine Statistik für eine KPI-Entität → `missing` mit Grund; restliche KPIs werden berechnet.
- Kein Recorder → KPIs `missing: ["recorder"]`.
- Alle Rechnungen in try/except; Fehler → WebSocket-Fehler mit Text, Regelung unberührt.

## 6. Tests
Kern: Ringpuffer-Grenzen, Hindcast exakt bei perfektem Modell und Neustart je Mitternacht, Horizont-Metriken
mit bekannten Zahlen, Kalibrierung, Parameter-Interpretation (τ, Lags), KPI-Tagesaggregation inkl.
Zeitumstellung und Zähler-Resets (negative change → 0), HGT, Vorher/Nachher.
HA: Options-Flow mit neuen Entitäten, Stunden-Log + Persistenz nach Stundenabschluss, `thermocast/model`,
`thermocast/kpis` mit importierten Statistiken (Recorder-Fixture), `thermocast/export`, DHW im view.
Frontend: Dev-Seite um Beispiel-Antworten für Modell/KPIs erweitert, Screenshots hell/dunkel/schmal.

## 7. Umsetzungsnotizen (Abweichungen von diesem Design)

- `thermocast/model` enthält **kein** `contrib_past` – vergangene Ursachen stehen im `view` (Tab A, „Warum?“),
  eine zweite Kopie im Modell-Tab war überflüssig.
- Die Modellgüte-Aufbereitung liegt als `zone_quality()` in `core/quality.py` (HA-frei); `model_view.py`
  kopiert nur die Eingaben im Event-Loop (Thread-Sicherheit) und ruft sie im Executor auf.
- `params[].label` ist für τ/Heizen/Basis `null` – Texte kommen aus dem Frontend (de/en).
- KPI-`missing`-Einträge haben die Form `<quelle>:not_configured` bzw. `<quelle>:no_statistics:<entity>`;
  „keine Statistik“ tritt auch bei frischen Installationen auf, bis HA die erste volle Stunde kompiliert hat.
- Befund aus den synthetischen Daten: die ±σ-Abdeckung der operativen Prognose liegt ab 3 h Horizont deutlich
  unter 68 % (σ kennt keine Wetterprognose-Fehler). Kandidat für eine spätere Kalibrierung der Sicherheitsmarge.
