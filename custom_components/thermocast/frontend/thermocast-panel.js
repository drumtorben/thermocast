import { LitElement, html, css } from "./lit.js";
import { langOf, t } from "./i18n.js";
import "./tc-summary.js";
import "./tc-days.js";
import "./tc-timeline.js";
import "./tc-details.js";
import "./tc-model.js";
import "./tc-kpis.js";

const TABS = ["now", "model", "kpis"];
const TAB_KEY = "thermocast.tab";

function loadTab() {
  try {
    const v = localStorage.getItem(TAB_KEY);
    return TABS.includes(v) ? v : "now";
  } catch {
    return "now";
  }
}

class ThermocastPanel extends LitElement {
  static properties = {
    hass: { attribute: false },
    narrow: { type: Boolean },
    panel: { attribute: false },
    demoView: { attribute: false },
    demoResponses: { attribute: false }, // dev page: {"thermocast/model": {...}, ...}
    _tab: { state: true },
    _view: { state: true },
    _error: { state: true },
    _hoverIndex: { state: true },
    _hoverCandidate: { state: true },
  };

  constructor() {
    super();
    this._view = null;
    this._error = null;
    this._hoverIndex = null;
    this._hoverCandidate = null;
    this._unsub = null;
    this._subscribing = false;
    this._retry = null;
    this._tab = loadTab();
    this._request = (msg) => {
      if (this.demoResponses) return Promise.resolve(this.demoResponses[msg.type]);
      return this.hass.connection.sendMessagePromise(msg);
    };
  }

  _setTab(tab) {
    this._tab = tab;
    try {
      localStorage.setItem(TAB_KEY, tab);
    } catch {
      /* ignore */
    }
  }

  connectedCallback() {
    super.connectedCallback();
    this._subscribe();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearTimeout(this._retry);
    this._unsubscribe();
  }

  updated(changed) {
    if (changed.has("demoView") && this.demoView) this._view = this.demoView;
    if (changed.has("hass") && !this._unsub && !this._subscribing && !this._retry) this._subscribe();
  }

  _scheduleRetry(ms) {
    clearTimeout(this._retry);
    this._retry = setTimeout(() => {
      this._retry = null;
      this._subscribe();
    }, ms);
  }

  async _subscribe() {
    if (this.demoView) {
      this._view = this.demoView;
      return;
    }
    if (!this.hass?.connection || this._unsub || this._subscribing || !this.isConnected) return;
    this._subscribing = true;
    try {
      this._unsub = await this.hass.connection.subscribeMessage((msg) => this._onMessage(msg), {
        type: "thermocast/subscribe",
      });
      this._error = null;
    } catch (err) {
      this._error = err?.code === "not_loaded" ? "not_loaded" : err?.message || String(err);
      this._scheduleRetry(5000);
    } finally {
      this._subscribing = false;
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      const unsub = this._unsub;
      this._unsub = null;
      Promise.resolve(unsub()).catch(() => {});
    }
  }

  _onMessage(msg) {
    const view = msg.view;
    if (view && view.error === "unloaded") {
      // the entry reloads (e.g. a zone was edited): subscribe again to the new coordinator
      this._unsubscribe();
      this._view = null;
      this._error = "not_loaded";
      this._scheduleRetry(2000);
      return;
    }
    this._view = view;
  }

  render() {
    const lang = langOf(this.hass);
    return html`
      <div class="toolbar">
        <ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>
        <div class="title">Thermocast</div>
      </div>
      <div class="tabs">
        ${TABS.map(
          (tab) => html`<button class=${tab === this._tab ? "on" : ""} @click=${() => this._setTab(tab)}>
            ${t(lang, "tab_" + tab)}
          </button>`,
        )}
      </div>
      <div class="content">${this._tabBody(lang)}</div>
    `;
  }

  _tabBody(lang) {
    if (this._error === "not_loaded") return html`<p class="notice">${t(lang, "not_loaded")}</p>`;
    const tz = this._view?.window?.tz || this.hass?.config?.time_zone;
    if (this._tab === "model") return html`<tc-model .request=${this._request} .lang=${lang} .tz=${tz}></tc-model>`;
    if (this._tab === "kpis") return html`<tc-kpis .request=${this._request} .lang=${lang} .tz=${tz}></tc-kpis>`;
    return this._body(lang);
  }

  _body(lang) {
    if (this._error === "not_loaded") return html`<p class="notice">${t(lang, "not_loaded")}</p>`;
    if (this._error) return html`<p class="notice">${t(lang, "error")}: ${this._error}</p>`;
    if (!this._view) return html`<p class="notice">${t(lang, "loading")}</p>`;
    if (this._view.error) return html`<p class="notice">${t(lang, "view_unavailable")}: ${this._view.error}</p>`;
    const v = this._view;
    const tz = v.window.tz || this.hass?.config?.time_zone;
    return html`
      ${v.errors.map((e) => html`<p class="notice small">${t(lang, "err_" + e)}</p>`)}
      <tc-summary .view=${v} .lang=${lang} .tz=${tz}></tc-summary>
      <tc-days .view=${v} .lang=${lang} .tz=${tz}></tc-days>
      <tc-timeline
        .view=${v}
        .lang=${lang}
        .tz=${tz}
        .hoverIndex=${this._hoverIndex}
        .hoverCandidate=${this._hoverCandidate}
        @tc-hover=${(e) => (this._hoverIndex = e.detail)}
      ></tc-timeline>
      <tc-details
        .view=${v}
        .lang=${lang}
        .tz=${tz}
        @tc-candidate=${(e) => (this._hoverCandidate = e.detail)}
      ></tc-details>
    `;
  }

  static styles = css`
    :host {
      display: block;
      min-height: 100vh;
      background: var(--primary-background-color, #fafafa);
      color: var(--primary-text-color, #212121);
      font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
    }
    .toolbar {
      display: flex;
      align-items: center;
      height: var(--header-height, 56px);
      padding: 0 12px;
      background: var(--app-header-background-color, var(--primary-color, #03a9f4));
      color: var(--app-header-text-color, #fff);
      font-size: 20px;
    }
    .title {
      margin-left: 8px;
    }
    .tabs {
      display: flex;
      gap: 4px;
      padding: 0 12px;
      background: var(--app-header-background-color, var(--primary-color, #03a9f4));
      overflow-x: auto;
    }
    .tabs button {
      font: inherit;
      font-size: 14px;
      background: none;
      border: none;
      border-bottom: 3px solid transparent;
      color: var(--app-header-text-color, #fff);
      opacity: 0.75;
      padding: 8px 14px 9px;
      cursor: pointer;
      white-space: nowrap;
    }
    .tabs button.on {
      opacity: 1;
      border-bottom-color: var(--app-header-text-color, #fff);
    }
    .content {
      max-width: 1600px;
      margin: 0 auto;
      padding: 16px;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
    }
    .content > * {
      min-width: 0;
    }
    .notice {
      margin: 0;
      padding: 16px;
      background: var(--card-background-color, #fff);
      border-radius: 12px;
    }
    .notice.small {
      padding: 8px 16px;
      font-size: 13px;
      color: var(--secondary-text-color, #727272);
    }
  `;
}

customElements.define("thermocast-panel", ThermocastPanel);
