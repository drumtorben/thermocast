// KPI tab: daily burner starts, kWh per degree day, comfort – and before/after control was enabled.
import { LitElement, html, svg, css, nothing } from "./lit.js";
import { t, fmtNum, fmtDay, localDate } from "./i18n.js";
import "./tc-chart.js";

const RANGES = [14, 30, 90];
const LS_KEY = "thermocast.kpiDays";

const C_BEFORE = "#999999";
const C_AFTER = "#0072B2";

function ticks(lo, hi, count = 4) {
  const raw = (hi - lo || 1) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

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
    _sw: { state: true }, // scatter width (px) – drawn in pixels like tc-chart, so the text keeps its size
  };

  constructor() {
    super();
    this._days = loadDays();
    this._sw = 600;
  }

  connectedCallback() {
    super.connectedCallback();
    this._ro = new ResizeObserver((e) => {
      const w = e[0].contentRect.width - 32; // card padding
      if (w > 0 && Math.abs(w - this._sw) > 2) this._sw = w;
    });
    this._ro.observe(this);
    this._load();
  }

  disconnectedCallback() {
    this._ro?.disconnect();
    super.disconnectedCallback();
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

  // kWh/day over degree days, one point per complete day (before/since control) with the fitted lines:
  // the slope stays comparable between mild and cold weeks, unlike the ratio kWh/HDD
  _scatter() {
    const L = this.lang;
    const d = this._data;
    const tz = this.tz || d.tz;
    const since = d.control_since ? localDate(d.control_since, tz) : null;
    const pts = d.days
      .filter((r) => r.complete && r.hdd !== null && r.energy_kwh !== null)
      .map((r) => ({ x: r.hdd, y: r.energy_kwh, after: since !== null && r.date >= since, date: r.date }));
    if (pts.filter((p) => p.x >= 1).length < 2) return nothing;
    const s = d.summary;
    const fits = since
      ? [
          { fit: s.before?.fit, color: C_BEFORE, label: t(L, "k_before"), after: false },
          { fit: s.after?.fit, color: C_AFTER, label: t(L, "k_after"), after: true },
        ]
      : [{ fit: s.all?.fit, color: C_AFTER, label: t(L, "k_all"), after: false }];
    const W = this._sw, H = 220, ml = 40, mr = 12, mt = 10, mb = 30;
    const xMax = Math.max(...pts.map((p) => p.x), 1) * 1.05;
    const yMax = Math.max(...pts.map((p) => p.y), 1) * 1.08;
    const X = (v) => ml + (v / xMax) * (W - ml - mr);
    const Y = (v) => mt + (1 - Math.max(0, v) / yMax) * (H - mt - mb);
    const line = (f, color, sel) => {
      if (!f) return nothing;
      const xs = pts.filter((p) => p.x >= 1 && (!since || p.after === sel)).map((p) => p.x);
      if (!xs.length) return nothing;
      const [a, b] = [Math.min(...xs), Math.max(...xs)];
      return svg`<line x1=${X(a)} y1=${Y(f.intercept + f.slope * a)} x2=${X(b)} y2=${Y(f.intercept + f.slope * b)}
        stroke=${color} stroke-width="2" />`;
    };
    const fmtFit = (f) =>
      f
        ? t(L, "k_fit", {
            slope: fmtNum(f.slope, L, 1),
            base: fmtNum(f.intercept, L, 0),
            r2: f.r2 === null ? "–" : fmtNum(f.r2, L, 2),
            n: f.n,
          })
        : t(L, "k_fit_none");
    return html`<div class="card">
      <div class="sub">${t(L, "k_chart_fit")}</div>
      <svg width=${W} height=${H} class="scatter" role="img">
        ${ticks(0, yMax).map(
          (v) => svg`<line x1=${ml} x2=${W - mr} y1=${Y(v)} y2=${Y(v)} class="grid" />
            <text x=${ml - 6} y=${Y(v) + 4} text-anchor="end" class="ax">${fmtNum(v, L, 0)}</text>`,
        )}
        ${ticks(0, xMax).map(
          (v) => svg`<text x=${X(v)} y=${H - mb + 16} text-anchor="middle" class="ax">${fmtNum(v, L, 0)}</text>`,
        )}
        <text x=${W - mr} y=${H - 2} text-anchor="end" class="ax">${t(L, "k_axis_hdd")}</text>
        <text x=${ml - 34} y=${mt + 4} class="ax">kWh</text>
        ${fits.map((f) => line(f.fit, f.color, f.after))}
        ${pts.map(
          (p) => svg`<circle cx=${X(p.x)} cy=${Y(p.y)} r="4" fill=${p.after || !since ? C_AFTER : C_BEFORE}
            fill-opacity=${p.x >= 1 ? 0.9 : 0.3}><title>${fmtDay(p.date + "T12:00:00Z", L, tz)}: ${fmtNum(p.y, L, 1)} kWh · ${fmtNum(p.x, L, 1)} Kd</title></circle>`,
        )}
      </svg>
      <div class="legend">
        ${fits.map((f) => html`<span><i style="background:${f.color}"></i>${f.label}: ${fmtFit(f.fit)}</span>`)}
      </div>
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
        ${this._tile(t(L, "k_starts"), "starts_per_day", f1)} ${this._tile(t(L, "k_kwh_hdd"), "fit", (v) => fmtNum(v.slope, L, 1))}
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
      ${this._scatter()}
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
    .scatter {
      display: block;
    }
    .scatter .grid {
      stroke: var(--divider-color, #e0e0e0);
    }
    .scatter .ax {
      font-size: 11px;
      fill: var(--secondary-text-color, #727272);
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 16px;
      font-size: 12px;
      color: var(--secondary-text-color, #727272);
    }
    .legend i {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-right: 6px;
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
