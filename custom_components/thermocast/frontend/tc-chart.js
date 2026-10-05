// Small SVG chart: lines/bars on a left and optional right axis, vertical marks, reference lines, tooltip.
import { LitElement, html, svg, css, nothing } from "./lit.js";

const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(v);

function niceTicks(lo, hi, count = 4) {
  const span = hi - lo || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

function extent(series, bars) {
  const vals = series.flatMap((s) => s.values.filter(isNum));
  if (!vals.length) return [0, 1];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (bars) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (hi - lo < 1e-9) {
    lo -= 0.5;
    hi += 0.5;
  }
  const pad = (hi - lo) * 0.08;
  return [bars && lo === 0 ? 0 : lo - pad, hi + pad];
}

class TcChart extends LitElement {
  static properties = {
    labels: { attribute: false }, // x categories (one per point)
    series: { attribute: false }, // [{name, values, color, type: line|bar|dots, dash, axis: l|r, width}]
    refs: { attribute: false }, // [{y, color, label}] on the left axis
    marks: { attribute: false }, // [{index, label, color}] vertical lines
    xTick: { attribute: false }, // (label, i) => string | null
    xTip: { attribute: false }, // (label, i) => string (tooltip title)
    fmt: { attribute: false }, // (value, series) => string
    height: { type: Number },
    _width: { state: true },
    _hover: { state: true },
  };

  constructor() {
    super();
    this.labels = [];
    this.series = [];
    this.refs = [];
    this.marks = [];
    this.height = 180;
    this._width = 600;
    this._hover = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._ro = new ResizeObserver((e) => {
      const w = e[0].contentRect.width;
      if (w > 0 && Math.abs(w - this._width) > 2) this._width = w;
    });
    this._ro.observe(this);
  }

  disconnectedCallback() {
    this._ro?.disconnect();
    super.disconnectedCallback();
  }

  render() {
    const n = this.labels.length;
    if (!n) return nothing;
    const right = this.series.some((s) => s.axis === "r");
    const L = 46;
    const R = right ? 46 : 10;
    const T = 8;
    const B = 22;
    const W = this._width;
    const H = this.height;
    const pw = Math.max(10, W - L - R);
    const ph = H - T - B;
    const step = pw / n;
    const x = (i) => L + (i + 0.5) * step;
    const axes = {};
    for (const side of ["l", "r"]) {
      const ss = this.series.filter((s) => (s.axis || "l") === side);
      if (!ss.length) continue;
      const vals = side === "l" ? [...ss, { values: (this.refs || []).map((r) => r.y) }] : ss;
      const [lo, hi] = extent(vals, ss.some((s) => s.type === "bar"));
      axes[side] = { lo, hi, y: (v) => T + ph - ((v - lo) / (hi - lo)) * ph, ticks: niceTicks(lo, hi) };
    }
    const bars = this.series.filter((s) => s.type === "bar");
    const bw = (step * 0.8) / Math.max(1, bars.length);
    const fmt = this.fmt || ((v) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)));
    const body = this.series.map((s) => {
      const a = axes[s.axis || "l"];
      if (s.type === "bar") {
        const k = bars.indexOf(s);
        const y0 = a.y(Math.max(a.lo, 0));
        return s.values.map((v, i) =>
          isNum(v)
            ? svg`<rect x=${L + i * step + step * 0.1 + k * bw} y=${Math.min(a.y(v), y0)} width=${bw * 0.92}
                height=${Math.abs(y0 - a.y(v))} fill=${s.color} opacity="0.85"></rect>`
            : nothing,
        );
      }
      if (s.type === "dots")
        return s.values.map((v, i) =>
          isNum(v) ? svg`<circle cx=${x(i)} cy=${a.y(v)} r="2.2" fill=${s.color}></circle>` : nothing,
        );
      const segs = [];
      let cur = [];
      s.values.forEach((v, i) => {
        if (isNum(v)) cur.push(`${x(i).toFixed(1)},${a.y(v).toFixed(1)}`);
        else if (cur.length) {
          segs.push(cur);
          cur = [];
        }
      });
      if (cur.length) segs.push(cur);
      return segs.map(
        (seg) => svg`<polyline points=${seg.join(" ")} fill="none" stroke=${s.color}
          stroke-width=${s.width || 1.8} stroke-dasharray=${s.dash || ""}></polyline>`,
      );
    });
    const yAxis = (side) => {
      const a = axes[side];
      if (!a) return nothing;
      const tx = side === "l" ? L - 6 : W - R + 6;
      const anchor = side === "l" ? "end" : "start";
      return a.ticks.map(
        (v) => svg`<line class="grid" x1=${L} x2=${W - R} y1=${a.y(v)} y2=${a.y(v)}></line>
          <text x=${tx} y=${a.y(v) + 4} text-anchor=${anchor}>${fmt(v)}</text>`,
      );
    };
    const ticks = this.labels.map((lab, i) => {
      const t = this.xTick ? this.xTick(lab, i) : null;
      return t ? svg`<text x=${x(i)} y=${H - 6} text-anchor="middle">${t}</text>` : nothing;
    });
    const refs = (this.refs || []).map(
      (r) => svg`<line x1=${L} x2=${W - R} y1=${axes.l.y(r.y)} y2=${axes.l.y(r.y)} stroke=${r.color}
        stroke-dasharray="4 3"></line>`,
    );
    const marks = (this.marks || []).map(
      (m) => svg`<line x1=${L + m.index * step} x2=${L + m.index * step} y1=${T} y2=${T + ph} stroke=${m.color || "#e53935"}
        stroke-width="1.5"></line><text x=${L + m.index * step + 4} y=${T + 10} class="mark">${m.label || ""}</text>`,
    );
    const hv = this._hover;
    return html`<div class="wrap">
      <svg width=${W} height=${H} @mousemove=${(e) => this._move(e, L, step, n)} @mouseleave=${() => (this._hover = null)}
        @click=${(e) => this._move(e, L, step, n)}>
        ${yAxis("l")} ${yAxis("r")} ${refs} ${body} ${marks} ${ticks}
        ${isNum(hv) ? svg`<line class="hover" x1=${x(hv)} x2=${x(hv)} y1=${T} y2=${T + ph}></line>` : nothing}
      </svg>
      ${isNum(hv) ? this._tip(hv, x(hv), W, fmt) : nothing}
      ${this.series.length > 1
        ? html`<div class="legend" style="padding-left:${L}px">
            ${this.series.map(
              (s) => html`<span><i class=${s.type === "bar" ? "" : "ln"} style="background:${s.color}"></i>${s.name}</span>`,
            )}
          </div>`
        : nothing}
    </div>`;
  }

  _move(e, L, step, n) {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - r.left - L) / step);
    this._hover = i >= 0 && i < n ? i : null;
  }

  _tip(i, px, W, fmt) {
    const title = this.xTip ? this.xTip(this.labels[i], i) : this.labels[i];
    const left = px + 200 > W ? px - 200 : px + 10;
    return html`<div class="tip" style="left:${Math.max(0, left)}px">
      <div class="tt">${title}</div>
      ${this.series
        .filter((s) => isNum(s.values[i]))
        .map((s) => html`<div><i style="background:${s.color}"></i>${s.name}: <b>${fmt(s.values[i], s)}</b></div>`)}
    </div>`;
  }

  static styles = css`
    :host {
      display: block;
    }
    .wrap {
      position: relative;
    }
    svg {
      display: block;
    }
    text {
      font: 11px Roboto, system-ui, sans-serif;
      fill: var(--secondary-text-color, #727272);
    }
    text.mark {
      fill: #e53935;
    }
    .grid {
      stroke: var(--divider-color, #e0e0e0);
      stroke-width: 0.6;
    }
    .hover {
      stroke: var(--primary-text-color, #000);
      stroke-opacity: 0.35;
      stroke-dasharray: 2 2;
    }
    .tip {
      position: absolute;
      top: 4px;
      width: 190px;
      pointer-events: none;
      font-size: 12px;
      line-height: 1.5;
      background: var(--card-background-color, #fff);
      color: var(--primary-text-color, #212121);
      border: 1px solid var(--divider-color, #ccc);
      border-radius: 8px;
      padding: 6px 8px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      z-index: 2;
    }
    .tt {
      font-weight: 600;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 14px;
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
    .legend i.ln {
      height: 3px;
      vertical-align: 3px;
    }
    .tip i {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 2px;
      margin-right: 4px;
    }
  `;
}

customElements.define("tc-chart", TcChart);
