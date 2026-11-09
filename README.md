# Water Insights for Home Assistant

[![Validate](https://github.com/marfoerst/ha-water-insights/actions/workflows/validate.yml/badge.svg)](https://github.com/marfoerst/ha-water-insights/actions/workflows/validate.yml)
[![Tests](https://github.com/marfoerst/ha-water-insights/actions/workflows/tests.yml/badge.svg)](https://github.com/marfoerst/ha-water-insights/actions/workflows/tests.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

**Learn what your water is used for.** Water Insights watches any water flow or
water meter sensor, cuts the stream into individual *draws* (a shower, a toilet
flush, a washing-machine fill …), lets you label them, trains a small model
**locally** on your labels and then classifies new draws automatically. You get
one water sensor per category – ready for the Energy dashboard.

*Deutsch: siehe [unten](#deutsch).*

- Works with **any** sensor reporting flow (`L/min`, `L/h`, `m³/h`, `gal/min`) or a meter reading (`L`, `m³`, `gal`)
- Your own categories; built-in **Mixed** for overlapping draws
- Label from day 1 – optionally including draws found in your recorder history
- Training runs **inside Home Assistant** (numpy only, no cloud, no extra packages; seconds on a Raspberry Pi)
- **Honest accuracy**: measured per category on the newest draws the model has not seen
- **Rather open than wrong**: draws the model is unsure about – or that look unlike anything it knows – stay open
- Optional **helper sensors** (washing-machine plug power, bathroom presence …) improve accuracy
- Sidebar panel to label, confirm, train and inspect; services and events for automations

## Installation

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/marfoerst/ha-water-insights` as *Integration*.
2. Install **Water Insights**, restart Home Assistant.
3. *Settings → Devices & services → Add integration → Water Insights*.

Manual: copy `custom_components/water_insights` into your `config/custom_components/`.

## Setup

1. **Pick your water sensor.** Flow vs. meter is detected from the unit.
2. **Define categories** (defaults: Dusche, Toilette, Waschmaschine, Spülmaschine, Wasserhahn, Garten – rename freely).
3. **Define what a draw is** – a draw starts at **1 L/min**, ends after **60 s** below that, and must be at least **2 L** (all adjustable).
4. Optionally add **helper sensors** and keep *detect past draws from history* on.

> **Sensor rate matters.** Telling draws apart needs readings every few seconds.
> If your sensor reports only every minute or less often, a repair issue tells you so.

## Daily use

Open **Water Insights** in the sidebar:

| Tab | What you do |
|---|---|
| **Classify** | Pick a category for open draws; confirm or correct automatic ones. Each row shows liters, duration and the flow curve. Tick several draws to classify them in one go. |
| **Usage** | Liters per category today / this week / this month / since install. |
| **Model** | *Train model*, then see per category: labeled count, precision of automatic classification, share classified automatically, and a confusion matrix. |
| **Settings** | Categories, thresholds, helper sensors, appliance coupling, export/import. |

After roughly two weeks of labeling, press **Train model**. A category is
classified automatically only if it has at least **10** labeled draws and its
automatic precision on held-out draws is at least **90 %**; individual draws
additionally need a confidence of **0.8** (adjustable). Everything else stays open
with a suggestion. Only draws *you* labeled or confirmed are used for training.

### Appliance runs (washing machine, dishwasher)

A washing machine fills several times per program, so one wash shows up as
10–20 separate draws. Keep them separate – a toilet flush in between must not end
up in the washing-machine total – but you don't have to classify them one by one:

1. Add the appliance's power plug as a helper sensor.
2. In **Settings → Appliances**, couple that helper to a category and set a
   threshold (e.g. *Waschmaschine* from 10 W).
3. On **Classify**, draws that happened while the appliance was running are
   grouped into one card per run, e.g. *Waschmaschine ran 07:47–12:35 · 13 draws*.
   Draws are pre-ticked unless the model is confident they are something else.
   Untick what was something else and press *Classify ticked as Waschmaschine*.

The coupling only groups and pre-selects – it never classifies by itself. Once
the category has enough labels, the model takes over the fills automatically.

## Entities

Per category plus **Mixed**, **Unclassified** and **Small amounts** (water that
never became a draw): liters, `device_class: water`, `state_class: total`. The
values can go *down* when you re-label a draw – that is intended, liters move
between categories and the sum stays the same. Use them under
*Energy → Water → Individual devices*. Small amounts come from the integration's
own flow integral, so meter jumps on reconnects never create phantom liters.
Draws from history or imports are available for labeling and training but are
not booked onto the sensors.

Diagnostic: **Model status** (`untrained`/`trained`/`stale`, with per-class
metrics as attributes) and **Last trained**.

## Events and services

| Event | Data |
|---|---|
| `water_insights_event_detected` | `entry_id, event_id, start, end, volume_l, duration_s, prediction, confidence, label, status` (`open`/`auto`) |
| `water_insights_event_classified` | same, after labeling/confirming |

Services: `water_insights.classify_event`, `confirm_event`, `train_model`
(returns a summary), `export_events`, `import_events` (export JSON or legacy JSONL).

The integration sends **no notifications** itself. A ready-made blueprint asks you
on your phone what a draw was:
[`blueprints/automation/water_insights/notify_new_draw.yaml`](blueprints/automation/water_insights/notify_new_draw.yaml).
An example dashboard with standard cards: [`examples/dashboard.yaml`](examples/dashboard.yaml).

## Limits – please read

- One whole-house sensor cannot separate **simultaneous** draws; label those *Mixed*.
- Similar draws (hand washing vs. brushing teeth) are hard; they will often stay open.
- Accuracy shown is measured on *your* data. Synthetic tests are not a promise.
- Not a leak detector.

## Privacy

All data and the model stay in your Home Assistant (`.storage/water_insights.*`,
included in backups). Nothing is sent anywhere.

## Benchmark your data offline

```bash
python -m tools.benchmark export.json      # file from the export service / panel
python -m tools.benchmark --synthetic      # sanity check
```

## Development

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```

The core (`detector`, `features`, `classifier`, `evaluation`, `storage`) is plain
Python + numpy and has no Home Assistant imports. Tests use synthetic data only.

---

## Deutsch

**Water Insights zeigt, wofür dein Wasser verbraucht wird.** Die Integration
erkennt einzelne Entnahmen aus einem beliebigen Durchfluss- oder Zählersensor,
du ordnest sie ein (Dusche, Toilette, Waschmaschine …), ein kleines Modell lernt
**lokal in Home Assistant** daraus und ordnet danach automatisch ein – nur wenn es
sich sicher ist. Pro Kategorie entsteht ein Wasser-Sensor für das Energie-Dashboard.

**Ablauf:** Integration hinzufügen → Wassersensor wählen → Kategorien und
Erkennungsschwellen festlegen (Standard: ab 1 L/min, Ende nach 60 s, ab 2 L) →
in der Seitenleiste *Water Insights* Entnahmen einordnen → nach etwa zwei Wochen
*Modell trainieren* → Genauigkeit pro Kategorie ansehen. Automatisch eingeordnet
wird eine Kategorie erst ab 10 eingeordneten Entnahmen und mindestens 90 %
Präzision auf ungesehenen Entnahmen; unsichere oder ungewohnte Entnahmen bleiben
offen. Korrekturen verschieben Liter zwischen den Sensoren. Alle Daten bleiben lokal.

**Gerätelauf (Waschmaschine, Spülmaschine):** Eine Waschmaschine zieht pro
Programm mehrmals Wasser, ein Waschgang erscheint also als 10–20 Entnahmen. Die
bleiben einzeln, damit eine Toilettenspülung dazwischen nicht im
Waschmaschinen-Topf landet. Koppelt man unter *Einstellungen → Gerätekopplung*
den Leistungssensor der Maschine an die Kategorie (z. B. ab 10 W), fasst der Tab
*Einordnen* jeden Lauf zu einer Karte zusammen. Man wählt ab, was etwas anderes
war, und ordnet den Rest mit einem Klick ein. Mehrere Entnahmen lassen sich auch
sonst per Häkchen gemeinsam einordnen.

## License

MIT
