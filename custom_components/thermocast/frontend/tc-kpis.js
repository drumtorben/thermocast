// KPI tab: daily burner starts, kWh per degree day, comfort – and before/after control was enabled.
import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtDay, localDate } from "./i18n.js";
import "./tc-chart.js";

const RANGES = [14, 30, 90];
const LS_KEY = "thermocast.kpiDays";

function loadDays() {
  try {
    const v = Number(localStorage.getItem(LS_KEY));
    return RANGES.includes(v) ? v : 30;
  } catch {
    return 30;
  }
}

class TcKpis extends LitElement {
  static properties = {
    request: { attribute: false },
    lang: {},
    tz: {},
    _days: { state: true },
    _data: { state: true },
    _error: { state: true },
  };

  constructor() {
    super();
    this._days = loadDays();
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    this._data = null;
    try {
      this._data = await this.request({ type: "thermocast/kpis", days: this._days });
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
  }

  _setDays(d) {
    this._days = d;
    try {
      localStorage.setItem(LS_KEY, String(d));
    } catch {
      /* ignore */
    }
    this._load();
  }

  _missing(m) {
    const L = this.lang;
    if (m === "recorder") return t(L, "k_missing_recorder");
    const [what, reason, entity] = m.split(":");
    const name = t(L, "w_" + what);
    return reason === "not_configured"
      ? t(L, "k_missing_not_configured", { what: name })
      : t(L, "k_missing_no_statistics", { what: name, e: entity });
  }

  _tile(label, key, fmt) {
    const s = this._data.summary;
    const L = this.lang;
    const val = (agg) => (agg && agg[key] !== null && agg[key] !== undefined ? fmt(agg[key]) : "–");
    return html`<div class="tile">
      <div class="label">${label}</div>
      <div class="value">${val(s.all)}</div>
      ${s.before || s.after
        ? html`<div class="cmp">
            <span>${t(L, "k_before")}: <b>${val(s.before)}</b></span>
            <span>${t(L, "k_after")}: <b>${val(s.after)}</b></span>
          </div>`
        : nothing}
    </div>`;
  }

  render() {
    const L = this.lang;
    const d = this._data;
    const ranges = html`<div class="ranges">
      ${RANGES.map(
        (r) => html`<button class=${r === this._days ? "on" : ""} @click=${() => this._setDays(r)}>${t(L, "k_days", { d: r })}</button>`,
      )}
    </div>`;
    if (this._error) return html`${ranges}<p class="card">${t(L, "error")}: ${this._error}</p>`;
    if (!d) return html`${ranges}<p class="card">${t(L, "loading")}</p>`;
    const labels = d.days.map((r) => r.date);
    const tz = this.tz || d.tz;
    const every = Math.max(1, Math.ceil(labels.length / 10));
    const tick = (lab, i) => (i % every === 0 ? fmtDay(lab + "T12:00:00Z", L, tz) : null);
    const tip = (lab) => fmtDay(lab + "T12:00:00Z", L, tz);
    const sinceIdx = d.control_since ? labels.indexOf(localDate(d.control_since, tz)) : -1;
    const marks = sinceIdx >= 0 ? [{ index: sinceIdx, label: t(L, "k_since") }] : [];
    const col = (key) => d.days.map((r) => r[key]);
    const f1 = (v) => fmtNum(v, L, 1);
    const any = (key) => d.days.some((r) => r[key] !== null);
    return html`
      ${ranges}
      ${d.missing.length ? html`<div class="card notes">${d.missing.map((m) => html`<div>${this._missing(m)}</div>`)}</div>` : nothing}
      <div class="tiles">
        ${this._tile(t(L, "k_starts"), "starts_per_day", f1)} ${this._tile(t(L, "k_kwh_hdd"), "kwh_per_hdd", (v) => fmtNum(v, L, 2))}
        ${this._tile(t(L, "k_min"), "min_leading", (v) => fmtNum(v, L, 1) + " °C")}
        ${this._tile(t(L, "k_below"), "below_comfort_h_per_day", f1)}
      </div>
      ${any("starts")
        ? html`<div class="card">
            <div class="sub">${t(L, "k_chart_starts")}</div>
            <tc-chart .labels=${labels} .series=${[{ name: t(L, "k_starts_short"), values: col("starts"), color: "#D55E00", type: "bar" }]}
              .marks=${marks} .xTick=${tick} .xTip=${tip} .fmt=${(v) => fmtNum(v, L, 0)}></tc-chart>
          </div>`
        : nothing}
      ${any("energy_kwh") || any("kwh_per_hdd") || any("t_out_mean")
        ? html`<div class="card">
            <div class="sub">${t(L, "k_chart_energy")}</div>
            <tc-chart .labels=${labels}
              .series=${[
                // energy per day always shows (also on mild days and for today so far); per degree day only
                // where it is defined (heating degree days > 0)
                { name: t(L, "k_energy_day"), values: col("energy_kwh"), color: "#56B4E9", type: "bar" },
                { name: t(L, "k_energy"), values: col("kwh_per_hdd"), color: "#0072B2", type: "dots" },
                { name: t(L, "k_tout"), values: col("t_out_mean"), color: "#CC79A7", axis: "r" },
              ]}
              .marks=${marks} .xTick=${tick} .xTip=${tip} .fmt=${(v) => fmtNum(v, L, 1)}></tc-chart>
          </div>`
        : nothing}
      ${any("min_leading")
        ? html`<div class="card">
            <div class="sub">${t(L, "k_chart_temp")}</div>
            <tc-chart .labels=${labels} .series=${[{ name: t(L, "k_min_short"), values: col("min_leading"), color: "#009E73" }]}
              .marks=${marks} .xTick=${tick} .xTip=${tip} .fmt=${(v) => fmtNum(v, L, 1)}></tc-chart>
          </div>`
        : nothing}
      ${!any("starts") && !any("energy_kwh") && !any("kwh_per_hdd") && !any("min_leading") ? html`<p class="card">${t(L, "k_no_data")}</p>` : nothing}
      <p class="muted small">${t(L, "k_hdd_base", { b: fmtNum(d.hdd_base, L, 0) })}</p>
    `;
  }

  static styles = css`
    :host {
      display: grid;
      gap: 16px;
    }
    .ranges {
      display: flex;
      gap: 8px;
    }
    button {
      font: inherit;
      font-size: 13px;
      padding: 6px 14px;
      border-radius: 16px;
      border: 1px solid var(--divider-color, #ccc);
      background: var(--card-background-color, #fff);
      color: var(--primary-text-color, #212121);
      cursor: pointer;
    }
    button.on {
      background: var(--primary-color, #03a9f4);
      border-color: var(--primary-color, #03a9f4);
      color: var(--text-primary-color, #fff);
    }
    .card {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 12px 16px;
      margin: 0;
      min-width: 0;
    }
    .notes {
      font-size: 13px;
      color: var(--secondary-text-color, #727272);
      line-height: 1.6;
    }
    .tiles {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    @media (max-width: 800px) {
      .tiles {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    .tile {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 12px 16px;
    }
    .label {
      font-size: 13px;
      color: var(--secondary-text-color, #727272);
    }
    .value {
      font-size: 26px;
      font-weight: 600;
      margin: 2px 0;
    }
    .cmp {
      display: flex;
      flex-wrap: wrap;
      gap: 2px 12px;
      font-size: 12px;
      color: var(--secondary-text-color, #727272);
    }
    .sub {
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 4px;
    }
    .muted {
      color: var(--secondary-text-color, #727272);
    }
    .small {
      font-size: 12px;
      margin: 0;
    }
  `;
}

// the panel module can load twice (new ?v= after an update without page reload)
if (!customElements.get("tc-kpis")) customElements.define("tc-kpis", TcKpis);
