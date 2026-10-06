import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtTime, fmtWhen } from "./i18n.js";

class TcDetails extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };

  _hover(i) {
    this.dispatchEvent(new CustomEvent("tc-candidate", { detail: i, bubbles: true, composed: true }));
  }

  _block(c) {
    if (!c.start) return t(this.lang, "c_none");
    return `${fmtWhen(c.start, this.lang, this.tz, this.view.generated_at)}–${fmtTime(c.end, this.lang, this.tz)}`;
  }

  _candidates() {
    const L = this.lang;
    const rows = this.view.candidates;
    if (!rows.length) return nothing;
    const overheat = rows.some((c) => "overheat" in c.parts);
    const cycling = rows.some((c) => (c.parts.cycling ?? 0) > 0);
    return html`<div class="card">
      <h3>${t(L, "candidates")}</h3>
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th>${t(L, "c_block")}</th>
              <th>${t(L, "c_violation")}</th>
              <th>${t(L, "c_comfort")}</th>
              ${overheat ? html`<th>${t(L, "c_overheat")}</th>` : nothing}
              <th>${t(L, "c_start")}</th>
              ${cycling ? html`<th title=${t(L, "c_cycling_hint")}>${t(L, "c_cycling")}</th>` : nothing}
              <th>${t(L, "c_energy")}</th>
              <th>${t(L, "c_delay")}</th>
              <th>${t(L, "c_total")}</th>
            </tr>
          </thead>
          <tbody @mouseleave=${() => this._hover(null)}>
            ${rows.map(
              (c, i) =>
                html`<tr
                  class=${c.chosen ? "chosen" : ""}
                  @mouseenter=${() => this._hover(i)}
                  @click=${() => this._hover(i)}
                >
                  <td>${this._block(c)}</td>
                  <td>${fmtNum(c.violation_kh, L, 2)}</td>
                  <td>${fmtNum(c.parts.comfort ?? 0, L, 2)}</td>
                  ${overheat ? html`<td>${fmtNum(c.parts.overheat ?? 0, L, 2)}</td>` : nothing}
                  <td>${fmtNum(c.parts.start ?? 0, L, 2)}</td>
                  ${cycling ? html`<td>${fmtNum(c.parts.cycling ?? 0, L, 2)}</td>` : nothing}
                  <td>${fmtNum(c.parts.energy ?? 0, L, 2)}</td>
                  <td>${fmtNum(c.parts.delay ?? 0, L, 2)}</td>
                  <td><b>${fmtNum(c.cost, L, 2)}</b></td>
                </tr>`,
            )}
          </tbody>
        </table>
      </div>
    </div>`;
  }

  _rules() {
    const L = this.lang;
    const d = this.view.decision;
    const r = d.rules || {};
    const item = (active, text) => html`<li class=${active ? "active" : ""}>${active ? "✗" : "✓"} ${text}</li>`;
    const state =
      d.release_state === true ? t(L, "allowed") : d.release_state === false ? t(L, "blocked") : t(L, "unknown");
    return html`<div class="card">
      <h3>${t(L, "rules")}</h3>
      <ul>
        ${item(d.override === "min_block", t(L, "r_min_block", { v: r.min_block_h }))}
        ${item(d.override === "min_pause", t(L, "r_min_pause", { v: r.min_pause_h }))}
        ${item(d.override === "budget", t(L, "r_budget", { n: d.switches_today, max: d.max_switches }))}
        ${item(d.override === "observe", t(L, "r_observe"))}
        ${item(d.override === "failsafe", t(L, "r_failsafe") + (d.failsafe_reason ? `: ${d.failsafe_reason}` : ""))}
      </ul>
      <p class="entity">${t(L, "r_entity", { e: d.release_entity, s: state })}</p>
    </div>`;
  }

  render() {
    if (!this.view) return nothing;
    return html`<div class="grid">${this._candidates()} ${this._rules()}</div>`;
  }

  static styles = css`
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
      gap: 12px;
    }
    @media (max-width: 700px) {
      .grid {
        grid-template-columns: minmax(0, 1fr);
      }
    }
    .card {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 12px 16px;
      min-width: 0;
    }
    .scroll {
      overflow-x: auto;
    }
    h3 {
      margin: 0 0 8px;
      font-size: 15px;
    }
    table {
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }
    th,
    td {
      text-align: right;
      padding: 4px 8px;
      border-bottom: 1px solid var(--divider-color, #e0e0e0);
      white-space: nowrap;
    }
    th:first-child,
    td:first-child {
      text-align: left;
    }
    th {
      font-weight: 500;
      color: var(--secondary-text-color, #727272);
    }
    tbody tr:hover {
      background: var(--secondary-background-color, #f2f2f2);
    }
    tr.chosen {
      background: rgba(0, 158, 115, 0.15);
      font-weight: 600;
    }
    ul {
      list-style: none;
      margin: 0;
      padding: 0;
      line-height: 1.8;
      font-size: 14px;
    }
    li.active {
      color: #d55e00;
      font-weight: 600;
    }
    .entity {
      margin: 8px 0 0;
      font-size: 13px;
      color: var(--secondary-text-color, #727272);
      overflow-wrap: anywhere;
    }
  `;
}

// the panel module can load twice (new ?v= after an update without page reload)
if (!customElements.get("tc-details")) customElements.define("tc-details", TcDetails);
