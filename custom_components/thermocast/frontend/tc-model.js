// Model tab: quality per zone (hindcast vs. measurement, forecast horizons), learned parameters, export.
import { LitElement, html, svg, css, nothing } from "./lit.js";
import { t, fmtNum, fmtSigned, fmtTime, fmtDay, localHour } from "./i18n.js";
import "./tc-chart.js";

const C = { measured: "var(--primary-text-color, #212121)", hindcast: "#0072B2", forecast: "#E69F00", error: "#CC79A7" };

class TcModel extends LitElement {
  static properties = {
    request: { attribute: false }, // (msg) => Promise
    lang: {},
    tz: {},
    _data: { state: true },
    _error: { state: true },
    _exporting: { state: true },
  };

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._data = await this.request({ type: "thermocast/model" });
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
  }

  async _export() {
    this._exporting = true;
    try {
      const data = await this.request({ type: "thermocast/export" });
      const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `thermocast-export-${new Date().toISOString().slice(0, 16).replace(":", "")}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._exporting = false;
    }
  }

  _paramLabel(p) {
    const L = this.lang;
    if (p.key === "tau") return t(L, "p_tau");
    if (p.kind === "sun") return t(L, "p_sun", { label: p.label });
    if (p.kind === "heat") return t(L, "p_heat");
    if (p.kind === "base") return t(L, "p_base");
    return t(L, `p_${p.kind}`, { label: p.label ?? p.key });
  }

  _spark(history) {
    const vals = history.map((h) => h[1]).filter((v) => v !== null);
    if (vals.length < 2) return nothing;
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const w = 90;
    const h = 22;
    const pts = history
      .map((hh, i) => (hh[1] === null ? null : [(i / (history.length - 1)) * w, h - 2 - ((hh[1] - lo) / (hi - lo || 1)) * (h - 4)]))
      .filter(Boolean)
      .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" ");
    return html`<svg width=${w} height=${h}><polyline points=${pts} fill="none" stroke="#0072B2" stroke-width="1.5"></polyline></svg>`;
  }

  // bar per hour of delay: when an input (heating, sun on a surface) shows up in the room
  _lagBars(lags, color, max) {
    const w = 26;
    return html`<svg width=${lags.length * w + 4} height="58">
      ${lags.map((l, i) => {
        const hgt = (Math.max(0, l.value) / max) * 36;
        return svg`<rect x=${i * w + 4} y=${42 - hgt} width=${w - 6} height=${hgt} fill=${color} opacity="0.8">
            <title>${l.lag} h: ${fmtNum(l.value, this.lang, 3)}</title></rect>
          <text x=${i * w + 4 + (w - 6) / 2} y="54" text-anchor="middle">${l.lag} h</text>`;
      })}
    </svg>`;
  }

  _lags(z) {
    const L = this.lang;
    const heatMax = Math.max(1e-9, ...z.heat_lags.map((l) => Math.abs(l.value)));
    const sun = z.sun_lags || [];
    const sunMax = Math.max(1e-9, ...sun.flatMap((p) => p.lags.map((l) => Math.abs(l.value))));
    return html`<div class="lags">
      <div class="sub">${t(L, z.heat_type === "fbh" ? "m_lags_fbh" : "m_lags")}</div>
      ${this._lagBars(z.heat_lags, "#D55E00", heatMax)}
      ${sun.length
        ? html`<div class="sub">${t(L, "m_sun_lags")}</div>
            <div class="sunlags">
              ${sun.map(
                (p) => html`<div><div class="muted small">${p.label}</div>${this._lagBars(p.lags, "#E69F00", sunMax)}</div>`,
              )}
            </div>`
        : nothing}
    </div>`;
  }

  // outdoor sensor offset per local hour (bars around a zero line) and the weather models' disagreement
  _weather(w) {
    const L = this.lang;
    if (!w) return nothing;
    const prof = w.offset_by_hour;
    const max = Math.max(0.5, ...(prof || []).map((v) => Math.abs(v ?? 0)));
    const bw = 11;
    const mid = 30;
    const bars = prof
      ? html`<svg width=${24 * bw + 4} height="76">
          <line x1="2" x2=${24 * bw + 2} y1=${mid} y2=${mid} stroke="var(--divider-color, #ccc)"></line>
          ${prof.map((v, h) => {
            const hgt = (Math.abs(v ?? 0) / max) * 26;
            return svg`<rect x=${h * bw + 3} y=${(v ?? 0) >= 0 ? mid - hgt : mid} width=${bw - 3} height=${hgt}
                fill="#0072B2" opacity="0.8"><title>${h}:00 · ${fmtSigned(v, L, 1)} K</title></rect>
              ${h % 6 === 0 ? svg`<text x=${h * bw + 3} y="72">${h}:00</text>` : nothing}`;
          })}
        </svg>`
      : nothing;
    return html`<div class="card">
      <div class="head">
        <b>${t(L, "w_title")}</b>
        ${w.offset_mean !== null
          ? html`<span class="chip">${t(L, "w_offset", { k: fmtSigned(w.offset_mean, L, 1), days: fmtNum(w.days, L, 0) })}</span>`
          : html`<span class="chip">${t(L, "w_offset_none")}</span>`}
        ${w.spread_mean !== null
          ? html`<span class="chip">${t(L, "w_spread", { mean: fmtNum(w.spread_mean, L, 1), max: fmtNum(w.spread_max, L, 1) })}</span>`
          : nothing}
        ${w.sd_mean !== null ? html`<span class="chip">${t(L, "w_sd", { sd: fmtNum(w.sd_mean, L, 1) })}</span>` : nothing}
      </div>
      ${prof ? html`<div class="sub">${t(L, "w_by_hour")}</div>${bars}` : nothing}
      <div class="muted small">${t(L, "w_explain")}</div>
    </div>`;
  }

  _zone(z) {
    const L = this.lang;
    const m = z.metrics;
    const tick = (iso) => {
      const h = localHour(iso, this.tz);
      return h === 0 ? fmtDay(iso, L, this.tz) : null;
    };
    const tip = (iso) => `${fmtDay(iso, L, this.tz)} ${fmtTime(iso, L, this.tz)}`;
    const fmt2 = (v) => fmtNum(v, L, 2);
    return html`<div class="card">
      <div class="head">
        <b>${z.name}</b>
        <span class="muted">${t(L, z.leads ? "leads" : "follows")} · ${t(L, "ht_" + z.heat_type)}</span>
        <span class="chip">${t(L, "m_learned", { n: m.n_learned })}</span>
        ${m.one_step_mae !== null
          ? html`<span class="chip">${t(L, "m_one_step", { mae: fmtNum(m.one_step_mae, L, 3), bias: fmtSigned(m.one_step_bias, L, 3) })}</span>`
          : nothing}
        ${m.hindcast_mae !== null
          ? html`<span class="chip">${t(L, "m_hindcast", { mae: fmt2(m.hindcast_mae), bias: fmtSigned(m.hindcast_bias, L, 2) })}</span>`
          : nothing}
      </div>
      ${z.hours.length
        ? html`<div class="sub">${t(L, "m_chart")}</div>
            <tc-chart
              .labels=${z.hours}
              .series=${[
                { name: t(L, "s_measured"), values: z.measured, color: C.measured },
                { name: t(L, "s_hindcast"), values: z.hindcast, color: C.hindcast },
                { name: t(L, "s_forecast6"), values: z.forecast6, color: C.forecast, dash: "4 3" },
              ]}
              .xTick=${tick}
              .xTip=${tip}
              .fmt=${(v) => fmtNum(v, L, 1)}
              .height=${200}
            ></tc-chart>
            <tc-chart
              .labels=${z.hours}
              .series=${[{ name: t(L, "s_error"), values: z.error, color: C.error, type: "bar" }]}
              .xTick=${tick}
              .xTip=${tip}
              .fmt=${(v) => fmtSigned(v, L, 2)}
              .height=${90}
            ></tc-chart>`
        : html`<p class="muted">${t(L, "m_no_data")}</p>`}
      <div class="grid">
        <div>
          <div class="sub">${t(L, "m_horizon")}</div>
          <table>
            <tr><th>h</th><th>${t(L, "m_mae")}</th><th>${t(L, "m_bias")}</th><th>${t(L, "m_cov")}</th><th>${t(L, "m_factor")}</th><th>${t(L, "m_n")}</th></tr>
            ${Object.entries(m.horizons).map(
              ([k, h]) => html`<tr>
                <td>${k}</td><td>${fmt2(h.mae)}</td><td>${fmtSigned(h.bias, L, 2)}</td>
                <td>${h.coverage === null ? "–" : Math.round(h.coverage * 100) + " %"}</td>
                <td>${h.factor && h.factor > 1 ? "×" + fmtNum(h.factor, L, 2) : "–"}</td><td>${h.n}</td>
              </tr>`,
            )}
          </table>
          <div class="muted small">${t(L, "m_cov")}: ${t(L, "m_cov_hint")} · ${t(L, "m_factor_hint")}</div>
          ${this._lags(z)}
        </div>
        <div>
          <div class="sub">${t(L, "m_params")}</div>
          <table>
            <tr><th>${t(L, "m_param")}</th><th>${t(L, "m_value")}</th><th>${t(L, "m_trend")}</th></tr>
            ${z.params.map(
              (p) => html`<tr>
                <td class="left">${this._paramLabel(p)}</td>
                <td>${p.value === null ? "–" : fmtNum(p.value, L, Math.abs(p.value) >= 10 ? 0 : 3)}${p.std !== null && p.value !== null
                    ? html` <span class="muted">± ${fmtNum(p.std, L, Math.abs(p.value) >= 10 ? 0 : 3)}</span>`
                    : nothing} <span class="muted">${p.unit}</span></td>
                <td>${this._spark(p.history)}</td>
              </tr>`,
            )}
          </table>
        </div>
      </div>
    </div>`;
  }

  render() {
    const L = this.lang;
    return html`
      <div class="bar">
        <span class="muted explain">${t(L, "m_explain")}</span>
        <button @click=${() => this._export()} ?disabled=${this._exporting}>${t(L, this._exporting ? "exporting" : "export")}</button>
      </div>
      ${this._error ? html`<p class="card">${t(L, "error")}: ${this._error}</p>` : nothing}
      ${this._data
        ? html`${this._weather(this._data.weather)}${this._data.zones.map((z) => this._zone(z))}`
        : html`<p class="card">${t(L, "loading")}</p>`}
    `;
  }

  static styles = css`
    :host {
      display: grid;
      gap: 16px;
    }
    .bar {
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .explain {
      flex: 1;
      font-size: 13px;
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
      white-space: nowrap;
    }
    .card {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 12px 16px;
      margin: 0;
      min-width: 0;
    }
    .head {
      display: flex;
      flex-wrap: wrap;
      gap: 6px 10px;
      align-items: center;
      font-size: 15px;
      margin-bottom: 8px;
    }
    .chip {
      font-size: 12px;
      padding: 3px 10px;
      border-radius: 12px;
      background: var(--secondary-background-color, #eee);
    }
    .muted {
      color: var(--secondary-text-color, #727272);
    }
    .small {
      font-size: 12px;
      margin-top: 4px;
    }
    .sub {
      font-size: 13px;
      font-weight: 600;
      margin: 10px 0 4px;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
      gap: 20px;
    }
    @media (max-width: 800px) {
      .grid {
        grid-template-columns: minmax(0, 1fr);
      }
    }
    table {
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }
    th,
    td {
      text-align: right;
      padding: 3px 6px;
      border-bottom: 1px solid var(--divider-color, #e0e0e0);
      white-space: nowrap;
    }
    th {
      font-weight: 500;
      color: var(--secondary-text-color, #727272);
    }
    .sunlags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 18px;
    }
    td.left,
    th:first-child {
      text-align: left;
    }
    text {
      font: 11px Roboto, system-ui, sans-serif;
      fill: var(--secondary-text-color, #727272);
    }
  `;
}

// the panel module can load twice (new ?v= after an update without page reload)
if (!customElements.get("tc-model")) customElements.define("tc-model", TcModel);
