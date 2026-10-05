# Thermocast – Installations-Checkliste (echte HA mit EMS-ESP)

Ziel: Thermocast sicher in Betrieb nehmen – erst **beobachten**, dann **steuern**. Die Entitätsnamen unten
sind typische EMS-ESP-Namen und **nur Beispiele** – in *Entwicklerwerkzeuge → Zustände* die echten suchen.

## 0. Voraussetzungen
- [ ] Home Assistant **≥ 2026.9** (Python 3.14), Recorder aktiv (kein `include`-Filter, `purge_keep_days: 120` passt).
- [ ] EMS-ESP liefert per MQTT: Außentemperatur, Vorlauf, Heizungspumpe, RC310-Sommer/Winter-Schwelle.

## 1. Entitäten vorab prüfen
| Zweck | Beispiel | Prüfen |
|---|---|---|
| Außentemperatur | `sensor.boiler_outside_temperature` | °C, ändert sich; Attribut `state_class: measurement` |
| Vorlauf | `sensor.boiler_current_flow_temperature` | steigt bei Brennerbetrieb |
| Heizungspumpe | `binary_sensor.boiler_heating_pump` | an/aus passt zum Heizbetrieb (nicht zur WW-Ladung) |
| **Freigabe** | `number.thermostat_hc1_summer_temperature` (RC310 Sommer/Winter-Schwelle) | siehe 2. |
| Raumtemperaturen | Wohnzimmer, Küche, Schlafräume | `state_class: measurement` (für Warmstart/KPIs) |
| Ventilöffnung (TRVZB) | `sensor.<trv>_valve_opening_degree` | 0–100 % |
| Brennerstarts | `sensor.boiler_burner_starts` | `state_class: total_increasing` |
| Heizenergie | `sensor.boiler_energy_heating` | kWh, `total_increasing` |
| Warmwasser aktiv | `binary_sensor.boiler_dhw_charging` | an während WW-Ladung |

## 2. Freigabe-Entität testen (wichtig, EEPROM!)
- [ ] In *Entwicklerwerkzeuge → Aktionen* `number.set_value` auf **10**, dann zurück auf **16**.
- [ ] Der Zustand folgt **innerhalb weniger Sekunden** – sonst stimmt die Entität nicht (Thermocast würde
      das als „nicht bestätigt“ melden und mit wachsenden Pausen erneut schreiben, max. 40×/Tag).
- [ ] Am RC310 bzw. in EMS-ESP sichtbar, dass der Wert ankam. Danach **auf 16 lassen**.

## 3. Installation
- [ ] `custom_components/thermocast` nach `config/custom_components/` kopieren (später: HACS-Custom-Repo).
- [ ] HA neu starten → *Einstellungen → Geräte & Dienste → Integration hinzufügen → Thermocast*.
- [ ] Haus: Außentemperatur, Vorlauf, **Heizungspumpe** (empfohlen), Freigabe = RC310-Schwelle, Werte **16 / 10**.

## 4. Zonen anlegen („Zone hinzufügen“)

Eine Zone = ein Raum oder eine Gruppe von Räumen am selben Heizkreis. Faustregeln:

- **Fußbodenheizung ohne Stellantriebe** (z. B. ein ganzes Geschoss): **eine** Zone, Typ Fußbodenheizung,
  Sensor des wichtigsten Raums (Wohnzimmer), **führt**.
- **Heizkörper mit TRV:** je Raum eine Zone, Typ Heizkörper, Ventilöffnung als Ventil-Entität.
- **Flächen** je Zone: alle Fenster, Türen mit viel Glas und Dachflächen mit Ausrichtung und Neigung; mehrere
  Fenster gleicher Ausrichtung als **eine** Fläche (die Wirkung wird gelernt). Dachüberstände, Balkone usw.
  nicht eintragen – sie verkleinern nur den gelernten Sonnenwert.
  ```yaml
  - {kind: window, azimuth: 180, tilt: 90, name: Fenster Süd}
  - {kind: roof,   azimuth: 180, tilt: 45, name: Dach Süd}
  - {kind: window, azimuth: 0,   tilt: 45, name: Dachfenster Nord}
  ```
  Azimut: 0 = N, 90 = O, 180 = S, 270 = W; Neigung 90 = senkrecht. Dachflächen wirken 1–6 h verzögert.
- **Nachbarn:** angrenzende Räume mit eigenem Sensor, besonders **Flure/Treppenhäuser** (offene Treppen und
  Türen koppeln stark). Ein Sensor im Treppenhaus ist oft der wertvollste im ganzen Haus.
- **Komfort:** festes Zeitfenster oder ein **Zeitplan-Helfer** (`schedule.*`, z. B. Büro werktags, Bad
  morgens + abends). Schlafräume mit lauten TRVs: nur **vor** der Schlafenszeit führen lassen und in Better
  Thermostat nachts einen Sollwert unter der Raumtemperatur setzen – Thermocast stellt TRVs nie selbst.
- **Zonen, die ihr Ziel evtl. nicht erreichen** (z. B. Bad an einem FBH-Kreis mit Rücklaufbegrenzer):
  zuerst **nicht führend** anlegen und im Panel beobachten – eine führende Zone, die ihr Ziel nie erreicht,
  hält den Kessel dauerhaft frei.
- [ ] Nach jeder Zone lernt Thermocast automatisch aus den letzten **30 Tagen** (Ereignis
      „Modell aus der Historie gelernt“, Logeintrag `warm start: zone … learned … hours`).

## 5. Optionen (⋮ → Konfigurieren)
- [ ] Brennerstarts, Heizenergie, Warmwasser aktiv eintragen.
- [ ] „Unsicherheit kalibrieren“ an lassen (Standard).
- [ ] Mindestblock 3 h, Mindestpause 2 h, max. 12 Wechsel/Tag sind gute Startwerte.

## 6. Plausibilität (Panel → Tab „Modell“)
- [ ] Modellfehler (Hindcast) je Zone **< 0,3 K** – größer: Sensoren/Flächen prüfen.
- [ ] Zeitkonstante τ plausibel (gedämmtes EFH: grob 30–200 h); Sonne je Fläche ≥ 0; Heizen > 0.
- [ ] EG: Estrich-Verzögerung – Gewicht liegt eher bei 2–6 h.
- [ ] Nach einigen Tagen: Spalte „innerhalb ±σ“ und „σ ×“ ansehen – der Faktor zeigt, wie stark die
      Sicherheitsmarge aus beobachteten Fehlern geweitet wird.

## 7. 2–3 Wochen beobachten (Steuerung **aus**)
- [ ] Tab „Jetzt & Plan“: Begründung nachvollziehbar? Geplante Blöcke sinnvoll? „Prognose von vor 6 h“ nah an gemessen?
- [ ] Keine Reparaturhinweise (*Einstellungen → Reparaturen*) zu Thermocast.

## 8. Steuerung einschalten
- [ ] `switch.thermocast_steuerung_aktiv` an. Ereignisse „Steuerung eingeschaltet“, „Freigabe geschrieben“.
- [ ] Erste Tage: keine Meldung „Freigabe nicht bestätigt“, Wechsel/Tag im Budget, Räume im Komfortband.
- [ ] Tab „KPIs“: Brennerstarts/Tag und kWh/Heizgradtag **vorher vs. seit Steuerung**.

## 9. Notfall / Rückweg
- Schalter „Steuerung aktiv“ **aus** → Freigabe sofort auf 16 (Heizen erlaubt).
- Integration entfernen/deaktivieren → ebenfalls 16.
- Fällt HA aus, während 10 gesetzt ist: das RC310 heizt bei Kälte (< 10 °C gedämpft) von selbst.
- *Diagnose herunterladen* (⋮ an der Integration) liefert alle Modelle, Logs und Ereignisse für die Analyse.
