// Water Insights sidebar panel – vanilla Web Component, no build step.
const I18N = {
  en: {
    title: "Water Insights", classify: "Classify", usage: "Usage", model: "Model", settings: "Settings",
    open: "Open", auto: "Auto, unconfirmed", none: "Nothing to classify right now.",
    confirm: "Confirm", choose: "Choose category…", suggestion: "Suggestion", confidence: "confidence",
    liters: "L", min: "min", today: "Today", week: "This week", month: "This month", total: "Since install",
    small: "Small amounts", unclassified: "Unclassified", mixed: "Mixed", category: "Category",
    train: "Train model", training: "Training…", status: "Status", untrained: "Not trained yet",
    trained: "Trained", stale: "Retraining recommended", lastTrained: "Last trained", chosen: "Model",
    n: "Labeled", precision: "Precision (auto)", coverage: "Auto share", enabled: "Automatic",
    yes: "yes", no: "no", needMore: "needs more data", notEnough: "Not enough labeled draws yet. Each category needs at least 10.",
    confusion: "Confusion (rows: truth, columns: prediction)", save: "Save", saved: "Saved.",
    categoriesHint: "One category per line", startFlow: "Start flow (L/min)", endQuiet: "Quiet time until end (s)",
    minVolume: "Minimum volume (L)", confThreshold: "Confidence for automatic classification",
    helpers: "Helper sensors (entity IDs, one per line)", export: "Export draws", import: "Import draws",
    imported: "Imported", noEntries: "No water sensor set up. Add the Water Insights integration first.",
    honest: "Measured on the newest draws the model has not seen during training.",
    unfamiliar: "Unfamiliar pattern – no suggestion",
    invalid: "Please fill in all fields with valid values.",
    tooLarge: "File too large for the browser upload. Copy it to /media and call the service water_insights.import_events with path.",
    ran: "ran", draws: "draws", groupHint: "Ticked = belongs to the appliance. Untick whatever was something else.",
    applyGroup: "Classify ticked as", others: "Other draws", selected: "selected", apply: "Classify",
    appliances: "Appliances", appliancesHint: "Couple a helper sensor to a category. Draws while its value is at or above the threshold are grouped as one appliance run on the Classify tab. Nothing is classified automatically by this.",
    noAppliance: "– no appliance –", threshold: "from value", noHelpers: "Add helper sensors above and save first.",
  },
  de: {
    title: "Water Insights", classify: "Einordnen", usage: "Verbrauch", model: "Modell", settings: "Einstellungen",
    open: "Offen", auto: "Automatisch, unbestätigt", none: "Gerade gibt es nichts einzuordnen.",
    confirm: "Bestätigen", choose: "Kategorie wählen…", suggestion: "Vorschlag", confidence: "Sicherheit",
    liters: "L", min: "Min.", today: "Heute", week: "Diese Woche", month: "Dieser Monat", total: "Seit Einrichtung",
    small: "Kleinmengen", unclassified: "Nicht eingeordnet", mixed: "Gemischt", category: "Kategorie",
    train: "Modell trainieren", training: "Training läuft…", status: "Status", untrained: "Noch nicht trainiert",
    trained: "Trainiert", stale: "Neues Training empfohlen", lastTrained: "Zuletzt trainiert", chosen: "Modell",
    n: "Eingeordnet", precision: "Präzision (automatisch)", coverage: "Anteil automatisch", enabled: "Automatisch",
    yes: "ja", no: "nein", needMore: "braucht mehr Daten", notEnough: "Noch zu wenige eingeordnete Entnahmen. Jede Kategorie braucht mindestens 10.",
    confusion: "Verwechslungen (Zeilen: tatsächlich, Spalten: vorhergesagt)", save: "Speichern", saved: "Gespeichert.",
    categoriesHint: "Eine Kategorie pro Zeile", startFlow: "Startschwelle (L/min)", endQuiet: "Ruhezeit bis Ende (s)",
    minVolume: "Mindestmenge (L)", confThreshold: "Sicherheit für automatische Einordnung",
    helpers: "Hilfssensoren (Entity-IDs, eine pro Zeile)", export: "Entnahmen exportieren", import: "Entnahmen importieren",
    imported: "Importiert", noEntries: "Kein Wassersensor eingerichtet. Bitte zuerst die Integration Water Insights hinzufügen.",
    honest: "Gemessen an den neuesten Entnahmen, die das Modell beim Training nicht gesehen hat.",
    unfamiliar: "Unbekanntes Muster – kein Vorschlag",
    invalid: "Bitte alle Felder mit gültigen Werten ausfüllen.",
    tooLarge: "Datei zu groß für den Upload im Browser. Nach /media kopieren und die Aktion water_insights.import_events mit path aufrufen.",
    ran: "lief", draws: "Entnahmen", groupHint: "Häkchen = gehört zum Gerät. Was etwas anderes war, abwählen.",
    applyGroup: "Ausgewählte einordnen als", others: "Weitere Entnahmen", selected: "ausgewählt", apply: "Einordnen",
    appliances: "Gerätekopplung", appliancesHint: "Koppelt einen Hilfssensor an eine Kategorie. Entnahmen, während sein Wert die Schwelle erreicht, werden im Tab Einordnen als ein Gerätelauf gruppiert. Automatisch eingeordnet wird dadurch nichts.",
    noAppliance: "– kein Gerät –", threshold: "ab Wert", noHelpers: "Zuerst oben Hilfssensoren eintragen und speichern.",
  },
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)} %`);

class WaterInsightsPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "classify";
    this._entries = [];
    this._entryId = null;
    this._events = [];
    this._groups = [];
    this._sel = new Set(); // ticked draws in the plain list
    this._gtoggle = new Map(); // draw id -> ticked state overriding a group's preselection
    this._busy = false;
    this._msg = "";
    this._loaded = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._refresh();
    }
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      if (!this._busy && this._tab !== "settings" && !this._sel.size && !this._gtoggle.size) this._refresh();
    }, 30000);
    if (this._loaded) this._refresh();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  set narrow(v) {
    this._narrow = v;
    this._render();
  }

  get t() {
    const lang = (this._hass?.language || "en").slice(0, 2);
    return I18N[lang] || I18N.en;
  }

  get entry() {
    return this._entries.find((e) => e.entry_id === this._entryId);
  }

  label(key) {
    const t = this.t;
    return { mixed: t.mixed, unclassified: t.unclassified, small: t.small }[key] || key;
  }

  async _refresh() {
    try {
      const r = await this._hass.callWS({ type: "water_insights/list" });
      this._entries = r.entries;
      if (!this.entry && this._entries.length) this._entryId = this._entries[0].entry_id;
      if (this.entry) {
        const [open, auto, groups] = await Promise.all([
          ...["open", "auto"].map((status) =>
            this._hass.callWS({ type: "water_insights/events", entry_id: this._entryId, status, limit: 300 })
          ),
          this._hass.callWS({ type: "water_insights/groups", entry_id: this._entryId }),
        ]);
        this._events = [...open.events, ...auto.events].sort((a, b) => b.start - a.start);
        this._groups = groups.groups;
        const ids = new Set(this._events.map((e) => e.id));
        this._sel = new Set([...this._sel].filter((id) => ids.has(id)));
        this._gtoggle = new Map([...this._gtoggle].filter(([id]) => ids.has(id)));
      }
    } catch (err) {
      this._msg = err.message || String(err);
    }
    this._render();
  }

  async _ws(msg) {
    this._busy = true;
    this._render();
    try {
      return await this._hass.callWS({ entry_id: this._entryId, ...msg });
    } catch (err) {
      this._msg = err.message || String(err);
      return null;
    } finally {
      this._busy = false;
      await this._refresh();
    }
  }

  // -- rendering ---------------------------------------------------------
  _render() {
    if (!this._hass) return;
    const t = this.t;
    const tabs = ["classify", "usage", "model", "settings"]
      .map((k) => `<button class="tab ${this._tab === k ? "on" : ""}" data-tab="${k}">${t[k]}</button>`)
      .join("");
    const picker =
      this._entries.length > 1
        ? `<select id="entry">${this._entries
            .map((e) => `<option value="${esc(e.entry_id)}" ${e.entry_id === this._entryId ? "selected" : ""}>${esc(e.title)}</option>`)
            .join("")}</select>`
        : "";
    let body = "";
    if (!this._entries.length) body = `<p class="muted">${t.noEntries}</p>`;
    else body = this[`_${this._tab}`]();
    this.shadowRoot.innerHTML = `<style>${CSS}</style>
      <header>
        <ha-menu-button></ha-menu-button>
        <h1>${t.title}</h1>${picker}
      </header>
      <nav>${tabs}</nav>
      ${this._msg ? `<div class="msg">${esc(this._msg)}</div>` : ""}
      <main>${body}</main>`;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) {
      menu.hass = this._hass;
      menu.narrow = this._narrow;
    }
    this._bind();
  }

  _spark(samples) {
    if (!samples?.length) return "";
    const w = 120, h = 28;
    const maxT = Math.max(...samples.map((s) => s[0]), 1);
    const maxF = Math.max(...samples.map((s) => s[1]), 1);
    const pts = samples.map(([x, y]) => `${((x / maxT) * w).toFixed(1)},${(h - (y / maxF) * h).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" class="spark" aria-hidden="true"><polyline points="${pts}"/></svg>`;
  }

  _groupChecked(g, id) {
    return this._gtoggle.has(id) ? this._gtoggle.get(id) : g.preselected.includes(id);
  }

  _classify() {
    const t = this.t;
    if (!this._events.length) return `<p class="muted">${t.none}</p>`;
    const cats = [...this.entry.categories, "mixed"];
    const fmt = new Intl.DateTimeFormat(this._hass.language, { dateStyle: "short", timeStyle: "short" });
    const tfmt = new Intl.DateTimeFormat(this._hass.language, { timeStyle: "short" });
    const byId = new Map(this._events.map((e) => [e.id, e]));
    const grouped = new Set(this._groups.flatMap((g) => g.event_ids));
    const groupsHtml = this._groups
      .map((g, gi) => {
        const evs = g.event_ids.map((id) => byId.get(id)).filter(Boolean);
        if (!evs.length) return "";
        const n = evs.filter((e) => this._groupChecked(g, e.id)).length;
        const rows = evs
          .map((e) => {
            const sugg = e.prediction && e.confidence > 0 && e.prediction !== g.category
              ? ` · ${t.suggestion}: ${esc(this.label(e.prediction))} ${pct(e.confidence)}` : "";
            return `<label class="grow">
              <input type="checkbox" data-gtick="${esc(e.id)}" data-gi="${gi}" ${this._groupChecked(g, e.id) ? "checked" : ""}>
              <span class="gtime">${tfmt.format(new Date(e.start * 1000))}</span>
              <span>${e.volume_l.toFixed(1)} ${t.liters} · ${Math.round(e.duration_s)} s · max ${e.max_flow.toFixed(1)}<span class="muted">${sugg}</span></span>
              ${this._spark(e.samples)}
            </label>`;
          })
          .join("");
        return `<div class="card group">
          <div class="ghead"><b>${esc(this.label(g.category))}</b> ${t.ran} ${fmt.format(new Date(g.start * 1000))}–${tfmt.format(new Date(g.end * 1000))} · ${evs.length} ${t.draws}</div>
          <p class="muted">${t.groupHint}</p>
          ${rows}
          <button data-gapply="${gi}" ${this._busy || !n ? "disabled" : ""}>${t.applyGroup} ${esc(this.label(g.category))} (${n})</button>
        </div>`;
      })
      .join("");
    const rest = this._events.filter((e) => !grouped.has(e.id));
    const bulk = this._sel.size
      ? `<div class="card bulk"><b>${this._sel.size} ${t.selected}</b>
          <select id="bulkcat" aria-label="${t.category}"><option value="">${t.choose}</option>${cats
            .map((c) => `<option value="${esc(c)}">${esc(this.label(c))}</option>`)
            .join("")}</select>
          <button id="bulkapply" ${this._busy ? "disabled" : ""}>${t.apply}</button></div>`
      : "";
    const head = groupsHtml && rest.length ? `<h3>${t.others}</h3>` : "";
    return groupsHtml + head + bulk + rest
      .map((e) => {
        const options = [`<option value="">${t.choose}</option>`]
          .concat(cats.map((c) => `<option value="${esc(c)}" ${e.label === c ? "selected" : ""}>${esc(this.label(c))}</option>`))
          .join("");
        const sugg = !e.prediction
          ? ""
          : e.confidence === 0
          ? t.unfamiliar
          : `${t.suggestion}: <b>${esc(this.label(e.prediction))}</b> · ${t.confidence} ${pct(e.confidence)}`;
        const badge = e.status === "auto" ? `<span class="badge">${t.auto}</span>` : `<span class="badge open">${t.open}</span>`;
        const canConfirm = e.status === "auto";
        return `<div class="card row">
          <input type="checkbox" data-sel="${esc(e.id)}" ${this._sel.has(e.id) ? "checked" : ""} aria-label="${t.selected}">
          <div class="meta">
            <div><b>${fmt.format(new Date(e.start * 1000))}</b> ${badge}</div>
            <div>${e.volume_l.toFixed(1)} ${t.liters} · ${(e.duration_s / 60).toFixed(1)} ${t.min} · max ${e.max_flow.toFixed(1)} L/min</div>
            <div class="muted">${sugg}</div>
          </div>
          ${this._spark(e.samples)}
          <div class="actions">
            <select data-classify="${esc(e.id)}" aria-label="${t.category}">${options}</select>
            ${canConfirm ? `<button data-confirm="${esc(e.id)}" ${this._busy ? "disabled" : ""}>${t.confirm}</button>` : ""}
          </div>
        </div>`;
      })
      .join("");
  }

  _usage() {
    const t = this.t;
    const e = this.entry;
    const keys = [...e.categories, "mixed", "unclassified", "small"];
    const cols = ["today", "week", "month"];
    const rows = keys
      .map(
        (k) => `<tr><td>${esc(this.label(k))}</td>${cols
          .map((c) => `<td class="num">${k === "small" ? "–" : (e.periods[c][k] ?? 0).toFixed(1)}</td>`)
          .join("")}<td class="num">${(e.totals[k] ?? 0).toFixed(1)}</td></tr>`
      )
      .join("");
    return `<div class="card"><table>
      <thead><tr><th>${t.category}</th>${cols.map((c) => `<th class="num">${t[c]}</th>`).join("")}<th class="num">${t.total}</th></tr></thead>
      <tbody>${rows}</tbody></table><p class="muted">L</p></div>`;
  }

  _model() {
    const t = this.t;
    const e = this.entry;
    const r = e.train_result;
    const status = t[e.model_status] || e.model_status;
    let html = `<div class="card">
      <p>${t.status}: <b>${status}</b>${r ? ` · ${t.lastTrained}: ${new Date(r.trained_at * 1000).toLocaleString(this._hass.language)}` : ""}${r?.chosen ? ` · ${t.chosen}: ${esc(r.chosen)}` : ""}</p>
      <button id="train" ${this._busy ? "disabled" : ""}>${this._busy ? t.training : t.train}</button>
    </div>`;
    if (!r) return html;
    if (!r.ok && r.reason === "not_enough_data") html += `<div class="card"><p>${t.notEnough}</p></div>`;
    const rows = Object.entries(r.per_class)
      .sort((a, b) => b[1].n_total - a[1].n_total)
      .map(
        ([c, m]) => `<tr><td>${esc(this.label(c))}</td><td class="num">${m.n_total}</td>
          <td class="num">${pct(m.auto_precision)}</td><td class="num">${pct(m.auto_coverage)}</td>
          <td>${m.enabled ? t.yes : m.n_total < 10 ? `<span class="badge open">${t.needMore}</span>` : t.no}</td></tr>`
      )
      .join("");
    html += `<div class="card"><table><thead><tr><th>${t.category}</th><th class="num">${t.n}</th><th class="num">${t.precision}</th><th class="num">${t.coverage}</th><th>${t.enabled}</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted">${t.honest}</p></div>`;
    const truths = Object.keys(r.confusion || {});
    if (truths.length) {
      const preds = [...new Set(truths.flatMap((k) => Object.keys(r.confusion[k])))];
      html += `<div class="card scroll"><p>${t.confusion}</p><table><thead><tr><th></th>${preds
        .map((p) => `<th class="num">${esc(this.label(p))}</th>`)
        .join("")}</tr></thead><tbody>${truths
        .map(
          (tr) => `<tr><td>${esc(this.label(tr))}</td>${preds
            .map((p) => `<td class="num ${tr === p ? "diag" : ""}">${r.confusion[tr][p] || ""}</td>`)
            .join("")}</tr>`
        )
        .join("")}</tbody></table></div>`;
    }
    return html;
  }

  _settings() {
    const t = this.t;
    const s = this.entry.settings;
    return `<div class="card form">
      <label>${t.categoriesHint}<textarea id="s_categories" rows="7">${esc((s.categories || []).join("\n"))}</textarea></label>
      <label>${t.startFlow}<input id="s_start_flow" type="number" step="0.1" min="0.1" max="20" required value="${s.start_flow}"></label>
      <label>${t.endQuiet}<input id="s_end_quiet_s" type="number" step="1" min="5" max="600" required value="${s.end_quiet_s}"></label>
      <label>${t.minVolume}<input id="s_min_volume_l" type="number" step="0.1" min="0.1" max="100" required value="${s.min_volume_l}"></label>
      <label>${t.confThreshold}<input id="s_conf_threshold" type="number" step="0.01" min="0.5" max="0.99" required value="${s.conf_threshold}"></label>
      <label>${t.helpers}<textarea id="s_helper_entities" rows="3">${esc((s.helper_entities || []).join("\n"))}</textarea></label>
      <fieldset><legend>${t.appliances}</legend><p class="muted">${t.appliancesHint}</p>${this._applianceRows(s)}</fieldset>
      <button id="save" ${this._busy ? "disabled" : ""}>${t.save}</button>
    </div>
    <div class="card">
      <button id="export">${t.export}</button>
      <label class="file">${t.import}<input id="import" type="file" accept=".json,.jsonl,application/json"></label>
    </div>`;
  }

  _applianceRows(s) {
    const t = this.t;
    const helpers = s.helper_entities || [];
    if (!helpers.length) return `<p class="muted">${t.noHelpers}</p>`;
    const rules = s.appliances || [];
    return helpers
      .map((h, i) => {
        const r = rules.find((x) => x.entity_id === h);
        const opts = [`<option value="">${t.noAppliance}</option>`]
          .concat(this.entry.categories.map((c) => `<option value="${esc(c)}" ${r?.category === c ? "selected" : ""}>${esc(c)}</option>`))
          .join("");
        const name = this._hass.states[h]?.attributes?.friendly_name || h;
        return `<div class="arow" data-helper="${esc(h)}">
          <span title="${esc(h)}">${esc(name)}</span>
          <select data-acat="${i}">${opts}</select>
          <label class="inline">${t.threshold}<input data-athr="${i}" type="number" step="any" min="0" value="${r ? r.threshold : 10}"></label>
        </div>`;
      })
      .join("");
  }

  // -- events --------------------------------------------------------------
  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => {
        this._tab = b.dataset.tab;
        this._msg = "";
        this._render();
        this._refresh();
      })
    );
    root.getElementById("entry")?.addEventListener("change", (ev) => {
      this._entryId = ev.target.value;
      this._refresh();
    });
    root.querySelectorAll("[data-classify]").forEach((sel) =>
      sel.addEventListener("change", () => {
        if (sel.value) this._ws({ type: "water_insights/classify", event_id: sel.dataset.classify, category: sel.value });
      })
    );
    root.querySelectorAll("[data-sel]").forEach((cb) =>
      cb.addEventListener("change", () => {
        if (cb.checked) this._sel.add(cb.dataset.sel);
        else this._sel.delete(cb.dataset.sel);
        this._render();
      })
    );
    root.getElementById("bulkapply")?.addEventListener("click", async () => {
      const cat = root.getElementById("bulkcat").value;
      if (!cat) return;
      const ids = [...this._sel];
      this._sel.clear();
      await this._ws({ type: "water_insights/classify_many", event_ids: ids, category: cat });
    });
    root.querySelectorAll("[data-gtick]").forEach((cb) =>
      cb.addEventListener("change", () => {
        this._gtoggle.set(cb.dataset.gtick, cb.checked);
        this._render();
      })
    );
    root.querySelectorAll("[data-gapply]").forEach((b) =>
      b.addEventListener("click", async () => {
        const g = this._groups[Number(b.dataset.gapply)];
        const ids = g.event_ids.filter((id) => this._groupChecked(g, id));
        if (!ids.length) return;
        g.event_ids.forEach((id) => this._gtoggle.delete(id));
        await this._ws({ type: "water_insights/classify_many", event_ids: ids, category: g.category });
      })
    );
    root.querySelectorAll("[data-confirm]").forEach((b) =>
      b.addEventListener("click", () => this._ws({ type: "water_insights/confirm", event_id: b.dataset.confirm }))
    );
    root.getElementById("train")?.addEventListener("click", () => this._ws({ type: "water_insights/train" }));
    root.getElementById("save")?.addEventListener("click", async () => {
      const v = (id) => root.getElementById(id).value;
      const lines = (id) => v(id).split("\n").map((x) => x.trim()).filter(Boolean);
      const nums = ["s_start_flow", "s_end_quiet_s", "s_min_volume_l", "s_conf_threshold"];
      const bad = nums.find((id) => {
        const el = root.getElementById(id);
        return el.value.trim() === "" || !el.checkValidity();
      });
      if (bad || !lines("s_categories").length) {
        this._msg = this.t.invalid;
        this._render();
        return;
      }
      const appliances = [...root.querySelectorAll("[data-helper]")]
        .map((row) => ({
          entity_id: row.dataset.helper,
          category: row.querySelector("[data-acat]").value,
          threshold: Number(row.querySelector("[data-athr]").value || 0),
        }))
        .filter((r) => r.category && lines("s_helper_entities").includes(r.entity_id));
      const res = await this._ws({
        type: "water_insights/update_settings",
        settings: {
          appliances,
          categories: lines("s_categories"),
          start_flow: Number(v("s_start_flow")),
          end_quiet_s: Number(v("s_end_quiet_s")),
          min_volume_l: Number(v("s_min_volume_l")),
          conf_threshold: Number(v("s_conf_threshold")),
          helper_entities: lines("s_helper_entities"),
        },
      });
      if (res) {
        this._msg = this.t.saved;
        setTimeout(() => this._refresh(), 1500);
      }
    });
    root.getElementById("export")?.addEventListener("click", async () => {
      const r = await this._hass.callWS({
        type: "call_service", domain: "water_insights", service: "export_events",
        service_data: { config_entry_id: this._entryId }, return_response: true,
      });
      const blob = new Blob([JSON.stringify(r.response, null, 1)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `water-insights-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
    root.getElementById("import")?.addEventListener("change", async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      if (file.size > 3500000) {
        this._msg = this.t.tooLarge;
        this._render();
        return;
      }
      const text = await file.text();
      try {
        const r = await this._hass.callWS({
          type: "call_service", domain: "water_insights", service: "import_events",
          service_data: { config_entry_id: this._entryId, data: text }, return_response: true,
        });
        this._msg = `${this.t.imported}: ${r.response.imported}`;
      } catch (err) {
        this._msg = err.message || String(err);
      }
      this._refresh();
    });
  }
}

const CSS = `
:host { display:block; min-height:100vh; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
header { display:flex; align-items:center; gap:8px; height:56px; padding:0 12px; background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); }
header h1 { font-size:20px; font-weight:400; margin:0; flex:1; }
header select { max-width:45vw; }
nav { display:flex; gap:4px; padding:8px 16px 0; overflow-x:auto; border-bottom:1px solid var(--divider-color); }
.tab { background:none; border:none; border-bottom:2px solid transparent; color:var(--secondary-text-color); padding:10px 12px; font-size:14px; cursor:pointer; white-space:nowrap; }
.tab.on { color:var(--primary-color); border-bottom-color:var(--primary-color); }
main { padding:16px; max-width:960px; margin:0 auto; box-sizing:border-box; }
.card { background:var(--card-background-color); border-radius:var(--ha-card-border-radius, 12px); box-shadow:var(--ha-card-box-shadow, none); border:1px solid var(--divider-color); padding:12px 16px; margin-bottom:12px; }
.row { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
.meta { flex:1 1 240px; display:grid; gap:2px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; }
.spark { width:120px; height:28px; flex:0 0 auto; }
.spark polyline { fill:none; stroke:var(--primary-color); stroke-width:1.5; }
.muted { color:var(--secondary-text-color); font-size:13px; }
.badge { font-size:11px; padding:2px 6px; border-radius:8px; background:var(--primary-color); color:var(--text-primary-color, #fff); margin-left:6px; }
.badge.open { background:var(--secondary-background-color); color:var(--primary-text-color); }
button { background:var(--primary-color); color:var(--text-primary-color, #fff); border:none; border-radius:6px; padding:8px 14px; font-size:14px; cursor:pointer; }
button:disabled { opacity:.5; cursor:default; }
select, input, textarea { background:var(--input-fill-color, var(--secondary-background-color)); color:var(--primary-text-color); border:1px solid var(--divider-color); border-radius:6px; padding:6px 8px; font:inherit; max-width:100%; box-sizing:border-box; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--divider-color); }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.diag { font-weight:700; }
.scroll { overflow-x:auto; }
.form { display:grid; gap:12px; }
.form label, .file { display:grid; gap:4px; font-size:14px; }
.file { margin-top:12px; }
.group .ghead { font-size:15px; }
.grow { display:flex; align-items:center; gap:10px; padding:6px 0; border-bottom:1px solid var(--divider-color); font-size:14px; flex-wrap:wrap; }
.grow .spark { margin-left:auto; }
.gtime { font-variant-numeric:tabular-nums; min-width:48px; font-weight:500; }
.group button { margin-top:10px; }
.bulk { display:flex; align-items:center; gap:10px; flex-wrap:wrap; position:sticky; top:0; z-index:1; }
h3 { font-size:15px; font-weight:500; margin:20px 0 8px; }
fieldset { border:1px solid var(--divider-color); border-radius:8px; padding:8px 12px; display:grid; gap:8px; }
legend { padding:0 4px; font-size:14px; }
.arow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:14px; }
.arow > span { flex:1 1 180px; overflow:hidden; text-overflow:ellipsis; }
label.inline { display:flex; align-items:center; gap:6px; }
label.inline input { width:90px; }
input[type=checkbox] { width:18px; height:18px; accent-color:var(--primary-color); }
.msg { margin:12px 16px 0; padding:8px 12px; border-radius:6px; background:var(--secondary-background-color); }
`;

customElements.define("water-insights-panel", WaterInsightsPanel);
