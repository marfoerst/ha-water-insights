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
      if (!this._busy && this._tab !== "settings") this._refresh();
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
        const [open, auto] = await Promise.all(
          ["open", "auto"].map((status) =>
            this._hass.callWS({ type: "water_insights/events", entry_id: this._entryId, status, limit: 100 })
          )
        );
        this._events = [...open.events, ...auto.events].sort((a, b) => b.start - a.start);
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

  _classify() {
    const t = this.t;
    if (!this._events.length) return `<p class="muted">${t.none}</p>`;
    const cats = [...this.entry.categories, "mixed"];
    const fmt = new Intl.DateTimeFormat(this._hass.language, { dateStyle: "short", timeStyle: "short" });
    return this._events
      .map((e) => {
        const options = [`<option value="">${t.choose}</option>`]
          .concat(cats.map((c) => `<option value="${esc(c)}" ${e.label === c ? "selected" : ""}>${esc(this.label(c))}</option>`))
          .join("");
        const sugg = e.prediction
          ? `${t.suggestion}: <b>${esc(this.label(e.prediction))}</b> · ${t.confidence} ${pct(e.confidence)}`
          : "";
        const badge = e.status === "auto" ? `<span class="badge">${t.auto}</span>` : `<span class="badge open">${t.open}</span>`;
        const canConfirm = e.label || e.prediction;
        return `<div class="card row">
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
      <label>${t.startFlow}<input id="s_start_flow" type="number" step="0.1" min="0.1" value="${s.start_flow}"></label>
      <label>${t.endQuiet}<input id="s_end_quiet_s" type="number" step="1" min="5" value="${s.end_quiet_s}"></label>
      <label>${t.minVolume}<input id="s_min_volume_l" type="number" step="0.1" min="0.1" value="${s.min_volume_l}"></label>
      <label>${t.confThreshold}<input id="s_conf_threshold" type="number" step="0.01" min="0.5" max="0.99" value="${s.conf_threshold}"></label>
      <label>${t.helpers}<textarea id="s_helper_entities" rows="3">${esc((s.helper_entities || []).join("\n"))}</textarea></label>
      <button id="save" ${this._busy ? "disabled" : ""}>${t.save}</button>
    </div>
    <div class="card">
      <button id="export">${t.export}</button>
      <label class="file">${t.import}<input id="import" type="file" accept=".json,.jsonl,application/json"></label>
    </div>`;
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
    root.querySelectorAll("[data-confirm]").forEach((b) =>
      b.addEventListener("click", () => this._ws({ type: "water_insights/confirm", event_id: b.dataset.confirm }))
    );
    root.getElementById("train")?.addEventListener("click", () => this._ws({ type: "water_insights/train" }));
    root.getElementById("save")?.addEventListener("click", async () => {
      const v = (id) => root.getElementById(id).value;
      const lines = (id) => v(id).split("\n").map((x) => x.trim()).filter(Boolean);
      const res = await this._ws({
        type: "water_insights/update_settings",
        settings: {
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
.msg { margin:12px 16px 0; padding:8px 12px; border-radius:6px; background:var(--secondary-background-color); }
`;

customElements.define("water-insights-panel", WaterInsightsPanel);
