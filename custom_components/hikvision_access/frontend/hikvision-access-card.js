/**
 * hikvision-access-card
 * Timeline of access events (photo + person + method + result) for the
 * Hikvision Access integration. Ships with the integration and registers
 * itself; add `type: custom:hikvision-access-card` to a dashboard.
 *
 * Config:
 *   entry_id: string   (optional — omit to merge every terminal)
 *   title:    string
 *   limit:    number    (default 30, per page)
 *   range:    "today" | "7d" | "30d" | "all"   (default "today")
 *   result:   "all" | "granted" | "denied"     (default "all")
 *   compact:  boolean   (smaller rows)
 */

const VERSION = "0.3.0";

// Event fields (person_name, door_name, device_name, event_uid) come from the
// terminal and are rendered via innerHTML — always escape them.
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const METHOD_ICON = {
  face: "mdi:face-recognition",
  card: "mdi:card-account-details-outline",
  fingerprint: "mdi:fingerprint",
  qr: "mdi:qrcode",
  password: "mdi:dialpad",
  remote: "mdi:cellphone-wireless",
  button: "mdi:gesture-tap-button",
  other: "mdi:help-circle-outline",
  unknown: "mdi:help-circle-outline",
};
const METHOD_LABEL = {
  face: "Facial", card: "Cartão", fingerprint: "Digital", qr: "QR Code",
  password: "Senha", remote: "Remoto", button: "Botão", other: "Outro",
  unknown: "—",
};
const RESULT = {
  granted: { label: "Permitido", icon: "mdi:check-circle", cls: "ok" },
  denied: { label: "Negado", icon: "mdi:close-circle", cls: "no" },
  unknown: { label: "—", icon: "mdi:minus-circle", cls: "unk" },
};
const RANGES = { today: "Hoje", "7d": "7 dias", "30d": "30 dias", all: "Tudo" };

function startOf(range) {
  if (range === "all") return null;
  const d = new Date();
  if (range === "today") d.setHours(0, 0, 0, 0);
  if (range === "7d") d.setDate(d.getDate() - 7);
  if (range === "30d") d.setDate(d.getDate() - 30);
  return d.toISOString();
}

class HikvisionAccessCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = [];
    this._cursor = null;
    this._loading = false;
    this._filters = { range: "today", result: "all", person: "" };
    this._imgUrls = new Map();
    this._unsub = null;
    this._lightbox = null;
  }

  setConfig(config) {
    this._config = { limit: 30, ...config };
    if (config.range) this._filters.range = config.range;
    if (config.result) this._filters.result = config.result;
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._reload();
      this._subscribe();
    }
  }

  getCardSize() {
    return 3 + Math.min(this._events.length, 8);
  }

  disconnectedCallback() {
    if (this._unsub) Promise.resolve(this._unsub).then((u) => u && u());
    this._unsub = null;
    for (const u of this._imgUrls.values()) URL.revokeObjectURL(u);
    this._imgUrls.clear();
  }

  // ---- data ---------------------------------------------------------

  _query(extra = {}) {
    const p = new URLSearchParams();
    if (this._config.entry_id) p.set("entry_id", this._config.entry_id);
    p.set("limit", String(this._config.limit));
    const start = startOf(this._filters.range);
    if (start) p.set("start", start);
    if (this._filters.result !== "all") p.set("result", this._filters.result);
    if (this._filters.person.trim()) p.set("person_id", this._filters.person.trim());
    for (const [k, v] of Object.entries(extra)) if (v) p.set(k, v);
    return `hikvision_access/events?${p.toString()}`;
  }

  async _reload() {
    if (!this._hass) return;
    this._loading = true;
    this._render();
    try {
      const res = await this._hass.callApi("GET", this._query());
      this._events = res.events || [];
      this._cursor = res.next_cursor || null;
    } catch (e) {
      this._error = String(e);
    }
    this._loading = false;
    this._render();
  }

  async _loadMore() {
    if (!this._cursor || this._loading) return;
    this._loading = true;
    this._render();
    try {
      const res = await this._hass.callApi("GET", this._query({ cursor: this._cursor }));
      this._events = this._events.concat(res.events || []);
      this._cursor = res.next_cursor || null;
    } catch (e) {
      this._error = String(e);
    }
    this._loading = false;
    this._render();
  }

  async _subscribe() {
    if (!this._hass || this._unsub) return;
    try {
      this._unsub = await this._hass.connection.subscribeMessage(
        (msg) => this._onLive(msg && msg.event),
        { type: "hikvision_access/subscribe", ...(this._config.entry_id ? { entry_id: this._config.entry_id } : {}) }
      );
    } catch (e) {
      /* WS command not available yet */
    }
  }

  _onLive(ev) {
    if (!ev || !ev.event_uid) return;
    if (this._events.some((e) => e.event_uid === ev.event_uid)) return;
    if (this._filters.result !== "all" && ev.result !== this._filters.result) return;
    this._events.unshift({
      event_uid: ev.event_uid,
      timestamp: ev.timestamp,
      person_name: ev.person_name,
      person_id: ev.person_id,
      result: ev.result,
      method: ev.method,
      has_event_picture: ev.has_picture,
      _isNew: true,
    });
    this._render();
  }

  async _imgUrl(uid) {
    if (this._imgUrls.has(uid)) return this._imgUrls.get(uid);
    try {
      const signed = await this._hass.callWS({
        type: "auth/sign_path",
        path: `/api/hikvision_access/events/${encodeURIComponent(uid)}/image`,
        expires: 3600,
      });
      const url = this._hass.hassUrl(signed.path);
      this._imgUrls.set(uid, url);
      return url;
    } catch (e) {
      return null;
    }
  }

  // ---- rendering ---------------------------------------------------

  _render() {
    if (!this.shadowRoot) return;
    const c = this._config || {};
    const compact = c.compact ? "compact" : "";
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title">${esc(c.title || "Acessos")}</div>
          <div class="filters">
            ${this._segment("range", RANGES)}
            ${this._segment("result", { all: "Todos", granted: "✓", denied: "✕" })}
          </div>
        </div>
        <div class="person">
          <ha-icon icon="mdi:magnify"></ha-icon>
          <input id="person" placeholder="Filtrar por ID de pessoa" value="${esc(this._filters.person)}">
        </div>
        <div class="list ${compact}">
          ${this._error ? `<div class="empty err">${esc(this._error)}</div>` : ""}
          ${!this._error && !this._events.length && !this._loading
            ? `<div class="empty">Nenhum acesso no período.</div>` : ""}
          ${this._events.map((e) => this._row(e)).join("")}
          ${this._loading ? `<div class="empty">Carregando…</div>` : ""}
        </div>
        ${this._cursor ? `<button class="more" id="more">Carregar mais</button>` : ""}
        <div class="foot">hikvision-access-card v${VERSION}</div>
      </ha-card>
      ${this._lightbox ? `<div class="lb" id="lb"><img src="${this._lightbox}"><span>fechar ✕</span></div>` : ""}
    `;

    this.shadowRoot.querySelectorAll("[data-seg]").forEach((el) =>
      el.addEventListener("click", () => {
        this._filters[el.dataset.seg] = el.dataset.val;
        this._reload();
      })
    );
    const person = this.shadowRoot.getElementById("person");
    if (person) {
      person.addEventListener("change", () => {
        this._filters.person = person.value;
        this._reload();
      });
    }
    const more = this.shadowRoot.getElementById("more");
    if (more) more.addEventListener("click", () => this._loadMore());
    const lb = this.shadowRoot.getElementById("lb");
    if (lb) lb.addEventListener("click", () => { this._lightbox = null; this._render(); });

    // lazy-load thumbnails
    this.shadowRoot.querySelectorAll("img[data-uid]").forEach(async (img) => {
      const url = await this._imgUrl(img.dataset.uid);
      if (url) {
        img.src = url;
        img.closest(".thumb").addEventListener("click", () => {
          this._lightbox = url;
          this._render();
        });
      } else {
        img.closest(".thumb").classList.add("noimg");
      }
    });
  }

  _segment(key, opts) {
    return `<div class="seg">${Object.entries(opts)
      .map(([v, l]) =>
        `<span data-seg="${key}" data-val="${v}" class="${this._filters[key] === v ? "on" : ""}">${l}</span>`)
      .join("")}</div>`;
  }

  _row(e) {
    const r = RESULT[e.result] || RESULT.unknown;
    const when = new Date(e.timestamp);
    const time = when.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    const date = when.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
    const name = esc(e.person_name || (e.person_id ? `#${e.person_id}` : "Desconhecido"));
    const method = METHOD_LABEL[e.method] || "—";
    const dev = e.device_name ? ` · ${esc(e.device_name)}` : "";
    return `
      <div class="row ${e._isNew ? "new" : ""}">
        <div class="thumb">
          ${e.has_event_picture
            ? `<img data-uid="${esc(e.event_uid)}" alt="">`
            : `<ha-icon icon="mdi:account"></ha-icon>`}
        </div>
        <div class="info">
          <div class="l1">${name}</div>
          <div class="l2">${date} ${time}${dev}${e.door_name ? ` · ${esc(e.door_name)}` : ""}</div>
          <div class="l3">
            <ha-icon icon="${METHOD_ICON[e.method] || METHOD_ICON.unknown}"></ha-icon> ${method}
          </div>
        </div>
        <div class="badge ${r.cls}"><ha-icon icon="${r.icon}"></ha-icon>${r.label}</div>
      </div>`;
  }
}

const STYLE = `
:host { display:block; }
.head { display:flex; justify-content:space-between; align-items:center;
  padding:12px 16px 4px; gap:8px; flex-wrap:wrap; }
.title { font-size:1.15rem; font-weight:600; }
.filters { display:flex; gap:8px; flex-wrap:wrap; }
.seg { display:flex; border:1px solid var(--divider-color); border-radius:16px; overflow:hidden; }
.seg span { padding:3px 10px; font-size:.8rem; cursor:pointer; user-select:none;
  color:var(--secondary-text-color); }
.seg span.on { background:var(--primary-color); color:var(--text-primary-color); }
.person { display:flex; align-items:center; gap:6px; margin:6px 16px 4px;
  border-bottom:1px solid var(--divider-color); }
.person ha-icon { --mdc-icon-size:18px; color:var(--secondary-text-color); }
.person input { flex:1; border:0; outline:0; background:transparent; padding:6px 0;
  color:var(--primary-text-color); font-size:.9rem; }
.list { display:flex; flex-direction:column; }
.row { display:flex; align-items:center; gap:12px; padding:10px 16px;
  border-top:1px solid var(--divider-color); }
.list .row:first-child { border-top:0; }
.row.new { animation:flash 1.6s ease-out; }
@keyframes flash { from { background:var(--primary-color); } to { background:transparent; } }
.thumb { width:56px; height:56px; border-radius:8px; overflow:hidden; flex:0 0 auto;
  background:var(--secondary-background-color); display:flex; align-items:center;
  justify-content:center; cursor:pointer; }
.compact .thumb { width:40px; height:40px; }
.thumb img { width:100%; height:100%; object-fit:cover; }
.thumb ha-icon, .thumb.noimg ha-icon { color:var(--disabled-text-color); --mdc-icon-size:28px; }
.info { flex:1; min-width:0; }
.l1 { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.l2 { font-size:.8rem; color:var(--secondary-text-color); }
.l3 { font-size:.8rem; color:var(--secondary-text-color); display:flex; align-items:center; gap:4px; }
.l3 ha-icon { --mdc-icon-size:16px; }
.compact .l3 { display:none; }
.badge { display:flex; align-items:center; gap:4px; font-size:.8rem; font-weight:600;
  padding:3px 8px; border-radius:12px; flex:0 0 auto; }
.badge ha-icon { --mdc-icon-size:16px; }
.badge.ok { color:var(--success-color, #4caf50); background:rgba(76,175,80,.12); }
.badge.no { color:var(--error-color, #f44336); background:rgba(244,67,54,.12); }
.badge.unk { color:var(--secondary-text-color); background:var(--secondary-background-color); }
.empty { padding:20px 16px; text-align:center; color:var(--secondary-text-color); }
.empty.err { color:var(--error-color); }
.more { display:block; width:calc(100% - 32px); margin:8px 16px 12px; padding:8px;
  border:1px solid var(--divider-color); border-radius:8px; background:transparent;
  color:var(--primary-color); cursor:pointer; font-weight:600; }
.foot { text-align:center; font-size:.7rem; color:var(--disabled-text-color); padding:0 0 8px; }
.lb { position:fixed; inset:0; background:rgba(0,0,0,.85); z-index:9999;
  display:flex; align-items:center; justify-content:center; flex-direction:column; gap:12px; }
.lb img { max-width:92vw; max-height:82vh; border-radius:8px; }
.lb span { color:#fff; font-size:.9rem; }
`;

customElements.define("hikvision-access-card", HikvisionAccessCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "hikvision-access-card",
  name: "AVANT — Timeline de Acessos",
  description: "Linha do tempo de acessos com foto, pessoa, método e resultado.",
  preview: false,
});

console.info(`%c hikvision-access-card %c v${VERSION} `,
  "background:#03a9f4;color:#fff;border-radius:3px 0 0 3px;padding:1px 4px",
  "background:#333;color:#fff;border-radius:0 3px 3px 0;padding:1px 4px");
