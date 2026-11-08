# Water Insights – Design

Status: Entwurf, 2026-11-08 · Projekt: `ha-water-insights` (Arbeitsname)

## 1. Intent – worum es geht und warum

Eine Home-Assistant-Integration, die **Wasserentnahmen erkennt**, sie den Nutzer **einordnen** lässt (Dusche, Toilette, Waschmaschine …), aus diesen Einordnungen **lokal ein Modell trainiert** und danach Entnahmen **automatisch einordnet**. Ergebnis: Der Nutzer versteht, **wie viel Wasser wofür** verbraucht wurde – als HA-Sensoren pro Kategorie und als Übersicht.

Hintergrund: Martin hat das heute als Bündel aus Automationen, Helfern und einer JSONL-Datei im eigenen HA (Aquaro-Durchflusssensor). Es soll ein eigenständiges, öffentliches Open-Source-Projekt werden, das bei **beliebigen Nutzern mit beliebigem Wassersensor** funktioniert, inklusive Modelltraining – ohne Cloud, ohne externen Rechner.

**Der Fehlschlag, den es zu vermeiden gilt (Anti-Goal Nr. 1):** schlechte Erkennungen. Ein Modell, das selbstbewusst falsch einordnet, ist schlimmer als keines. Qualität und Ehrlichkeit über die eigene Genauigkeit stehen über Feature-Umfang.

## 2. Nutzerreise

**Tag 1 – Einrichten (Nutzervorgabe)**
1. Installation über HACS, Einrichtung über den normalen HA-Einrichtungsdialog (Config Flow).
2. **Wassersensor wählen** – beliebige HA-Entität, die entweder Durchfluss (z. B. L/min) **oder** einen Zählerstand (L, m³) liefert.
3. **Kategorien festlegen** – frei definierbar; vorbelegt mit Vorschlägen (Dusche, Toilette, Waschmaschine, Spülmaschine, Wasserhahn, Garten), editierbar. *(Vorbelegung: meine Entscheidung.)*
4. **Definieren, ab wann eine Entnahme eine Entnahme ist** – Startschwelle Durchfluss, Ruhezeit bis Ende, Mindestmenge. Standardwerte aus Martins Praxis: **≥ 1 L/min, Ende nach 60 s unter Schwelle, Mindestmenge 2 L**. *(Standardwerte: meine Entscheidung.)*
5. Optional: **Hilfssensoren** angeben (siehe §5).
6. **Ab sofort** werden Entnahmen erkannt und können **eingeordnet** werden (Nutzervorgabe: Einordnen ab Tag 1).

**Ca. Woche 2 – Training (Nutzervorgabe)**
- Nutzer startet den Trainingslauf selbst (Knopf / Aktion) und sieht danach die **Genauigkeit**, pro Kategorie (*pro Kategorie: meine Entscheidung*).

**Danach – Automatik (Nutzervorgabe)**
- Neue Entnahmen werden **automatisch eingeordnet**; der Nutzer kann sie **später bestätigen oder ablehnen und neu einsortieren**.
- Regel für „gut genug“ (*meine Entscheidung, Nutzer hat das offen gelassen*): Jede Vorhersage hat eine Sicherheit (Wahrscheinlichkeit). Liegt sie über einer **einstellbaren Schwelle**, gilt die Entnahme als automatisch eingeordnet (Status „auto, unbestätigt“). Darunter bleibt sie **offen**, mit dem Modelltipp als Vorschlag.
- Bestätigungen und Korrekturen fließen ins nächste Training ein.

## 3. Form & Verteilung (Nutzervorgaben)

- **HACS-Custom-Integration** (nicht Supervisor-Add-on) – läuft auf allen HA-Installationsarten. Nutzer hat entschieden: HACS-Regeln und HA-Konventionen einhalten.
- **Öffentliches GitHub-Repo, voll Open Source.** Lokal entwickelt in `~/GitHub/ha-water-insights`; **Veröffentlichung auf GitHub erst nach explizitem OK von Martin.**
- Technik-Wahl dem Builder/mir überlassen, mit diesen Festlegungen (*meine Entscheidungen*):
  - Python, Struktur nach HA-Konventionen (`custom_components/water_insights/`, `manifest.json`, `config_flow.py`, `strings.json` + Übersetzungen **de** und **en**, `hacs.json`, GitHub Releases, HACS-/hassfest-Validierung in CI). Lizenz: MIT.
  - **Keine schweren Abhängigkeiten.** Modell und Training nur mit **numpy** (ist in HA Core enthalten) bzw. reinem Python. Kein scikit-learn/scipy/torch – Installationsprobleme auf Alpine/ARM wären ein Risiko für Verbreitung.
  - Training muss auf einem **Raspberry Pi 3/4** mit einigen tausend Entnahmen in **unter ~1 Minute** laufen und darf die HA-Event-Loop nicht blockieren (Executor/Thread).

## 4. Oberfläche (Nutzervorgabe: beides)

**A. Eigener Seitenleisten-Eintrag „Wasser“ (Haupt-UI)** – eigenes Frontend-Panel, wie es HACS selbst macht. Enthält:
- **Offene Entnahmen** einordnen; **auto-eingeordnete** bestätigen/ablehnen/neu einsortieren. Pro Entnahme sichtbar: Zeit, Menge, Dauer, Verlaufskurve, Modelltipp + Sicherheit.
- **Kategorien** und **Erkennungsschwellen** anpassen.
- **Training starten**, Ergebnis ansehen: Genauigkeit pro Kategorie, Verwechslungen (welche Kategorie wird mit welcher verwechselt), Anzahl Trainingsdaten pro Kategorie, Hinweis wenn zu wenig.
- **Übersicht Verbrauch pro Kategorie** (heute / Woche / Monat).
- Frontend-Technik: Lit-Webkomponente im HA-Stil, als gebautes Bundle im Repo ausgeliefert (*meine Entscheidung*).

**B. Für normale Dashboards** – Sensoren pro Kategorie (§6) und eine **mitgelieferte Beispiel-Dashboardseite (YAML) nur aus Standard-Karten**, damit keine zweite UI gepflegt werden muss.

**C. HA-Aktionen (Services)** – alles, was das Panel tut, ist auch als Aktion verfügbar, z. B. `water_insights.classify_event`, `water_insights.confirm_event`, `water_insights.train_model`. Damit funktionieren Push-Antworten und Automationen.

## 5. Erkennung & Modell

**Entnahme-Erkennung**
- Durchfluss-Sensor: direkt mit den Schwellen aus §2.
- Zählerstand-Sensor: Durchfluss aus Differenzen ableiten.
- **Robust gegen Zählersprünge** – eine Lehre aus Martins Aquaro (springt bei Neuverbindung um ±zehntausende Liter): Sprünge/Rücksetzer und Ausfälle (`unavailable`/`unknown`) dürfen keine Fantasie-Entnahmen erzeugen; plausible Obergrenze für Durchfluss, Lücken werden als Lücke behandelt.
- **Messrate prüfen:** Bei der Einrichtung bzw. nach den ersten Daten erkennt die Integration Sensoren mit zu grober Messrate (z. B. nur alle 5 min) und warnt, dass Unterscheidung dann kaum möglich ist (*meine Entscheidung*).
- Pro Entnahme wird der **rohe Durchflussverlauf** gespeichert (für Merkmale, Neuberechnung und die Kurve im Panel).

**Merkmale**, die das Modell sieht:
- **Dauer** (ausdrücklich vom Nutzer als wichtig genannt), Menge, höchster und typischer Durchfluss, Form des Verlaufs (gleichmäßig vs. stufig/pulsierend, Anzahl Pausen), Uhrzeit, Wochentag.
- **Optionale Hilfssensoren** (Nutzer zugestimmt): z. B. Leistung einer Waschmaschinen-/Spülmaschinen-Steckdose, Präsenzmelder Bad, Gerätestatus. Werte rund um die Entnahme werden als zusätzliche Merkmale genutzt. Modell funktioniert auch ohne.

**Modell** – konkreter Algorithmus dem Builder überlassen, mit Anforderungen:
- liefert **kalibrierte Wahrscheinlichkeiten** pro Kategorie (für die Sicherheitsschwelle);
- numpy-only, schnell auf Pi (siehe §3);
- Baseline und Kandidaten werden per Kreuzvalidierung verglichen; das beste kommt in Produktion.

**Qualitätsregeln (aus Anti-Goal Nr. 1)**
1. **Ehrlich messen:** Genauigkeit nur auf zurückgehaltenen, ungesehenen Entnahmen; zeitlich getrennt (neuere Daten als Test), damit das Modell nicht „sich selbst erkennt“. **Pro Kategorie** ausgewiesen, keine geschönte Gesamtzahl.
2. **Lieber offen als falsch:** Unter der Sicherheitsschwelle wird nicht automatisch eingeordnet. Training/Automatik erst ab einer **Mindestzahl eingeordneter Entnahmen pro Kategorie** (Startwert z. B. 10 – Builder justiert); Kategorien darunter werden nicht vorhergesagt und im Panel als „braucht mehr Daten“ markiert.
3. **„Gemischt“** als eingebaute Kategorie für verschmolzene, gleichzeitige Entnahmen (z. B. Toilette während Dusche). Nutzer kann sie vergeben; das Modell darf sie vorhersagen.
4. Nur vom Nutzer **bestätigte oder manuell eingeordnete** Entnahmen sind Trainingsdaten – automatisch eingeordnete, unbestätigte nicht (keine Selbstverstärkung von Fehlern).

## 6. Ergebnisse in HA

- **Sensor pro Kategorie** in Litern, `device_class: water`, damit nutzbar im **Energie-Dashboard unter „Wasserverbrauch einzelner Geräte“** und in Statistik-Karten (Nutzervorgabe: eigene Sensoren).
  - Zusätzlich Sensoren **„Nicht eingeordnet“** und **„Gemischt“**.
  - **Buchung (meine Entscheidung):** Automatisch eingeordnete, noch unbestätigte Entnahmen zählen **sofort** auf ihre Kategorie (sonst läge der Großteil dauerhaft in „Nicht eingeordnet“ und der Nutzer sähe nicht, wofür das Wasser verbraucht wurde). Offene Entnahmen zählen auf „Nicht eingeordnet“. Eine Korrektur verschiebt die Liter.
  - **Summe (meine Entscheidung):** Die Summe aller Sensoren soll dem **gemessenen Gesamtverbrauch** entsprechen. Dafür gibt es einen Sensor **„Kleinmengen“** für Wasser, das zu keiner Entnahme wird (unter Mindestmenge oder Schwelle). Er wird aus dem eigenen Durchfluss-Integral der Integration berechnet (Durchfluss × Zeit, robust), **nie** aus Sprüngen eines Zählerstands. Ausfall-Lücken des Sensors werden nicht geschätzt, sie fehlen in Summe und Teilen gleichermaßen.
  - **Wichtig:** Neueinordnung verschiebt Liter zwischen Kategorien, ein Sensor kann also sinken. Darum `state_class: total` (erlaubt Abnahmen), **nicht** `total_increasing` (würde Abnahmen als Zählerreset werten und Fantasiewerte erzeugen). Ob Korrekturen rückwirkend in die Statistik des ursprünglichen Zeitraums gebucht werden sollen oder zum Korrekturzeitpunkt, entscheidet der Builder; zum Korrekturzeitpunkt ist das Minimum.
- Optional ein **Gesamtverbrauch-Sensor** auf Basis der erkannten Entnahmen.
- Diagnose-Entitäten: Modellstatus, letzte Trainingszeit, Genauigkeit.

**Ereignisse (Nutzervorgabe: Ereignis reicht, keine eingebauten Push-Nachrichten)**
- `water_insights_event_detected` (Entnahme beendet: id, Start, Ende, Menge, Dauer, Modelltipp, Sicherheit, Status) und `water_insights_event_classified`.
- Mitgeliefert, aber nicht Teil der Integration: **Beispiel-Blueprint** für eine Push-Nachricht mit Antwort-Buttons, die `water_insights.classify_event` aufrufen.

## 7. Daten

- Ablage lokal im HA-Konfigurationsverzeichnis (HA-`Store` oder SQLite – Builder entscheidet), überlebt Neustarts und Updates, ist in HA-Backups enthalten.
- **Export/Import** eingeordneter Entnahmen als JSON (*meine Entscheidung*) – für Backup, Umzug, Fehlersuche und um Martins bisherige Daten aus `/media/wasser_events.jsonl` zu übernehmen (Format dort: JSON-Zeilen mit drei Typen, verknüpft über `event_id` (`%Y%m%dT%H%M%S` des Starts): `type:"p"` = Durchfluss-Stützpunkt `{t: Sekunden seit Start, f: L/min}`; `type:"event"` = `{start, dauer_s, liter, max_flow, mittel_flow, stunde, wochentag, waschmaschine, label}`; `type:"label"` = `{label, ts}`, das jüngste Label gewinnt. Ein kleiner Adapter dafür ist ausdrücklich erlaubt.)
- **Rückblick beim Einrichten** (*meine Entscheidung, nice-to-have*): Falls der gewählte Sensor Verlauf im HA-Recorder hat, können vergangene Entnahmen (Recorder-Aufbewahrung, standardmäßig 10 Tage) nachträglich erkannt und sofort eingeordnet werden – verkürzt die Wartezeit bis zum ersten Training.
- Keine Daten verlassen die HA-Instanz.

## 8. Ziele, Nicht-Ziele, Anti-Ziele

**Ziele**
- Funktioniert mit jedem Durchfluss- oder Zählerstand-Sensor.
- Kategorien, Erkennungsschwellen frei definierbar.
- Einordnen ab Tag 1; Training lokal per Knopf; ehrliche Genauigkeit pro Kategorie.
- Automatische Einordnung mit Bestätigen/Korrigieren.
- Sensoren pro Kategorie fürs Energie-Dashboard; eigenes Seitenleisten-Panel.
- HACS-tauglich, HA-konform, Open Source, de + en.

**Nicht-Ziele (v1)**
- Eingebaute Push-Benachrichtigungen (nur Ereignis + Beispiel-Blueprint).
- Leckerkennung / Alarm bei Dauerfluss (anderes Problem, evtl. später).
- Cloud-Training, geteilte/vortrainierte Modelle über Haushalte hinweg.
- Mehrere Wassersensoren pro Instanz zusammenführen (eine Config-Entry = ein Sensor; mehrere Entries sind möglich).
- Supervisor-Add-on.
- Automatische Migration von Martins bestehenden Automationen (er stellt manuell um; nur Datenimport, §7).

**Anti-Ziele (wäre Fehlschlag, auch wenn es technisch läuft)**
- Selbstbewusst falsche Einordnungen / geschönte Genauigkeitsangaben.
- Schwere Abhängigkeiten, die auf Pi/Alpine nicht installieren.
- Fantasie-Verbräuche durch Sensor-Sprünge oder falsche `state_class`.
- Blockieren der HA-Event-Loop beim Training.

## 9. Dem Builder überlassen

- Konkreter Algorithmus und Merkmalsdetails (innerhalb §3/§5).
- Speicherformat (Store vs. SQLite), interne Architektur, Teststrategie (Ziel: HA-übliche pytest-Tests mit `pytest-homeassistant-custom-component`).
- Exakte Mindestzahlen, Default-Sicherheitsschwelle (Startvorschlag 0,8).
- Panel-Layout im Detail (innerhalb §4A).
- Endgültiger Name/Domain (Arbeitsname `water_insights`).

## 10. Entwicklung & Test (Entscheidungen Martin, 2026-11-08)

- **Echte Daten nur lokal:** Martins `/media/wasser_events.jsonl` wird zur Entwicklung (Modellauswahl, Kalibrierung, Default-Schwellen) lokal genutzt, **nicht** ins Repo eingecheckt. Repo-Tests und CI laufen mit **synthetisch erzeugten** Durchflusskurven (Generator im Repo).
- **Kein Testen auf Martins Live-HA ohne ausdrückliches OK in der jeweiligen Session.** Entwicklung lokal mit pytest und einem Replay-Werkzeug, das aufgezeichnete/synthetische Sensordaten abspielt. Installation auf Martins HA als separater, explizit freizugebender Probelauf.
- **Release-Kriterium v1:** Auf Martins Daten, gemessen auf ungesehenen Entnahmen, sind bei **Dusche, Toilette und Waschmaschine ≥ 90 % der automatischen Einordnungen korrekt** (Präzision bei aktiver Sicherheitsschwelle). Erreicht eine Kategorie das nicht, wird sie nicht automatisch eingeordnet (bleibt offen) – **v1 erscheint trotzdem**.

## 11. Erfolg

- Martin ersetzt sein eigenes Automationsbündel durch die Integration.
- Auf Martins Daten (Aquaro, Hilfssensor Waschmaschine) nach ~2 Wochen Einordnen: ehrliche Genauigkeit pro Kategorie sichtbar; die Hauptkategorien (Dusche, Toilette, Waschmaschine) werden mit hoher Sicherheit getroffen, schwierige (Wasserhahn, Gemischt) bleiben eher offen statt falsch.
- Ein fremder Nutzer kann die Integration über HACS installieren und ohne Doku-Studium bis zum ersten Einordnen kommen.
