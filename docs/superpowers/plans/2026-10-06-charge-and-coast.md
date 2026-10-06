# Laden & Zehren (Stufe 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wenige lange Brennerläufe: Planer mit Obergrenze/Grundwert/Starts-Regler, BT-Sollwert-Aktor je Zone mit Ruhezeiten, Panel-Anzeige.

**Architecture:** Kern (`core/planner.py`, `core/rollout.py`, neu `core/bt.py`) bleibt HA-frei; der Planer bekommt pro Zone eine
effektive Untergrenze (`comfort_low`: Komfort oder Grundwert), eine Obergrenze (`comfort_high`) und eine Lade-Deckelung
(`charge_cap`). Ein neuer HA-Aktor (`zone_actuator.py`) setzt BT-Sollwerte aus einer reinen Entscheidungsfunktion. Das Panel
bekommt additive Felder im View-Vertrag v1.

**Tech Stack:** Python 3.14, Home Assistant 2026.9, numpy, pytest + pytest-homeassistant-custom-component, Lit (vendort).

**Spec:** `docs/superpowers/specs/2026-10-06-charge-and-coast-design.md`

## Global Constraints

- `core/` ohne HA-Import; numpy-Arbeit im Executor.
- Fail-safe-Richtung = Heizen erlaubt; BT-Fail-safe = aktuelle Untergrenze.
- BT-Sollwerte nur in 0,5-K-Schritten, hart 15–24 °C, max. 24 Änderungen je Zone und Tag.
- Ruhezeit: 15 min vorher einmal Grundwert, danach keine Schreibvorgänge (Ausnahme: führende Zone < Grundwert − 1 K).
- Beobachtungsmodus schreibt nie.
- View-Vertrag bleibt Version 1 (nur additive Felder); Texte nur in `frontend/i18n.js`.
- Bestehende Zonen ohne neue Felder müssen unverändert laufen; Modelle werden nicht zurückgesetzt.
- Rollout-Laufzeit im bestehenden Budget-Test (< 2 s lokal, < 5 s CI).
- Repo öffentlich: keine Haus-/Familiendetails in versionierten Dateien.

## Review Focus

- BT-Entität `unavailable`/ohne `temperature`-Attribut → Zone wird nicht angefasst, kein Fehler (Task 3 Test).
- Ruhezeit über Mitternacht (19:00–07:00) → korrekt erkannt inkl. 15-min-Vorlauf (Task 2/3 Test).
- Manuelle Änderung während des Beobachtungsmodus → keine Übersteuerungs-Erkennung (Task 3 Test).
- Zone mit `bt_control` aber leerem `bt_entity` → Validierungsfehler im Config Flow (Task 2 Test).
- Obergrenze ≤ Komfort → Validierungsfehler (Task 2 Test).

---

### Task 1: Planer – Untergrenze/Obergrenze/Deckelung + Kosten mit Regler

**Files:** Modify `core/planner.py`, `core/rollout.py`; Test `tests/test_planner.py`, `tests/test_rollout.py`

**Interfaces – Produces:**
- `ZonePlanInput.comfort_high: list[float | None] = []` (leer = keine Obergrenze), `ZonePlanInput.charge_cap: list[float | None] = []`
  (leer/None = ungedeckelt; sonst q=0 in Stunden, deren Prognose den Deckel erreicht).
- `CostFn = Callable[[Candidate, float, float], dict[str, float]]` – `(candidate, violation_kh, overheat_kh)`.
- `default_cost(candidate, comfort_violation, overheat=0.0)` (unverändert in der Wirkung).
- `charge_cost(weight: float) -> CostFn` mit `weight ∈ [0, 1]`: comfort 20·v, overheat 4·o, start 1+9w, energy (0.5−0.35w)·len, delay 0.01·start.
- `DEFAULT_BLOCK_LENGTHS = (2, 3, 4, 6, 8, 10, 12)`.
- `ZonePlanInput.heating_records(cand) -> list[HourRecord]` (mit Deckelung, max. 3 Iterationen).
- Rollout: Simulation setzt q=0, wenn `charge_cap[h]` gesetzt und Temperatur ≥ Deckel.

- [ ] Tests: Deckelung (Prognose mit Block bleibt ≤ Deckel + 0,2 K, ohne Deckel darüber); Überschreitung zählt in `parts["overheat"]`;
  `charge_cost(0.8)` → im synthetischen Haus bei 8 °C ≤ 2 Blöcke/Tag im Rollout, `charge_cost(0.0)` mehr/kürzere Blöcke; alte
  Tests mit `default_cost` grün.
- [ ] Implementieren, `uv run pytest tests/test_planner.py tests/test_rollout.py` grün, Commit.

### Task 2: Zonenfelder, Optionen, Migration, Planer-Eingaben

**Files:** Modify `const.py`, `config_flow.py`, `coordinator.py`, `strings.json`, `translations/*.json`; Test `tests/test_config_flow.py`, `tests/test_init.py`

**Interfaces – Produces:**
- Konstanten: `CONF_COMFORT_HIGH="comfort_high"`, `CONF_BASE_TEMP="base_temp"`, `CONF_BT_CONTROL="bt_control"`,
  `CONF_BT_ENTITY="bt_entity"`, `CONF_QUIET_FROM="quiet_from"`, `CONF_QUIET_TO="quiet_to"`, Option `CONF_STARTS_WEIGHT="starts_weight"`
  (Standard 80), `QUIET_LEAD = timedelta(minutes=15)`.
- `coordinator.zone_high(z) -> float` (Feld oder Komfort + 1), `zone_base(z) -> float` (Feld oder Komfort − 2),
  `zone_floor(z, when) -> float` (Komfort − Band in Komfortzeit, sonst Grundwert), `zone_quiet(z, when, lead=timedelta(0)) -> bool`,
  `zone_charge_cap(z, when) -> float | None` (nur `bt_control`: Ruhezeit → Grundwert, sonst Obergrenze).
- `build_plan_inputs` füllt `comfort_low` mit `zone_floor`, `comfort_high`, `charge_cap`.
- Planer-Aufrufe (Coordinator + ViewBuilder) mit `cost_fn=charge_cost(weight)`.
- Validierung `_clean`: `invalid_bounds` (Grundwert ≤ Komfort − Band, Obergrenze > Komfort), `bt_entity_missing`.

- [ ] Tests: Flow mit neuen Feldern, beide Fehler; alte Zone ohne Felder → Defaults; Ruhezeit über Mitternacht; Optionen mit Regler.
- [ ] Implementieren, volle Suite grün, Commit.

### Task 3: BT-Aktor

**Files:** Create `core/bt.py`, `zone_actuator.py`; Modify `coordinator.py` (Store, Update, Shutdown, Steuerung aus), `events` i18n;
Test `tests/test_bt.py` (Kern), `tests/test_zone_actuator.py` (HA)

**Interfaces – Produces:**
- `core.bt.BtInput` (dataclass: block_on, failsafe, floor_now, high, base, quiet_now, quiet_soon, temp, leads, current_target,
  last_written, writes_today, override_active) und `bt_decide(inp) -> tuple[float | None, str]` – Ziel (oder None = nicht schreiben) +
  Grund-Code (`write`, `unchanged`, `quiet`, `override`, `budget`, `no_temperature`, `unavailable`).
- `round_target(x) -> float` (0,5 K, 15–24 °C).
- `zone_actuator.ZoneActuator(hass, events)`: `async_apply(zones, block_on, now, enabled, failsafe, block_end) -> dict[zone_id, dict]`
  (`target`, `reason`, `override_until`), `async_failsafe(zones, now)`, `to_dict()/load()`.
- `ThermocastData.bt: dict[str, dict]`.

- [ ] Kern-Tests: Block → Obergrenze; außerhalb → Untergrenze; Fail-safe → Untergrenze; Ruhezeit (Vorlauf schreibt Grundwert, in
  Ruhezeit nichts, Ausnahme führend & < Grundwert − 1); unverändert → kein Schreiben; Budget; Rundung/Grenzen.
- [ ] HA-Tests: schreibt `climate.set_temperature` nur bei Änderung; Übersteuerung erkannt (Ruhe bis max(Blockende, +3 h));
  Beobachtungsmodus schreibt nie und erkennt keine Übersteuerung; `unavailable` → nichts; Entladen → einmal Untergrenze.
- [ ] Implementieren, volle Suite grün, Commit.

### Task 4: Panel

**Files:** Modify `core/view.py`, `view_builder.py`, `frontend/tc-timeline.js`, `frontend/tc-summary.js`, `frontend/tc-days.js`,
`frontend/i18n.js`, `scripts/sample_view.py`; Test `tests/test_view.py`

**Interfaces – Produces (View v1, additiv):** je Zone `comfort_high`, `floor`, `quiet` (bool je Stunde), `bt_control`, `bt_target`
(Vergangenheit Ist aus Recorder-Attribut `temperature`, Zukunft geplant: Block → Deckel, sonst Untergrenze); `decision.bt`
(Ergebnis des Aktors); Tage `starts` (gemessen aus Brennerstart-Zähler), `starts_planned`, `over_high_max`.

- [ ] Tests in `test_view.py` für neue Felder; Frontend: Obergrenze-Linie + Band, BT-Treppenlinie, Ruhezeit-Schraffur, Tooltip,
  Story-Zusatz (Laden bis / übersteuert / Ruhezeit), Tageskarten; Sichtprüfung `frontend/dev/`.
- [ ] Commit.

### Task 5: Doku, Version, Release

**Files:** `README.md`, `docs/INSTALLATION-CHECKLISTE.md`, `CLAUDE.md`, `manifest.json`, `pyproject.toml`, `uv.lock`

- [ ] Checkliste: Freigabe Winter/Auto + Schwelle 10 °C; BT-Steuerung, Ruhezeiten, BT-Zeitpläne deaktivieren; Regler.
- [ ] CLAUDE.md Architektur/Status; Version 0.5.0; volle Suite + ruff + sample_view; Commit, Push, Release, CI abwarten.
