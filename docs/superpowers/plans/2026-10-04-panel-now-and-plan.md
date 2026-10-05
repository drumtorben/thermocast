# Thermocast Panel A („Gestern · Heute · Morgen“) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein eigenes HA-Seitenpanel, das zeigt, warum Thermocast gerade (nicht) heizt und welche Blockfolge bis morgen 24:00 geplant ist – inkl. Vergangenheit ab gestern 00:00.

**Architecture:** Alle Rechenlogik (Ursachen-Zerlegung, Kostenkomponenten, Regler-Rollout, Begründung, Stundenaggregation, View-JSON) liegt HA-frei in `core/`. Die HA-Schicht sammelt Zustände, Recorder-Historie und Ereignisse, baut das `view` getrennt vom Regelpfad und liefert es per WebSocket-Abo an ein Lit-Panel ohne Build-Schritt, das die Integration selbst registriert.

**Tech Stack:** Python ≥ 3.14.2, numpy, Home Assistant 2026.9 (`panel_custom`, `websocket_api`, `recorder`), pytest + pytest-homeassistant-custom-component, uv; Frontend: Plain-JS-ES-Module + vendortes Lit 3.3.1, SVG.

**Spec:** `docs/superpowers/specs/2026-10-04-panel-now-and-plan-design.md`

## Global Constraints

- `core/` importiert nie `homeassistant` (bleibt per pytest ohne HA testbar).
- Fail-safe-Richtung jeder Steuerfunktion = „Heizen erlaubt“; der `view`-Bau darf die Regelung nie beeinflussen.
- Keine harten Entitäts-IDs im Code – alles über Config Flow / Entity Registry.
- Zeitfenster fest **gestern 00:00 → morgen 24:00 Ortszeit** (HA-Zeitzone), 71/72/73 Stunden; Stunden immer explizit über `hours`, nie 24 h/Tag annehmen.
- `view`-Vertrag Version 1 wie Spec 4.4; alle Zeitreihen auf `hours` ausgerichtet, fehlende Werte `null`.
- Frontend: keine externen Requests/CDN zur Laufzeit, keine Bedienelemente (nur Ansicht), Texte de/en.
- Performance-Budget Rollout: < 2 s auf dem Entwicklungs-Mac für 4 Zonen und 52 Schritte.
- Ruff, Zeilenlänge 120, Python ≥ 3.14.2. Befehle immer mit `uv run …`.
- Commit-Messages englisch, enden mit `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **Zeitumstellungstag** (25.10.2026: 73 h, 29.03.2026: 71 h) – Fenster, Tageskarten und Achse müssen stimmen → Test in Task 7.
- **Prognose kürzer als das Fenster** (Open-Meteo liefert weniger Tage) – Rollout kürzt, Zukunft bleibt `null` statt Absturz → Tests in Task 4 und Task 7.
- **Kein Recorder / Zone ohne Historie** – Vergangenheit `null`, `errors: ["no_recorder"]`, Panel rendert trotzdem → Tests in Task 7 und Task 9.
- **Eintrag wird neu geladen (Zone geändert) während das Panel offen ist** – Panel bekommt `{"error": "unloaded"}` und abonniert neu → Test in Task 10, Verhalten in Task 11.
- **Ausnahme beim `view`-Bau** – Freigabe/Entitäten unverändert, `view = {"error": …}` → Test in Task 9.

---

## Datei-Übersicht

| Datei | Verantwortung |
|---|---|
| `core/model.py` (ändern) | `Prediction.contrib`, `predict(var0, history)`, `group_labels()` |
| `core/planner.py` (ändern) | Kosten als Komponenten, `ranked`, „mit Block“ für alle Zonen, `shortcut` |
| `core/rules.py` (neu) | `Rules`, `apply_rules`, `release_state`, `binary_value` |
| `core/rollout.py` (neu) | Regler-Rollout, `block_lengths_for` |
| `core/explain.py` (neu) | `explain`, `robustness`, `PlanSnapshot`, `snapshot_from`, `plan_change` |
| `core/series.py` (neu) | Stundenmittel/An-Anteil aus Zustandsfolgen |
| `core/view.py` (neu) | `Window`, `make_window`, `align`, `compute_outlook`, `build_view` |
| `actuator.py` (ändern) | nutzt `core.rules`, liefert `(Zustand, Grund)`, `on_write`-Callback |
| `events.py` (neu) | `EventLog`-Ringpuffer |
| `history.py` (neu) | Recorder-Abfrage → Zustandsfolgen |
| `view_builder.py` (neu) | sammelt Eingaben, baut/cached `view`, Planänderung |
| `coordinator.py` (ändern) | Plan-Inputs als Funktion, Ereignisse, Override, `view` |
| `websocket_api.py` (neu) | `thermocast/subscribe` |
| `panel.py` (neu) | Static-Path, Panel registrieren/entfernen |
| `__init__.py`, `manifest.json` (ändern) | `async_setup`, Panel, Abhängigkeiten |
| `frontend/*.js`, `frontend/dev/*` (neu) | Panel-UI, Dev-Seite |
| `scripts/sample_view.py` (neu) | Beispiel-`view` aus synthetischen Daten |

---

### Task 1: Ursachen-Zerlegung und Startvarianz im Modell

**Files:**
- Modify: `custom_components/thermocast/core/model.py`
- Create: `tests/core_helpers.py`
- Test: `tests/test_model_contrib.py`

**Interfaces:**
- Produces: `Prediction(mean, std, contrib: list[dict[str, float]])`; `OnlineZoneModel.predict(temp_now, future, var0=0.0, history=None) -> Prediction`; `OnlineZoneModel.groups: list[str]` (Gruppe je Parameter); `OnlineZoneModel.group_labels() -> dict[str, str]`; Gruppenschlüssel `base`, `loss`, `sun:<key>:<kind>`, `heat`, `neighbor:<i>`, `gain:<i>`.
- Produces (Tests): `tests/core_helpers.py` mit `SPEC`, `record(sim, i)`, `trained_model(seed=4, days=30)`, `future(t_out, hours, irr=None)`.

- [ ] **Step 1: Test-Helfer anlegen**

`tests/core_helpers.py`:
```python
"""Shared helpers for HA-free core tests (same import style as test_core.py)."""
from __future__ import annotations

import os
import sys
from functools import lru_cache

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "thermocast"))

from core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec

from .synthetic import simulate

SPEC = ZoneSpec(
    heat_type="fbh",
    surfaces=(
        SurfaceSpec(kind="window", azimuth=90, tilt=90, name="Fenster Ost"),
        SurfaceSpec(kind="roof", azimuth=180, tilt=40, name="Schräge Süd"),
    ),
)


def record(sim, i: int) -> HourRecord:
    return HourRecord(
        temp=float(sim["air"][i]),
        t_out=float(sim["t_out"][i]),
        irr={k: float(v[i]) for k, v in sim["irr"].items()},
        q=float(sim["q"][i]),
    )


@lru_cache
def _trained_state(seed: int, days: int) -> dict:
    sim = simulate(days=days, seed=seed)
    m = OnlineZoneModel(SPEC)
    for i in range(days * 24 - 1):
        m.update(record(sim, i), float(sim["air"][i + 1]))
    return m.to_dict()


def trained_model(seed: int = 4, days: int = 30) -> OnlineZoneModel:
    m = OnlineZoneModel(SPEC)
    assert m.load_dict(_trained_state(seed, days))
    return m


def future(t_out: float, hours: int, irr: dict[str, float] | None = None) -> list[HourRecord]:
    return [HourRecord(temp=0.0, t_out=t_out, irr=dict(irr or {}), q=0.0) for _ in range(hours)]
```

- [ ] **Step 2: Failing tests schreiben**

`tests/test_model_contrib.py`:
```python
"""Contribution decomposition, start variance and external history of predict()."""
from __future__ import annotations

from dataclasses import replace

import pytest

from .core_helpers import SPEC, future, trained_model


def test_contributions_sum_to_temperature_change():
    m = trained_model()
    fut = future(5.0, 24, irr={"90_90": 300.0, "40_180": 500.0})
    fut = [replace(r, q=12.0 if 3 <= i < 7 else 0.0) for i, r in enumerate(fut)]
    pred = m.predict(20.5, fut)
    prev = 20.5
    assert len(pred.contrib) == 24
    for mean, contrib in zip(pred.mean, pred.contrib):
        assert sum(contrib.values()) == pytest.approx(mean - prev, abs=1e-9)
        prev = mean


def test_contribution_groups():
    m = trained_model()
    pred = m.predict(20.0, future(5.0, 2, irr={"90_90": 300.0, "40_180": 500.0}))
    assert set(pred.contrib[0]) == {"base", "loss", "sun:90_90:window", "sun:40_180:roof", "heat"}
    assert m.group_labels() == {"sun:90_90:window": "Fenster Ost", "sun:40_180:roof": "Schräge Süd"}


def test_var0_raises_std_and_default_unchanged():
    m = trained_model()
    fut = future(5.0, 6)
    base = m.predict(20.0, fut)
    assert base.std == m.predict(20.0, fut, var0=0.0).std
    wider = m.predict(20.0, fut, var0=0.25)
    assert all(w > b for w, b in zip(wider.std, base.std))


def test_external_history_does_not_touch_model():
    m = trained_model()
    before = list(m.history)
    other = [replace(r, q=20.0) for r in before]
    a = m.predict(20.0, future(5.0, 6))
    b = m.predict(20.0, future(5.0, 6), history=other)
    assert m.history == before
    assert a.mean != b.mean  # FBH heat lags see the hot history


def test_unnamed_surface_label():
    from core.model import OnlineZoneModel, SurfaceSpec, ZoneSpec

    m = OnlineZoneModel(ZoneSpec(surfaces=(SurfaceSpec(kind="wall", azimuth=270, tilt=90),)))
    assert m.group_labels() == {"sun:90_270:wall": "wall 270°/90°"}
    assert SPEC.heat_type == "fbh"
```

- [ ] **Step 3: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_model_contrib.py -q`
Expected: FAIL (`Prediction` hat kein `contrib`, `predict` kennt `var0`/`history` nicht, `group_labels` fehlt).

- [ ] **Step 4: Implementierung in `core/model.py`**

`Prediction` ersetzen:
```python
@dataclass
class Prediction:
    mean: list[float]
    std: list[float]
    contrib: list[dict[str, float]] = field(default_factory=list)  # per hour: change by cause (K/h)

    def lower(self, z: float = 1.0) -> list[float]:
        return [m - z * s for m, s in zip(self.mean, self.std)]
```

Vor `class OnlineZoneModel` einfügen:
```python
def _group_of(name: str) -> str:
    """Parameter name -> display group (all lags of one surface/heating share a group)."""
    if name == "bias":
        return "base"
    if name.startswith("solar:"):
        _, key, kind, _lag = name.split(":")
        return f"sun:{key}:{kind}"
    if name.startswith("heat:"):
        return "heat"
    return name  # loss, neighbor:<i>, gain:<i>
```

Am Ende von `_build_layout` ergänzen:
```python
        self.groups = [_group_of(n) for n in names]
```

`predict` ersetzen:
```python
    def predict(
        self,
        temp_now: float,
        future: list[HourRecord],
        var0: float = 0.0,
        history: list[HourRecord] | None = None,
    ) -> Prediction:
        """Roll the model forward. ``future[i].temp`` is ignored (simulated).

        ``var0`` is the variance of ``temp_now`` (rollouts carry uncertainty across re-plans);
        ``history`` replaces the model's own lag history without modifying it.
        """
        hist = list(self.history if history is None else history)
        keep = max(self.spec.max_lag, 1)
        temp = temp_now
        means: list[float] = []
        stds: list[float] = []
        contribs: list[dict[str, float]] = []
        var = var0
        sigma2 = self.resid_var
        for rec in future:
            r = HourRecord(
                temp=temp,
                t_out=rec.t_out,
                irr=rec.irr,
                q=rec.q,
                neighbors=rec.neighbors,
                gains=rec.gains,
            )
            phi = self.features(r, hist)
            terms = phi * self.theta
            contrib: dict[str, float] = {}
            for group, value in zip(self.groups, terms):
                contrib[group] = contrib.get(group, 0.0) + float(value)
            temp = temp + float(terms.sum())
            var += sigma2 + float(phi @ self.P @ phi) * sigma2
            means.append(temp)
            stds.append(math.sqrt(var))
            contribs.append(contrib)
            hist.append(r)
            if len(hist) > keep:
                hist = hist[-keep:]
        return Prediction(mean=means, std=stds, contrib=contribs)
```

Nach `solar_response` ergänzen:
```python
    def group_labels(self) -> dict[str, str]:
        """Display names of the surface groups (name from the config, else kind + orientation)."""
        labels: dict[str, str] = {}
        for s in self.spec.surfaces:
            labels.setdefault(f"sun:{s.key}:{s.kind}", s.name or f"{s.kind} {round(s.azimuth)}°/{round(s.tilt)}°")
        return labels
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/test_model_contrib.py tests/test_core.py -q`
Expected: PASS (alle; `test_core.py` unverändert grün).

- [ ] **Step 6: Commit**

```bash
git add custom_components/thermocast/core/model.py tests/core_helpers.py tests/test_model_contrib.py
git commit -m "feat(core): per-cause contributions, start variance and external history in predict

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Planer mit Kostenkomponenten, Rangliste und Abkürzung

**Files:**
- Modify: `custom_components/thermocast/core/planner.py` (komplett ersetzen)
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `OnlineZoneModel.predict(temp_now, future, var0, history)` (Task 1).
- Produces:
  - `ZonePlanInput(name, model, temp_now, future, comfort_low, q_on=10.0, leads_release=True, var0=0.0, history=None)` mit Methode `predict(records) -> Prediction`.
  - `CostFn = Callable[[Candidate, float], dict[str, float]]`; `default_cost` liefert `{"comfort", "start", "energy", "delay"}`.
  - `ScoredCandidate(candidate, cost, parts, violation, preds: dict[str, Prediction])`.
  - `PlanResult(best, cost, free_run, planned, violation_free, ranked: list[ScoredCandidate])`, `planned` für **alle** Zonen.
  - `plan(zones, block_lengths=(2,3,4,6,8), start_step=1, z=1.0, cost_fn=default_cost, shortcut=False) -> PlanResult`.
  - `violations(pred, comfort_low, z) -> list[tuple[int, float]]` (Index, Defizit in K).

- [ ] **Step 1: Failing tests schreiben**

`tests/test_planner.py`:
```python
"""Planner: cost components, ranking, all-zone planned predictions, shortcut."""
from __future__ import annotations

from core.planner import Candidate, ZonePlanInput, default_cost, plan, violations

from .core_helpers import future, trained_model


def _zone(name: str, t_out: float, temp_now: float, leads: bool = True) -> ZonePlanInput:
    return ZonePlanInput(
        name=name, model=trained_model(), temp_now=temp_now, future=future(t_out, 24),
        comfort_low=[20.0] * 24, q_on=15.0, leads_release=leads,
    )


def test_default_cost_components_match_old_scalar():
    parts = default_cost(Candidate(3, 4), 0.5)
    assert parts == {"comfort": 10.0, "start": 1.0, "energy": 0.6000000000000001, "delay": 0.03}
    assert default_cost(Candidate(None), 0.0) == {"comfort": 0.0, "start": 0.0, "energy": 0.0, "delay": 0.0}


def test_ranked_is_sorted_and_best_first():
    res = plan([_zone("eg", -5.0, 20.3)])
    costs = [s.cost for s in res.ranked]
    assert costs == sorted(costs)
    assert res.ranked[0].candidate == res.best
    assert res.cost == res.ranked[0].cost
    assert sum(res.ranked[0].parts.values()) == res.cost
    assert any(s.candidate.start is None for s in res.ranked)


def test_planned_covers_following_zones():
    res = plan([_zone("eg", -5.0, 20.3), _zone("eltern", -5.0, 19.0, leads=False)])
    assert res.best.start is not None
    assert set(res.planned) == {"eg", "eltern"}
    assert res.planned["eltern"].mean != res.free_run["eltern"].mean


def test_shortcut_identical_when_no_violation():
    zones = [_zone("eg", 18.0, 21.5)]
    full, short = plan(zones), plan(zones, shortcut=True)
    assert full.best == short.best == Candidate(None)
    assert full.cost == short.cost
    assert len(short.ranked) == 1


def test_shortcut_ignored_when_violation():
    zones = [_zone("eg", -5.0, 20.3)]
    assert plan(zones, shortcut=True).best == plan(zones).best


def test_violations_lists_index_and_deficit():
    res = plan([_zone("eg", -5.0, 20.3)])
    v = violations(res.free_run["eg"], [20.0] * 24, 1.0)
    assert v and v[0][0] >= 0 and all(d > 0 for _, d in v)
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_planner.py -q`
Expected: FAIL (`violations` nicht vorhanden, `default_cost` liefert float).

- [ ] **Step 3: `core/planner.py` komplett ersetzen**

```python
"""Heating block planner: brute-force search over block start × length.

Heating "blocks" exploit the thermal mass of the screed: an oversized boiler runs
long and rarely instead of short-cycling. The cost function is pluggable so that
later a heat pump can optimise for COP / electricity price / PV surplus.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .model import HourRecord, OnlineZoneModel, Prediction


@dataclass
class ZonePlanInput:
    name: str  # stable id (subentry id in Home Assistant)
    model: OnlineZoneModel
    temp_now: float
    future: list[HourRecord]  # forecast inputs with q = 0
    comfort_low: list[float | None]  # lower comfort bound per hour, None = don't care
    q_on: float = 10.0  # typical heating proxy while a block runs
    leads_release: bool = True
    var0: float = 0.0  # variance of temp_now (rollout)
    history: list[HourRecord] | None = None  # lag history override (rollout)

    def predict(self, records: list[HourRecord]) -> Prediction:
        return self.model.predict(self.temp_now, records, var0=self.var0, history=self.history)


@dataclass
class Candidate:
    start: int | None  # hour offset, None = no block
    length: int = 0

    def active(self, hour: int) -> bool:
        return self.start is not None and self.start <= hour < self.start + self.length


@dataclass
class ScoredCandidate:
    candidate: Candidate
    cost: float
    parts: dict[str, float]
    violation: float  # K·h below comfort (leading zones, lower bound)
    preds: dict[str, Prediction]  # leading zones with this candidate


@dataclass
class PlanResult:
    best: Candidate
    cost: float
    free_run: dict[str, Prediction] = field(default_factory=dict)  # without heating
    planned: dict[str, Prediction] = field(default_factory=dict)  # all zones with the chosen block
    violation_free: dict[str, float] = field(default_factory=dict)
    ranked: list[ScoredCandidate] = field(default_factory=list)  # cheapest first

    @property
    def heat_now(self) -> bool:
        return self.best.active(0)


CostFn = Callable[[Candidate, float], dict[str, float]]


def default_cost(candidate: Candidate, comfort_violation: float) -> dict[str, float]:
    """Gas boiler: comfort first, then few starts, then little energy."""
    parts = {"comfort": 20.0 * comfort_violation, "start": 0.0, "energy": 0.0, "delay": 0.0}
    if candidate.start is not None:
        parts["start"] = 1.0  # one burner start-up phase
        parts["energy"] = 0.15 * candidate.length
        parts["delay"] = 0.01 * candidate.start  # prefer late starts slightly (less loss)
    return parts


def violations(pred: Prediction, comfort_low: list[float | None], z: float) -> list[tuple[int, float]]:
    """(hour index, deficit K) where the lower bound falls below comfort."""
    return [(i, low - lb) for i, (low, lb) in enumerate(zip(comfort_low, pred.lower(z))) if low is not None and lb < low]


def _violation(pred: Prediction, comfort_low: list[float | None], z: float) -> float:
    return sum(d for _, d in violations(pred, comfort_low, z))


def _with_block(zone: ZonePlanInput, cand: Candidate) -> list[HourRecord]:
    return [replace(rec, q=zone.q_on if cand.active(i) else 0.0) for i, rec in enumerate(zone.future)]


def plan(
    zones: list[ZonePlanInput],
    block_lengths: tuple[int, ...] = (2, 3, 4, 6, 8),
    start_step: int = 1,
    z: float = 1.0,
    cost_fn: CostFn = default_cost,
    shortcut: bool = False,
) -> PlanResult:
    """Choose the cheapest block. ``shortcut`` skips the search when no leading zone is violated
    without heating – valid for cost functions where an unneeded block never pays off (default_cost)."""
    leading = [zz for zz in zones if zz.leads_release]
    horizon = min((len(zz.future) for zz in zones), default=0)

    free = {zz.name: zz.predict(zz.future) for zz in zones}
    free_violation = {zz.name: _violation(free[zz.name], zz.comfort_low, z) for zz in zones}

    if shortcut and sum(free_violation[zz.name] for zz in leading) == 0.0:
        none = Candidate(None)
        parts = cost_fn(none, 0.0)
        scored = ScoredCandidate(none, sum(parts.values()), parts, 0.0, {zz.name: free[zz.name] for zz in leading})
        return PlanResult(
            best=none, cost=scored.cost, free_run=free, planned=dict(free),
            violation_free=free_violation, ranked=[scored],
        )

    candidates = [Candidate(None)]
    for length in block_lengths:
        for start in range(0, max(horizon - length + 1, 0), start_step):
            candidates.append(Candidate(start, length))

    scored_all: list[ScoredCandidate] = []
    for cand in candidates:
        preds: dict[str, Prediction] = {}
        violation = 0.0
        for zz in leading:
            pred = free[zz.name] if cand.start is None else zz.predict(_with_block(zz, cand))
            preds[zz.name] = pred
            violation += _violation(pred, zz.comfort_low, z)
        parts = cost_fn(cand, violation)
        scored_all.append(ScoredCandidate(cand, sum(parts.values()), parts, violation, preds))

    ranked = sorted(scored_all, key=lambda s: s.cost)  # stable: ties keep search order
    best = ranked[0]
    planned: dict[str, Prediction] = {}
    for zz in zones:
        if best.candidate.start is None:
            planned[zz.name] = free[zz.name]
        elif zz.name in best.preds:
            planned[zz.name] = best.preds[zz.name]
        else:
            planned[zz.name] = zz.predict(_with_block(zz, best.candidate))
    return PlanResult(
        best=best.candidate, cost=best.cost, free_run=free, planned=planned,
        violation_free=free_violation, ranked=ranked,
    )
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest -q`
Expected: PASS (alle, inkl. HA-Tests – Coordinator nutzt nur `best`, `free_run`, `violation_free`).

- [ ] **Step 5: Commit**

```bash
git add custom_components/thermocast/core/planner.py tests/test_planner.py
git commit -m "feat(core): planner cost components, ranked candidates, all-zone plans, shortcut

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Aktor-Regeln als reine Funktionen, Abweichungsgrund

**Files:**
- Create: `custom_components/thermocast/core/rules.py`
- Modify: `custom_components/thermocast/actuator.py`, `custom_components/thermocast/coordinator.py`
- Test: `tests/test_rules.py`, `tests/test_init.py`

**Interfaces:**
- Produces (core): `Rules(min_block_h=3.0, min_pause_h=2.0, max_switches=12)`; `apply_rules(want, current, elapsed_h, switches_today, rules) -> tuple[bool, str | None]` mit Grund `None|"min_block"|"min_pause"|"budget"`; `release_state(value, domain, on_value, off_value) -> bool | None`; `binary_value(value) -> float | None`.
- Produces (HA): `Actuator.rules -> Rules`; `Actuator.elapsed_h(now) -> float` (`math.inf` ohne Wechsel); `Actuator.async_apply(...) -> tuple[bool, str | None]` (`"observe"` im Beobachtungsmodus); `Actuator.on_write: Callable[[bool], None] | None`; `ThermocastData.override: str | None`, `ThermocastData.planner_heat: bool | None`.

- [ ] **Step 1: Failing tests schreiben**

`tests/test_rules.py`:
```python
"""Pure actuator rules shared by the live actuator and the rollout."""
from __future__ import annotations

import math

from core.rules import Rules, apply_rules, binary_value, release_state

from . import core_helpers  # noqa: F401  (sets sys.path for `core`)

R = Rules(min_block_h=3, min_pause_h=2, max_switches=4)


def test_turn_on_allowed_after_pause():
    assert apply_rules(True, False, math.inf, 0, R) == (True, None)
    assert apply_rules(True, False, 1.0, 0, R) == (False, "min_pause")


def test_turn_off_respects_min_block_and_budget():
    assert apply_rules(False, True, 1.0, 0, R) == (True, "min_block")
    assert apply_rules(False, True, 5.0, 4, R) == (True, "budget")
    assert apply_rules(False, True, 5.0, 3, R) == (False, None)


def test_turn_on_ignores_budget():
    assert apply_rules(True, False, 5.0, 99, R) == (True, None)


def test_release_state():
    assert release_state("16.0", "number", "16", "10") is True
    assert release_state("10", "input_number", "16", "10") is False
    assert release_state("12", "number", "16", "10") is None
    assert release_state("winter", "select", "winter", "summer") is True
    assert release_state("auto", "select", "winter", "summer") is None
    assert release_state("on", "switch", None, None) is True
    assert release_state("unavailable", "switch", None, None) is None
    assert release_state(None, "switch", None, None) is None


def test_binary_value():
    assert binary_value("on") == 1.0
    assert binary_value("off") == 0.0
    assert binary_value("42") == 1.0
    assert binary_value("0") == 0.0
    assert binary_value("unknown") is None
```

`core_helpers` muss vor `core.rules` importiert sein, damit `sys.path` stimmt. Deshalb die Import-Reihenfolge in `tests/test_rules.py` so ändern, dass `from . import core_helpers` **vor** `from core.rules import …` steht (Ruff-Isort: Datei-Kopf `# ruff: noqa: I001` ergänzen).

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_rules.py -q`
Expected: FAIL (`ModuleNotFoundError: core.rules`).

- [ ] **Step 3: `core/rules.py` anlegen**

```python
"""Actuator rules as pure functions (shared by the live actuator and the rollout)."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rules:
    min_block_h: float = 3.0
    min_pause_h: float = 2.0
    max_switches: int = 12


def apply_rules(
    want: bool, current: bool, elapsed_h: float, switches_today: int, rules: Rules
) -> tuple[bool, str | None]:
    """Turning ON is always allowed except during the minimum pause (anti short-cycling);
    turning OFF needs the minimum block and budget left (fail-safe direction = heating allowed)."""
    if current and not want:
        if elapsed_h < rules.min_block_h:
            return True, "min_block"
        if switches_today >= rules.max_switches:
            return True, "budget"
    elif not current and want and elapsed_h < rules.min_pause_h:
        return False, "min_pause"
    return want, None


def release_state(value: str | None, domain: str, on_value: str | None, off_value: str | None) -> bool | None:
    """Interpret a state of the release entity: True = heating allowed, None = unknown."""
    if value is None or value in ("unknown", "unavailable"):
        return None
    if domain in ("select", "input_select"):
        if value == on_value:
            return True
        if value == off_value:
            return False
        return None
    if domain in ("number", "input_number"):
        try:
            v, on_v, off_v = float(value), float(on_value), float(off_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if abs(v - on_v) < 1e-6:
            return True
        if abs(v - off_v) < 1e-6:
            return False
        return None
    return value == "on"


def binary_value(value: str | None) -> float | None:
    """on/off or numeric (> 0 = on) state -> 1.0 / 0.0, None if unknown."""
    if value == "on":
        return 1.0
    if value == "off":
        return 0.0
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (1.0 if v > 0 else 0.0) if math.isfinite(v) else None
```

- [ ] **Step 4: `actuator.py` auf `core.rules` umstellen**

Imports ergänzen/ändern:
```python
from collections.abc import Callable
from datetime import date, datetime
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MAX_SWITCHES,
    CONF_MIN_BLOCK_H,
    CONF_MIN_PAUSE_H,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
    DEFAULT_MAX_SWITCHES,
    DEFAULT_MIN_BLOCK_H,
    DEFAULT_MIN_PAUSE_H,
)
from .core.rules import Rules, apply_rules, release_state
```

In `__init__` ergänzen:
```python
        self.on_write: Callable[[bool], None] | None = None  # event hook
```

`entity_is_on` ersetzen:
```python
    def entity_is_on(self) -> bool | None:
        state = self.hass.states.get(self.entity_id)
        return release_state(
            state.state if state else None,
            self.entity_id.split(".")[0],
            self.entry.data.get(CONF_RELEASE_ON),
            self.entry.data.get(CONF_RELEASE_OFF),
        )
```

Nach `entity_id` ergänzen:
```python
    @property
    def rules(self) -> Rules:
        return Rules(
            min_block_h=self._opt(CONF_MIN_BLOCK_H, DEFAULT_MIN_BLOCK_H),
            min_pause_h=self._opt(CONF_MIN_PAUSE_H, DEFAULT_MIN_PAUSE_H),
            max_switches=int(self._opt(CONF_MAX_SWITCHES, DEFAULT_MAX_SWITCHES)),
        )

    def elapsed_h(self, now: datetime) -> float:
        return (now - self.last_change).total_seconds() / 3600 if self.last_change else math.inf
```

`async_apply` ersetzen (Tagesbudget jetzt nach **lokalem** Datum, wie im Rollout):
```python
    async def async_apply(self, want_on: bool, enabled: bool, now: datetime) -> tuple[bool, str | None]:
        """Apply the planner decision. Returns (intended release state, reason it differs from the planner)."""
        self.last_decision = want_on
        if not enabled:
            return want_on, "observe"  # observe mode: never touch the heat source

        today = dt_util.as_local(now).date()
        if self.day != today:
            self.day, self.switches_today = today, 0

        current = self.entity_is_on()
        if current is None:
            current = self.commanded if self.commanded is not None else True
        target, reason = apply_rules(want_on, current, self.elapsed_h(now), self.switches_today, self.rules)
        if target != current:
            await self._write(target)
            self.last_change = now
            self.switches_today += 1
        self.commanded = target
        return target, reason
```

In `_write` direkt nach der Log-Zeile ergänzen:
```python
        if self.on_write is not None:
            self.on_write(on)
```

Die Imports `timedelta` und `date` werden weiter für `load()` (date) gebraucht; `timedelta` entfernen, falls Ruff es als ungenutzt meldet.

- [ ] **Step 5: Coordinator anpassen**

In `ThermocastData` am Ende ergänzen:
```python
    override: str | None = None  # observe | failsafe | min_block | min_pause | budget
    planner_heat: bool | None = None  # raw planner wish (before fail-safe)
```

In `_update` den Block ab `result = await self._forecast_and_plan(now)` ersetzen durch:
```python
        result = await self._forecast_and_plan(now)
        failsafe = result.pop("failsafe")
        want_heat = True if failsafe else result["want_heat"]
        release, override = await self.actuator.async_apply(want_heat, self.control_enabled, now)
        if failsafe:
            override = "failsafe"
        age = (now - self.forecast.fetched_at).total_seconds() / 60 if self.forecast and self.forecast.fetched_at else None
        return ThermocastData(
            zones=result["zones"],
            want_heat=want_heat,
            release_on=release,
            control_enabled=self.control_enabled,
            block_start=result["block_start"],
            block_end=result["block_end"],
            failsafe_reason=failsafe,
            forecast_age_min=age,
            override=override,
            planner_heat=result["want_heat"],
        )
```

- [ ] **Step 6: HA-Test für den Grund ergänzen**

In `tests/test_init.py` ans Ende von `test_control_blocks_when_warm_and_unload_releases` vor dem Entladen einfügen:
```python
    coordinator = mock_entry.runtime_data
    assert coordinator.data.override is None
    assert coordinator.data.planner_heat is False
```
und in `test_observe_mode_never_writes` am Ende:
```python
    assert mock_entry.runtime_data.data.override == "observe"
```

- [ ] **Step 7: Tests laufen lassen**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, „All checks passed!“

- [ ] **Step 8: Commit**

```bash
git add custom_components/thermocast/core/rules.py custom_components/thermocast/actuator.py custom_components/thermocast/coordinator.py tests/test_rules.py tests/test_init.py
git commit -m "refactor: pure actuator rules in core, report override reason

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Regler-Rollout

**Files:**
- Create: `custom_components/thermocast/core/rollout.py`
- Test: `tests/test_rollout.py`

**Interfaces:**
- Consumes: `plan`, `ZonePlanInput`, `PlanResult`, `default_cost`, `CostFn` (Task 2); `Rules`, `apply_rules` (Task 3); `Prediction.contrib` (Task 1).
- Produces:
  - `DEFAULT_BLOCK_LENGTHS = (2, 3, 4, 6, 8)`; `block_lengths_for(min_block_h: float) -> tuple[int, ...]`.
  - `ActuatorState(on: bool, since_h: float = math.inf, switches_today: int = 0)`.
  - `ZoneTrajectory(temp0, mean, std, contrib, free_mean, free_std)` – `mean[h]` = Temperatur am **Ende** von Schritt h, `contrib[h]` = Änderung **während** Schritt h.
  - `RolloutResult(first: PlanResult, on: list[bool], zones: dict[str, ZoneTrajectory])` mit Property `blocks -> list[tuple[int, int]]`.
  - `rollout(zones, steps, rules, state, day_index=None, lookahead=24, block_lengths=DEFAULT_BLOCK_LENGTHS, z=1.0, cost_fn=default_cost) -> RolloutResult`.

- [ ] **Step 1: Failing tests schreiben**

`tests/test_rollout.py`:
```python
"""Controller rollout until the end of tomorrow."""
# ruff: noqa: I001
from __future__ import annotations

import time
from dataclasses import replace

from . import core_helpers  # noqa: F401
from core.planner import ZonePlanInput, plan
from core.rollout import ActuatorState, block_lengths_for, rollout
from core.rules import Rules

from .core_helpers import future, trained_model

RULES = Rules(min_block_h=3, min_pause_h=2, max_switches=12)


def _zone(name: str, t_out: float, temp_now: float, hours: int = 76, leads: bool = True) -> ZonePlanInput:
    comfort = [20.0 if 6 <= (20 + h + 1) % 24 < 22 else None for h in range(hours)]
    return ZonePlanInput(
        name=name, model=trained_model(), temp_now=temp_now, future=future(t_out, hours),
        comfort_low=comfort, q_on=15.0, leads_release=leads,
    )


def test_first_step_equals_live_plan():
    zones = [_zone("eg", -3.0, 20.4)]
    ro = rollout(zones, 52, RULES, ActuatorState(on=False))
    live = plan([replace(z, future=z.future[:24], comfort_low=z.comfort_low[:24]) for z in zones])
    assert ro.first.best == live.best
    assert ro.first.cost == live.cost


def test_cold_produces_blocks_and_warm_none():
    cold = rollout([_zone("eg", -5.0, 20.4)], 52, RULES, ActuatorState(on=False))
    assert cold.blocks, "expected heating blocks in cold weather"
    warm = rollout([_zone("eg", 18.0, 21.5)], 52, RULES, ActuatorState(on=False))
    assert warm.blocks == []
    assert len(warm.on) == 52


def test_rules_respected():
    ro = rollout([_zone("eg", -5.0, 20.4)], 52, RULES, ActuatorState(on=False))
    for start, end in ro.blocks:
        if end < len(ro.on):  # block finished inside the rollout
            assert end - start >= RULES.min_block_h
    for (_, e1), (s2, _) in zip(ro.blocks, ro.blocks[1:]):
        assert s2 - e1 >= RULES.min_pause_h


def test_running_block_is_continued_until_min_block():
    ro = rollout([_zone("eg", 18.0, 21.5)], 10, RULES, ActuatorState(on=True, since_h=1.0))
    assert ro.on[:2] == [True, True]
    assert ro.on[2] is False


def test_trajectories_and_contributions():
    ro = rollout([_zone("eg", -5.0, 20.4)], 30, RULES, ActuatorState(on=False))
    tr = ro.zones["eg"]
    assert len(tr.mean) == len(tr.std) == len(tr.contrib) == len(tr.free_mean) == 30
    prev = tr.temp0
    for m, c in zip(tr.mean, tr.contrib):
        assert abs(sum(c.values()) - (m - prev)) < 1e-9
        prev = m
    assert all(b >= a for a, b in zip(tr.std, tr.std[1:]))  # uncertainty carried across re-plans


def test_short_forecast_truncates_steps():
    ro = rollout([_zone("eg", -5.0, 20.4, hours=20)], 52, RULES, ActuatorState(on=False))
    assert len(ro.on) == 20


def test_block_lengths_for():
    assert block_lengths_for(3) == (3, 4, 6, 8)
    assert block_lengths_for(9) == (9,)


def test_budget_four_zones_52_steps():
    zones = [_zone(f"z{i}", -5.0, 20.3, leads=i < 2) for i in range(4)]
    t0 = time.perf_counter()
    rollout(zones, 52, RULES, ActuatorState(on=False))
    assert time.perf_counter() - t0 < 2.0
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_rollout.py -q`
Expected: FAIL (`ModuleNotFoundError: core.rollout`).

- [ ] **Step 3: `core/rollout.py` anlegen**

```python
"""Roll the real controller forward: plan hourly, apply actuator rules, simulate.

Shows the block sequence the controller would run if the forecast came true.
Step 0 uses exactly the inputs of the live ``plan()`` call, so it *is* the live decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .planner import CostFn, PlanResult, ZonePlanInput, default_cost, plan
from .rules import Rules, apply_rules

DEFAULT_BLOCK_LENGTHS: tuple[int, ...] = (2, 3, 4, 6, 8)


def block_lengths_for(min_block_h: float) -> tuple[int, ...]:
    """Candidate lengths not shorter than the minimum block (same rule as the live planner)."""
    return tuple(n for n in DEFAULT_BLOCK_LENGTHS if n >= min_block_h) or (int(min_block_h),)


@dataclass
class ActuatorState:
    on: bool
    since_h: float = math.inf  # hours since the last switch
    switches_today: int = 0


@dataclass
class ZoneTrajectory:
    temp0: float
    mean: list[float] = field(default_factory=list)  # temperature at the end of step h
    std: list[float] = field(default_factory=list)
    contrib: list[dict[str, float]] = field(default_factory=list)  # change during step h by cause
    free_mean: list[float] = field(default_factory=list)  # never heating
    free_std: list[float] = field(default_factory=list)


@dataclass
class RolloutResult:
    first: PlanResult
    on: list[bool]
    zones: dict[str, ZoneTrajectory]

    @property
    def blocks(self) -> list[tuple[int, int]]:
        """[start, end) step indices of contiguous heating."""
        out: list[tuple[int, int]] = []
        start: int | None = None
        for i, on in enumerate(self.on + [False]):
            if on and start is None:
                start = i
            elif not on and start is not None:
                out.append((start, i))
                start = None
        return out


def rollout(
    zones: list[ZonePlanInput],
    steps: int,
    rules: Rules,
    state: ActuatorState,
    day_index: list[int] | None = None,
    lookahead: int = 24,
    block_lengths: tuple[int, ...] = DEFAULT_BLOCK_LENGTHS,
    z: float = 1.0,
    cost_fn: CostFn = default_cost,
) -> RolloutResult:
    """Simulate ``steps`` hours. ``zones[i].future``/``comfort_low`` start at the current hour and
    should reach ``steps + lookahead`` hours; shorter forecasts shorten the rollout.
    ``day_index[h]`` identifies the local day of step h (daily switch budget)."""
    if not zones:
        raise ValueError("rollout needs at least one zone")
    steps = min([steps] + [len(zi.future) for zi in zones])
    if steps < 1:
        raise ValueError("no forecast inputs")
    days = day_index or [0] * steps
    available = min(len(zi.future) for zi in zones)

    temps = {zi.name: zi.temp_now for zi in zones}
    var = {zi.name: zi.var0 for zi in zones}
    hist = {zi.name: list(zi.model.history if zi.history is None else zi.history) for zi in zones}
    trajs = {zi.name: ZoneTrajectory(temp0=zi.temp_now) for zi in zones}
    on, since, switches = state.on, state.since_h, state.switches_today
    first: PlanResult | None = None
    on_list: list[bool] = []

    for h in range(steps):
        if h > 0 and days[h] != days[h - 1]:
            switches = 0
        forced = (on and since < rules.min_block_h) or (not on and since < rules.min_pause_h)
        if h == 0 or not forced:
            n = min(lookahead, available - h)
            inputs = [
                replace(
                    zi, temp_now=temps[zi.name], future=zi.future[h : h + n], comfort_low=zi.comfort_low[h : h + n],
                    var0=var[zi.name], history=hist[zi.name],
                )
                for zi in zones
            ]
            res = plan(inputs, block_lengths=block_lengths, z=z, cost_fn=cost_fn, shortcut=h > 0)
            if h == 0:
                first = res
            want = res.heat_now
        else:
            want = on  # the rules decide anyway
        target, _ = apply_rules(want, on, since, switches, rules)
        if target != on:
            switches += 1
            since = 0.0
            on = target
        on_list.append(on)

        for zi in zones:
            name = zi.name
            rec = replace(zi.future[h], q=zi.q_on if on else 0.0)
            p = zi.model.predict(temps[name], [rec], var0=var[name], history=hist[name])
            tr = trajs[name]
            tr.mean.append(p.mean[0])
            tr.std.append(p.std[0])
            tr.contrib.append(p.contrib[0])
            hist[name] = (hist[name] + [replace(rec, temp=temps[name])])[-max(zi.model.spec.max_lag, 1) :]
            var[name] = p.std[0] ** 2
            temps[name] = p.mean[0]
        since += 1.0

    for zi in zones:
        free = zi.predict(zi.future[:steps])
        trajs[zi.name].free_mean = free.mean
        trajs[zi.name].free_std = free.std
    assert first is not None
    return RolloutResult(first=first, on=on_list, zones=trajs)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_rollout.py -q`
Expected: PASS. **Falls nur `test_budget_four_zones_52_steps` fehlschlägt:** gemessene Zeit notieren, Task anhalten und dem Menschen melden (Spec 6 sieht dann einen Kandidaten-Vorfilter vor – Entscheidung nicht eigenmächtig treffen).

- [ ] **Step 5: Commit**

```bash
git add custom_components/thermocast/core/rollout.py tests/test_rollout.py
git commit -m "feat(core): controller rollout until the end of tomorrow

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Begründung, Robustheit, Planänderung

**Files:**
- Create: `custom_components/thermocast/core/explain.py`
- Test: `tests/test_explain.py`

**Interfaces:**
- Consumes: `PlanResult`, `ZonePlanInput`, `violations`, `plan` (Task 2).
- Produces:
  - `explain(first: PlanResult, zones: list[ZonePlanInput], hour0: datetime, z: float) -> dict` mit Schlüsseln `code, driver, first_violation, deficit_k, next_block, lead_h`; Codes `heating_now | block_planned | violation_accepted | no_need | no_need_sun`.
  - `robustness(res: PlanResult, res_z0: PlanResult, close_below: float = 1.0) -> dict` (`margin, level, sigma_driven`).
  - `PlanSnapshot(hour0, next_block, t_out: dict[datetime, float], irr: dict[datetime, float], expected_next: dict[str, float])`.
  - `snapshot_from(first: PlanResult, zones: list[ZonePlanInput], hour0: datetime) -> PlanSnapshot`.
  - `plan_change(prev: PlanSnapshot | None, cur: PlanSnapshot, temps_now: dict[str, float]) -> dict | None` (`previous_block, current_block, cause{kind, delta, at, zone}`).

- [ ] **Step 1: Failing tests schreiben**

`tests/test_explain.py`:
```python
"""Explanation codes, decision robustness and plan-change attribution."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from . import core_helpers  # noqa: F401
from core.explain import PlanSnapshot, explain, plan_change, robustness, snapshot_from
from core.planner import ZonePlanInput, plan

from .core_helpers import future, trained_model

H0 = datetime(2026, 10, 4, 18, tzinfo=UTC)


def _zone(t_out: float, temp_now: float, irr: dict | None = None) -> ZonePlanInput:
    return ZonePlanInput(
        name="eg", model=trained_model(), temp_now=temp_now, future=future(t_out, 24, irr),
        comfort_low=[20.0] * 24, q_on=15.0,
    )


def test_block_planned_or_heating_now_when_cold():
    zones = [_zone(-5.0, 20.4)]
    res = plan(zones)
    ex = explain(res, zones, H0, 1.0)
    assert ex["code"] in ("block_planned", "heating_now")
    assert ex["driver"] == "eg"
    assert ex["next_block"]["start"] == (H0 + timedelta(hours=res.best.start)).isoformat()
    assert ex["first_violation"] is not None and ex["deficit_k"] > 0


def test_no_need_when_warm():
    zones = [_zone(18.0, 21.5)]
    ex = explain(plan(zones), zones, H0, 1.0)
    assert ex == {"code": "no_need", "driver": None, "first_violation": None, "deficit_k": None,
                  "next_block": None, "lead_h": None}


def test_no_need_sun_when_only_sun_keeps_it_warm():
    m = trained_model()
    cold_dark = ZonePlanInput(name="eg", model=m, temp_now=20.6, future=future(9.0, 8), comfort_low=[20.0] * 8)
    sunny = replace(cold_dark, future=future(9.0, 8, {"90_90": 600.0, "40_180": 800.0}))
    if plan([cold_dark]).violation_free["eg"] > 0 and plan([sunny]).violation_free["eg"] == 0:
        assert explain(plan([sunny]), [sunny], H0, 1.0)["code"] == "no_need_sun"


def test_robustness_close_and_sigma_driven():
    zones = [_zone(-5.0, 20.4)]
    res, res0 = plan(zones), plan(zones, z=0.0)
    rb = robustness(res, res0)
    assert rb["level"] in ("clear", "close") and rb["margin"] >= 0
    fake0 = replace(res0, best=replace(res0.best, start=None, length=0))
    assert robustness(res, fake0)["sigma_driven"] is (res.best.start is not None)


def _snap(block_start: int | None, t_out: float, hour0: datetime = H0, expected: float = 20.0) -> PlanSnapshot:
    blk = None if block_start is None else (hour0 + timedelta(hours=block_start), hour0 + timedelta(hours=block_start + 4))
    hours = [hour0 + timedelta(hours=i) for i in range(24)]
    return PlanSnapshot(hour0, blk, {h: t_out for h in hours}, {h: 0.0 for h in hours}, {"eg": expected})


def test_plan_change_none_without_previous_or_same_block():
    assert plan_change(None, _snap(5, 3.0), {}) is None
    assert plan_change(_snap(5, 3.0), _snap(5, 3.0), {}) is None


def test_plan_change_attributes_outdoor_temperature():
    prev = _snap(5, 3.0)
    cur = _snap(4, 1.5, hour0=H0 + timedelta(hours=1))
    ch = plan_change(prev, cur, {"eg": 20.0})
    assert ch["previous_block"]["start"] == (H0 + timedelta(hours=5)).isoformat()
    assert ch["cause"]["kind"] == "t_out" and ch["cause"]["delta"] == -1.5


def test_plan_change_attributes_room_temperature():
    prev = _snap(5, 3.0, expected=20.5)
    cur = _snap(None, 3.0, hour0=H0 + timedelta(hours=1))
    ch = plan_change(prev, cur, {"eg": 21.3})
    assert ch["current_block"] is None
    assert ch["cause"] == {"kind": "room", "delta": 0.8, "at": None, "zone": "eg"}


def test_snapshot_from_plan():
    zones = [_zone(-5.0, 20.4)]
    res = plan(zones)
    snap = snapshot_from(res, zones, H0)
    assert snap.t_out[H0] == -5.0
    assert set(snap.expected_next) == {"eg"}
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_explain.py -q`
Expected: FAIL (`ModuleNotFoundError: core.explain`).

- [ ] **Step 3: `core/explain.py` anlegen**

```python
"""Why the planner decided what it did – as codes and numbers (texts live in the frontend)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .planner import PlanResult, ZonePlanInput, violations

HOUR = timedelta(hours=1)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def explain(first: PlanResult, zones: list[ZonePlanInput], hour0: datetime, z: float) -> dict:
    """``hour0`` = start of the current hour; prediction index i refers to hour0 + (i + 1) h."""
    leading = [zz for zz in zones if zz.leads_release]
    driver: str | None = None
    first_i: int | None = None
    deficit = 0.0
    for zz in leading:
        viol = violations(first.free_run[zz.name], zz.comfort_low, z)
        if viol and (first_i is None or viol[0][0] < first_i):
            driver, first_i, deficit = zz.name, viol[0][0], max(d for _, d in viol)
    out: dict = {
        "code": None,
        "driver": driver,
        "first_violation": _iso(hour0 + (first_i + 1) * HOUR) if first_i is not None else None,
        "deficit_k": round(deficit, 2) if driver else None,
        "next_block": None,
        "lead_h": None,
    }
    best = first.best
    if best.start is not None:
        out["next_block"] = {
            "start": _iso(hour0 + best.start * HOUR),
            "end": _iso(hour0 + (best.start + best.length) * HOUR),
        }
        out["code"] = "heating_now" if first.heat_now else "block_planned"
        if first_i is not None:
            out["lead_h"] = first_i + 1 - best.start
        return out
    if driver is not None:
        out["code"] = "violation_accepted"
        return out
    sunless = any(
        violations(zz.predict([replace(r, irr={}) for r in zz.future]), zz.comfort_low, z) for zz in leading
    )
    out["code"] = "no_need_sun" if sunless else "no_need"
    return out


def robustness(res: PlanResult, res_z0: PlanResult, close_below: float = 1.0) -> dict:
    """``close`` if the runner-up costs less than one extra burner start more."""
    margin = res.ranked[1].cost - res.ranked[0].cost if len(res.ranked) > 1 else None
    return {
        "margin": round(margin, 3) if margin is not None else None,
        "level": "close" if margin is not None and margin < close_below else "clear",
        "sigma_driven": res.best.start is not None and res_z0.best.start is None,
    }


@dataclass
class PlanSnapshot:
    hour0: datetime
    next_block: tuple[datetime, datetime] | None
    t_out: dict[datetime, float]
    irr: dict[datetime, float]  # summed over surfaces, W/m²
    expected_next: dict[str, float]  # zone -> expected temperature at hour0 + 1 h


def snapshot_from(first: PlanResult, zones: list[ZonePlanInput], hour0: datetime) -> PlanSnapshot:
    best = first.best
    block = None
    if best.start is not None:
        block = (hour0 + best.start * HOUR, hour0 + (best.start + best.length) * HOUR)
    ref = zones[0].future if zones else []
    return PlanSnapshot(
        hour0=hour0,
        next_block=block,
        t_out={hour0 + i * HOUR: r.t_out for i, r in enumerate(ref)},
        irr={hour0 + i * HOUR: sum(r.irr.values()) for i, r in enumerate(ref)},
        expected_next={zz.name: first.planned[zz.name].mean[0] for zz in zones if first.planned[zz.name].mean},
    )


def _block_dict(block: tuple[datetime, datetime] | None) -> dict | None:
    return {"start": _iso(block[0]), "end": _iso(block[1])} if block else None


def _changed(a: tuple[datetime, datetime] | None, b: tuple[datetime, datetime] | None) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a[0] - b[0]) >= HOUR or abs(a[1] - b[1]) >= HOUR


def plan_change(prev: PlanSnapshot | None, cur: PlanSnapshot, temps_now: dict[str, float]) -> dict | None:
    """Report a shifted/new/dropped next block and its most likely cause (largest input deviation)."""
    if prev is None or not _changed(prev.next_block, cur.next_block):
        return None
    ends = [b[1] for b in (prev.next_block, cur.next_block) if b]
    until = max(ends) if ends else cur.hour0 + 24 * HOUR
    best: tuple[float, dict] | None = None

    def consider(score: float, cause: dict) -> None:
        nonlocal best
        if best is None or score > best[0]:
            best = (score, cause)

    for h in sorted(cur.t_out):
        if cur.hour0 <= h < until and h in prev.t_out:
            d = round(cur.t_out[h] - prev.t_out[h], 2)
            consider(abs(d) / 1.0, {"kind": "t_out", "delta": d, "at": _iso(h), "zone": None})
            if h in prev.irr and h in cur.irr:
                di = round(cur.irr[h] - prev.irr[h], 0)
                consider(abs(di) / 100.0, {"kind": "sun", "delta": di, "at": _iso(h), "zone": None})
    if prev.hour0 + HOUR == cur.hour0:
        for zone, expected in prev.expected_next.items():
            if zone in temps_now:
                d = round(temps_now[zone] - expected, 2)
                consider(abs(d) / 0.2, {"kind": "room", "delta": d, "at": None, "zone": zone})
    return {
        "previous_block": _block_dict(prev.next_block),
        "current_block": _block_dict(cur.next_block),
        "cause": best[1] if best and best[0] >= 0.5 else None,
    }
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_explain.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/thermocast/core/explain.py tests/test_explain.py
git commit -m "feat(core): explanation codes, decision robustness, plan-change attribution

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Stundenaggregation von Zustandsfolgen

**Files:**
- Create: `custom_components/thermocast/core/series.py`
- Test: `tests/test_series.py`

**Interfaces:**
- Produces: `StateSeries = list[tuple[datetime, str]]` (zeitlich sortiert, Zustand gilt bis zur nächsten Änderung); `hourly_mean(series, hours, end) -> list[float | None]` (zeitgewichtet, nur bis `end`); `hourly_fraction(series, hours, end, value_of: Callable[[str], float | None]) -> list[float | None]`.

- [ ] **Step 1: Failing tests schreiben**

`tests/test_series.py`:
```python
"""Time-weighted hourly aggregation of recorder state sequences."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import core_helpers  # noqa: F401
from core.rules import binary_value
from core.series import hourly_fraction, hourly_mean

T0 = datetime(2026, 10, 3, 22, tzinfo=UTC)
HOURS = [T0 + timedelta(hours=i) for i in range(4)]


def test_time_weighted_mean_and_future_none():
    series = [(T0 - timedelta(minutes=5), "20.0"), (T0 + timedelta(minutes=30), "21.0")]
    end = T0 + timedelta(hours=1, minutes=30)
    assert hourly_mean(series, HOURS, end) == [20.5, 21.0, None, None]


def test_unavailable_segments_are_skipped():
    series = [(T0, "20.0"), (T0 + timedelta(minutes=30), "unavailable")]
    assert hourly_mean(series, HOURS[:1], T0 + timedelta(hours=1)) == [20.0]
    assert hourly_mean([(T0, "unavailable")], HOURS[:1], T0 + timedelta(hours=1)) == [None]


def test_empty_series():
    assert hourly_mean([], HOURS, T0 + timedelta(hours=4)) == [None] * 4


def test_on_fraction():
    series = [(T0, "off"), (T0 + timedelta(minutes=15), "on"), (T0 + timedelta(minutes=45), "off")]
    assert hourly_fraction(series, HOURS[:2], T0 + timedelta(hours=2), binary_value) == [0.5, 0.0]
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_series.py -q`
Expected: FAIL (`ModuleNotFoundError: core.series`).

- [ ] **Step 3: `core/series.py` anlegen**

```python
"""Hourly aggregation of state sequences (e.g. from the HA recorder) – no HA imports."""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

StateSeries = list[tuple[datetime, str]]
HOUR = timedelta(hours=1)


def _float(value: str) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _segments(series: StateSeries, start: datetime, end: datetime) -> Iterator[tuple[float, str]]:
    """(duration in s, state) of every piece of [start, end)."""
    for k, (t, state) in enumerate(series):
        t1 = series[k + 1][0] if k + 1 < len(series) else end
        a, b = max(t, start), min(t1, end)
        if b > a:
            yield (b - a).total_seconds(), state


def _hourly(series: StateSeries, hours: list[datetime], end: datetime, value_of: Callable[[str], float | None]):
    out: list[float | None] = []
    for h in hours:
        b = min(h + HOUR, end)
        total = weight = 0.0
        if b > h:
            for seconds, state in _segments(series, h, b):
                v = value_of(state)
                if v is not None:
                    total += v * seconds
                    weight += seconds
        out.append(round(total / weight, 3) if weight > 0 else None)
    return out


def hourly_mean(series: StateSeries, hours: list[datetime], end: datetime) -> list[float | None]:
    """Time-weighted mean of numeric states per hour, up to ``end`` (later hours: None)."""
    return _hourly(series, hours, end, _float)


def hourly_fraction(
    series: StateSeries, hours: list[datetime], end: datetime, value_of: Callable[[str], float | None]
) -> list[float | None]:
    """Share of each hour in which ``value_of(state)`` is 1 (on)."""
    return _hourly(series, hours, end, value_of)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_series.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/thermocast/core/series.py tests/test_series.py
git commit -m "feat(core): hourly aggregation of state sequences

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: View-Vertrag (`core/view.py`)

**Files:**
- Create: `custom_components/thermocast/core/view.py`
- Test: `tests/test_view.py`

**Interfaces:**
- Consumes: `rollout`, `ActuatorState`, `RolloutResult`, `DEFAULT_BLOCK_LENGTHS` (Task 4); `plan`, `ZonePlanInput` (Task 2); `explain`, `robustness` (Task 5); `Rules` (Task 3).
- Produces:
  - `VIEW_VERSION = 1`; `Window(hours: list[datetime], now_index: int, tz: str)` mit `end`; `make_window(now, tz: tzinfo, tz_name: str) -> Window`.
  - `align(times, values, hours) -> list`.
  - `ZoneViewInput(id, name, heat_type, leads, measured, comfort_low, group_labels)` (Listen auf `hours` ausgerichtet).
  - `Outlook(rollout, first_inputs, explanation, robustness)`; `compute_outlook(zones, steps, rules, state, day_index, hour0, z=1.0, lookahead=24, block_lengths=DEFAULT_BLOCK_LENGTHS) -> Outlook`.
  - `build_view(*, window, tz, generated_at, t_out_measured, t_out_forecast, irr, heating_actual, release, planner, zones, outlook, z, decision, plan_change, events, errors) -> dict` (Spec 4.4).

- [ ] **Step 1: Failing tests schreiben**

`tests/test_view.py`:
```python
"""View contract: window, alignment, outlook, days, DST."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import core_helpers  # noqa: F401
from core.planner import ZonePlanInput
from core.rollout import ActuatorState
from core.rules import Rules
from core.view import VIEW_VERSION, ZoneViewInput, align, build_view, compute_outlook, make_window

from .core_helpers import future, trained_model

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 4, 18, 5, tzinfo=UTC)  # 20:05 local


def test_window_normal_and_dst():
    w = make_window(NOW, TZ, "Europe/Berlin")
    assert len(w.hours) == 72
    assert w.hours[0] == datetime(2026, 10, 2, 22, tzinfo=UTC)  # 03.10. 00:00 local
    assert w.hours[w.now_index] == datetime(2026, 10, 4, 18, tzinfo=UTC)
    assert len(make_window(datetime(2026, 10, 25, 12, tzinfo=UTC), TZ, "Europe/Berlin").hours) == 73
    assert len(make_window(datetime(2026, 3, 29, 12, tzinfo=UTC), TZ, "Europe/Berlin").hours) == 71


def test_align():
    w = make_window(NOW, TZ, "Europe/Berlin")
    times = [w.hours[5], w.hours[6]]
    out = align(times, [1.0, 2.0], w.hours)
    assert out[5:7] == [1.0, 2.0] and out[4] is None and len(out) == len(w.hours)


def _inputs(w, t_out: float, hours_avail: int):
    def comfort_at(t):
        return 20.0 if 6 <= t.astimezone(TZ).hour < 22 else None

    steps_times = [w.hours[w.now_index] + timedelta(hours=h + 1) for h in range(hours_avail)]
    zpi = ZonePlanInput(
        name="eg", model=trained_model(), temp_now=20.4, future=future(t_out, hours_avail),
        comfort_low=[comfort_at(t) for t in steps_times], q_on=15.0,
    )
    zvi = ZoneViewInput(
        id="eg", name="EG", heat_type="fbh", leads=True,
        measured=[20.5 if i < w.now_index else None for i in range(len(w.hours))],
        comfort_low=[comfort_at(h) for h in w.hours], group_labels={"sun:90_90:window": "Fenster Ost"},
    )
    return zpi, zvi


def _view(t_out: float = -3.0, hours_avail: int = 60, outlook: bool = True, decision: dict | None = None):
    w = make_window(NOW, TZ, "Europe/Berlin")
    n = len(w.hours)
    zpi, zvi = _inputs(w, t_out, hours_avail)
    steps = n - w.now_index
    day_index = [(w.hours[min(w.now_index + h, n - 1)]).astimezone(TZ).toordinal() for h in range(steps)]
    out = (
        compute_outlook([zpi], steps, Rules(), ActuatorState(on=False), day_index, w.hours[w.now_index])
        if outlook else None
    )
    past = [1.0 if 3 <= i < 7 else 0.0 if i < w.now_index else None for i in range(n)]
    return w, build_view(
        window=w, tz=TZ, generated_at=NOW,
        t_out_measured=[5.0 if i < w.now_index else None for i in range(n)],
        t_out_forecast=[t_out] * n,
        irr=[{"key": "90_90", "label": "Fenster Ost", "values": [0.0] * n}],
        heating_actual=past, release=[True if p is not None else None for p in past],
        planner=[p >= 0.5 if p is not None else None for p in past],
        zones=[zvi], outlook=out, z=1.0,
        decision=decision or {"override": "observe", "failsafe_reason": None},
        plan_change=None,
        events=[{"time": (NOW - timedelta(hours=3)).isoformat(), "type": "window_open", "zone": "eg"},
                {"time": (NOW - timedelta(days=5)).isoformat(), "type": "control_on"}],
        errors=[],
    )


def test_series_lengths_and_contract():
    w, v = _view()
    n = len(w.hours)
    assert v["version"] == VIEW_VERSION
    assert v["window"]["now_index"] == w.now_index and len(v["hours"]) == n
    z = v["zones"][0]
    for arr in (z["measured"], z["comfort_low"], z["plan"]["mean"], z["plan"]["std"], z["free"]["mean"], z["contrib"],
                v["weather"]["t_out"], v["heating"]["actual"]):
        assert len(arr) == n
    assert z["plan"]["mean"][w.now_index] == 20.4
    assert z["plan"]["mean"][w.now_index - 1] is None
    assert z["contrib"][w.now_index] and z["contrib"][w.now_index - 1] is None
    assert {g["kind"] for g in z["groups"]} >= {"loss", "heat"}
    assert v["weather"]["t_out_measured"][0] is True and v["weather"]["t_out_measured"][-1] is False


def test_blocks_candidates_days_events():
    w, v = _view()
    assert v["heating"]["past_blocks"] == [{"start": w.hours[3].isoformat(), "end": w.hours[7].isoformat()}]
    assert v["heating"]["planned_blocks"], "cold weather → planned blocks"
    cands = v["candidates"]
    assert sum(c["chosen"] for c in cands) == 1
    assert any(c["start"] is None for c in cands) and len(cands) <= 6
    assert [c["cost"] for c in cands] == sorted(c["cost"] for c in cands)
    assert [d["kind"] for d in v["days"]] == ["past", "today", "future"]
    assert v["days"][0]["heat_hours"] == 4.0 and v["days"][0]["blocks"] == 1
    assert v["days"][0]["release_followed"] == 0.75  # release True all hours, planner only during 4 of 16 past hours? see below
    assert v["days"][2]["heat_hours_planned"] is not None
    assert [e["type"] for e in v["events"]] == ["window_open"]


def test_short_forecast_and_no_outlook():
    w, v = _view(hours_avail=10)
    assert v["zones"][0]["plan"]["mean"][-1] is None
    _, v2 = _view(outlook=False)
    assert v2["explanation"]["code"] == "no_forecast" and v2["candidates"] == []
    assert v2["zones"][0]["plan"]["mean"][w.now_index] is None


def test_failsafe_overrides_explanation():
    _, v = _view(decision={"override": "failsafe", "failsafe_reason": "no_temperature:EG"})
    assert v["explanation"]["code"] == "failsafe" and v["explanation"]["reason"] == "no_temperature:EG"
```

Hinweis zu `release_followed` im Test: Gestern (Index 0–23) hat `release=True` für alle 24 Stunden, `planner=True` nur für Index 3–6 → 4 von 24 übereinstimmend = 0.1667. Ersetze vor dem Ausführen die Zeile mit `release_followed` durch:
```python
    assert v["days"][0]["release_followed"] == round(4 / 24, 3)
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_view.py -q`
Expected: FAIL (`ModuleNotFoundError: core.view`).

- [ ] **Step 3: `core/view.py` anlegen**

```python
"""Panel view: the JSON contract (version 1) built from pure inputs – no HA imports."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from .explain import explain, robustness
from .planner import ZonePlanInput, plan
from .rollout import DEFAULT_BLOCK_LENGTHS, ActuatorState, RolloutResult, rollout
from .rules import Rules

VIEW_VERSION = 1
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class Window:
    hours: list[datetime]  # UTC hour starts: local yesterday 00:00 … local tomorrow 23:00
    now_index: int
    tz: str

    @property
    def end(self) -> datetime:
        return self.hours[-1] + HOUR


def make_window(now: datetime, tz: tzinfo, tz_name: str) -> Window:
    local = now.astimezone(tz)
    start = datetime.combine(local.date() - timedelta(days=1), time(0), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(local.date() + timedelta(days=2), time(0), tzinfo=tz).astimezone(timezone.utc)
    hours: list[datetime] = []
    t = start
    while t < end:
        hours.append(t)
        t += HOUR
    now_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return Window(hours=hours, now_index=hours.index(now_hour), tz=tz_name)


def align(times: list[datetime], values: list, hours: list[datetime]) -> list:
    idx = {t: i for i, t in enumerate(times)}
    return [values[idx[h]] if h in idx else None for h in hours]


@dataclass
class ZoneViewInput:
    id: str
    name: str
    heat_type: str
    leads: bool
    measured: list[float | None]  # aligned to window hours
    comfort_low: list[float | None]  # aligned to window hours
    group_labels: dict[str, str]  # group key -> display name (surfaces, neighbours, gains)


@dataclass
class Outlook:
    rollout: RolloutResult
    first_inputs: list[ZonePlanInput]
    explanation: dict
    robustness: dict


def compute_outlook(
    zones: list[ZonePlanInput],
    steps: int,
    rules: Rules,
    state: ActuatorState,
    day_index: list[int],
    hour0: datetime,
    z: float = 1.0,
    lookahead: int = 24,
    block_lengths: tuple[int, ...] = DEFAULT_BLOCK_LENGTHS,
) -> Outlook:
    """CPU part (run in an executor): rollout, z=0 comparison plan, explanation."""
    ro = rollout(zones, steps, rules, state, day_index, lookahead=lookahead, block_lengths=block_lengths, z=z)
    first_inputs = [replace(zi, future=zi.future[:lookahead], comfort_low=zi.comfort_low[:lookahead]) for zi in zones]
    res_z0 = plan(first_inputs, block_lengths=block_lengths, z=0.0)
    return Outlook(ro, first_inputs, explain(ro.first, first_inputs, hour0, z), robustness(ro.first, res_z0))


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    return out


def _group_kind(key: str) -> str:
    return key.split(":", 1)[0]


def build_view(
    *,
    window: Window,
    tz: tzinfo,
    generated_at: datetime,
    t_out_measured: list[float | None],
    t_out_forecast: list[float | None],
    irr: list[dict[str, Any]],
    heating_actual: list[float | None],
    release: list[bool | None],
    planner: list[bool | None],
    zones: list[ZoneViewInput],
    outlook: Outlook | None,
    z: float,
    decision: dict[str, Any],
    plan_change: dict | None,
    events: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    n = len(window.hours)
    now = window.now_index

    def iso(i: int) -> str:
        return (window.hours[0] + i * HOUR).isoformat()

    # --- heating
    on_plan: list[bool | None] = [None] * n
    if outlook:
        for h, on in enumerate(outlook.rollout.on):
            if now + h < n:
                on_plan[now + h] = on
    past_flags = [i < now and a is not None and a >= 0.5 for i, a in enumerate(heating_actual)]

    # --- zones
    zviews: list[dict[str, Any]] = []
    for zi in zones:
        plan_mean: list[float | None] = [None] * n
        plan_std: list[float | None] = [None] * n
        free_mean: list[float | None] = [None] * n
        free_std: list[float | None] = [None] * n
        contrib: list[dict[str, float] | None] = [None] * n
        tr = outlook.rollout.zones.get(zi.id) if outlook else None
        if tr is not None:
            plan_mean[now] = free_mean[now] = _r(tr.temp0)
            plan_std[now] = free_std[now] = 0.0
            for h in range(len(tr.mean)):
                if now + h + 1 < n:
                    plan_mean[now + h + 1] = _r(tr.mean[h])
                    plan_std[now + h + 1] = _r(tr.std[h], 3)
                    free_mean[now + h + 1] = _r(tr.free_mean[h])
                    free_std[now + h + 1] = _r(tr.free_std[h], 3)
                if now + h < n:
                    contrib[now + h] = {k: round(v, 4) for k, v in tr.contrib[h].items()}
        keys = sorted({k for c in contrib if c for k in c})
        zviews.append(
            {
                "id": zi.id,
                "name": zi.name,
                "heat_type": zi.heat_type,
                "leads": zi.leads,
                "measured": [_r(v) for v in zi.measured],
                "comfort_low": zi.comfort_low,
                "plan": {"mean": plan_mean, "std": plan_std},
                "free": {"mean": free_mean, "std": free_std},
                "contrib": contrib,
                "groups": [{"key": k, "label": zi.group_labels.get(k, k), "kind": _group_kind(k)} for k in keys],
            }
        )

    # --- candidates for the next block
    candidates: list[dict[str, Any]] = []
    if outlook:
        first = outlook.rollout.first
        none = next((s for s in first.ranked if s.candidate.start is None), None)
        top = [s for s in first.ranked if s.candidate.start is not None][:5]
        for sc in sorted(([none] if none else []) + top, key=lambda s: s.cost):
            c = sc.candidate
            traj: dict[str, list[float | None]] = {}
            for zid, pred in sc.preds.items():
                arr: list[float | None] = [None] * n
                tr = outlook.rollout.zones.get(zid)
                arr[now] = _r(tr.temp0) if tr else None
                for h, m in enumerate(pred.mean):
                    if now + h + 1 < n:
                        arr[now + h + 1] = _r(m)
                traj[zid] = arr
            candidates.append(
                {
                    "start": iso(now + c.start) if c.start is not None else None,
                    "end": iso(now + c.start + c.length) if c.start is not None else None,
                    "cost": round(sc.cost, 3),
                    "violation_kh": round(sc.violation, 3),
                    "parts": {k: round(v, 3) for k, v in sc.parts.items()},
                    "chosen": c == first.best,
                    "trajectories": traj,
                }
            )

    # --- days
    dates: list[date] = [h.astimezone(tz).date() for h in window.hours]
    today = dates[now]
    leading = [zv for zv in zviews if zv["leads"]]
    days: list[dict[str, Any]] = []
    for d in sorted(set(dates)):
        idx = [i for i in range(n) if dates[i] == d]
        past = [i for i in idx if i < now]
        fut = [i for i in idx if i >= now]
        kind = "past" if d < today else "today" if d == today else "future"
        heat_vals = [heating_actual[i] for i in past if heating_actual[i] is not None]
        meas = [
            zv["measured"][i] for zv in leading for i in past
            if zv["comfort_low"][i] is not None and zv["measured"][i] is not None
        ]
        low = [
            zv["plan"]["mean"][i] - z * zv["plan"]["std"][i] for zv in leading for i in fut
            if zv["comfort_low"][i] is not None and zv["plan"]["mean"][i] is not None
        ]
        pairs = [(release[i], planner[i]) for i in past if release[i] is not None and planner[i] is not None]
        has_plan = outlook is not None and kind != "past"

        def planned_start(i: int) -> bool:
            if not on_plan[i]:
                return False
            prev = on_plan[i - 1] if i - 1 >= now else (past_flags[i - 1] if i >= 1 else False)
            return not prev

        last_std = [zv["plan"]["std"][idx[-1]] for zv in zviews if zv["plan"]["std"][idx[-1]] is not None]
        days.append(
            {
                "date": d.isoformat(),
                "kind": kind,
                "heat_hours": round(sum(heat_vals), 1) if kind != "future" and heat_vals else None,
                "blocks": sum(1 for i in past if past_flags[i] and (i == 0 or not past_flags[i - 1]))
                if kind != "future" else None,
                "heat_hours_planned": float(sum(1 for i in fut if on_plan[i])) if has_plan else None,
                "blocks_planned": sum(1 for i in fut if planned_start(i)) if has_plan else None,
                "min_leading": _r(min(meas)) if meas else None,
                "min_leading_planned": _r(min(low)) if low else None,
                "release_followed": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
                "std_end": _r(max(last_std), 2) if last_std and kind == "future" else None,
            }
        )

    # --- explanation
    explanation: dict[str, Any] = dict(outlook.explanation) if outlook else {"code": "no_forecast"}
    if decision.get("override") == "failsafe":
        explanation = {**explanation, "code": "failsafe", "reason": decision.get("failsafe_reason")}

    start, end = window.hours[0], window.end
    return {
        "version": VIEW_VERSION,
        "generated_at": generated_at.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "now_index": now, "tz": window.tz},
        "hours": [h.isoformat() for h in window.hours],
        "weather": {
            "t_out": [_r(m if m is not None else f, 1) for m, f in zip(t_out_measured, t_out_forecast)],
            "t_out_measured": [m is not None for m in t_out_measured],
            "irr": irr,
        },
        "heating": {
            "actual": heating_actual,
            "release": release,
            "planner": planner,
            "past_blocks": [{"start": iso(s), "end": iso(e)} for s, e in _runs(past_flags)],
            "planned_blocks": [{"start": iso(s), "end": iso(e)} for s, e in _runs([bool(o) for o in on_plan])],
        },
        "zones": zviews,
        "decision": decision,
        "explanation": explanation,
        "robustness": outlook.robustness if outlook else None,
        "plan_change": plan_change,
        "candidates": candidates,
        "days": days,
        "events": [e for e in events if start <= datetime.fromisoformat(e["time"]) < end],
        "errors": errors,
    }
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_view.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/thermocast/core/view.py tests/test_view.py
git commit -m "feat(core): panel view contract v1 (window, outlook, days, candidates)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Ereignis-Log

**Files:**
- Create: `custom_components/thermocast/events.py`
- Modify: `custom_components/thermocast/coordinator.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces: `EventLog(maxlen=200)` mit `add(when, type_, zone=None, detail=None, dedupe=None) -> bool`, `to_list()`, `load(items)`, Attribut `dirty: bool`; `ThermocastCoordinator.events: EventLog`. Ereignistypen: `window_open`, `forecast_failed`, `failsafe_start`, `failsafe_end`, `control_on`, `control_off`, `model_reset`, `release_written`.

- [ ] **Step 1: Failing tests schreiben**

`tests/test_events.py`:
```python
"""Event log: ring buffer, dedupe, persistence and coordinator hooks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.thermocast.const import DOMAIN
from custom_components.thermocast.events import EventLog

from .test_init import _setup_entry, _setup_states

T = datetime(2026, 10, 4, 12, tzinfo=UTC)


def test_ring_buffer_and_dedupe():
    log = EventLog(maxlen=3)
    for i in range(5):
        log.add(T + timedelta(minutes=i), "control_on")
    assert len(log.to_list()) == 3
    assert log.add(T, "forecast_failed") is True
    assert log.add(T + timedelta(minutes=15), "forecast_failed", dedupe=timedelta(hours=1)) is False
    assert log.add(T + timedelta(hours=2), "forecast_failed", dedupe=timedelta(hours=1)) is True
    restored = EventLog(maxlen=3)
    restored.load(log.to_list())
    assert restored.to_list() == log.to_list() and restored.dirty is False


async def test_coordinator_records_events(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_storage) -> None:
    mock_open_meteo(18.0)
    await _setup_states(hass, temp=22.0)
    await _setup_entry(hass, mock_entry)
    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{mock_entry.entry_id}_control_enabled")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    types = [e["type"] for e in mock_entry.runtime_data.events.to_list()]
    assert types[:4] == ["control_on", "release_written", "control_off", "release_written"]
    stored = hass_storage[f"{DOMAIN}.{mock_entry.entry_id}"]["data"]
    assert [e["type"] for e in stored["events"]][:4] == types[:4]


async def test_failsafe_and_forecast_events(hass: HomeAssistant, mock_entry, aioclient_mock) -> None:
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    types = [e["type"] for e in mock_entry.runtime_data.events.to_list()]
    assert "forecast_failed" in types and "failsafe_start" in types
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL (`ModuleNotFoundError: custom_components.thermocast.events`).

- [ ] **Step 3: `events.py` anlegen**

```python
"""Ring buffer of notable events for the panel timeline (persisted in the coordinator store)."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any


class EventLog:
    def __init__(self, maxlen: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.dirty = False

    def add(
        self,
        when: datetime,
        type_: str,
        zone: str | None = None,
        detail: str | None = None,
        dedupe: timedelta | None = None,
    ) -> bool:
        """Append an event; with ``dedupe`` skip it if the same type/zone was logged within that time."""
        if dedupe is not None:
            for item in reversed(self._items):
                if item["type"] == type_ and item.get("zone") == zone:
                    if when - datetime.fromisoformat(item["time"]) < dedupe:
                        return False
                    break
        item: dict[str, Any] = {"time": when.isoformat(), "type": type_}
        if zone is not None:
            item["zone"] = zone
        if detail is not None:
            item["detail"] = detail
        self._items.append(item)
        self.dirty = True
        return True

    def to_list(self) -> list[dict[str, Any]]:
        return list(self._items)

    def load(self, items: list[dict[str, Any]]) -> None:
        self._items.clear()
        self._items.extend(items)
        self.dirty = False
```

- [ ] **Step 4: Coordinator-Hooks**

Import ergänzen: `from .events import EventLog`.

In `__init__` nach `self.house_device_id …`:
```python
        self.events = EventLog()
        self._failsafe_active = False
        self.actuator.on_write = lambda on: self.events.add(dt_util.utcnow(), "release_written", detail="on" if on else "off")
```

In `async_load` direkt nach `self.actuator.load(...)`:
```python
        self.events.load(stored.get("events", []))
```
und den Zweig `if not zr.model.load_dict(...)` erweitern:
```python
                if not zr.model.load_dict(saved.get("model", {})):
                    _LOGGER.info("Zone %s: configuration changed, model restarts from prior", subentry.title)
                    self.events.add(dt_util.utcnow(), "model_reset", zone=subentry.subentry_id)
```

`async_save` – Dict um `"events": self.events.to_list(),` ergänzen und danach `self.events.dirty = False` setzen:
```python
    async def async_save(self) -> None:
        await self.store.async_save(
            {
                "control_enabled": self.control_enabled,
                "actuator": self.actuator.to_dict(),
                "events": self.events.to_list(),
                "zones": {
                    sid: {"model": z.model.to_dict(), "q_on": z.q_on} for sid, z in self.zones.items()
                },
            }
        )
        self.events.dirty = False
```

`async_set_control` – vor `if not enabled:`:
```python
        self.events.add(dt_util.utcnow(), "control_on" if enabled else "control_off")
```

`_async_update_data` – im `except`-Zweig vor `if self.control_enabled:`:
```python
            if not self._failsafe_active:
                self.events.add(dt_util.utcnow(), "failsafe_start", detail="update_error")
                self._failsafe_active = True
```

`_close_hour` – nach Berechnung von `valid`:
```python
        if any(acc["window"]):
            self.events.add(dt_util.utcnow(), "window_open", zone=z.subentry_id, dedupe=timedelta(minutes=59))
```

`_ensure_forecast` – im `except`-Zweig:
```python
            self.events.add(dt_util.utcnow(), "forecast_failed", detail=str(err)[:120], dedupe=timedelta(hours=1))
```

`_update` – direkt nach `failsafe = result.pop("failsafe")`:
```python
        if failsafe and not self._failsafe_active:
            self.events.add(now, "failsafe_start", detail=failsafe)
        elif not failsafe and self._failsafe_active:
            self.events.add(now, "failsafe_end")
        self._failsafe_active = bool(failsafe)
```
und vor dem `return ThermocastData(...)` (ersetzt das bisherige `if hour_closed: await self.async_save()` – das `hour_closed`-Speichern bleibt, zusätzlich):
```python
        if self.events.dirty:
            await self.async_save()
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest -q`
Expected: PASS (alle).

- [ ] **Step 6: Commit**

```bash
git add custom_components/thermocast/events.py custom_components/thermocast/coordinator.py tests/test_events.py
git commit -m "feat: persistent event log (control, fail-safe, forecast, window, writes)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Historie, View-Builder und Coordinator-Verdrahtung

**Files:**
- Create: `custom_components/thermocast/history.py`, `custom_components/thermocast/view_builder.py`
- Modify: `custom_components/thermocast/coordinator.py`, `custom_components/thermocast/core/forecast.py`
- Test: `tests/test_view_builder.py`

**Interfaces:**
- Consumes: alles aus Tasks 1–8.
- Produces:
  - `history.async_fetch_states(hass, entity_ids, start, end) -> dict[str, StateSeries] | None` (`None` = kein Recorder).
  - `coordinator.build_plan_inputs(hass, zones: dict[str, ZoneRuntime], fc, idx0, horizon) -> list[ZonePlanInput]` (Name = Subentry-ID; Zonen ohne Temperatur fehlen).
  - `ViewBuilder(coordinator)` mit `view: dict | None`, `async_refresh(now, data)`, `mark_unloaded()`.
  - `ThermocastCoordinator.view` (Property) – vom WebSocket gelesen.
  - `fetch_forecast(..., past_days=2, forecast_days=3)` Standardwerte.

- [ ] **Step 1: Failing tests schreiben**

`tests/test_view_builder.py`:
```python
"""The coordinator builds the panel view next to (never inside) the control path."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from .test_init import _setup_entry, _setup_states


async def test_view_built_with_recorder(recorder_mock, hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    view = mock_entry.runtime_data.view
    assert view["version"] == 1, view
    assert len(view["hours"]) in (71, 72, 73)
    assert view["errors"] == []
    assert view["zones"][0]["id"] == "zone_eg" and view["zones"][0]["name"] == "EG"
    now = view["window"]["now_index"]
    assert view["zones"][0]["plan"]["mean"][now] == 20.4
    assert view["decision"]["override"] == "observe"
    assert view["decision"]["rules"]["max_switches"] == 12
    assert view["candidates"]


async def test_view_without_recorder(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    view = mock_entry.runtime_data.view
    assert "no_recorder" in view["errors"]
    assert all(v is None for v in view["zones"][0]["measured"])


async def test_view_without_forecast(hass: HomeAssistant, mock_entry, aioclient_mock) -> None:
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    view = mock_entry.runtime_data.view
    assert "no_forecast" in view["errors"]
    assert view["explanation"]["code"] == "failsafe"


async def test_view_error_does_not_touch_control(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    with patch("custom_components.thermocast.view_builder.build_view", side_effect=RuntimeError("boom")):
        await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    assert coordinator.view == {"error": "RuntimeError: boom"}
    assert coordinator.last_update_success is True
    assert coordinator.data.failsafe_reason is None
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_view_builder.py -q`
Expected: FAIL (`AttributeError: 'ThermocastCoordinator' object has no attribute 'view'`).

- [ ] **Step 3: Prognose-Fenster erweitern**

In `core/forecast.py` die Standardwerte von `fetch_forecast` ändern:
```python
    past_days: int = 2,
    forecast_days: int = 3,
```

- [ ] **Step 4: `history.py` anlegen**

```python
"""Recorder access for the panel: state sequences of the last ~2 days."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .core.series import StateSeries


async def async_fetch_states(
    hass: HomeAssistant, entity_ids: Iterable[str], start: datetime, end: datetime
) -> dict[str, StateSeries] | None:
    """State changes per entity in [start, end] incl. the state at ``start``. None if there is no recorder."""
    if "recorder" not in hass.config.components:
        return None
    ids = sorted({e for e in entity_ids if e})
    if not ids:
        return {}
    from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415 - optional dependency

    def _query():
        return history.get_significant_states(
            hass, start, end, entity_ids=ids, significant_changes_only=False, minimal_response=True, no_attributes=True
        )

    raw = await get_instance(hass).async_add_executor_job(_query)
    out: dict[str, StateSeries] = {}
    for eid, states in raw.items():
        series: StateSeries = []
        for st in states:
            if isinstance(st, dict):
                series.append((dt_util.parse_datetime(st["last_changed"]), st["state"]))
            else:
                series.append((st.last_changed, st.state))
        out[eid] = series
    return out
```

- [ ] **Step 5: Plan-Inputs im Coordinator als Funktion herauslösen**

In `coordinator.py` oberhalb von `class ZoneRuntime` nichts ändern; **nach** der Klasse `ThermocastData` diese Funktion einfügen:
```python
def build_plan_inputs(
    hass: HomeAssistant, zones: dict[str, ZoneRuntime], fc: Forecast, idx0: int, horizon: int
) -> list[ZonePlanInput]:
    """Plan inputs from forecast index ``idx0`` for ``horizon`` hours (zones without temperature are left out).

    Prediction i refers to the end of hour i, i.e. ``fc.times[idx0 + 1 + i]`` (comfort is evaluated there).
    """
    horizon = min(horizon, len(fc.times) - idx0 - 1)
    inputs: list[ZonePlanInput] = []
    for sid, z in zones.items():
        temp = _mean([_num(hass, e) for e in z.cfg.get(CONF_TEMP_SENSORS, [])])
        if temp is None:
            continue
        neighbors = tuple(
            (v if (v := _num(hass, e)) is not None else temp) for e in z.cfg.get(CONF_NEIGHBOR_SENSORS, [])
        )
        gains = tuple((_num(hass, e) or 0.0) for e in z.cfg.get(CONF_GAIN_ENTITIES, []))
        future = [
            HourRecord(temp=temp, t_out=fc.t_out[idx0 + h], irr=fc.irr_at(idx0 + h), q=0.0, neighbors=neighbors, gains=gains)
            for h in range(horizon)
        ]
        inputs.append(
            ZonePlanInput(
                name=sid, model=z.model, temp_now=temp, future=future,
                comfort_low=[zone_comfort(z, fc.times[idx0 + 1 + h]) for h in range(horizon)],
                q_on=z.q_on, leads_release=bool(z.cfg.get(CONF_LEADS_RELEASE, True)),
            )
        )
    return inputs


def zone_comfort(z: ZoneRuntime, when: datetime) -> float | None:
    """Lower comfort bound at ``when`` (None outside the comfort window)."""
    start, end = _parse_time(z.cfg[CONF_ACTIVE_FROM]), _parse_time(z.cfg[CONF_ACTIVE_TO])
    if not _in_window(dt_util.as_local(when), start, end):
        return None
    return float(z.cfg[CONF_COMFORT_TEMP]) - float(z.cfg[CONF_COMFORT_BAND])
```

`_forecast_and_plan` – den Teil ab `horizon = min(HORIZON_HOURS, …)` bis einschließlich `if not inputs: …return …` ersetzen durch:
```python
        horizon = min(HORIZON_HOURS, len(fc.times) - idx0 - 1)
        times = [fc.times[idx0 + 1 + h] for h in range(horizon)]
        inputs = build_plan_inputs(hass, self.zones, fc, idx0, horizon)
        failsafe = None
        planned_ids = {zi.name for zi in inputs}
        for sid, z in self.zones.items():
            if sid not in planned_ids and z.cfg.get(CONF_LEADS_RELEASE, True):
                failsafe = f"no_temperature:{z.title}"
        for zi in inputs:
            z = self.zones[zi.name]
            zones_out[zi.name] = ZoneResult(
                temp=zi.temp_now, times=times, comfort_low=zi.comfort_low, mae=z.model.mae,
                n_updates=z.model.n_updates, solar=z.model.solar_response(), params=z.model.params(),
            )

        if not inputs:
            return {
                "zones": zones_out, "want_heat": True, "failsafe": failsafe or "no_zones",
                "block_start": None, "block_end": None,
            }
```
und in der Schleife nach dem Planen `sid_by_name` entfernen:
```python
        for zi in inputs:
            pred = result.free_run[zi.name]
            zr = zones_out[zi.name]
```
Außerdem `lengths = tuple(...)` ersetzen durch `lengths = block_lengths_for(min_block)` und importieren: `from .core.rollout import block_lengths_for`. Die nun ungenutzte lokale Variable `hass = self.hass` in `_forecast_and_plan` bleibt (wird von `build_plan_inputs` genutzt).

- [ ] **Step 6: `view_builder.py` anlegen**

```python
"""Assemble the panel view next to the control path (never inside it)."""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial
import logging
import math
from statistics import fmean
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONFIDENCE_Z,
    CONF_GAIN_ENTITIES,
    CONF_HEAT_TYPE,
    CONF_HEATING_ACTIVE,
    CONF_LEADS_RELEASE,
    CONF_MIN_BLOCK_H,
    CONF_NEIGHBOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
    CONF_TEMP_SENSORS,
    DEFAULT_CONFIDENCE_Z,
    DEFAULT_MIN_BLOCK_H,
    DOMAIN,
)
from .core.explain import PlanSnapshot, plan_change, snapshot_from
from .core.rollout import ActuatorState, block_lengths_for
from .core.rules import binary_value, release_state
from .core.series import hourly_fraction, hourly_mean
from .core.view import ZoneViewInput, align, build_view, compute_outlook, make_window
from .history import async_fetch_states

if TYPE_CHECKING:
    from .coordinator import ThermocastCoordinator, ThermocastData

_LOGGER = logging.getLogger(__name__)
HOUR = timedelta(hours=1)


def _round(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


class ViewBuilder:
    def __init__(self, coordinator: ThermocastCoordinator) -> None:
        self._c = coordinator
        self.view: dict[str, Any] | None = None
        self._key: tuple | None = None
        self._window_start: datetime | None = None
        self._window_end: datetime | None = None
        self._snapshots: dict[datetime, PlanSnapshot] = {}

    def mark_unloaded(self) -> None:
        self.view = {"error": "unloaded"}

    async def async_refresh(self, now: datetime, data: ThermocastData) -> None:
        """Rebuild on a new hour / forecast / fail-safe change, otherwise only refresh decision + events."""
        c = self._c
        try:
            hour0 = now.replace(minute=0, second=0, microsecond=0)
            key = (hour0, c.forecast.fetched_at if c.forecast else None, data.failsafe_reason)
            if self.view is None or "error" in self.view or key != self._key:
                self.view = await self._async_build(now, hour0, data)
                self._key = key
            else:
                self.view = {
                    **self.view,
                    "generated_at": now.isoformat(),
                    "decision": self._decision(now, data),
                    "events": self._events(),
                }
        except Exception as err:  # noqa: BLE001 - the panel must never break control
            _LOGGER.exception("Thermocast: building the panel view failed")
            self.view = {"error": f"{type(err).__name__}: {err}"}

    # ------------------------------------------------------------------ parts
    def _decision(self, now: datetime, data: ThermocastData) -> dict[str, Any]:
        a = self._c.actuator
        rules = a.rules
        return {
            "planner_wants": data.planner_heat,
            "applied": data.release_on,
            "control_enabled": data.control_enabled,
            "override": data.override,
            "failsafe_reason": data.failsafe_reason,
            "switches_today": a.switches_today,
            "max_switches": rules.max_switches,
            "rules": {"min_block_h": rules.min_block_h, "min_pause_h": rules.min_pause_h,
                      "max_switches": rules.max_switches},
            "since_last_change_min": round(a.elapsed_h(now) * 60) if a.last_change else None,
            "forecast_age_min": _round(data.forecast_age_min, 0),
            "release_entity": a.entity_id,
            "release_state": a.entity_is_on(),
        }

    def _events(self) -> list[dict[str, Any]]:
        start, end = self._window_start, self._window_end
        return [
            e for e in self._c.events.to_list()
            if start is None or end is None or start <= datetime.fromisoformat(e["time"]) < end
        ]

    def _friendly(self, entity_id: str) -> str:
        st = self._c.hass.states.get(entity_id)
        return st.name if st else entity_id

    async def _async_build(self, now: datetime, hour0: datetime, data: ThermocastData) -> dict[str, Any]:
        c = self._c
        hass = c.hass
        cfg = c.config_entry.data
        tz_name = hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name)
        window = make_window(now, tz, tz_name)
        self._window_start, self._window_end = window.hours[0], window.end
        hours, n = window.hours, len(window.hours)
        errors: list[str] = []

        # ---------------------------------------------------------------- past
        planner_eid = er.async_get(hass).async_get_entity_id(
            "binary_sensor", DOMAIN, f"{c.config_entry.entry_id}_heating_release"
        )
        sensors = {sid: list(z.cfg.get(CONF_TEMP_SENSORS, [])) for sid, z in c.zones.items()}
        outdoor, pump, rel = cfg.get(CONF_OUTDOOR_SENSOR), cfg.get(CONF_HEATING_ACTIVE), cfg.get(CONF_RELEASE_ENTITY)
        wanted = {e for lst in sensors.values() for e in lst} | {outdoor, pump, rel, planner_eid}
        states = await async_fetch_states(hass, {e for e in wanted if e}, hours[0], now)
        if states is None:
            errors.append("no_recorder")
            states = {}

        def mean_of(eid: str | None) -> list[float | None]:
            return hourly_mean(states.get(eid, []), hours, now) if eid else [None] * n

        def flags(eid: str | None, value_of) -> list[float | None]:
            return hourly_fraction(states.get(eid, []), hours, now, value_of) if eid else [None] * n

        rel_domain = rel.split(".")[0] if rel else ""

        def rel_value(s: str) -> float | None:
            v = release_state(s, rel_domain, cfg.get(CONF_RELEASE_ON), cfg.get(CONF_RELEASE_OFF))
            return None if v is None else float(v)

        release = [None if f is None else f >= 0.5 for f in flags(rel, rel_value)]
        planner = [None if f is None else f >= 0.5 for f in flags(planner_eid, binary_value)]
        heating_actual = flags(pump, binary_value)
        t_meas = mean_of(outdoor)

        # ------------------------------------------------------------ forecast
        from .coordinator import build_plan_inputs, zone_comfort  # noqa: PLC0415 - avoid import cycle

        fc = c.forecast
        idx0 = fc.index_of(now) if fc else None
        zval = float(c.config_entry.options.get(CONF_CONFIDENCE_Z, DEFAULT_CONFIDENCE_Z))
        t_fc: list[float | None] = [None] * n
        irr: list[dict[str, Any]] = []
        outlook = None
        change = None
        labels: dict[str, str] = {}
        for z in c.zones.values():
            for s in z.model.spec.surfaces:
                labels.setdefault(s.key, s.name or f"{s.kind} {round(s.azimuth)}°")
        if fc is None or idx0 is None:
            errors.append("no_forecast")
        else:
            t_fc = align(fc.times, fc.t_out, hours)
            irr = [
                {"key": key, "label": labels.get(key, key), "values": [_round(v, 0) for v in align(fc.times, vals, hours)]}
                for key, vals in fc.irr.items()
                if key != "0_0" or not labels
            ]
            inputs = build_plan_inputs(hass, c.zones, fc, idx0, len(fc.times) - idx0 - 1)
            if inputs:
                rules = c.actuator.rules
                state = ActuatorState(
                    on=data.release_on,
                    since_h=c.actuator.elapsed_h(now) if c.control_enabled else math.inf,
                    switches_today=c.actuator.switches_today if c.control_enabled else 0,
                )
                steps = n - window.now_index
                day_index = [
                    hours[min(window.now_index + h, n - 1)].astimezone(tz).toordinal() for h in range(steps)
                ]
                lengths = block_lengths_for(float(c.config_entry.options.get(CONF_MIN_BLOCK_H, DEFAULT_MIN_BLOCK_H)))
                outlook = await hass.async_add_executor_job(
                    partial(compute_outlook, inputs, steps, rules, state, day_index, hour0, z=zval, block_lengths=lengths)
                )
                snap = snapshot_from(outlook.rollout.first, outlook.first_inputs, hour0)
                self._snapshots = {h: s for h, s in self._snapshots.items() if h >= hour0 - HOUR}
                self._snapshots[hour0] = snap
                change = plan_change(self._snapshots.get(hour0 - HOUR), snap, {zi.name: zi.temp_now for zi in inputs})

        # --------------------------------------------------------------- zones
        zones: list[ZoneViewInput] = []
        for sid, z in c.zones.items():
            series = [mean_of(e) for e in sensors[sid]]
            measured = [
                fmean(vals) if (vals := [s[i] for s in series if s[i] is not None]) else None for i in range(n)
            ]
            group_labels = dict(z.model.group_labels())
            for i, e in enumerate(z.cfg.get(CONF_NEIGHBOR_SENSORS, [])):
                group_labels[f"neighbor:{i}"] = self._friendly(e)
            for i, e in enumerate(z.cfg.get(CONF_GAIN_ENTITIES, [])):
                group_labels[f"gain:{i}"] = self._friendly(e)
            zones.append(
                ZoneViewInput(
                    id=sid, name=z.title, heat_type=z.cfg.get(CONF_HEAT_TYPE, "fbh"),
                    leads=bool(z.cfg.get(CONF_LEADS_RELEASE, True)), measured=measured,
                    comfort_low=[zone_comfort(z, h) for h in hours], group_labels=group_labels,
                )
            )
        zones.sort(key=lambda zv: (not zv.leads, zv.name.lower()))

        return build_view(
            window=window, tz=tz, generated_at=now, t_out_measured=t_meas, t_out_forecast=t_fc, irr=irr,
            heating_actual=heating_actual, release=release, planner=planner, zones=zones, outlook=outlook,
            z=zval, decision=self._decision(now, data), plan_change=change, events=self._events(), errors=errors,
        )
```

- [ ] **Step 7: Coordinator verdrahten**

Import: `from .view_builder import ViewBuilder`.

In `__init__` ergänzen:
```python
        self.view_builder = ViewBuilder(self)
```

Nach `async_shutdown_failsafe` diese Property einfügen und `async_shutdown_failsafe` erweitern:
```python
    @property
    def view(self) -> dict[str, Any] | None:
        return self.view_builder.view

    async def async_shutdown_failsafe(self) -> None:
        if self.control_enabled:
            await self.actuator.async_force_on()
        await self.async_save()
        self.view_builder.mark_unloaded()
        self.async_update_listeners()  # open panels re-subscribe
```

In `_update` das `return ThermocastData(...)` umbauen zu:
```python
        data = ThermocastData(
            zones=result["zones"],
            want_heat=want_heat,
            release_on=release,
            control_enabled=self.control_enabled,
            block_start=result["block_start"],
            block_end=result["block_end"],
            failsafe_reason=failsafe,
            forecast_age_min=age,
            override=override,
            planner_heat=result["want_heat"],
        )
        await self.view_builder.async_refresh(now, data)
        return data
```

- [ ] **Step 8: Tests laufen lassen**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, „All checks passed!“. Falls `recorder_mock` meckert, dass der Recorder vor `hass` stehen muss: Parameterreihenfolge ist bereits `recorder_mock, hass` – nicht ändern.

- [ ] **Step 9: Commit**

```bash
git add custom_components/thermocast/history.py custom_components/thermocast/view_builder.py custom_components/thermocast/coordinator.py custom_components/thermocast/core/forecast.py tests/test_view_builder.py
git commit -m "feat: build the panel view from recorder history and the controller rollout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: WebSocket-Abo, Panel-Registrierung, Lit vendoren

**Files:**
- Create: `custom_components/thermocast/websocket_api.py`, `custom_components/thermocast/panel.py`, `custom_components/thermocast/frontend/lit.js`
- Modify: `custom_components/thermocast/__init__.py`, `custom_components/thermocast/manifest.json`
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: `ThermocastCoordinator.view`, `async_add_listener`, `view_builder.mark_unloaded` (Task 9).
- Produces: WebSocket-Befehl `thermocast/subscribe` → `result` und Events `{"view": <dict>}`; Fehlercode `not_loaded`. Panel-URL `/thermocast`, Webcomponent `thermocast-panel`, Modul `/thermocast_static/thermocast-panel.js?v=<version>`; statisch `/thermocast_static/*` → `frontend/`. `frontend/lit.js` exportiert `LitElement, html, svg, css, nothing`.

- [ ] **Step 1: Lit vendoren**

```bash
mkdir -p custom_components/thermocast/frontend
{ printf '/* Lit 3.3.1 – lit-core.min.js from https://cdn.jsdelivr.net/gh/lit/dist@3.3.1/core/lit-core.min.js\n * Copyright 2017 Google LLC, SPDX-License-Identifier: BSD-3-Clause. Vendored: no build step, no CDN at runtime. */\n'; \
  curl -fsSL https://cdn.jsdelivr.net/gh/lit/dist@3.3.1/core/lit-core.min.js | sed '/sourceMappingURL/d'; } \
  > custom_components/thermocast/frontend/lit.js
grep -o 'ft as LitElement' custom_components/thermocast/frontend/lit.js
```
Expected: `ft as LitElement`.

- [ ] **Step 2: Failing tests schreiben**

`tests/test_panel.py`:
```python
"""Sidebar panel, static files and the websocket subscription."""
from __future__ import annotations

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.core import HomeAssistant

from .test_init import _setup_entry, _setup_states


async def test_panel_registered_and_removed(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    panel = hass.data[DATA_PANELS]["thermocast"]
    assert panel.sidebar_title == "Thermocast"
    client = await hass_client()
    resp = await client.get("/thermocast_static/lit.js")
    assert resp.status == 200
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    assert "thermocast" not in hass.data[DATA_PANELS]


async def test_subscribe_pushes_view(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_ws_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "thermocast/subscribe"})
    assert (await ws.receive_json())["success"] is True
    first = await ws.receive_json()
    assert first["event"]["view"]["version"] == 1

    await mock_entry.runtime_data.async_refresh()
    pushed = await ws.receive_json()
    assert pushed["event"]["view"]["version"] == 1

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    gone = await ws.receive_json()
    assert gone["event"]["view"] == {"error": "unloaded"}


async def test_subscribe_without_entry(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_ws_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "thermocast/subscribe"})
    msg = await ws.receive_json()
    assert msg["success"] is False and msg["error"]["code"] == "not_loaded"
```

- [ ] **Step 3: Tests laufen lassen – müssen fehlschlagen**

Run: `uv run pytest tests/test_panel.py -q`
Expected: FAIL (`KeyError: 'thermocast'` bzw. unbekannter Befehl).

- [ ] **Step 4: `websocket_api.py` anlegen**

```python
"""Websocket API for the panel: push the view on every coordinator update."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command({vol.Required("type"): "thermocast/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "Thermocast is not loaded")
        return
    coordinator = entries[0].runtime_data

    @callback
    def forward() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"view": coordinator.view}))

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(forward)
    connection.send_result(msg["id"])
    forward()
```

- [ ] **Step 5: `panel.py` anlegen**

```python
"""Sidebar panel: static files + panel registration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_URL = "thermocast"
STATIC_URL = "/thermocast_static"
_STATIC_FLAG = f"{DOMAIN}_static_registered"


async def async_register_static(hass: HomeAssistant) -> None:
    """Static paths cannot be removed – register once per HA run."""
    if hass.data.get(_STATIC_FLAG):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    hass.data[_STATIC_FLAG] = True


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="thermocast-panel",
        sidebar_title="Thermocast",
        sidebar_icon="mdi:home-thermometer",
        module_url=f"{STATIC_URL}/thermocast-panel.js?v={version}",
        require_admin=False,
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
```

- [ ] **Step 6: `__init__.py` und Manifest**

`__init__.py` komplett:
```python
"""Thermocast – predictive heating release with online-learned room models."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import panel, websocket_api
from .const import DOMAIN, PLATFORMS
from .coordinator import ThermocastCoordinator

type ThermocastConfigEntry = ConfigEntry[ThermocastCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket_api.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ThermocastConfigEntry) -> bool:
    # house device first: zone devices link to it via its registry id
    house = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    coordinator = ThermocastCoordinator(hass, entry)
    coordinator.house_device_id = house.id
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await panel.async_register_static(hass)
    await panel.async_register_panel(hass, str((await async_get_integration(hass, DOMAIN)).version))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThermocastConfigEntry) -> bool:
    coordinator: ThermocastCoordinator = entry.runtime_data
    # never leave the heating blocked when the integration goes away
    await coordinator.async_shutdown_failsafe()
    panel.async_remove_panel(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ThermocastConfigEntry) -> None:
    """Options or zones (subentries) changed."""
    await hass.config_entries.async_reload(entry.entry_id)
```

`manifest.json`: Zeile `"after_dependencies": ["recorder"],` behalten und davor einfügen:
```json
  "dependencies": ["frontend", "http", "panel_custom", "websocket_api"],
```

- [ ] **Step 7: Tests laufen lassen**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, „All checks passed!“.

- [ ] **Step 8: Commit**

```bash
git add custom_components/thermocast/websocket_api.py custom_components/thermocast/panel.py custom_components/thermocast/frontend/lit.js custom_components/thermocast/__init__.py custom_components/thermocast/manifest.json tests/test_panel.py
git commit -m "feat: sidebar panel registration, websocket subscription, vendored Lit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Frontend-Gerüst, Story-Kopf, Dev-Seite

**Files:**
- Create: `custom_components/thermocast/frontend/i18n.js`, `thermocast-panel.js`, `tc-summary.js`, `tc-days.js` (Platzhalter-frei: vollständige Datei in Task 12 – hier minimale Registrierung, siehe Step 4), `frontend/dev/index.html`, `scripts/sample_view.py`
- Generated: `custom_components/thermocast/frontend/dev/sample-view.json`

**Interfaces:**
- Consumes: `view` (Spec 4.4), WebSocket `thermocast/subscribe` (Task 10), `core.view.build_view` (Task 7).
- Produces:
  - `i18n.js`: `langOf(hass) -> "de"|"en"`, `t(lang, key, vars)`, `fmtTime(iso, lang, tz)`, `fmtWhen(iso, lang, tz, todayIso)` („morgen 06:00“), `fmtDay(iso, lang, tz)`, `fmtNum(x, lang, digits)`, `fmtSigned(x, lang, digits)`.
  - `thermocast-panel` (Properties `hass`, `narrow`, `panel`, `demoView`); Events von Kindern: `tc-hover` (detail: Stundenindex|null), `tc-candidate` (detail: Kandidatenindex|null).
  - Kind-Elemente erhalten `.view .lang .tz` (+ `.hoverIndex .hoverCandidate` für die Zeitachse).

- [ ] **Step 1: `i18n.js`**

```js
// Texts and formatting (de/en). Language from hass.locale, time zone from hass.config.
const STR = {
  en: {
    loading: "Loading…",
    not_loaded: "Thermocast is not loaded. Waiting for the integration…",
    error: "Error",
    view_unavailable: "View not available",
    heating_on: "Heating: ON",
    heating_off: "Heating: OFF",
    next_block: "next block {start}–{end}",
    no_block: "no block planned",
    exp_block_planned: "{zone} drops below {temp} °C without heating {when}. The screed needs lead time, so the block starts {lead} h earlier.",
    exp_heating_now: "{zone} would drop below {temp} °C {when} – the block runs until {end}.",
    exp_heating_now_short: "A heating block runs until {end}.",
    exp_no_need: "No zone drops below its comfort band within the next 24 h.",
    exp_no_need_sun: "No heating needed – sunshine keeps the zones above their comfort band.",
    exp_violation_accepted: "{zone} drops slightly below comfort {when}, but a block would cost more.",
    exp_failsafe: "Fail-safe active ({reason}): heating stays allowed.",
    exp_no_forecast: "No weather forecast – heating stays allowed.",
    override_min_block: "The planner would stop, but the minimum block keeps heating on.",
    override_min_pause: "The planner wants heat, but the minimum pause is still running.",
    override_budget: "The planner would stop, but today's switch budget is used up – heating stays on.",
    mode_observe: "Observe mode",
    mode_active: "Control active",
    forecast_age: "Forecast {min} min old",
    switches: "Switches today {n}/{max}",
    failsafe_chip: "Fail-safe: {reason}",
    robust_clear: "Clear decision",
    robust_close: "Close decision",
    sigma_driven: "Without the safety margin no block would be needed",
    plan_changed: "Probably changed: {what}.",
    pc_moved: "block {old} → {new}",
    pc_new: "new block {new}",
    pc_dropped: "block {old} dropped",
    cause_t_out: "forecast at {at} {delta} K",
    cause_sun: "irradiance at {at} {delta} W/m²",
    cause_room: "{zone} is {delta} K off the expectation",
    yesterday: "Yesterday",
    today: "Today",
    tomorrow: "Tomorrow",
    day_heat: "Heating {h} h · {n} blocks",
    day_heat_today: "Heating {h} h + {p} h planned · {n} + {m} blocks",
    day_heat_plan: "Heating {p} h planned · {m} blocks",
    day_min: "Min. leading zones {t} °C",
    day_min_plan: "Min. lower bound {t} °C",
    day_followed: "Release followed planner {p} %",
    day_std: "Uncertainty ±{s} K",
    weather: "Weather",
    heating: "Heating",
    leads: "leads",
    follows: "follows",
    ht_fbh: "underfloor",
    ht_radiator: "radiator",
    why: "Why?",
    measured: "measured",
    plan: "plan",
    free: "without heating",
    comfort: "comfort",
    t_out: "Outdoor",
    net: "net",
    candidates: "Candidates for the next block",
    c_block: "Block",
    c_violation: "Below comfort K·h",
    c_comfort: "Comfort",
    c_start: "Start",
    c_energy: "Energy",
    c_delay: "Delay",
    c_total: "Total",
    c_none: "no block",
    rules: "Actuator rules",
    r_min_block: "Minimum block {v} h",
    r_min_pause: "Minimum pause {v} h",
    r_budget: "Switch budget {n}/{max}",
    r_observe: "Observe mode (nothing is switched)",
    r_failsafe: "Fail-safe",
    r_entity: "Release entity {e}: {s}",
    allowed: "allowed",
    blocked: "blocked",
    unknown: "unknown",
    g_base: "base",
    g_loss: "loss",
    g_heat: "heating",
    ev_window_open: "Window open – hour not learned",
    ev_forecast_failed: "Forecast fetch failed",
    ev_failsafe_start: "Fail-safe started",
    ev_failsafe_end: "Fail-safe ended",
    ev_control_on: "Control enabled",
    ev_control_off: "Control disabled",
    ev_model_reset: "Model reset (zone changed)",
    ev_release_written: "Release written",
    err_no_recorder: "No recorder history – the past stays empty.",
    err_no_forecast: "No forecast – the future stays empty.",
  },
  de: {
    loading: "Lade…",
    not_loaded: "Thermocast ist nicht geladen. Warte auf die Integration…",
    error: "Fehler",
    view_unavailable: "Ansicht nicht verfügbar",
    heating_on: "Heizen: AN",
    heating_off: "Heizen: AUS",
    next_block: "nächster Block {start}–{end}",
    no_block: "kein Block geplant",
    exp_block_planned: "{zone} fällt ohne Heizen {when} unter {temp} °C. Der Estrich braucht Vorlauf, deshalb startet der Block {lead} h vorher.",
    exp_heating_now: "{zone} würde {when} unter {temp} °C fallen – der Block läuft bis {end}.",
    exp_heating_now_short: "Ein Heizblock läuft bis {end}.",
    exp_no_need: "Keine Zone fällt in den nächsten 24 h unter ihr Komfortband.",
    exp_no_need_sun: "Kein Heizen nötig – die Sonne hält die Zonen über dem Komfortband.",
    exp_violation_accepted: "{zone} fällt {when} knapp unter Komfort, ein Block wäre aber teurer.",
    exp_failsafe: "Fail-safe aktiv ({reason}): Heizen bleibt erlaubt.",
    exp_no_forecast: "Keine Wetterprognose – Heizen bleibt erlaubt.",
    override_min_block: "Der Planer würde stoppen, aber der Mindestblock hält die Heizung an.",
    override_min_pause: "Der Planer will heizen, aber die Mindestpause läuft noch.",
    override_budget: "Der Planer würde stoppen, aber das Tagesbudget an Wechseln ist aufgebraucht – Heizung bleibt an.",
    mode_observe: "Beobachtungsmodus",
    mode_active: "Steuerung aktiv",
    forecast_age: "Prognose {min} min alt",
    switches: "Wechsel heute {n}/{max}",
    failsafe_chip: "Fail-safe: {reason}",
    robust_clear: "Entscheidung klar",
    robust_close: "Entscheidung knapp",
    sigma_driven: "Ohne Sicherheitsmarge wäre kein Block nötig",
    plan_changed: "Vermutlich geändert: {what}.",
    pc_moved: "Block {old} → {new}",
    pc_new: "neuer Block {new}",
    pc_dropped: "Block {old} entfällt",
    cause_t_out: "Prognose um {at} {delta} K",
    cause_sun: "Einstrahlung um {at} {delta} W/m²",
    cause_room: "{zone} weicht {delta} K von der Erwartung ab",
    yesterday: "Gestern",
    today: "Heute",
    tomorrow: "Morgen",
    day_heat: "Heizen {h} h · {n} Blöcke",
    day_heat_today: "Heizen {h} h + {p} h geplant · {n} + {m} Blöcke",
    day_heat_plan: "Heizen {p} h geplant · {m} Blöcke",
    day_min: "Min. führende Zonen {t} °C",
    day_min_plan: "Min. untere Grenze {t} °C",
    day_followed: "Freigabe folgte Planer {p} %",
    day_std: "Unsicherheit ±{s} K",
    weather: "Wetter",
    heating: "Heizen",
    leads: "führt",
    follows: "folgt",
    ht_fbh: "Fußbodenheizung",
    ht_radiator: "Heizkörper",
    why: "Warum?",
    measured: "gemessen",
    plan: "Plan",
    free: "ohne Heizen",
    comfort: "Komfort",
    t_out: "Außen",
    net: "netto",
    candidates: "Kandidaten für den nächsten Block",
    c_block: "Block",
    c_violation: "unter Komfort K·h",
    c_comfort: "Komfort",
    c_start: "Start",
    c_energy: "Energie",
    c_delay: "Verzögerung",
    c_total: "Summe",
    c_none: "kein Block",
    rules: "Aktor-Regeln",
    r_min_block: "Mindestblock {v} h",
    r_min_pause: "Mindestpause {v} h",
    r_budget: "Wechselbudget {n}/{max}",
    r_observe: "Beobachtungsmodus (es wird nichts geschaltet)",
    r_failsafe: "Fail-safe",
    r_entity: "Freigabe-Entität {e}: {s}",
    allowed: "erlaubt",
    blocked: "gesperrt",
    unknown: "unbekannt",
    g_base: "Basis",
    g_loss: "Verlust",
    g_heat: "Heizen",
    ev_window_open: "Fenster offen – Stunde nicht gelernt",
    ev_forecast_failed: "Prognoseabruf fehlgeschlagen",
    ev_failsafe_start: "Fail-safe begonnen",
    ev_failsafe_end: "Fail-safe beendet",
    ev_control_on: "Steuerung eingeschaltet",
    ev_control_off: "Steuerung ausgeschaltet",
    ev_model_reset: "Modell zurückgesetzt (Zone geändert)",
    ev_release_written: "Freigabe geschrieben",
    err_no_recorder: "Keine Recorder-Historie – die Vergangenheit bleibt leer.",
    err_no_forecast: "Keine Prognose – die Zukunft bleibt leer.",
  },
};

export function langOf(hass) {
  const l = (hass?.locale?.language || hass?.language || "en").slice(0, 2);
  return STR[l] ? l : "en";
}

export function t(lang, key, vars = {}) {
  let s = STR[lang]?.[key] ?? STR.en[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

export function fmtNum(x, lang, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return new Intl.NumberFormat(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(x);
}

export function fmtSigned(x, lang, digits = 1) {
  if (x === null || x === undefined) return "–";
  return (x > 0 ? "+" : "") + fmtNum(x, lang, digits);
}

export function fmtTime(iso, lang, tz) {
  return new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit", timeZone: tz }).format(new Date(iso));
}

export function localDate(iso, tz) {
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: tz })
    .format(new Date(iso));
}

export function fmtDay(iso, lang, tz) {
  return new Intl.DateTimeFormat(lang, { weekday: "short", day: "2-digit", month: "2-digit", timeZone: tz })
    .format(new Date(iso));
}

// "06:00" for today, "morgen 06:00" / "tomorrow 06:00" otherwise.
export function fmtWhen(iso, lang, tz, nowIso) {
  const d = localDate(iso, tz);
  const today = localDate(nowIso, tz);
  const time = fmtTime(iso, lang, tz);
  if (d === today) return (lang === "de" ? "um " : "at ") + time;
  const diff = Math.round((new Date(d) - new Date(today)) / 86400000);
  if (diff === 1) return (lang === "de" ? "morgen " : "tomorrow ") + time;
  if (diff === -1) return (lang === "de" ? "gestern " : "yesterday ") + time;
  return fmtDay(iso, lang, tz) + " " + time;
}
```

- [ ] **Step 2: `thermocast-panel.js`**

```js
import { LitElement, html, css } from "./lit.js";
import { langOf, t } from "./i18n.js";
import "./tc-summary.js";
import "./tc-days.js";
import "./tc-timeline.js";
import "./tc-details.js";

class ThermocastPanel extends LitElement {
  static properties = {
    hass: { attribute: false },
    narrow: { type: Boolean },
    panel: { attribute: false },
    demoView: { attribute: false },
    _view: { state: true },
    _error: { state: true },
    _hoverIndex: { state: true },
    _hoverCandidate: { state: true },
  };

  constructor() {
    super();
    this._view = null;
    this._error = null;
    this._hoverIndex = null;
    this._hoverCandidate = null;
    this._unsub = null;
    this._subscribing = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._subscribe();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._unsubscribe();
  }

  updated(changed) {
    if (changed.has("demoView") && this.demoView) this._view = this.demoView;
    if (changed.has("hass") && !this._unsub && !this._subscribing) this._subscribe();
  }

  async _subscribe() {
    if (this.demoView) {
      this._view = this.demoView;
      return;
    }
    if (!this.hass?.connection || this._unsub || this._subscribing) return;
    this._subscribing = true;
    try {
      this._unsub = await this.hass.connection.subscribeMessage((msg) => this._onMessage(msg), {
        type: "thermocast/subscribe",
      });
      this._error = null;
    } catch (err) {
      this._error = err?.code === "not_loaded" ? "not_loaded" : err?.message || String(err);
      setTimeout(() => this._subscribe(), 5000);
    } finally {
      this._subscribing = false;
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
  }

  _onMessage(msg) {
    const view = msg.view;
    if (view && view.error === "unloaded") {
      // entry reloads (e.g. a zone was edited): subscribe again to the new coordinator
      this._unsubscribe();
      this._view = null;
      this._error = "not_loaded";
      setTimeout(() => this._subscribe(), 2000);
      return;
    }
    this._view = view;
  }

  render() {
    const lang = langOf(this.hass);
    return html`
      <div class="toolbar">
        <ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>
        <div class="title">Thermocast</div>
      </div>
      <div class="content">${this._body(lang)}</div>
    `;
  }

  _body(lang) {
    if (this._error === "not_loaded") return html`<p class="notice">${t(lang, "not_loaded")}</p>`;
    if (this._error) return html`<p class="notice">${t(lang, "error")}: ${this._error}</p>`;
    if (!this._view) return html`<p class="notice">${t(lang, "loading")}</p>`;
    if (this._view.error) return html`<p class="notice">${t(lang, "view_unavailable")}: ${this._view.error}</p>`;
    const v = this._view;
    const tz = v.window.tz || this.hass?.config?.time_zone;
    return html`
      ${v.errors.map((e) => html`<p class="notice small">${t(lang, "err_" + e)}</p>`)}
      <tc-summary .view=${v} .lang=${lang} .tz=${tz}></tc-summary>
      <tc-days .view=${v} .lang=${lang} .tz=${tz}></tc-days>
      <tc-timeline
        .view=${v}
        .lang=${lang}
        .tz=${tz}
        .hoverIndex=${this._hoverIndex}
        .hoverCandidate=${this._hoverCandidate}
        @tc-hover=${(e) => (this._hoverIndex = e.detail)}
      ></tc-timeline>
      <tc-details .view=${v} .lang=${lang} .tz=${tz} @tc-candidate=${(e) => (this._hoverCandidate = e.detail)}></tc-details>
    `;
  }

  static styles = css`
    :host {
      display: block;
      min-height: 100vh;
      background: var(--primary-background-color, #fafafa);
      color: var(--primary-text-color, #212121);
      font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
    }
    .toolbar {
      display: flex;
      align-items: center;
      height: var(--header-height, 56px);
      padding: 0 12px;
      background: var(--app-header-background-color, var(--primary-color, #03a9f4));
      color: var(--app-header-text-color, #fff);
      font-size: 20px;
    }
    .title { margin-left: 8px; }
    .content { max-width: 1600px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
    .notice { padding: 16px; background: var(--card-background-color, #fff); border-radius: 8px; }
    .notice.small { padding: 8px 16px; font-size: 13px; color: var(--secondary-text-color, #727272); }
  `;
}

customElements.define("thermocast-panel", ThermocastPanel);
```

- [ ] **Step 3: `tc-summary.js`**

```js
import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtSigned, fmtTime, fmtWhen } from "./i18n.js";

class TcSummary extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };

  _zoneName(id) {
    return this.view.zones.find((z) => z.id === id)?.name ?? id;
  }

  _when(iso) {
    return fmtWhen(iso, this.lang, this.tz, this.view.generated_at);
  }

  _headline() {
    const { decision, explanation } = this.view;
    const on = decision.planner_wants ?? decision.applied;
    const head = t(this.lang, on ? "heating_on" : "heating_off");
    const nb = explanation.next_block;
    const tail = nb
      ? t(this.lang, "next_block", { start: this._when(nb.start), end: fmtTime(nb.end, this.lang, this.tz) })
      : t(this.lang, "no_block");
    return `${on ? "🔥" : "❄️"} ${head} · ${tail}`;
  }

  _sentence() {
    const ex = this.view.explanation;
    const L = this.lang;
    const zone = ex.driver ? this._zoneName(ex.driver) : "";
    const z = this.view.zones.find((zz) => zz.id === ex.driver);
    const idx = ex.first_violation ? this.view.hours.indexOf(ex.first_violation) : -1;
    const temp = z && idx >= 0 ? fmtNum(z.comfort_low[idx], L) : "–";
    const when = ex.first_violation ? this._when(ex.first_violation) : "";
    switch (ex.code) {
      case "block_planned":
        return t(L, "exp_block_planned", { zone, temp, when, lead: ex.lead_h ?? "–" });
      case "heating_now":
        return ex.driver
          ? t(L, "exp_heating_now", { zone, temp, when, end: fmtTime(ex.next_block.end, L, this.tz) })
          : t(L, "exp_heating_now_short", { end: fmtTime(ex.next_block.end, L, this.tz) });
      case "violation_accepted":
        return t(L, "exp_violation_accepted", { zone, when });
      case "failsafe":
        return t(L, "exp_failsafe", { reason: ex.reason ?? "–" });
      case "no_forecast":
        return t(L, "exp_no_forecast");
      default:
        return t(L, "exp_" + ex.code);
    }
  }

  _planChange() {
    const pc = this.view.plan_change;
    if (!pc) return nothing;
    const L = this.lang;
    const fmtB = (b) => `${this._when(b.start)}–${fmtTime(b.end, L, this.tz)}`;
    let what;
    if (pc.previous_block && pc.current_block)
      what = t(L, "pc_moved", { old: fmtB(pc.previous_block), new: fmtB(pc.current_block) });
    else if (pc.current_block) what = t(L, "pc_new", { new: fmtB(pc.current_block) });
    else what = t(L, "pc_dropped", { old: fmtB(pc.previous_block) });
    const c = pc.cause;
    if (c) {
      const at = c.at ? fmtTime(c.at, L, this.tz) : "";
      const delta = fmtSigned(c.delta, L, c.kind === "sun" ? 0 : 1);
      what += " – " + t(L, "cause_" + c.kind, { at, delta, zone: c.zone ? this._zoneName(c.zone) : "" });
    }
    return html`<p class="change">${t(L, "plan_changed", { what })}</p>`;
  }

  _chips() {
    const { decision: d, robustness: r } = this.view;
    const L = this.lang;
    const chips = [
      html`<span class="chip ${d.control_enabled ? "warn" : "ok"}">${t(L, d.control_enabled ? "mode_active" : "mode_observe")}</span>`,
    ];
    if (d.forecast_age_min !== null && d.forecast_age_min !== undefined)
      chips.push(html`<span class="chip">${t(L, "forecast_age", { min: d.forecast_age_min })}</span>`);
    chips.push(html`<span class="chip">${t(L, "switches", { n: d.switches_today, max: d.max_switches })}</span>`);
    if (d.failsafe_reason) chips.push(html`<span class="chip bad">${t(L, "failsafe_chip", { reason: d.failsafe_reason })}</span>`);
    if (r) {
      chips.push(html`<span class="chip ${r.level === "close" ? "warn" : "ok"}">${t(L, "robust_" + r.level)}</span>`);
      if (r.sigma_driven) chips.push(html`<span class="chip warn">${t(L, "sigma_driven")}</span>`);
    }
    return chips;
  }

  render() {
    if (!this.view) return nothing;
    const o = this.view.decision.override;
    const overrideNote = ["min_block", "min_pause", "budget"].includes(o)
      ? html`<p class="note">${t(this.lang, "override_" + o)}</p>`
      : nothing;
    return html`
      <div class="card">
        <div class="headline">${this._headline()}</div>
        <p class="why">${this._sentence()}</p>
        ${overrideNote} ${this._planChange()}
        <div class="chips">${this._chips()}</div>
      </div>
    `;
  }

  static styles = css`
    .card { background: var(--card-background-color, #fff); border-radius: 12px; padding: 16px 20px; }
    .headline { font-size: 22px; font-weight: 600; }
    .why { margin: 8px 0 0; line-height: 1.5; }
    .note, .change { margin: 6px 0 0; color: var(--secondary-text-color, #727272); }
    .chips { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
    .chip { font-size: 12px; padding: 3px 10px; border-radius: 12px; background: var(--secondary-background-color, #eee); }
    .chip.ok { background: rgba(0, 158, 115, 0.15); }
    .chip.warn { background: rgba(230, 159, 0, 0.2); }
    .chip.bad { background: rgba(213, 94, 0, 0.25); }
  `;
}

customElements.define("tc-summary", TcSummary);
```

- [ ] **Step 4: Minimale Kind-Elemente, damit das Panel lädt**

`tc-days.js`, `tc-timeline.js`, `tc-details.js` je mit diesem Inhalt anlegen (Elementname anpassen: `tc-days`, `tc-timeline`, `tc-details`); Tasks 12/13 ersetzen sie vollständig:
```js
import { LitElement, html } from "./lit.js";

customElements.define("tc-days", class extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };
  render() {
    return html``;
  }
});
```

- [ ] **Step 5: `scripts/sample_view.py`**

```python
"""Write frontend/dev/sample-view.json from synthetic data (no Home Assistant needed).

    uv run python scripts/sample_view.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "thermocast"))
sys.path.insert(0, str(ROOT))

from core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec  # noqa: E402
from core.planner import ZonePlanInput  # noqa: E402
from core.rollout import ActuatorState, block_lengths_for  # noqa: E402
from core.rules import Rules  # noqa: E402
from core.view import ZoneViewInput, build_view, compute_outlook, make_window  # noqa: E402
from tests.synthetic import simulate  # noqa: E402

TZ_NAME = "Europe/Berlin"
TZ = ZoneInfo(TZ_NAME)
NOW = datetime(2026, 10, 4, 18, 5, tzinfo=UTC)
OUT = ROOT / "custom_components" / "thermocast" / "frontend" / "dev" / "sample-view.json"
SURFACES = (
    SurfaceSpec(kind="window", azimuth=90, tilt=90, name="Fenster Ost"),
    SurfaceSpec(kind="roof", azimuth=180, tilt=40, name="Schräge Süd"),
)
COLD = -6.0  # shift the synthetic autumn into a colder week, so the planner has work to do


def _record(sim, k: int) -> HourRecord:
    return HourRecord(
        temp=float(sim["air"][k]), t_out=float(sim["t_out"][k]) + COLD,
        irr={key: float(v[k]) for key, v in sim["irr"].items()}, q=float(sim["q"][k]),
    )


def _model(sim, heat_type: str, train_until: int) -> OnlineZoneModel:
    m = OnlineZoneModel(ZoneSpec(heat_type=heat_type, surfaces=SURFACES))
    for k in range(train_until):
        m.update(_record(sim, k), float(sim["air"][k + 1]))
    return m


def main() -> None:
    sim = simulate(days=40, seed=4)
    w = make_window(NOW, TZ, TZ_NAME)
    n, now = len(w.hours), w.now_index
    base = 30 * 24 - now  # sim index of window hour 0
    k_of = [base + i for i in range(n + 26)]

    def comfort(t: datetime, low: float) -> float | None:
        return low if 6 <= t.astimezone(TZ).hour < 22 else None

    zones_cfg = [("zone_eg", "EG", "fbh", True, 20.2), ("zone_eltern", "Elternschlafzimmer", "radiator", False, 19.0)]
    plan_inputs, view_zones = [], []
    for zid, name, heat_type, leads, low in zones_cfg:
        m = _model(sim, heat_type, base + now)
        fut_k = k_of[now : now + 76]
        fut_k = [k for k in fut_k if k < len(sim["t_out"])]
        times = [w.hours[now] + timedelta(hours=h + 1) for h in range(len(fut_k))]
        plan_inputs.append(
            ZonePlanInput(
                name=zid, model=m, temp_now=float(sim["air"][base + now]),
                future=[HourRecord(temp=0.0, t_out=float(sim["t_out"][k]) + COLD,
                                   irr={key: float(v[k]) for key, v in sim["irr"].items()}) for k in fut_k],
                comfort_low=[comfort(t, low) for t in times], q_on=15.0, leads_release=leads,
            )
        )
        view_zones.append(
            ZoneViewInput(
                id=zid, name=name, heat_type=heat_type, leads=leads,
                measured=[round(float(sim["air"][base + i]), 2) if i < now else None for i in range(n)],
                comfort_low=[comfort(h, low) for h in w.hours], group_labels=m.group_labels(),
            )
        )

    steps = n - now
    day_index = [w.hours[min(now + h, n - 1)].astimezone(TZ).toordinal() for h in range(steps)]
    outlook = compute_outlook(plan_inputs, steps, Rules(), ActuatorState(on=False), day_index, w.hours[now],
                              block_lengths=block_lengths_for(3))
    heating = [1.0 if i < now and sim["q"][base + i] > 0 else (0.0 if i < now else None) for i in range(n)]
    view = build_view(
        window=w, tz=TZ, generated_at=NOW,
        t_out_measured=[round(float(sim["t_out"][base + i]) + COLD, 1) if i <= now else None for i in range(n)],
        t_out_forecast=[round(float(sim["t_out"][base + i]) + COLD, 1) for i in range(n)],
        irr=[{"key": key, "label": label, "values": [round(float(sim["irr"][key][base + i])) for i in range(n)]}
             for key, label in (("90_90", "Fenster Ost"), ("40_180", "Schräge Süd"))],
        heating_actual=heating,
        release=[True if i < now else None for i in range(n)],
        planner=[(h >= 0.5) if h is not None else None for h in heating],
        zones=view_zones, outlook=outlook, z=1.0,
        decision={
            "planner_wants": outlook.rollout.first.heat_now, "applied": True, "control_enabled": False,
            "override": "observe", "failsafe_reason": None, "switches_today": 0, "max_switches": 12,
            "rules": {"min_block_h": 3, "min_pause_h": 2, "max_switches": 12}, "since_last_change_min": None,
            "forecast_age_min": 12, "release_entity": "number.boiler_summer_threshold", "release_state": True,
        },
        plan_change={
            "previous_block": {"start": w.hours[now + 6].isoformat(), "end": w.hours[now + 10].isoformat()},
            "current_block": {"start": w.hours[now + 5].isoformat(), "end": w.hours[now + 9].isoformat()},
            "cause": {"kind": "t_out", "delta": -1.2, "at": w.hours[now + 10].isoformat(), "zone": None},
        },
        events=[
            {"time": (w.hours[8] + timedelta(minutes=20)).isoformat(), "type": "window_open", "zone": "zone_eltern"},
            {"time": (w.hours[27] + timedelta(minutes=5)).isoformat(), "type": "forecast_failed"},
        ],
        errors=[],
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(view, ensure_ascii=False))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
```

Run: `uv run python scripts/sample_view.py`
Expected: `wrote …/sample-view.json (NN KB)` mit NN < 100.

- [ ] **Step 6: Dev-Seite `frontend/dev/index.html`**

```html
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Thermocast Panel Dev</title>
    <style>
      body { margin: 0; font-family: Roboto, system-ui, sans-serif; }
      body.dark {
        --primary-background-color: #111111;
        --secondary-background-color: #282828;
        --card-background-color: #1c1c1c;
        --primary-text-color: #e1e1e1;
        --secondary-text-color: #9b9b9b;
        --divider-color: #3a3a3a;
      }
      .devbar { position: fixed; right: 8px; top: 8px; z-index: 10; background: #fff8; padding: 4px 8px; border-radius: 6px; font-size: 12px; }
    </style>
  </head>
  <body>
    <div class="devbar">
      <label><input type="checkbox" id="dark" /> dark</label>
      <a href="?lang=de">de</a> · <a href="?lang=en">en</a>
    </div>
    <script type="module">
      import "../thermocast-panel.js";
      const view = await (await fetch("./sample-view.json")).json();
      const el = document.createElement("thermocast-panel");
      el.hass = { locale: { language: new URLSearchParams(location.search).get("lang") || "de" }, config: { time_zone: "Europe/Berlin" } };
      el.demoView = view;
      document.body.append(el);
      document.getElementById("dark").onchange = (e) => document.body.classList.toggle("dark", e.target.checked);
    </script>
  </body>
</html>
```

- [ ] **Step 7: Im Browser prüfen**

Run: `uv run python -m http.server -d custom_components/thermocast/frontend 8765` (im Hintergrund), dann `http://localhost:8765/dev/` öffnen.
Expected: Toolbar „Thermocast“, Story-Karte mit Überschrift, einem Begründungssatz, Planänderungs-Satz und Chips; keine Fehler in der Browser-Konsole. `?lang=en` zeigt englische Texte.

- [ ] **Step 8: Commit**

```bash
git add custom_components/thermocast/frontend scripts/sample_view.py
git commit -m "feat(panel): panel shell, i18n, story header, dev page with sample view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Tageskarten und Details (Kandidaten, Aktor-Regeln)

**Files:**
- Modify (ersetzen): `custom_components/thermocast/frontend/tc-days.js`, `custom_components/thermocast/frontend/tc-details.js`

**Interfaces:**
- Consumes: `view.days`, `view.candidates`, `view.decision` (Spec 4.4); `i18n.js` (Task 11).
- Produces: `tc-details` feuert `tc-candidate` (detail: Index in `view.candidates` oder `null`).

- [ ] **Step 1: `tc-days.js` ersetzen**

```js
import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtDay } from "./i18n.js";

class TcDays extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };

  _card(d) {
    const L = this.lang;
    const title = { past: "yesterday", today: "today", future: "tomorrow" }[d.kind];
    const lines = [];
    if (d.kind === "past") lines.push(t(L, "day_heat", { h: fmtNum(d.heat_hours ?? 0, L), n: d.blocks ?? 0 }));
    if (d.kind === "today")
      lines.push(
        t(L, "day_heat_today", {
          h: fmtNum(d.heat_hours ?? 0, L),
          p: fmtNum(d.heat_hours_planned ?? 0, L),
          n: d.blocks ?? 0,
          m: d.blocks_planned ?? 0,
        }),
      );
    if (d.kind === "future")
      lines.push(t(L, "day_heat_plan", { p: fmtNum(d.heat_hours_planned ?? 0, L), m: d.blocks_planned ?? 0 }));
    if (d.min_leading !== null) lines.push(t(L, "day_min", { t: fmtNum(d.min_leading, L) }));
    if (d.min_leading_planned !== null) lines.push(t(L, "day_min_plan", { t: fmtNum(d.min_leading_planned, L) }));
    if (d.release_followed !== null) lines.push(t(L, "day_followed", { p: Math.round(d.release_followed * 100) }));
    if (d.std_end !== null) lines.push(t(L, "day_std", { s: fmtNum(d.std_end, L) }));
    return html`<div class="day ${d.kind}">
      <div class="title">${t(L, title)} <span class="date">${fmtDay(d.date + "T12:00:00", L, this.tz)}</span></div>
      ${lines.map((l) => html`<div>${l}</div>`)}
    </div>`;
  }

  render() {
    if (!this.view) return nothing;
    return html`<div class="days">${this.view.days.map((d) => this._card(d))}</div>`;
  }

  static styles = css`
    .days { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    @media (max-width: 700px) { .days { grid-template-columns: 1fr; } }
    .day { background: var(--card-background-color, #fff); border-radius: 12px; padding: 12px 16px; font-size: 14px; line-height: 1.6; }
    .day.today { outline: 2px solid var(--primary-color, #03a9f4); }
    .title { font-weight: 600; font-size: 15px; }
    .date { font-weight: 400; color: var(--secondary-text-color, #727272); font-size: 13px; }
  `;
}

customElements.define("tc-days", TcDays);
```

- [ ] **Step 2: `tc-details.js` ersetzen**

```js
import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtTime, fmtWhen } from "./i18n.js";

class TcDetails extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };

  _hover(i) {
    this.dispatchEvent(new CustomEvent("tc-candidate", { detail: i, bubbles: true, composed: true }));
  }

  _block(c) {
    if (!c.start) return t(this.lang, "c_none");
    return `${fmtWhen(c.start, this.lang, this.tz, this.view.generated_at)}–${fmtTime(c.end, this.lang, this.tz)}`;
  }

  _candidates() {
    const L = this.lang;
    const rows = this.view.candidates;
    if (!rows.length) return nothing;
    return html`<div class="card">
      <h3>${t(L, "candidates")}</h3>
      <table>
        <thead>
          <tr>
            <th>${t(L, "c_block")}</th><th>${t(L, "c_violation")}</th><th>${t(L, "c_comfort")}</th>
            <th>${t(L, "c_start")}</th><th>${t(L, "c_energy")}</th><th>${t(L, "c_delay")}</th><th>${t(L, "c_total")}</th>
          </tr>
        </thead>
        <tbody @mouseleave=${() => this._hover(null)}>
          ${rows.map(
            (c, i) => html`<tr class=${c.chosen ? "chosen" : ""} @mouseenter=${() => this._hover(i)} @click=${() => this._hover(i)}>
              <td>${this._block(c)}</td>
              <td>${fmtNum(c.violation_kh, L, 2)}</td>
              <td>${fmtNum(c.parts.comfort ?? 0, L, 2)}</td>
              <td>${fmtNum(c.parts.start ?? 0, L, 2)}</td>
              <td>${fmtNum(c.parts.energy ?? 0, L, 2)}</td>
              <td>${fmtNum(c.parts.delay ?? 0, L, 2)}</td>
              <td><b>${fmtNum(c.cost, L, 2)}</b></td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>`;
  }

  _rules() {
    const L = this.lang;
    const d = this.view.decision;
    const r = d.rules || {};
    const item = (active, text) => html`<li class=${active ? "active" : ""}>${active ? "✗" : "✓"} ${text}</li>`;
    const state = d.release_state === true ? t(L, "allowed") : d.release_state === false ? t(L, "blocked") : t(L, "unknown");
    return html`<div class="card">
      <h3>${t(L, "rules")}</h3>
      <ul>
        ${item(d.override === "min_block", t(L, "r_min_block", { v: r.min_block_h }))}
        ${item(d.override === "min_pause", t(L, "r_min_pause", { v: r.min_pause_h }))}
        ${item(d.override === "budget", t(L, "r_budget", { n: d.switches_today, max: d.max_switches }))}
        ${item(d.override === "observe", t(L, "r_observe"))}
        ${item(d.override === "failsafe", t(L, "r_failsafe") + (d.failsafe_reason ? `: ${d.failsafe_reason}` : ""))}
      </ul>
      <p class="entity">${t(L, "r_entity", { e: d.release_entity, s: state })}</p>
    </div>`;
  }

  render() {
    if (!this.view) return nothing;
    return html`<div class="grid">${this._candidates()} ${this._rules()}</div>`;
  }

  static styles = css`
    .grid { display: grid; grid-template-columns: 3fr 2fr; gap: 12px; }
    @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
    .card { background: var(--card-background-color, #fff); border-radius: 12px; padding: 12px 16px; overflow-x: auto; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--divider-color, #e0e0e0); white-space: nowrap; }
    th:first-child, td:first-child { text-align: left; }
    tbody tr { cursor: default; }
    tbody tr:hover { background: var(--secondary-background-color, #f2f2f2); }
    tr.chosen { background: rgba(0, 158, 115, 0.15); font-weight: 600; }
    ul { list-style: none; margin: 0; padding: 0; line-height: 1.8; font-size: 14px; }
    li.active { color: #d55e00; font-weight: 600; }
    .entity { margin: 8px 0 0; font-size: 13px; color: var(--secondary-text-color, #727272); }
  `;
}

customElements.define("tc-details", TcDetails);
```

- [ ] **Step 3: Im Browser prüfen**

Dev-Seite neu laden (`http://localhost:8765/dev/`).
Expected: drei Tageskarten (heute umrandet); Kandidatentabelle mit hervorgehobener gewählter Zeile, „kein Block“-Zeile vorhanden, nach Summe sortiert; Regel-Liste mit ✗ bei „Beobachtungsmodus“. Bei 375 px Breite (DevTools) stehen Karten und Tabellen untereinander, Tabelle scrollt horizontal. Konsole ohne Fehler.

- [ ] **Step 4: Commit**

```bash
git add custom_components/thermocast/frontend/tc-days.js custom_components/thermocast/frontend/tc-details.js
git commit -m "feat(panel): day cards, candidate table and actuator rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Zeitachse (Wetter, Heizen, Zonen, Ursachen, Tooltip, Overlay)

**Files:**
- Modify (ersetzen): `custom_components/thermocast/frontend/tc-timeline.js`

**Interfaces:**
- Consumes: `view.hours`, `view.window`, `view.weather`, `view.heating`, `view.zones`, `view.events`, `view.candidates`; Properties `hoverIndex`, `hoverCandidate` (Task 11); `i18n.js`.
- Produces: feuert `tc-hover` (detail: Stundenindex|null).

- [ ] **Step 1: `tc-timeline.js` ersetzen**

```js
import { LitElement, html, svg, css, nothing } from "./lit.js";
import { t, fmtNum, fmtSigned, fmtTime, fmtDay, localDate } from "./i18n.js";

const GUTTER = 64;
const MIN_PPH = 10; // px per hour; below that the timeline scrolls horizontally
const C = {
  plan: "#0072B2",
  free: "#999999",
  comfort: "#009E73",
  heat: "#D55E00",
  outdoor: "#CC79A7",
  loss: "#56B4E9",
  neighbor: "#8C8C8C",
  gain: "#CC79A7",
  base: "#BDBDBD",
  cand: "#E69F00",
  event: "#D55E00",
};
const SUN = ["#E69F00", "#F0E442", "#B8860B", "#FFB000"];
const LS_KEY = "thermocast.why";

function loadWhy() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveWhy(v) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(v));
  } catch {
    /* private mode: just don't remember */
  }
}

// contiguous runs of non-null values: [[i, v], ...]
function segments(values) {
  const out = [];
  let cur = [];
  values.forEach((v, i) => {
    if (v === null || v === undefined) {
      if (cur.length) out.push(cur);
      cur = [];
    } else cur.push([i, v]);
  });
  if (cur.length) out.push(cur);
  return out;
}

function runs(flags) {
  const out = [];
  let start = null;
  flags.forEach((f, i) => {
    if (f && start === null) start = i;
    if (!f && start !== null) {
      out.push([start, i]);
      start = null;
    }
  });
  if (start !== null) out.push([start, flags.length]);
  return out;
}

class TcTimeline extends LitElement {
  static properties = {
    view: { attribute: false },
    lang: {},
    tz: {},
    hoverIndex: { attribute: false },
    hoverCandidate: { attribute: false },
    _width: { state: true },
    _why: { state: true },
  };

  constructor() {
    super();
    this._width = 900;
    this._why = loadWhy();
    this._scrolled = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._ro = new ResizeObserver((entries) => {
      const w = entries[0].contentRect.width;
      if (Math.abs(w - this._width) > 2) this._width = w;
    });
    this._ro.observe(this);
  }

  disconnectedCallback() {
    this._ro?.disconnect();
    super.disconnectedCallback();
  }

  updated() {
    if (this._scrolled || !this.view) return;
    const sc = this.renderRoot.querySelector(".scroll");
    if (sc && sc.scrollWidth > sc.clientWidth) {
      sc.scrollLeft = this._x(this.view.window.now_index) - sc.clientWidth / 2;
      this._scrolled = true;
    }
  }

  // ------------------------------------------------------------- geometry
  get _n() {
    return this.view.hours.length;
  }
  get _pph() {
    return Math.max(MIN_PPH, (this._width - GUTTER) / this._n);
  }
  get _w() {
    return GUTTER + this._n * this._pph;
  }
  _x(i) {
    return GUTTER + i * this._pph;
  }
  _nowPos() {
    const v = this.view;
    const h0 = new Date(v.hours[v.window.now_index]).getTime();
    const g = new Date(v.generated_at).getTime();
    return v.window.now_index + Math.min(1, Math.max(0, (g - h0) / 3600000));
  }
  _indexOfTime(iso) {
    const t0 = new Date(this.view.hours[0]).getTime();
    return (new Date(iso).getTime() - t0) / 3600000;
  }
  _scale(values, h, pad = 0.3, minRange = 1) {
    const vals = values.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
    let lo = vals.length ? Math.min(...vals) : 0;
    let hi = vals.length ? Math.max(...vals) : 1;
    lo -= pad;
    hi += pad;
    if (hi - lo < minRange) {
      const mid = (hi + lo) / 2;
      lo = mid - minRange / 2;
      hi = mid + minRange / 2;
    }
    const top = 6;
    const bottom = h - 6;
    const y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
    return { y, lo, hi };
  }
  _path(points) {
    return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  }

  // ------------------------------------------------------------- events
  _emit(i) {
    if (i === this.hoverIndex) return;
    this.dispatchEvent(new CustomEvent("tc-hover", { detail: i, bubbles: true, composed: true }));
  }
  _onMove(ev) {
    const rect = ev.currentTarget.getBoundingClientRect();
    const i = Math.floor((ev.clientX - rect.left - GUTTER) / this._pph);
    this._emit(i >= 0 && i < this._n ? i : null);
  }
  _toggle(id) {
    this._why = { ...this._why, [id]: !this._why[id] };
    saveWhy(this._why);
  }

  // ------------------------------------------------------------- shared frame
  _days() {
    const v = this.view;
    const out = [];
    let cur = null;
    v.hours.forEach((h, i) => {
      const d = localDate(h, this.tz);
      if (!cur || cur.date !== d) {
        cur = { date: d, start: i, end: i + 1, iso: h };
        out.push(cur);
      } else cur.end = i + 1;
    });
    return out;
  }

  _frame(h, body, label) {
    const nowX = this._x(this._nowPos());
    const hi = this.hoverIndex;
    return html`<svg
      width=${this._w}
      height=${h}
      @mousemove=${(e) => this._onMove(e)}
      @click=${(e) => this._onMove(e)}
    >
      <rect class="past" x=${this._x(0)} y="0" width=${Math.max(0, nowX - this._x(0))} height=${h}></rect>
      ${this._days().slice(1).map((d) => svg`<line class="day" x1=${this._x(d.start)} x2=${this._x(d.start)} y1="0" y2=${h}></line>`)}
      ${body}
      <line class="now" x1=${nowX} x2=${nowX} y1="0" y2=${h}></line>
      ${hi !== null && hi !== undefined
        ? svg`<line class="hover" x1=${this._x(hi + 0.5)} x2=${this._x(hi + 0.5)} y1="0" y2=${h}></line>`
        : nothing}
      ${label ? svg`<text class="lane" x="4" y="14">${label}</text>` : nothing}
    </svg>`;
  }

  // ------------------------------------------------------------- lanes
  _dayHeader() {
    return html`<svg width=${this._w} height="20">
      ${this._days().map(
        (d) => svg`<text class="dayname" x=${this._x(d.start) + 4} y="14">${fmtDay(d.iso, this.lang, this.tz)}</text>`,
      )}
    </svg>`;
  }

  _weatherLane() {
    const v = this.view;
    const h = 80;
    const irrMax = Math.max(400, ...v.weather.irr.flatMap((s) => s.values.filter((x) => x !== null)));
    const base = h - 4;
    const irrY = (x) => base - (x / irrMax) * (h - 24);
    const areas = v.weather.irr.map((s, k) => {
      const pts = s.values.map((val, i) => [this._x(i + 0.5), irrY(val ?? 0)]);
      const poly = [[this._x(0.5), base], ...pts, [this._x(this._n - 0.5), base]];
      return svg`<polygon points=${this._path(poly)} fill=${SUN[k % SUN.length]} opacity="0.35"></polygon>`;
    });
    const sc = this._scale(v.weather.t_out, h, 1, 4);
    const meas = v.weather.t_out.map((x, i) => (v.weather.t_out_measured[i] ? x : null));
    const fc = v.weather.t_out.map((x, i) => (!v.weather.t_out_measured[i] || v.weather.t_out_measured[i + 1] === false ? x : null));
    const line = (vals, dash) =>
      segments(vals).map(
        (seg) => svg`<polyline points=${this._path(seg.map(([i, x]) => [this._x(i + 0.5), sc.y(x)]))}
          fill="none" stroke=${C.outdoor} stroke-width="1.6" stroke-dasharray=${dash}></polyline>`,
      );
    const axis = svg`<text class="axis" x=${GUTTER - 4} y=${sc.y(sc.hi) + 10} text-anchor="end">${fmtNum(sc.hi, this.lang, 0)}°</text>
      <text class="axis" x=${GUTTER - 4} y=${sc.y(sc.lo)} text-anchor="end">${fmtNum(sc.lo, this.lang, 0)}°</text>`;
    return this._frame(h, svg`${areas}${line(meas, "")}${line(fc, "4 3")}${axis}`, t(this.lang, "weather"));
  }

  _heatingLane() {
    const v = this.view;
    const h = 40;
    const now = v.window.now_index;
    const bars = v.heating.actual.map((a, i) =>
      i < now && a ? svg`<rect x=${this._x(i)} y=${20 - 14 * a} width=${this._pph} height=${14 * a} fill=${C.heat}></rect>` : nothing,
    );
    const planned = v.heating.planned_blocks.map((b) => {
      const s = this._indexOfTime(b.start);
      const e = this._indexOfTime(b.end);
      return svg`<rect x=${this._x(s)} y="6" width=${(e - s) * this._pph} height="14" rx="2"
        fill="none" stroke=${C.heat} stroke-width="1.5" stroke-dasharray="3 2"></rect>`;
    });
    let cand = nothing;
    const c = this.hoverCandidate !== null && this.hoverCandidate !== undefined ? v.candidates[this.hoverCandidate] : null;
    if (c && c.start) {
      const s = this._indexOfTime(c.start);
      const e = this._indexOfTime(c.end);
      cand = svg`<rect x=${this._x(s)} y="4" width=${(e - s) * this._pph} height="18" rx="2" fill=${C.cand} opacity="0.45"></rect>`;
    }
    const events = v.events.map((ev) => {
      const x = this._x(this._indexOfTime(ev.time));
      const zone = ev.zone ? v.zones.find((z) => z.id === ev.zone)?.name ?? ev.zone : "";
      const label = `${fmtTime(ev.time, this.lang, this.tz)} ${t(this.lang, "ev_" + ev.type)}${zone ? " · " + zone : ""}${ev.detail ? " · " + ev.detail : ""}`;
      return svg`<g class="event"><path d="M${x},${h - 2} l-5,-9 h10 z" fill=${C.event}></path><title>${label}</title></g>`;
    });
    return this._frame(h, svg`${bars}${planned}${cand}${events}`, t(this.lang, "heating"));
  }

  _zoneLane(z) {
    const v = this.view;
    const L = this.lang;
    const h = 120;
    const c = this.hoverCandidate !== null && this.hoverCandidate !== undefined ? v.candidates[this.hoverCandidate] : null;
    const candTraj = c?.trajectories?.[z.id] ?? null;
    const upper = z.plan.mean.map((m, i) => (m === null ? null : m + z.plan.std[i]));
    const lower = z.plan.mean.map((m, i) => (m === null ? null : m - z.plan.std[i]));
    const sc = this._scale([...z.measured, ...upper, ...lower, ...z.free.mean, ...z.comfort_low, ...(candTraj ?? [])], h);

    const comfort = runs(z.comfort_low.map((x) => x !== null)).map(([s, e]) => {
      const pts = [];
      for (let i = s; i < e; i++) pts.push([this._x(i), sc.y(z.comfort_low[i])], [this._x(i + 1), sc.y(z.comfort_low[i])]);
      return svg`<rect x=${this._x(s)} y="0" width=${(e - s) * this._pph} height=${h} fill=${C.comfort} opacity="0.07"></rect>
        <polyline points=${this._path(pts)} fill="none" stroke=${C.comfort} stroke-dasharray="4 3"></polyline>`;
    });
    const band = segments(z.plan.mean).map((seg) => {
      const up = seg.map(([i]) => [this._x(i), sc.y(upper[i])]);
      const lo = seg.map(([i]) => [this._x(i), sc.y(lower[i])]).reverse();
      return svg`<polygon points=${this._path([...up, ...lo])} fill=${C.plan} opacity="0.13"></polygon>`;
    });
    const line = (vals, color, width, dash, center = false) =>
      segments(vals).map(
        (seg) => svg`<polyline points=${this._path(seg.map(([i, x]) => [this._x(center ? i + 0.5 : i), sc.y(x)]))}
          fill="none" stroke=${color} stroke-width=${width} stroke-dasharray=${dash}></polyline>`,
      );
    const axis = svg`<text class="axis" x=${GUTTER - 4} y=${sc.y(sc.hi) + 10} text-anchor="end">${fmtNum(sc.hi, L)}°</text>
      <text class="axis" x=${GUTTER - 4} y=${sc.y(sc.lo)} text-anchor="end">${fmtNum(sc.lo, L)}°</text>`;
    const body = svg`${comfort}${band}
      ${line(z.free.mean, C.free, 1.4, "3 3")}
      ${line(z.measured, "var(--primary-text-color, #212121)", 1.8, "", true)}
      ${line(z.plan.mean, C.plan, 2, "")}
      ${candTraj ? line(candTraj, C.cand, 2.2, "6 3") : nothing}
      ${axis}`;
    const open = !!this._why[z.id];
    return html`
      <div class="zone-head">
        <b>${z.name}</b>
        <span class="muted">${t(L, z.leads ? "leads" : "follows")} · ${t(L, "ht_" + z.heat_type)}</span>
        <button @click=${() => this._toggle(z.id)}>${t(L, "why")} ${open ? "▾" : "▸"}</button>
      </div>
      ${this._frame(h, body)} ${open ? this._whyLane(z) : nothing}
    `;
  }

  _groupColor(g, z) {
    if (g.kind === "sun") {
      const suns = z.groups.filter((x) => x.kind === "sun").map((x) => x.key);
      return SUN[suns.indexOf(g.key) % SUN.length];
    }
    return C[g.kind] ?? C.base;
  }

  _groupLabel(g) {
    return ["base", "loss", "heat"].includes(g.kind) ? t(this.lang, "g_" + g.kind) : g.label;
  }

  _whyLane(z) {
    const h = 70;
    const mid = h / 2;
    let maxAbs = 0.05;
    z.contrib.forEach((c) => {
      if (!c) return;
      let pos = 0;
      let neg = 0;
      for (const v of Object.values(c)) v >= 0 ? (pos += v) : (neg -= v);
      maxAbs = Math.max(maxAbs, pos, neg);
    });
    const k = (mid - 4) / maxAbs;
    const bars = z.contrib.map((c, i) => {
      if (!c) return nothing;
      let up = mid;
      let down = mid;
      return z.groups.map((g) => {
        const v = c[g.key] ?? 0;
        if (!v) return nothing;
        const hh = Math.abs(v) * k;
        const y = v > 0 ? (up -= hh) : down;
        if (v < 0) down += hh;
        return svg`<rect x=${this._x(i) + this._pph * 0.1} y=${y} width=${this._pph * 0.8} height=${hh} fill=${this._groupColor(g, z)}></rect>`;
      });
    });
    const zero = svg`<line x1=${GUTTER} x2=${this._w} y1=${mid} y2=${mid} stroke="var(--divider-color, #ccc)"></line>
      <text class="axis" x=${GUTTER - 4} y="12" text-anchor="end">+</text><text class="axis" x=${GUTTER - 4} y=${h - 4} text-anchor="end">−</text>`;
    return html`${this._frame(h, svg`${zero}${bars}`)}
      <div class="legend">
        ${z.groups.map((g) => html`<span><i style="background:${this._groupColor(g, z)}"></i>${this._groupLabel(g)}</span>`)}
      </div>`;
  }

  _axis() {
    const step = this._pph >= 14 ? 3 : 6;
    const labels = [];
    this.view.hours.forEach((h, i) => {
      const hour = Number(fmtTime(h, "de", this.tz).slice(0, 2));
      if (hour % step === 0) labels.push(svg`<text class="axis" x=${this._x(i)} y="12" text-anchor="middle">${String(hour).padStart(2, "0")}</text>`);
    });
    return html`<svg width=${this._w} height="16">${labels}</svg>`;
  }

  // ------------------------------------------------------------- tooltip
  _tooltip() {
    const i = this.hoverIndex;
    if (i === null || i === undefined || !this.view) return nothing;
    const v = this.view;
    const L = this.lang;
    const now = v.window.now_index;
    const irr = v.weather.irr.filter((s) => s.values[i]).map((s) => `${s.label} ${fmtNum(s.values[i], L, 0)} W/m²`);
    const zones = v.zones.map((z) => {
      const past = i < now;
      const val = past ? z.measured[i] : z.plan.mean[i];
      const std = past ? null : z.plan.std[i];
      const c = z.contrib[i];
      const parts = c
        ? z.groups
            .map((g) => [g, c[g.key] ?? 0])
            .filter(([, x]) => Math.abs(x) >= 0.005)
            .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
            .slice(0, 5)
        : [];
      const net = c ? Object.values(c).reduce((a, b) => a + b, 0) : null;
      return html`<div class="tz">
        <b>${z.name}</b> ${fmtNum(val, L)} °C${std ? html` (±${fmtNum(std, L)})` : nothing}
        <span class="muted">${t(L, past ? "measured" : "plan")}</span>
        ${parts.length
          ? html`<div class="parts">
              ${parts.map(([g, x]) => html`<span>${this._groupLabel(g)} ${fmtSigned(x, L, 2)}</span>`)}
              <span><b>${t(L, "net")} ${fmtSigned(net, L, 2)} K/h</b></span>
            </div>`
          : nothing}
      </div>`;
    });
    const x = this._x(i + 0.5);
    const left = x > this._w / 2 ? x - 300 : x + 12;
    return html`<div class="tip" style="left:${Math.max(4, left)}px">
      <div class="tt">${fmtDay(v.hours[i], L, this.tz)} ${fmtTime(v.hours[i], L, this.tz)}</div>
      <div>${t(L, "t_out")} ${fmtNum(v.weather.t_out[i], L)} °C${irr.length ? " · " + irr.join(" · ") : ""}</div>
      ${zones}
    </div>`;
  }

  render() {
    if (!this.view) return nothing;
    return html`<div class="card">
      <div class="scroll">
        <div class="inner" style="width:${this._w}px" @mouseleave=${() => this._emit(null)}>
          ${this._dayHeader()} ${this._weatherLane()} ${this._heatingLane()}
          ${this.view.zones.map((z) => this._zoneLane(z))} ${this._axis()} ${this._tooltip()}
        </div>
      </div>
      <div class="legend">
        <span><i style="background:var(--primary-text-color,#212121)"></i>${t(this.lang, "measured")}</span>
        <span><i style="background:${C.plan}"></i>${t(this.lang, "plan")} ±σ</span>
        <span><i style="background:${C.free}"></i>${t(this.lang, "free")}</span>
        <span><i style="background:${C.comfort}"></i>${t(this.lang, "comfort")}</span>
        <span><i style="background:${C.heat}"></i>${t(this.lang, "heating")}</span>
      </div>
    </div>`;
  }

  static styles = css`
    .card { background: var(--card-background-color, #fff); border-radius: 12px; padding: 8px 0 12px; }
    .scroll { overflow-x: auto; }
    .inner { position: relative; }
    svg { display: block; }
    .past { fill: var(--primary-text-color, #000); opacity: 0.04; }
    .day { stroke: var(--primary-text-color, #000); stroke-opacity: 0.35; stroke-width: 0.7; }
    .now { stroke: #e53935; stroke-width: 2; }
    .hover { stroke: var(--primary-text-color, #000); stroke-opacity: 0.4; stroke-dasharray: 2 2; }
    text { font: 11px Roboto, system-ui, sans-serif; fill: var(--secondary-text-color, #727272); }
    text.lane { font-weight: 600; }
    text.dayname { font-weight: 600; fill: var(--primary-text-color, #212121); }
    .event { cursor: help; }
    .zone-head { display: flex; align-items: center; gap: 8px; padding: 8px 8px 0 8px; font-size: 14px; }
    .muted { color: var(--secondary-text-color, #727272); font-size: 12px; }
    button { margin-left: auto; font: inherit; font-size: 12px; background: none; border: 1px solid var(--divider-color, #ccc);
      border-radius: 12px; padding: 2px 10px; color: var(--primary-text-color, #212121); cursor: pointer; }
    .legend { display: flex; flex-wrap: wrap; gap: 12px; padding: 4px 8px 0 ${GUTTER}px; font-size: 12px; color: var(--secondary-text-color, #727272); }
    .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
    .tip { position: absolute; top: 24px; width: 288px; z-index: 2; pointer-events: none; font-size: 12px; line-height: 1.5;
      background: var(--card-background-color, #fff); color: var(--primary-text-color, #212121);
      border: 1px solid var(--divider-color, #ccc); border-radius: 8px; padding: 8px 10px; box-shadow: 0 2px 8px rgba(0,0,0,.15); }
    .tt { font-weight: 600; }
    .tz { margin-top: 4px; }
    .parts { display: flex; flex-wrap: wrap; gap: 2px 10px; color: var(--secondary-text-color, #727272); }
  `;
}

customElements.define("tc-timeline", TcTimeline);
```

`${GUTTER}` in `css` ist kein gültiger Lit-CSS-Wert (nur `unsafeCSS`); daher in `.legend` den Wert fest eintragen: `padding: 4px 8px 0 64px;`.

- [ ] **Step 2: Im Browser prüfen**

Dev-Seite neu laden.
Expected:
- Tagesnamen über der Achse, zwei Tagestrenner, Vergangenheit leicht grau, rote Jetzt-Linie.
- Wetter-Bahn mit Außentemperatur (durchgezogen bis jetzt, danach gestrichelt) und zwei Sonnenflächen.
- Heizen-Bahn: gefüllte Balken in der Vergangenheit, gestrichelte geplante Blöcke, zwei Ereignis-Dreiecke mit Tooltip.
- Zonen „EG“ (führt) und „Elternschlafzimmer“ (folgt) mit Komfortfenstern, schwarzer Messkurve, blauem Plan mit Band, grau gestrichelter Kurve „ohne Heizen“.
- „Warum?“ klappt Ursachen-Balken + Legende auf; Zustand bleibt nach Neuladen erhalten.
- Hover zeigt Fadenkreuz über alle Bahnen und Tooltip mit Ursachen (Zukunft) bzw. Messwert (Vergangenheit).
- Hover über eine Kandidatenzeile zeichnet orangefarbenen Block + gestrichelte Kurve in EG.
- Dunkelmodus (Checkbox) lesbar; 375 px Breite: Zeitachse scrollt horizontal und startet bei „jetzt“.
- Keine Konsolenfehler.

- [ ] **Step 3: Commit**

```bash
git add custom_components/thermocast/frontend/tc-timeline.js
git commit -m "feat(panel): 72-hour timeline with weather, heating, zones, causes, tooltip and candidate overlay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Ende-zu-Ende in der Dev-Instanz, Doku

**Files:**
- Modify: `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: alles.

- [ ] **Step 1: Gesamte Testsuite + Lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: alle Tests grün, „All checks passed!“.

- [ ] **Step 2: Dev-Instanz starten und Panel prüfen**

```bash
rm -rf config/.storage config/home-assistant*
./scripts/develop
```
Im Browser `http://localhost:8123`, Onboarding durchklicken, Thermocast einrichten (Außen `sensor.outdoor`, Vorlauf `sensor.flow`, Pumpe `binary_sensor.heating_pump`, Freigabe `input_number.summer_threshold` mit 16/10), Zone „EG“ mit `sensor.living`, Fläche `[{kind: window, azimuth: 90, tilt: 90}]`.
Expected:
- Sidebar-Eintrag „Thermocast“ öffnet das Panel ohne Konsolenfehler.
- Story, Tageskarten, Zeitachse, Kandidaten, Regeln sichtbar; Hinweis „Keine Recorder-Historie“ erscheint **nicht** (Recorder läuft über `history:` in der Dev-Config).
- `input_number.outdoor_temp` auf −10 stellen, Integration über ⋮ → „Neu laden“: Panel lädt nach wenigen Sekunden selbst neu (Abo-Neuverbindung) und zeigt geplante Blöcke.
- Zone über „Rekonfigurieren“ ändern: Panel verbindet sich neu, keine leere Seite.

- [ ] **Step 3: Doku aktualisieren**

`README.md` – nach dem Abschnitt „Entitäten“ einfügen:
```markdown
## Panel „Thermocast“

Die Integration bringt ein eigenes Seitenpanel mit (Sidebar → *Thermocast*):

- **Story:** Heizen an/aus, nächster Block, Begründung in einem Satz, Robustheit („knapp“, „nur wegen σ“),
  Planänderung seit der letzten Stunde.
- **Gestern · Heute · Morgen:** Heizstunden, Blöcke, Minimum der führenden Zonen, Freigabe vs. Planer.
- **Zeitachse** (gestern 00:00 → morgen 24:00): Wetter, gelaufene/geplante Blöcke, Ereignisse,
  je Zone gemessen / Plan ± σ / ohne Heizen / Komfort; „Warum?“ zerlegt jede künftige Stunde in
  Sonne je Fläche, Heizen, Verlust, Nachbarn, Gewinne.
- **Kandidaten** für den nächsten Block mit Kostenaufschlüsselung (Hover zeichnet den Verlauf ein) und **Aktor-Regeln**.

Der Plan bis morgen entsteht durch einen Rollout des echten Reglers (stündlich neu planen inkl.
Mindestblock/-pause/Budget). Die Vergangenheit kommt aus dem Recorder.

Frontend-Entwicklung ohne HA: `uv run python scripts/sample_view.py`, dann
`uv run python -m http.server -d custom_components/thermocast/frontend 8765` → `http://localhost:8765/dev/`.
```

`CLAUDE.md` – in Abschnitt 3 „Architektur“ den Baum um diese Zeilen ergänzen (unter `core/`):
```
│   ├── rules.py          # Aktor-Regeln (rein), auch für den Rollout
│   ├── rollout.py        # Regler-Rollout bis morgen 24:00
│   ├── explain.py        # Begründungs-Codes, Robustheit, Planänderung
│   ├── series.py         # Stundenaggregation von Zustandsfolgen
│   └── view.py           # Panel-View-Vertrag v1
├── view_builder.py, history.py, events.py   # Panel-Daten (getrennt vom Regelpfad)
├── websocket_api.py, panel.py, frontend/    # Seitenpanel (Lit, ohne Build)
```
und in Abschnitt 4 „Status“ ergänzen:
```markdown
- ✅ Panel A („Gestern · Heute · Morgen“): Spec `docs/superpowers/specs/2026-10-04-panel-now-and-plan-design.md`.
  Teilprojekte B (Modellgüte, Export, WW-Schraffur, Plan-gestern-vs-Ist) und C (KPIs) offen.
```

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: describe the Thermocast panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
