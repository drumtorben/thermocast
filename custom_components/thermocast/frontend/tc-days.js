import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtDay } from "./i18n.js";

class TcDays extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };

  _card(d) {
    const L = this.lang;
    const title = { past: "yesterday", today: "today", future: "tomorrow" }[d.kind];
    const lines = [];
    if (d.kind === "past") lines.push(t(L, "day_heat", { h: fmtNum(d.heat_hours ?? 0, L), n: d.blocks ?? 0 }));
    if (d.kind === "today")
      lines.push(
        t(L, "day_heat_today", {
          h: fmtNum(d.heat_hours ?? 0, L),
          p: fmtNum(d.heat_hours_planned ?? 0, L),
          n: d.blocks ?? 0,
          m: d.blocks_planned ?? 0,
        }),
      );
    if (d.kind === "future")
      lines.push(t(L, "day_heat_plan", { p: fmtNum(d.heat_hours_planned ?? 0, L), m: d.blocks_planned ?? 0 }));
    if (d.starts !== null && d.starts !== undefined)
      lines.push(
        d.starts_per_block !== null && d.starts_per_block !== undefined
          ? t(L, "day_starts_per_block", { n: d.starts, k: fmtNum(d.starts_per_block, L) })
          : t(L, "day_starts", { n: d.starts }),
      );
    if (d.over_high_max !== null && d.over_high_max !== undefined && d.over_high_max > 0)
      lines.push(t(L, "day_over_high", { k: fmtNum(d.over_high_max, L) }));
    if (d.min_leading !== null) lines.push(t(L, "day_min", { t: fmtNum(d.min_leading, L) }));
    if (d.min_leading_planned !== null) lines.push(t(L, "day_min_plan", { t: fmtNum(d.min_leading_planned, L) }));
    if (d.release_followed !== null) lines.push(t(L, "day_followed", { p: Math.round(d.release_followed * 100) }));
    if (d.std_end !== null) lines.push(t(L, "day_std", { s: fmtNum(d.std_end, L) }));
    return html`<div class="day ${d.kind}">
      <div class="title">${t(L, title)} <span class="date">${fmtDay(d.date + "T12:00:00Z", L, this.tz)}</span></div>
      ${lines.map((l) => html`<div>${l}</div>`)}
    </div>`;
  }

  render() {
    if (!this.view) return nothing;
    return html`<div class="days">${this.view.days.map((d) => this._card(d))}</div>`;
  }

  static styles = css`
    .days {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    @media (max-width: 700px) {
      .days {
        grid-template-columns: 1fr;
      }
    }
    .day {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 12px 16px;
      font-size: 14px;
      line-height: 1.6;
    }
    .day.today {
      outline: 2px solid var(--primary-color, #03a9f4);
    }
    .title {
      font-weight: 600;
      font-size: 15px;
    }
    .date {
      font-weight: 400;
      color: var(--secondary-text-color, #727272);
      font-size: 13px;
    }
  `;
}

// the panel module can load twice (new ?v= after an update without page reload)
if (!customElements.get("tc-days")) customElements.define("tc-days", TcDays);
