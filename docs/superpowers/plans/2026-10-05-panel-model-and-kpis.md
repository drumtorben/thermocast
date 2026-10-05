# Panel B + C (Modellgüte, KPIs) – Implementation Plan

> Autonom umgesetzt (Nutzer offline). Der Plan ist bewusst als Task-Liste mit Tests formuliert statt mit
> vollständigem Code je Schritt – es gab keinen Plan-Review vor der Umsetzung. Jede Task: Test zuerst,
> dann Code, `uv run pytest`, `uv run ruff check .`, Commit.

**Spec:** `docs/superpowers/specs/2026-10-05-panel-model-and-kpis-design.md`

## Global Constraints
- `core/` ohne HA-Import; Panel nie im Regelpfad; Fail-safe-Richtung unverändert.
- Texte nur in `frontend/i18n.js`; Backend liefert Codes + Zahlen.
- Neue Options-Entitäten optional – ohne sie läuft alles wie bisher.
- Store-Erweiterungen rückwärtskompatibel (fehlende Schlüssel = leer).

## Review Focus
- Lücke im Stunden-Log (HA-Neustart) → Hindcast startet neu, kein Sprung über die Lücke.
- Zähler-Reset (Brennerstarts nach Gateway-Tausch) → negative Tagesänderung wird 0, nicht −10 000.
- Zeitumstellungstag in den KPIs (23/25 h) → ein Tag, korrekte Summen.
- Sensor ohne `state_class` → keine Statistik → klarer Hinweis statt leerer Grafik.
- Sehr frische Installation (0 Log-Einträge) → Modell-Tab rendert ohne Fehler.

## Tasks
1. **core/quality.py – Logs + Hindcast + Metriken + Interpretation** (`tests/test_quality.py`)
   - `RingLog` (append, maxlen, to_list/load), `ForecastLog` (record(issue, horizons→(mean,std)), prune)
   - `hindcast(model, entries, tz)` → je Eintrag `mean`, `contrib`; Neustart je lokaler Mitternacht/Lücke
   - `forecast_metrics(forecast_log, measured_by_hour, horizons)`, `one_step_metrics(entries)`
   - `interpret(model, q_on)` → Parameterliste mit Einheit/σ; `heat_lag_profile(model)`
2. **Coordinator: Stunden-Log, Param-Historie, control_since** (`tests/test_init.py` erweitert)
   - `_close_hour` loggt `{t, rec, temp_next, err}`; täglicher Param-Snapshot; Persistenz; `control_since`
3. **View-Builder: operative Prognose loggen; A-Ergänzungen** (`tests/test_view_builder.py`, `test_view.py`)
   - Rollout-Trajektorien → ForecastLog (Horizonte 1/3/6/12/24)
   - `zones[].forecast6`, Hindcast-Ursachen für Vergangenheit, `heating.dhw`
4. **Options-Flow: dhw/burner_starts/heat_energy** (`tests/test_config_flow.py`), Übersetzungen en/de
5. **core/kpi.py + history.async_fetch_statistics + kpi_view.py** (`tests/test_kpi.py`, `tests/test_kpi_ha.py`)
6. **model_view.py + Export + WebSocket-Befehle** (`tests/test_panel.py` erweitert)
7. **Frontend: Tabs, tc-chart.js, tc-model.js, tc-kpis.js, Export-Download, A-Ergänzungen**;
   `scripts/sample_view.py` erzeugt zusätzlich `sample-model.json`, `sample-kpis.json`; Screenshots
8. **E2E in der Dev-Instanz** (Statistik-fähige Fake-Sensoren in `config/configuration.yaml`), Doku
