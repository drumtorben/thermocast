import { LitElement, html, css, nothing } from "./lit.js";
import { t, fmtNum, fmtSigned, fmtTime, fmtWhen } from "./i18n.js";

class TcSummary extends LitElement {
  static properties = { view: { attribute: false }, lang: {}, tz: {} };

  _zoneName(id) {
    return this.view.zones.find((z) => z.id === id)?.name ?? id;
  }

  _when(iso) {
    return fmtWhen(iso, this.lang, this.tz, this.view.generated_at);
  }

  _headline() {
    const { decision, explanation } = this.view;
    // observe mode: nothing is switched, so this is the planner's wish, not the boiler state
    const on = decision.control_enabled ? decision.applied : decision.planner_wants ?? decision.applied;
    const prefix = decision.control_enabled ? "heating" : "planner";
    const head = t(this.lang, `${prefix}_${on ? "on" : "off"}`);
    const nb = explanation.next_block;
    let tail = t(this.lang, "no_block");
    if (nb && explanation.code === "heating_now") tail = t(this.lang, "block_until", { end: fmtWhen(nb.end, this.lang, this.tz, this.view.generated_at, true) });
    else if (nb)
      tail = t(this.lang, "next_block", { start: this._when(nb.start), end: fmtTime(nb.end, this.lang, this.tz) });
    return `${on ? "🔥" : "❄️"} ${head} · ${tail}`;
  }

  _sentence() {
    const ex = this.view.explanation;
    const L = this.lang;
    const zone = ex.driver ? this._zoneName(ex.driver) : "";
    const z = this.view.zones.find((zz) => zz.id === ex.driver);
    const idx = ex.first_violation ? this.view.hours.indexOf(ex.first_violation) : -1;
    const comfort = z && idx >= 0 ? z.comfort_low[idx] : null;
    const temp = fmtNum(comfort, L);
    const when = ex.first_violation ? this._when(ex.first_violation) : "";
    switch (ex.code) {
      case "block_planned":
        return t(L, "exp_block_planned", { zone, temp, when, lead: ex.lead_h ?? "–" });
      case "heating_now":
        return ex.driver
          ? t(L, "exp_heating_now", { zone, temp, when, end: fmtTime(ex.next_block.end, L, this.tz) })
          : t(L, "exp_heating_now_short", { end: fmtTime(ex.next_block.end, L, this.tz) });
      case "violation_accepted":
        return t(L, "exp_violation_accepted", { zone, when });
      case "failsafe":
        return t(L, "exp_failsafe", { reason: ex.reason ?? "–" });
      case "no_forecast":
        return t(L, "exp_no_forecast");
      default:
        return t(L, "exp_" + ex.code);
    }
  }

  _planChange() {
    const pc = this.view.plan_change;
    if (!pc) return nothing;
    const L = this.lang;
    const fmtB = (b) => `${this._when(b.start)}–${fmtTime(b.end, L, this.tz)}`;
    let what;
    if (pc.previous_block && pc.current_block)
      what = t(L, "pc_moved", { old: fmtB(pc.previous_block), new: fmtB(pc.current_block) });
    else if (pc.current_block) what = t(L, "pc_new", { new: fmtB(pc.current_block) });
    else what = t(L, "pc_dropped", { old: fmtB(pc.previous_block) });
    const c = pc.cause;
    if (c) {
      const at = c.at ? fmtTime(c.at, L, this.tz) : "";
      const delta = fmtSigned(c.delta, L, c.kind === "sun" ? 0 : 1);
      what += " – " + t(L, "cause_" + c.kind, { at, delta, zone: c.zone ? this._zoneName(c.zone) : "" });
    }
    return html`<p class="change">${t(L, "plan_changed", { what })}</p>`;
  }

  _chips() {
    const { decision: d, robustness: r } = this.view;
    const L = this.lang;
    const chips = [
      html`<span class="chip ${d.control_enabled ? "warn" : "ok"}"
        >${t(L, d.control_enabled ? "mode_active" : "mode_observe")}</span
      >`,
    ];
    if (d.forecast_age_min !== null && d.forecast_age_min !== undefined)
      chips.push(html`<span class="chip">${t(L, "forecast_age", { min: d.forecast_age_min })}</span>`);
    chips.push(html`<span class="chip">${t(L, "switches", { n: d.switches_today, max: d.max_switches })}</span>`);
    if (d.failsafe_reason)
      chips.push(html`<span class="chip bad">${t(L, "failsafe_chip", { reason: d.failsafe_reason })}</span>`);
    if (r) {
      chips.push(html`<span class="chip ${r.level === "close" ? "warn" : "ok"}">${t(L, "robust_" + r.level)}</span>`);
      if (r.sigma_driven) chips.push(html`<span class="chip warn">${t(L, "sigma_driven")}</span>`);
    }
    return chips;
  }

  render() {
    if (!this.view) return nothing;
    const o = this.view.decision.override;
    const overrideNote = ["min_block", "min_pause", "budget"].includes(o)
      ? html`<p class="note">${t(this.lang, "override_" + o)}</p>`
      : nothing;
    return html`
      <div class="card">
        <div class="headline">${this._headline()}</div>
        <p class="why">${this._sentence()}</p>
        ${overrideNote} ${this._planChange()}
        <div class="chips">${this._chips()}</div>
      </div>
    `;
  }

  static styles = css`
    .card {
      background: var(--card-background-color, #fff);
      border-radius: 12px;
      padding: 16px 20px;
    }
    .headline {
      font-size: 22px;
      font-weight: 600;
    }
    .why {
      margin: 8px 0 0;
      line-height: 1.5;
    }
    .note,
    .change {
      margin: 6px 0 0;
      color: var(--secondary-text-color, #727272);
    }
    .chips {
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .chip {
      font-size: 12px;
      padding: 3px 10px;
      border-radius: 12px;
      background: var(--secondary-background-color, #eee);
    }
    .chip.ok {
      background: rgba(0, 158, 115, 0.15);
    }
    .chip.warn {
      background: rgba(230, 159, 0, 0.22);
    }
    .chip.bad {
      background: rgba(213, 94, 0, 0.25);
    }
  `;
}

customElements.define("tc-summary", TcSummary);
