import { LitElement, html, svg, css, nothing } from "./lit.js";
import { t, fmtNum, fmtSigned, fmtTime, fmtDay, localDate, localHour } from "./i18n.js";

const GUTTER = 64; // px left of the time axis (lane labels, y ticks); keep in sync with .legend padding
const MIN_PPH = 10; // px per hour; below that the timeline scrolls horizontally
const TIP_W = 300;
// Okabe–Ito based, colour-blind safe
const C = {
  plan: "#0072B2",
  free: "#999999",
  comfort: "#009E73",
  heat: "#D55E00",
  outdoor: "#CC79A7",
  loss: "#56B4E9",
  neighbor: "#8C8C8C",
  gain: "#CC79A7",
  base: "#BDBDBD",
  cand: "#E69F00",
  event: "#D55E00",
  high: "#E69F00", // upper bound (charging limit)
  bt: "#7B3294", // thermostat target (Better Thermostat)
};
const SUN = ["#E69F00", "#F0E442", "#B8860B", "#FFB000"];
const LS_KEY = "thermocast.why";

function loadWhy() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveWhy(v) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(v));
  } catch {
    /* private mode: just don't remember */
  }
}

const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(v);

// contiguous runs of non-null values: [[[i, v], ...], ...]
function segments(values) {
  const out = [];
  let cur = [];
  values.forEach((v, i) => {
    if (!isNum(v)) {
      if (cur.length) out.push(cur);
      cur = [];
    } else cur.push([i, v]);
  });
  if (cur.length) out.push(cur);
  return out;
}

function runs(flags) {
  const out = [];
  let start = null;
  flags.forEach((f, i) => {
    if (f && start === null) start = i;
    if (!f && start !== null) {
      out.push([start, i]);
      start = null;
    }
  });
  if (start !== null) out.push([start, flags.length]);
  return out;
}

class TcTimeline extends LitElement {
  static properties = {
    view: { attribute: false },
    lang: {},
    tz: {},
    hoverIndex: { attribute: false },
    hoverCandidate: { attribute: false },
    _width: { state: true },
    _why: { state: true },
  };

  constructor() {
    super();
    this._width = 900;
    this._why = loadWhy();
    this._scrolled = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._ro = new ResizeObserver((entries) => {
      const w = entries[0].contentRect.width;
      if (w > 0 && Math.abs(w - this._width) > 2) this._width = w;
    });
    this._ro.observe(this);
  }

  disconnectedCallback() {
    this._ro?.disconnect();
    super.disconnectedCallback();
  }

  willUpdate(changed) {
    if (changed.has("view") || changed.has("tz")) this._dayCache = null;
  }

  updated() {
    if (this._scrolled || !this.view) return;
    const sc = this.renderRoot.querySelector(".scroll");
    if (sc && sc.scrollWidth > sc.clientWidth + 2) {
      sc.scrollLeft = Math.max(0, this._x(this.view.window.now_index) - sc.clientWidth / 2);
      this._scrolled = true;
    }
  }

  // ------------------------------------------------------------- geometry
  get _n() {
    return this.view.hours.length;
  }
  get _pph() {
    return Math.max(MIN_PPH, (this._width - GUTTER - 8) / this._n);
  }
  get _w() {
    return GUTTER + this._n * this._pph + 8;
  }
  _x(i) {
    return GUTTER + i * this._pph;
  }
  _nowPos() {
    const v = this.view;
    const h0 = new Date(v.hours[v.window.now_index]).getTime();
    const g = new Date(v.generated_at).getTime();
    return v.window.now_index + Math.min(1, Math.max(0, (g - h0) / 3600000));
  }
  _indexOfTime(iso) {
    const t0 = new Date(this.view.hours[0]).getTime();
    return (new Date(iso).getTime() - t0) / 3600000;
  }
  _scale(values, h, pad = 0.3, minRange = 1, top = 6) {
    const vals = values.filter(isNum);
    let lo = vals.length ? Math.min(...vals) : 0;
    let hi = vals.length ? Math.max(...vals) : 1;
    lo -= pad;
    hi += pad;
    if (hi - lo < minRange) {
      const mid = (hi + lo) / 2;
      lo = mid - minRange / 2;
      hi = mid + minRange / 2;
    }
    const bottom = h - 6;
    const y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
    return { y, lo, hi };
  }
  _path(points) {
    return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  }
  _candidate() {
    const i = this.hoverCandidate;
    return i !== null && i !== undefined ? this.view.candidates[i] ?? null : null;
  }

  // ------------------------------------------------------------- events
  _emit(i) {
    if (i === this.hoverIndex) return;
    this.dispatchEvent(new CustomEvent("tc-hover", { detail: i, bubbles: true, composed: true }));
  }
  _onMove(ev) {
    const rect = ev.currentTarget.getBoundingClientRect();
    const i = Math.floor((ev.clientX - rect.left - GUTTER) / this._pph);
    this._emit(i >= 0 && i < this._n ? i : null);
  }
  _toggle(id) {
    this._why = { ...this._why, [id]: !this._why[id] };
    saveWhy(this._why);
  }

  // ------------------------------------------------------------- shared frame
  _days() {
    if (this._dayCache) return this._dayCache;
    const out = [];
    let cur = null;
    this.view.hours.forEach((h, i) => {
      const d = localDate(h, this.tz);
      if (!cur || cur.date !== d) {
        cur = { date: d, start: i, end: i + 1, iso: h };
        out.push(cur);
      } else cur.end = i + 1;
    });
    this._dayCache = out;
    return out;
  }

  _frame(h, body, label) {
    const nowX = this._x(this._nowPos());
    const hi = this.hoverIndex;
    return html`<svg width=${this._w} height=${h} @mousemove=${(e) => this._onMove(e)} @click=${(e) => this._onMove(e)}>
      <rect class="past" x=${this._x(0)} y="0" width=${Math.max(0, nowX - this._x(0))} height=${h}></rect>
      ${this._days()
        .slice(1)
        .map((d) => svg`<line class="day" x1=${this._x(d.start)} x2=${this._x(d.start)} y1="0" y2=${h}></line>`)}
      ${body}
      <line class="now" x1=${nowX} x2=${nowX} y1="0" y2=${h}></line>
      ${isNum(hi)
        ? svg`<line class="hover" x1=${this._x(hi + 0.5)} x2=${this._x(hi + 0.5)} y1="0" y2=${h}></line>`
        : nothing}
      ${label ? svg`<text class="lane" x="4" y="14">${label}</text>` : nothing}
    </svg>`;
  }

  // ------------------------------------------------------------- lanes
  _dayHeader() {
    return html`<svg width=${this._w} height="20">
      ${this._days().map(
        (d) => svg`<text class="dayname" x=${this._x(d.start) + 4} y="14">${fmtDay(d.iso, this.lang, this.tz)}</text>`,
      )}
    </svg>`;
  }

  _weatherLane() {
    const v = this.view;
    const h = 80;
    const irrVals = v.weather.irr.flatMap((s) => s.values.filter(isNum));
    const irrMax = Math.max(400, ...irrVals);
    const base = h - 4;
    const irrY = (x) => base - (x / irrMax) * (h - 24);
    const areas = v.weather.irr.map((s, k) => {
      const pts = s.values.map((val, i) => [this._x(i + 0.5), irrY(isNum(val) ? val : 0)]);
      const poly = [[this._x(0.5), base], ...pts, [this._x(this._n - 0.5), base]];
      return svg`<polygon points=${this._path(poly)} fill=${SUN[k % SUN.length]} opacity="0.35"><title>${s.label}</title></polygon>`;
    });
    const sc = this._scale(v.weather.t_out, h, 1, 4, 24); // leave room for the lane label
    const measured = v.weather.t_out_measured;
    // measured part solid, forecast dashed; the dashed part starts at the last measured point
    const meas = v.weather.t_out.map((x, i) => (measured[i] ? x : null));
    const fc = v.weather.t_out.map((x, i) => (!measured[i] || measured[i + 1] === false ? x : null));
    const line = (vals, dash) =>
      segments(vals).map(
        (seg) =>
          svg`<polyline points=${this._path(seg.map(([i, x]) => [this._x(i + 0.5), sc.y(x)]))}
            fill="none" stroke=${C.outdoor} stroke-width="1.6" stroke-dasharray=${dash}></polyline>`,
      );
    const axis = svg`<text class="axis" x=${GUTTER - 4} y=${sc.y(sc.hi) + 10} text-anchor="end">${fmtNum(sc.hi, this.lang, 0)}°</text>
      <text class="axis" x=${GUTTER - 4} y=${sc.y(sc.lo)} text-anchor="end">${fmtNum(sc.lo, this.lang, 0)}°</text>`;
    return this._frame(h, svg`${areas}${line(meas, "")}${line(fc, "4 3")}${axis}`, t(this.lang, "weather"));
  }

  _heatingLane() {
    const v = this.view;
    const h = 40;
    const now = v.window.now_index;
    const bars = v.heating.actual.map((a, i) =>
      i <= now && isNum(a) && a > 0
        ? svg`<rect x=${this._x(i)} y=${20 - 14 * a} width=${this._pph} height=${14 * a} fill=${C.heat}></rect>`
        : nothing,
    );
    // hot water is not space heating: stacked on top of the heating bar, at least 3 px so short charges show
    const dhw = (v.heating.dhw || []).map((a, i) => {
      if (!(i <= now && isNum(a) && a > 0)) return nothing;
      const below = isNum(v.heating.actual[i]) ? 14 * v.heating.actual[i] : 0;
      const hd = Math.max(3, 14 * a);
      return svg`<rect x=${this._x(i)} y=${20 - below - hd} width=${this._pph} height=${hd} fill="url(#tc-dhw)"
            stroke=${C.loss} stroke-width="0.6"><title>${t(this.lang, "dhw")} ${Math.round(a * 100)} %</title></rect>`;
    });
    const defs = svg`<defs><pattern id="tc-dhw" width="5" height="5" patternUnits="userSpaceOnUse"
      patternTransform="rotate(45)"><rect width="2" height="5" fill=${C.loss}></rect></pattern></defs>`;
    const planned = v.heating.planned_blocks.map((b) => {
      const s = this._indexOfTime(b.start);
      const e = this._indexOfTime(b.end);
      return svg`<rect x=${this._x(s)} y="6" width=${(e - s) * this._pph} height="14" rx="2"
        fill="none" stroke=${C.heat} stroke-width="1.5" stroke-dasharray="3 2"></rect>`;
    });
    let cand = nothing;
    const c = this._candidate();
    if (c && c.start) {
      const s = this._indexOfTime(c.start);
      const e = this._indexOfTime(c.end);
      cand = svg`<rect x=${this._x(s)} y="4" width=${(e - s) * this._pph} height="18" rx="2" fill=${C.cand} opacity="0.45"></rect>`;
    }
    // details live in the hover tooltip (an SVG <title> never shows under the hover layer or on touch)
    const events = v.events.map((ev) => {
      const x = this._x(this._indexOfTime(ev.time));
      return svg`<g class="event"><path d="M${x},${h - 2} l-5,-9 h10 z" fill=${C.event}></path></g>`;
    });
    return this._frame(h, svg`${defs}${bars}${dhw}${planned}${cand}${events}`, t(this.lang, "heating"));
  }

  _zoneLane(z) {
    const L = this.lang;
    const h = 120;
    const c = this._candidate();
    const candTraj = c?.trajectories?.[z.id] ?? null;
    const upper = z.plan.mean.map((m, i) => (isNum(m) ? m + (z.plan.std[i] ?? 0) : null));
    const lower = z.plan.mean.map((m, i) => (isNum(m) ? m - (z.plan.std[i] ?? 0) : null));
    const high = z.comfort_high ?? [];
    const btTarget = z.bt_target ?? [];
    const sc = this._scale(
      [...z.measured, ...upper, ...lower, ...z.free.mean, ...z.comfort_low, ...(candTraj ?? []), ...(z.forecast6 ?? []),
        ...high, ...btTarget],
      h,
    );
    // step line (value holds for the whole hour)
    const steps = (vals, color, width, dash) =>
      segments(vals).map((seg) => {
        const pts = [];
        seg.forEach(([i, v]) => pts.push([this._x(i), sc.y(v)], [this._x(i + 1), sc.y(v)]));
        return svg`<polyline points=${this._path(pts)} fill="none" stroke=${color} stroke-width=${width}
          stroke-dasharray=${dash}></polyline>`;
      });
    const quiet = runs(z.quiet ?? []).map(
      ([s, e]) => svg`<rect x=${this._x(s)} y="0" width=${(e - s) * this._pph} height=${h} fill="url(#tc-quiet)"></rect>`,
    );
    const quietDefs = svg`<defs><pattern id="tc-quiet" width="6" height="6" patternUnits="userSpaceOnUse"
      patternTransform="rotate(-45)"><rect width="1" height="6" fill="var(--secondary-text-color, #727272)"
      opacity="0.25"></rect></pattern></defs>`;

    const comfort = runs(z.comfort_low.map(isNum)).map(([s, e]) => {
      const pts = [];
      for (let i = s; i < e; i++) pts.push([this._x(i), sc.y(z.comfort_low[i])], [this._x(i + 1), sc.y(z.comfort_low[i])]);
      return svg`<rect x=${this._x(s)} y="0" width=${(e - s) * this._pph} height=${h} fill=${C.comfort} opacity="0.07"></rect>
        <polyline points=${this._path(pts)} fill="none" stroke=${C.comfort} stroke-dasharray="4 3"></polyline>`;
    });
    const band = segments(z.plan.mean).map((seg) => {
      const up = seg.map(([i]) => [this._x(i), sc.y(upper[i])]);
      const lo = seg.map(([i]) => [this._x(i), sc.y(lower[i])]).reverse();
      return svg`<polygon points=${this._path([...up, ...lo])} fill=${C.plan} opacity="0.13"></polygon>`;
    });
    const line = (vals, color, width, dash, center = false) =>
      segments(vals).map(
        (seg) =>
          svg`<polyline points=${this._path(seg.map(([i, x]) => [this._x(center ? i + 0.5 : i), sc.y(x)]))}
            fill="none" stroke=${color} stroke-width=${width} stroke-dasharray=${dash}></polyline>`,
      );
    const axis = svg`<text class="axis" x=${GUTTER - 4} y=${sc.y(sc.hi) + 10} text-anchor="end">${fmtNum(sc.hi, L)}°</text>
      <text class="axis" x=${GUTTER - 4} y=${sc.y(sc.lo)} text-anchor="end">${fmtNum(sc.lo, L)}°</text>`;
    const body = svg`${quietDefs}${quiet}${comfort}${band}
      ${steps(high, C.high, 1.2, "6 3")}
      ${z.bt_control ? steps(btTarget, C.bt, 1.6, "") : nothing}
      ${line(z.free.mean, C.free, 1.4, "3 3")}
      ${line(z.measured, "var(--primary-text-color, #212121)", 1.8, "", true)}
      ${line(z.plan.mean, C.plan, 2, "")}
      ${z.forecast6 ? line(z.forecast6, C.cand, 1.4, "1.5 2.5") : nothing}
      ${candTraj ? line(candTraj, C.cand, 2.2, "6 3") : nothing}
      ${axis}`;
    const open = !!this._why[z.id];
    return html`
      <div class="zone-head" style="width:${Math.min(this._width, this._w)}px">
        <b>${z.name}</b>
        <span class="muted">${t(L, z.leads ? "leads" : "follows")} · ${t(L, "ht_" + z.heat_type)}</span>
        <button @click=${() => this._toggle(z.id)}>${t(L, "why")} ${open ? "▾" : "▸"}</button>
      </div>
      ${this._frame(h, body)} ${open ? this._whyLane(z) : nothing}
    `;
  }

  _groupColor(g, z) {
    if (g.kind === "sun") {
      const suns = z.groups.filter((x) => x.kind === "sun").map((x) => x.key);
      return SUN[suns.indexOf(g.key) % SUN.length];
    }
    return C[g.kind] ?? C.base;
  }

  _groupLabel(g) {
    return ["base", "loss", "heat"].includes(g.kind) ? t(this.lang, "g_" + g.kind) : g.label;
  }

  _whyLane(z) {
    const h = 70;
    const mid = h / 2;
    let maxAbs = 0.05;
    z.contrib.forEach((c) => {
      if (!c) return;
      let pos = 0;
      let neg = 0;
      for (const v of Object.values(c)) {
        if (v >= 0) pos += v;
        else neg -= v;
      }
      maxAbs = Math.max(maxAbs, pos, neg);
    });
    const k = (mid - 4) / maxAbs;
    const bars = z.contrib.map((c, i) => {
      if (!c) return nothing;
      let up = mid;
      let down = mid;
      return z.groups.map((g) => {
        const v = c[g.key] ?? 0;
        if (!v) return nothing;
        const hh = Math.abs(v) * k;
        let y;
        if (v > 0) {
          up -= hh;
          y = up;
        } else {
          y = down;
          down += hh;
        }
        return svg`<rect x=${this._x(i) + this._pph * 0.1} y=${y} width=${this._pph * 0.8} height=${hh}
          fill=${this._groupColor(g, z)}></rect>`;
      });
    });
    const zero = svg`<line x1=${GUTTER} x2=${this._w} y1=${mid} y2=${mid} stroke="var(--divider-color, #ccc)"></line>
      <text class="axis" x=${GUTTER - 4} y="12" text-anchor="end">+${fmtNum(maxAbs, this.lang, 2)}</text>
      <text class="axis" x=${GUTTER - 4} y=${h - 4} text-anchor="end">−${fmtNum(maxAbs, this.lang, 2)}</text>`;
    return html`${this._frame(h, svg`${zero}${bars}`, "K/h")}
      <div class="legend">
        ${z.groups.map((g) => html`<span><i style="background:${this._groupColor(g, z)}"></i>${this._groupLabel(g)}</span>`)}
      </div>`;
  }

  _axis() {
    const step = this._pph >= 14 ? 3 : 6;
    const labels = [];
    this.view.hours.forEach((iso, i) => {
      const hour = localHour(iso, this.tz);
      if (hour % step === 0)
        labels.push(
          svg`<text class="axis" x=${this._x(i)} y="12" text-anchor="middle">${String(hour).padStart(2, "0")}</text>`,
        );
    });
    return html`<svg width=${this._w} height="16">${labels}</svg>`;
  }

  // ------------------------------------------------------------- tooltip
  _tooltip() {
    const i = this.hoverIndex;
    if (!isNum(i) || !this.view || i >= this._n) return nothing;
    const v = this.view;
    const L = this.lang;
    const now = v.window.now_index;
    const irr = v.weather.irr.filter((s) => s.values[i]).map((s) => `${s.label} ${fmtNum(s.values[i], L, 0)} W/m²`);
    const zones = v.zones.map((z) => {
      const past = i < now;
      const val = past ? z.measured[i] : z.plan.mean[i];
      const std = past ? null : z.plan.std[i];
      const c = z.contrib[i];
      const parts = c
        ? z.groups
            .map((g) => [g, c[g.key] ?? 0])
            .filter(([, x]) => Math.abs(x) >= 0.005)
            .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
            .slice(0, 5)
        : [];
      const net = c ? Object.values(c).reduce((a, b) => a + b, 0) : null;
      const bt = z.bt_control && isNum((z.bt_target || [])[i]) ? z.bt_target[i] : null;
      return html`<div class="tz">
        <b>${z.name}</b> ${fmtNum(val, L)} °C${std ? html` (±${fmtNum(std, L)})` : nothing}
        <span class="muted">${t(L, past ? "measured" : "plan")}</span>
        ${bt !== null ? html`<span class="muted">· ${t(L, "bt_target")} ${fmtNum(bt, L)} °C</span>` : nothing}
        ${(z.quiet || [])[i] ? html`<span class="muted">· ${t(L, "quiet")}</span>` : nothing}
        ${parts.length
          ? html`<div class="parts">
              ${parts.map(([g, x]) => html`<span>${this._groupLabel(g)} ${fmtSigned(x, L, 2)}</span>`)}
              <span><b>${t(L, "net")} ${fmtSigned(net, L, 2)} K/h</b></span>
            </div>`
          : nothing}
      </div>`;
    });
    const heat = i < now && isNum(v.heating.actual[i]) ? v.heating.actual[i] : null;
    const dhw = i < now && isNum((v.heating.dhw || [])[i]) ? v.heating.dhw[i] : null;
    const heatLine = heat || dhw
      ? html`<div>${t(L, "heating")} ${Math.round((heat || 0) * 60)} min${
          dhw ? html` · ${t(L, "dhw")} ${Math.round(dhw * 60)} min` : nothing
        }</div>`
      : nothing;
    const events = v.events
      .filter((ev) => Math.floor(this._indexOfTime(ev.time)) === i)
      .map((ev) => {
        const zone = ev.zone ? v.zones.find((z) => z.id === ev.zone)?.name ?? ev.zone : "";
        return html`<div class="ev">▼ ${fmtTime(ev.time, L, this.tz)} ${t(L, "ev_" + ev.type)}${
          zone ? " · " + zone : ""
        }${ev.detail ? " · " + ev.detail : ""}</div>`;
      });
    const x = this._x(i + 0.5);
    const left = x + 12 + TIP_W > this._w ? x - 12 - TIP_W : x + 12;
    return html`<div class="tip" style="left:${Math.max(4, left)}px">
      <div class="tt">${fmtDay(v.hours[i], L, this.tz)} ${fmtTime(v.hours[i], L, this.tz)}</div>
      <div>${t(L, "t_out")} ${fmtNum(v.weather.t_out[i], L)} °C${irr.length ? " · " + irr.join(" · ") : ""}</div>
      ${heatLine} ${events}
      ${zones}
    </div>`;
  }

  render() {
    if (!this.view) return nothing;
    return html`<div class="card">
      <div class="scroll">
        <div class="inner" style="width:${this._w}px" @mouseleave=${() => this._emit(null)}>
          ${this._dayHeader()} ${this._weatherLane()} ${this._heatingLane()}
          ${this.view.zones.map((z) => this._zoneLane(z))} ${this._axis()} ${this._tooltip()}
        </div>
      </div>
      <div class="legend">
        <span><i style="background:var(--primary-text-color,#212121)"></i>${t(this.lang, "measured")}</span>
        <span><i style="background:${C.plan}"></i>${t(this.lang, "plan")} ±σ</span>
        <span><i style="background:${C.free}"></i>${t(this.lang, "free")}</span>
        <span><i style="background:${C.cand}"></i>${t(this.lang, "s_forecast6")}</span>
        ${(this.view.heating.dhw || []).some((a) => a > 0)
          ? html`<span><i style="background:${C.loss}"></i>${t(this.lang, "dhw")}</span>`
          : nothing}
        <span><i style="background:${C.comfort}"></i>${t(this.lang, "comfort")}</span>
        <span><i style="background:${C.high}"></i>${t(this.lang, "upper_bound")}</span>
        ${this.view.zones.some((z) => z.bt_control)
          ? html`<span><i style="background:${C.bt}"></i>${t(this.lang, "bt_target")}</span>`
          : nothing}
        ${this.view.zones.some((z) => (z.quiet || []).some(Boolean))
          ? html`<span><i style="background:var(--divider-color,#ccc)"></i>${t(this.lang, "quiet")}</span>`
          : nothing}
        <span><i style="background:${C.heat}"></i>${t(this.lang, "heating")}</span>
        <span><i style="background:${C.outdoor}"></i>${t(this.lang, "t_out")}</span>
      </div>
    </div>`;
  }

  static styles = css`
    :host {
      display: block;
    }
    .card {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 8px 0 12px;
    }
    .scroll {
      overflow-x: auto;
    }
    .inner {
      position: relative;
    }
    svg {
      display: block;
    }
    .past {
      fill: var(--primary-text-color, #000);
      opacity: 0.04;
    }
    .day {
      stroke: var(--primary-text-color, #000);
      stroke-opacity: 0.35;
      stroke-width: 0.7;
    }
    .now {
      stroke: #e53935;
      stroke-width: 2;
    }
    .hover {
      stroke: var(--primary-text-color, #000);
      stroke-opacity: 0.4;
      stroke-dasharray: 2 2;
    }
    text {
      font: 11px Roboto, system-ui, sans-serif;
      fill: var(--secondary-text-color, #727272);
    }
    text.lane {
      font-weight: 600;
    }
    text.dayname {
      font-weight: 600;
      fill: var(--primary-text-color, #212121);
    }
    .ev {
      color: var(--secondary-text-color, #727272);
    }
    .zone-head {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 8px 0 8px;
      font-size: 14px;
      position: sticky;
      left: 0;
      box-sizing: border-box;
    }
    .muted {
      color: var(--secondary-text-color, #727272);
      font-size: 12px;
    }
    button {
      margin-left: auto;
      font: inherit;
      font-size: 12px;
      background: none;
      border: 1px solid var(--divider-color, #ccc);
      border-radius: 12px;
      padding: 2px 10px;
      color: var(--primary-text-color, #212121);
      cursor: pointer;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      padding: 4px 8px 0 64px;
      font-size: 12px;
      color: var(--secondary-text-color, #727272);
    }
    .legend i {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 2px;
      margin-right: 4px;
      vertical-align: -1px;
    }
    .tip {
      position: absolute;
      top: 24px;
      width: 300px;
      box-sizing: border-box;
      z-index: 2;
      pointer-events: none;
      font-size: 12px;
      line-height: 1.5;
      background: var(--card-background-color, #fff);
      color: var(--primary-text-color, #212121);
      border: 1px solid var(--divider-color, #ccc);
      border-radius: 8px;
      padding: 8px 10px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    }
    .tt {
      font-weight: 600;
    }
    .tz {
      margin-top: 4px;
    }
    .parts {
      display: flex;
      flex-wrap: wrap;
      gap: 2px 10px;
      color: var(--secondary-text-color, #727272);
    }
  `;
}

// the panel module can load twice (new ?v= after an update without page reload)
if (!customElements.get("tc-timeline")) customElements.define("tc-timeline", TcTimeline);
