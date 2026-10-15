# Urlaubsmodus – Design

Status: Design abgestimmt (Abschnitte 1–3 einzeln freigegeben) · Datum: 2026-10-15
Baut auf v0.8.10 auf. Ziel-Release: v0.9.0.

## 1. Ziel

Während eines Urlaubs (Abreise bis Rückkehr) heizt Thermocast nicht auf Komfort, sondern hält alle Zonen auf
einer niedrigen, haus-weiten **Urlaubs-Untergrenze**. Rechtzeitig vor der Rückkehr heizt es von selbst wieder
hoch, sodass zur Rückkehr der normale Komfortplan gilt. Start und Ende laufen automatisch über einen eingegebenen
Zeitraum – nichts muss am Abreisetag oder danach von Hand umgeschaltet werden.

**Nicht-Ziele:** Warmwasser (das RC310 hat ein eigenes Urlaubsprogramm, Thermocast steuert WW nicht),
Untergrenze je Zone, Bearbeiten im Panel (Eingabe über Entitäten), Anwesenheit (`zone.home`).

## 2. Entscheidungen

| Thema | Entscheidung | Begründung |
|---|---|---|
| Verhalten | Absenken + Rückkehr vorheizen | Spart im Urlaub, Haus ist bei Rückkehr warm |
| Bedienung | Zeitraum (Abreise, Rückkehr) als `datetime`-Entitäten | Startet/endet automatisch, kein Vergessen |
| Untergrenze | haus-weit, Standard 15 °C, `min(Grundwert, Urlaub)` | Einfach; der Urlaub senkt nur, hebt nie an |
| Vorlauf | eigene Number bei der Urlaubseingabe, Standard 12 h | Aufheizen aus 15 °C dauert länger als ein Block (≤ 12 h); je Urlaub anpassbar |
| Ansatz | zeitabhängige Grenzen in den `zone_*`-Funktionen | Planer, Rollout, BT-Aktor, Panel, KPIs folgen ohne eigene Urlaubslogik |
| Lernen | läuft normal weiter | Abkühlphasen ohne Heizen sind gute Lerndaten |

Verworfen: eigener Urlaubs-Planer (dupliziert Logik), Schedule-Helfer umbiegen (unvollständig für BT-Zonen,
nicht jede Zone hat einen).

## 3. Verhalten

Phasen (`t` = Zeitpunkt, an dem eine Grenze ausgewertet wird):

| Phase | Zeitraum | Komfort | Untergrenze | Obergrenze |
|---|---|---|---|---|
| — | vor Abreise / ab Rückkehr | normal | normal | normal |
| `away` | Abreise ≤ t < Rückkehr − Vorlauf | keiner | `min(Grundwert, Urlaub)` | Untergrenze + 1 K |
| `preheat` | Rückkehr − Vorlauf ≤ t < Rückkehr | keiner | Grundwert | normal |

- **Vor der Abreise:** Normalbetrieb. Weil `zone_comfort` im Urlaub `None` liefert, lädt `zone_charge_target`
  vor der Abreise nicht mehr für Komfortzeiten, die in den Urlaub fallen.
- **`away`:** BT-Zonen halten die Untergrenze; Ruhezeiten, Fenster-Logik, Fail-safes (→ Heizen erlaubt),
  Mindestblock/-pause, Tagesbudget und EEPROM-Schutz bleiben unverändert.
- **`preheat`:** wieder normaler Grundwert; Planer/Rollout heizen in normalen Blöcken stufenweise hoch.
  Ab der Rückkehr gilt der Komfortplan, das übliche Vorladen (`precharge_hours`) greift für den ersten
  Komfortbeginn nach der Rückkehr.
- **Vorlauf 0** → keine `preheat`-Phase; **Vorlauf ≥ Urlaubsdauer** → nur `preheat`.
- **Ungültig** (Abreise oder Rückkehr leer, Rückkehr ≤ Abreise): Urlaub inaktiv, ein Log-Hinweis, kein Fehler.
- **Nach der Rückkehr** bleiben die Werte stehen; ein vergangener Zeitraum ist einfach inaktiv.

## 4. Komponenten

### `core/vacation.py` (HA-frei)

```python
@dataclass(frozen=True)
class Vacation:
    start: datetime | None
    end: datetime | None
    floor: float = 15.0
    lead_h: float = 12.0

    def valid(self) -> bool
    def phase(self, when: datetime) -> Literal["away", "preheat"] | None
    def preheat_from(self) -> datetime | None
    def to_dict(self) -> dict / from_dict(d) -> Vacation   # Store, robust gegen fehlende/kaputte Werte
```

Konstanten in `const.py`: `DEFAULT_VACATION_FLOOR = 15.0`, `DEFAULT_VACATION_LEAD_H = 12`,
Grenzen 10–20 °C (Schritt 0,5) und 0–48 h (Schritt 1).

### Coordinator

- `self.vacation: Vacation`, im kleinen Store unter `vacation` (`_state_data` / `async_load`).
- `async_set_vacation(**changes)` – ersetzt Felder, speichert, fordert sofort einen Refresh an.
- `ZoneRuntime.vacation` wird wie `precharge_h` bei jedem Update gesetzt.
- Grenzfunktionen werten die Phase aus:
  - `zone_comfort(z, when)` → `None` in `away`/`preheat`.
  - `zone_base(z, when=None)` → in `away` `min(Grundwert, floor)`.
  - `zone_high(z, when=None)` → in `away` `zone_floor + DEFAULT_HIGH_OFFSET`.
  - `zone_floor`, `zone_charge_target`, `zone_charge_cap` folgen über diese.
  - `when=None` bleibt für Aufrufer ohne Zeitbezug erlaubt (= keine Urlaubsauswertung); alle Aufrufer in
    `build_plan_inputs`, `view_builder` und `zone_actuator` übergeben die Zeit. `build_plan_inputs` setzt
    `comfort_high` damit je Stunde statt `[high] * horizon`.
- Phasenwechsel (gegenüber dem letzten Update) → Ereignisse `vacation_start`, `vacation_preheat`,
  `vacation_end` im Ereignis-Ringpuffer.

### Entitäten (House-Device)

| Entität | Plattform | Key | Hinweis |
|---|---|---|---|
| Urlaub Abreise | `datetime` | `vacation_start` | leer = nicht gesetzt |
| Urlaub Rückkehr | `datetime` | `vacation_end` | |
| Urlaub Untergrenze | `number` | `vacation_floor` | 10–20 °C, 0,5 |
| Urlaub Vorlauf | `number` | `vacation_lead` | 0–48 h, 1 |
| Urlaub aktiv | `binary_sensor` | `vacation_active` | an in `away`/`preheat`; Attr. `phase`, `preheat_from`, `end` |

Dünne Hüllen wie `ControlSwitch`: Zustand aus dem Coordinator, Setzen über `async_set_vacation`.
Neue Plattformen `datetime`, `number` in `PLATFORMS`. Übersetzungen en + de.

### Panel

- View-Vertrag v1 bekommt optional `vacation: {start, end, preheat_from, floor, phase}` (oder `null`, wenn
  kein gültiger Zeitraum in der Zukunft/aktiv ist). Ältere Frontends ignorieren das Feld.
- Tab „Gestern · Heute · Morgen“: Banner („Urlaub bis … · Absenkung auf … °C · Vorheizen ab …“), Klick öffnet
  den More-Info-Dialog von „Urlaub Rückkehr“; 72-h-Zeitachse: Urlaub hinterlegt, Vorlauf heller.
- Story: Begründungs-Code `vacation`, wenn kein Block nötig ist und die Zone sich im Urlaub befindet.
- Ereignisse `vacation_*` im Tooltip/Ereignisliste. Texte nur in `frontend/i18n.js` (de/en).
- Untergrenzen-/Obergrenzen-Linien zeigen die Absenkung ohne Zusatzlogik (kommen aus `zone_floor`/`zone_high`).

## 5. Tests

- `tests/test_vacation.py` (Kern): Phasen an allen Grenzen (inkl. Vorlauf 0 und ≥ Dauer), ungültige
  Zeiträume, `to_dict`/`from_dict` inkl. kaputter Store-Werte.
- Coordinator-Grenzen: `zone_comfort`/`zone_floor`/`zone_high`/`zone_charge_target` in `away`, `preheat`,
  davor/danach; `min(Grundwert, Urlaub)` hebt nie an; kein Laden vor der Abreise für Urlaubs-Komfort.
- Synthetik-Szenario (`tests/synthetic.py`): bei −5 °C hält der Rollout im Urlaub die Urlaubs-Untergrenze
  (Prognose ≥ Untergrenze − Toleranz) und erreicht bis zur Rückkehr den Komfort − Band.
- HA: Entitäten vorhanden; setzen → Coordinator + Store; nach Reload wiederhergestellt; `binary_sensor`
  folgt der Phase (freezer); Fail-safe im Urlaub gibt weiterhin frei; Ereignisse beim Phasenwechsel.
- `tests/test_view.py`: neues Feld `vacation`; `scripts/sample_view.py` erzeugt eine Probe mit Urlaub.

## 6. Dokumentation und Release

README-Abschnitt „Urlaub“ (Bedienung, Vorlauf wählen, WW am RC310 separat), `CLAUDE.md` (Architektur:
`core/vacation.py`, Entitäten, Status v0.9.0), `manifest.json` → 0.9.0.
