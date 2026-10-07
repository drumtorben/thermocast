# Thermocast – Installations-Checkliste (echte HA mit EMS-ESP)

Ziel: Thermocast sicher in Betrieb nehmen – erst **beobachten**, dann **steuern**. Die Entitätsnamen unten
sind typische EMS-ESP-Namen und **nur Beispiele** – in *Entwicklerwerkzeuge → Zustände* die echten suchen.

## 0. Voraussetzungen
- [ ] Home Assistant **≥ 2026.9** (Python 3.14), Recorder aktiv (kein `include`-Filter, `purge_keep_days: 120` passt).
- [ ] EMS-ESP liefert per MQTT: Außentemperatur, Vorlauf, Heizungspumpe, RC310-Sommer/Winter-Modus und -Schwelle.

## 1. Entitäten vorab prüfen
| Zweck | Beispiel | Prüfen |
|---|---|---|
| Außentemperatur | `sensor.boiler_outside_temperature` | °C, ändert sich; Attribut `state_class: measurement` |
| Vorlauf | `sensor.boiler_current_flow_temperature` | steigt bei Brennerbetrieb |
| Heizungspumpe | `binary_sensor.boiler_heating_pump` | an/aus passt zum Heizbetrieb (nicht zur WW-Ladung) |
| **Freigabe** | `select.thermostat_hc1_summersetmode` (RC310 Sommer/Winter-Modus) | siehe 2. |
| Sommerschwelle | `number.thermostat_hc1_summertemp` | einmalig fest auf **10 °C** |
| Better Thermostat (optional) | `climate.<bt_raum>` | nur für Zonen, deren Sollwert Thermocast setzen soll |
| Raumtemperaturen | Wohnzimmer, Küche, Schlafräume | `state_class: measurement` (für Warmstart/KPIs) |
| Ventil (TRV) | `climate.<trv>` (das Thermostat selbst, nicht Better Thermostat) | `hvac_action` wechselt zwischen `heating` und `idle`; **nicht** `valve_opening_degree` (nur eine eingestellte Grenze) |
| Brennerstarts | `sensor.boiler_burner_starts` | `state_class: total_increasing` |
| Heizenergie | `sensor.boiler_energy_heating` | kWh, `total_increasing` |
| Warmwasser aktiv | `binary_sensor.boiler_dhw_charging` | an während WW-Ladung |

## 2. Freigabe einrichten und testen (wichtig, EEPROM!)

Empfohlen: **Winter** = Heizen erlaubt, **Auto** = gesperrt, Sommerschwelle fest **10 °C**.
- Ein Block heizt damit sicher – auch an milden Tagen (die Schwelle allein wirkt nur unterhalb ihres Werts).
- Zwischen den Blöcken steht die Heizungspumpe (im Winterbetrieb läuft sie sonst periodisch).
- Fällt HA aus: in „Winter“ heizt das RC310 normal weiter, in „Auto“ spätestens unter 10 °C (gedämpft).
- Grenze: unter 10 °C kann Thermocast nicht sperren. „Sommer“ als Sperre nur mit einer Absicherung außerhalb
  von HA (z. B. EMS-ESP-Scheduler), sonst heizt bei einem HA-Ausfall nichts mehr.

- [ ] `number.thermostat_hc1_summertemp` einmalig auf **10** setzen.
- [ ] Den Modus in *Entwicklerwerkzeuge → Aktionen* (`select.select_option`) auf **Winter**, dann **Auto** stellen.
- [ ] Der Zustand folgt **innerhalb weniger Sekunden** – sonst stimmt die Entität nicht (Thermocast würde
      das als „nicht bestätigt“ melden und mit wachsenden Pausen erneut schreiben, max. 40×/Tag).
- [ ] Danach auf **Auto** lassen.

**Kessel und Regler für Blöcke vorbereiten** (Erfahrungen aus dem Betrieb):
- [ ] **Heizkurve:** Bei witterungsgeführter Kurve liegt der Soll-Vorlauf an milden Tagen oft kaum über der
      Raumtemperatur – dann zündet der Brenner im Block nicht. Regelungsart mit Fußpunkt („Basispunkt
      Außentemp.“) wählen und den Fußpunkt auf ~30–35 °C (bei +20 °C außen) anheben. Kontrolle: Sensor
      Soll-Vorlauf (`…targetflowtemp`) im Block deutlich über der Raumtemperatur + Einschalthysterese.
- [ ] **Pumpennachlauf** kurz (z. B. 10 min) und Pumpenoptimierung aus – sonst läuft die Pumpe zwischen den Blöcken.
- [ ] **Warmwasser-Komfort** (EMS-ESP `select.boiler_dhw_comfort`): bei „Eco“ wurde beobachtet, dass der Kessel
      während Heizbetrieb nicht nachlädt (Speicher fiel weit unter die Einschaltschwelle); „Heiß“ lädt normal.

## 3. Installation
- [ ] `custom_components/thermocast` nach `config/custom_components/` kopieren (später: HACS-Custom-Repo).
- [ ] HA neu starten → *Einstellungen → Geräte & Dienste → Integration hinzufügen → Thermocast*.
- [ ] Haus: Außentemperatur, Vorlauf, **Heizungspumpe** (empfohlen), Freigabe = Sommer/Winter-Modus,
      „Heizen erlaubt“ = **Winter**, „gesperrt“ = **Auto**.
      Falsch gewählt? *Thermocast → ⋮ → Rekonfigurieren* – Zonen und gelernte Modelle bleiben erhalten.

## 4. Zonen anlegen („Zone hinzufügen“)

Eine Zone = ein Raum oder eine Gruppe von Räumen am selben Heizkreis. Faustregeln:

- **Fußbodenheizung ohne Stellantriebe** (z. B. ein ganzes Geschoss): **eine** Zone, Typ Fußbodenheizung,
  Sensor des wichtigsten Raums (Wohnzimmer), **führt**.
- **Heizkörper mit TRV:** je Raum eine Zone, Typ Heizkörper, das TRV-Thermostat (`climate`) als Ventil-Entität –
  ohne Ventil-Entität zählt jede Pumpenlaufzeit als Heizen im Raum.
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
  morgens + abends).
- **Laden & Zehren:** *Obergrenze* (bis hierher darf ein Block die Zone aufwärmen, Standard Komfort + 1 K) und
  *Grundwert* (Untergrenze außerhalb der Komfortzeit, Standard Komfort − 2 K). Je weiter das Band, desto
  längere Pausen und weniger Brennerstarts.
- **Better Thermostat steuern (optional, je Zone):** Thermocast setzt den BT-Sollwert – im Block die
  Obergrenze, sonst die Untergrenze. Die TRVs selbst stellt weiter nur BT. **BT-Zeitpläne/Automationen für
  diese Räume deaktivieren**, sonst regeln zwei Stellen gegeneinander. Von Hand verstellt? Thermocast lässt
  den Raum bis zum Blockende (mindestens 3 h) in Ruhe.
- **Ruhezeit** (z. B. Kinderzimmer 19–07 Uhr): 15 min vorher einmal Grundwert, danach keine Änderung mehr
  (keine Stellgeräusche). Ausnahme: eine führende Zone fällt mehr als 1 K unter den Grundwert.
  Mehrere Ruhefenster (z. B. Mittagsschlaf + Nacht, je Wochentag): einen **Zeitplan-Helfer** als
  „Ruhezeit-Zeitplan“ wählen – Ruhe gilt, wenn Zeitplan **oder** Von/Bis aktiv ist.
- **Zonen, die ihr Ziel evtl. nicht erreichen** (z. B. Bad an einem FBH-Kreis mit Rücklaufbegrenzer):
  zuerst **nicht führend** anlegen und im Panel beobachten – eine führende Zone, die ihr Ziel nie erreicht,
  hält den Kessel dauerhaft frei.
- [ ] Nach jeder Zone lernt Thermocast automatisch aus den letzten **30 Tagen** (Ereignis
      „Modell aus der Historie gelernt“, Logeintrag `warm start: zone … learned … hours`).

## 5. Optionen (⋮ → Konfigurieren)
- [ ] Brennerstarts, Heizenergie, Warmwasser aktiv eintragen.
- [ ] „Unsicherheit kalibrieren“ an lassen (Standard).
- [ ] Mindestblock 2–3 h, Mindestpause 2 h, max. 12 Wechsel/Tag sind gute Startwerte. Der Mindestblock begrenzt
      nur, wann ein Block frühestens enden darf (geplant wird ab 2 h); 1 h erlaubt frühes Abbrechen, wenn die
      Räume schon voll sind.
- [ ] „Wenige Brennerstarts ↔ wenig Gas“: Standard 80 (wenige lange Blöcke). Wirkung im KPI-Tab ablesen.
- [ ] „Taktsperre des Kessels“: die am Kessel eingestellte Mindestzeit zwischen zwei Starts (z. B. 45 min);
      braucht den Sensor Brennerstarts (am besten nur Heiz-Starts).
- [ ] Sicherheitsabstand σ: 1 = vorsichtig, 0 = nur der Mittelwert der Prognose.

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
- [ ] Tageskarten: „Brennerstarts … je Block“ – mehr als ~2 je Block heißt, dass der Brenner im Block taktet
      (Wärmeabnahme zu gering) → Obergrenze/BT-Steuerung prüfen.

## 9. Notfall / Rückweg
- Schalter „Steuerung aktiv“ **aus** → Freigabe sofort auf „Heizen erlaubt“, BT-Zonen auf ihre Untergrenze.
- Integration entfernen/deaktivieren → ebenso.
- Fällt HA aus, während „gesperrt“ gesetzt ist: das RC310 heizt bei Kälte (< 10 °C gedämpft) von selbst.
  Better Thermostat läuft in HA – die TRVs behalten dann ihren letzten Sollwert.
- Fenster-Entitäten je Zone eintragen: bei offenem Fenster löst die Zone keinen Block aus und ihr Thermostat bleibt
  auf dem Grundwert.
- *Diagnose herunterladen* (⋮ an der Integration) liefert alle Modelle, Logs und Ereignisse für die Analyse.
