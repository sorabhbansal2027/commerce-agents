import { LightningElement, api, track } from 'lwc';

const STARTER_PROMPTS = [
    { id: 'p1', label: 'What needs my attention this morning?' },
    { id: 'p2', label: 'How did sales do this week compared to last?' },
    { id: 'p3', label: 'Which listings are running low on stock?' },
    { id: 'p4', label: 'Which slow movers should we mark down?' },
];

const KIND = {
    USER: 'user', TEXT: 'text', ACTIVITY: 'activity',
    SKELETON: 'skeleton', ERROR: 'error', CHIPS: 'chips',
    METRICS: 'metrics', DIGEST: 'digest', CHANGE_PREVIEW: 'change_preview',
};

// ── Formatting helpers ─────────────────────────────────────────────────────────

const CURRENCY_METRICS = new Set(['sales', 'average_order_value', 'revenue', 'spend']);
const RATE_METRICS     = new Set(['conversion_rate', 'return_rate', 'click_through_rate']);

function titleCase(s) {
    return (s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function metricLabel(m) {
    if (m === 'average_order_value') return 'Avg order';
    if (m === 'conversion_rate')     return 'Conversion';
    return titleCase(m);
}

function fmtMoney(v, currency) {
    const sym = currency && currency !== 'USD' ? currency + ' ' : '$';
    if (v >= 1000000) return sym + (v / 1000000).toFixed(1) + 'M';
    if (v >= 1000)    return sym + (v / 1000).toFixed(1) + 'K';
    return sym + v.toFixed(2);
}

function fmtMetricValue(entry) {
    const v = entry.value;
    if (v == null) return '—';
    if (CURRENCY_METRICS.has(entry.metric)) return fmtMoney(v, entry.currency);
    if (RATE_METRICS.has(entry.metric))     return (v * 100).toFixed(1) + '%';
    if (v >= 1000000) return (v / 1000000).toFixed(1) + 'M';
    if (v >= 1000)    return (v / 1000).toFixed(1) + 'K';
    return String(Math.round(v));
}

function fmtChangePct(pct) {
    if (pct == null) return '';
    const sign = pct >= 0 ? '+' : '';
    return sign + pct.toFixed(1) + '%';
}

function fmtDiffVal(val) {
    if (val == null) return '';
    if (typeof val === 'number') {
        if (val >= 10000) return val.toLocaleString();
        return String(val);
    }
    return String(val);
}

function digestCtx(item) {
    if (item.listing) {
        const l = item.listing;
        const stock = l.stock === 0 ? 'sold out' : l.stock + ' in stock';
        return l.listing_id + ' · ' + stock;
    }
    if (item.change) {
        return item.change.change_id + ' · ' + item.change.status;
    }
    return null;
}

// ── Component ──────────────────────────────────────────────────────────────────

export default class SalesforceMerchant extends LightningElement {
    @api apiUrl             = '';
    @api apiPrefix          = '/api/merchant';
    @api panelTitle         = 'Merchant assistant';
    @api introText          = 'Ask about performance, inventory, pricing, or campaigns.';
    @api placeholder        = 'Ask about sales, stock, pricing…';
    @api hideMetrics        = false;
    @api hideDigest         = false;
    @api hideChangePreview  = false;

    @track _open      = false;
    @track _busy      = false;
    @track _items     = [];
    @track _draft     = '';
    @track _starters  = STARTER_PROMPTS.map(s => ({ ...s, disabled: false }));
    @track _showIntro = true;

    _sessionId     = null;
    _rafId         = null;
    _pendingTurnId = null;
    _activityId    = null;
    _skeletonId    = null;
    _abortCtrl     = null;
    _nextId        = 0;

    // ── Lifecycle ──────────────────────────────────────────────────────────────
    connectedCallback()    { this._createSession(); }
    disconnectedCallback() { if (this._abortCtrl) this._abortCtrl.abort(); }

    // ── Template getters ───────────────────────────────────────────────────────
    get isOpen()      { return this._open; }
    get isBusy()      { return this._busy; }
    get showIntro()   { return this._showIntro && this._items.length === 0; }
    get draftValue()  { return this._draft; }
    get canSend()     { return !this._busy && this._draft.trim().length > 0; }
    get cannotSend()  { return !this.canSend; }
    get starters()    { return this._starters; }
    get toggleClass() { return this._busy ? 'sf-toggle busy' : 'sf-toggle'; }
    get actDotClass() { return this._busy ? 'sf-act-dot pulsing' : 'sf-act-dot'; }
    get actLabel()    { return this._busy ? 'Active' : 'Ready'; }

    get itemViews() {
        return this._items.map((it, idx) => {
            const base = {
                ...it,
                uid:             `${it.id}-${idx}`,
                isUser:          it.kind === KIND.USER,
                isText:          it.kind === KIND.TEXT,
                isActivity:      it.kind === KIND.ACTIVITY,
                isSkeleton:      it.kind === KIND.SKELETON,
                isError:         it.kind === KIND.ERROR,
                isChips:         it.kind === KIND.CHIPS,
                isMetrics:       it.kind === KIND.METRICS,
                isDigest:        it.kind === KIND.DIGEST,
                isChangePreview: it.kind === KIND.CHANGE_PREVIEW,
            };

            if (base.isText) {
                base.textClass = it.streaming ? 'sf-text streaming' : 'sf-text';
            }

            if (base.isMetrics) {
                base.cardTitle   = it.title || 'Performance';
                base.cardAside   = it.period || null;
                base.hasCardAside = !!it.period;
                base.metricCells = (it.metrics || []).map((m, i) => ({
                    uid:         `${it.id}-m${i}`,
                    label:       metricLabel(m.metric),
                    value:       fmtMetricValue(m),
                    hasChange:   m.change_pct != null,
                    changeText:  fmtChangePct(m.change_pct),
                    changeClass: m.change_pct == null ? '' :
                                 m.change_pct >= 0 ? 'sf-change-chip pos' : 'sf-change-chip neg',
                    hasNote:     !!m.note,
                    note:        m.note || '',
                }));
            }

            if (base.isDigest) {
                const items = it.items || [];
                base.cardTitle   = it.title || 'Needs attention';
                base.digestCount = items.length === 1 ? '1 item' : items.length + ' items';
                base.digestRows  = items.map((d, i) => {
                    const ctx = digestCtx(d);
                    return {
                        uid:      `${it.id}-d${i}`,
                        headline: d.headline,
                        hasWhy:   !!d.why_it_matters,
                        why:      d.why_it_matters || '',
                        hasCtx:   !!ctx,
                        ctx:      ctx || '',
                        iconClass: 'sf-di-icon di-' + (d.kind || 'note'),
                    };
                });
            }

            if (base.isChangePreview) {
                const ch = it.change || {};
                const s  = ch.status || 'staged';
                base.headline       = it.headline || 'Proposed change';
                base.hasChangeNote  = !!it.note;
                base.changeNote     = it.note || '';
                base.changeSummary  = ch.summary || '';
                base.statusLabel    = s.charAt(0).toUpperCase() + s.slice(1);
                base.statusClass    = 'sf-status-pill sf-status-' + s;
                base.isStaged       = s === 'staged';
                base.changeId       = it.changeId || '';
                base.diffRows       = (ch.items || []).map((r, i) => ({
                    uid:      `${it.id}-r${i}`,
                    target:   r.target || '',
                    field:    titleCase(r.field || ''),
                    hasBefore: r.before != null,
                    before:   fmtDiffVal(r.before),
                    after:    fmtDiffVal(r.after),
                }));
                const mi = ch.margin_impact;
                base.hasMargin     = mi != null;
                base.marginText    = mi != null ? (mi >= 0 ? '+' : '') + fmtMoney(mi, ch.currency) : '';
                base.marginClass   = mi != null ? (mi < 0 ? 'sf-margin neg' : 'sf-margin pos') : '';
            }

            return base;
        });
    }

    // ── Toggle / close ─────────────────────────────────────────────────────────
    handleToggle() { this._open = !this._open; }
    handleClose()  { this._open = false; }

    // ── Draft input ────────────────────────────────────────────────────────────
    handleInput(evt) {
        this._draft = evt.target.value;
        evt.target.style.height = 'auto';
        evt.target.style.height = Math.min(evt.target.scrollHeight, 120) + 'px';
    }

    handleKeyDown(evt) {
        if (evt.key === 'Enter' && !evt.shiftKey) {
            evt.preventDefault();
            this._submit(this._draft.trim());
        }
    }

    handleSend()          { this._submit(this._draft.trim()); }
    handleFormSubmit(evt) { evt.preventDefault(); this._submit(this._draft.trim()); }
    handleStarter(evt)    { this._submit(evt.currentTarget.dataset.label); }
    handleChip(evt)       { this._submit(evt.currentTarget.dataset.label); }
    handleApprove(evt)    { this._applyChange(evt.currentTarget.dataset.id, 'apply'); }
    handleDiscard(evt)    { this._applyChange(evt.currentTarget.dataset.id, 'discard'); }

    // ── Session ────────────────────────────────────────────────────────────────
    async _createSession() {
        if (!(this.apiUrl ?? '').trim()) {
            this._pushError('API URL is not configured. Set the apiUrl property in Lightning App Builder.');
            return;
        }
        try {
            const res = await fetch(this._url('session'), { method: 'POST', headers: this._hdrs() });
            if (!res.ok) throw new Error(`Session ${res.status}`);
            const body = await res.json();
            this._sessionId = body.session_id ?? body.id ?? null;
        } catch (err) {
            this._pushError('Could not start session: ' + err.message);
        }
    }

    // ── Submit ─────────────────────────────────────────────────────────────────
    _submit(text) {
        if (!text || this._busy) return;
        this._draft = '';
        const ta = this.template.querySelector('textarea');
        if (ta) { ta.value = ''; ta.style.height = 'auto'; }
        this._showIntro = false;
        this._pushItem({ kind: KIND.USER, id: this._id(), text });
        this._startTurn(text);
    }

    async _startTurn(text) {
        this._busy = true;
        this._disableStarters(true);
        if (this._abortCtrl) this._abortCtrl.abort();
        this._abortCtrl = typeof AbortController !== 'undefined' ? new AbortController() : null;

        const skelId = this._id();
        this._skeletonId = skelId;
        this._pushItem({ kind: KIND.SKELETON, id: skelId });

        const sessionHdr = this._sessionId ? { 'X-Session-Id': this._sessionId } : {};
        const fetchOpts  = {
            method: 'POST',
            headers: this._hdrs(sessionHdr),
            body: JSON.stringify({ message: text }),
        };
        if (this._abortCtrl) fetchOpts.signal = this._abortCtrl.signal;

        try {
            const res = await fetch(this._url('chat'), fetchOpts);
            if (!res.ok) throw new Error(`Chat ${res.status}`);
            this._removeSkeleton();
            await this._readStream(res.body);
        } catch (err) {
            this._removeSkeleton();
            if (err.name !== 'AbortError') this._pushError(err.message);
        } finally {
            this._busy = false;
            this._disableStarters(false);
            this._pendingTurnId = null;
            this._activityId    = null;
            this._scheduleRender();
        }
    }

    // ── SSE stream reader ──────────────────────────────────────────────────────
    async _readStream(body) {
        const reader  = body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        let sseType = null;
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let nl;
            while ((nl = buf.indexOf('\n')) !== -1) {
                const line = buf.slice(0, nl).trimEnd();
                buf = buf.slice(nl + 1);
                if (line.startsWith('event:')) {
                    sseType = line.slice(6).trim();
                    continue;
                }
                if (!line.startsWith('data:')) continue;
                const raw = line.slice(5).trim();
                if (!raw || raw === '[DONE]') { sseType = null; continue; }
                try {
                    const ev = JSON.parse(raw);
                    if (!ev.type && sseType) ev.type = sseType;
                    this._handleEvent(ev);
                } catch (_) { }
                sseType = null;
            }
        }
    }

    _handleEvent(ev) {
        const t = ev.type;

        if (t === 'text_delta') {
            if (!this._pendingTurnId) {
                const id = this._id();
                this._pendingTurnId = id;
                this._pushItem({ kind: KIND.TEXT, id, text: '', streaming: true });
            }
            this._removeActivity();
            // server sends {"text":"..."} — not "delta"
            this._mutateItem(this._pendingTurnId, it => ({
                ...it, text: it.text + (ev.text ?? ev.delta ?? ''), streaming: true,
            }));
        }
        else if (t === 'progress' || t === 'tool_call') {
            // progress: {message, tool}, tool_call: {tool, label}
            const label = ev.message ?? ev.label ?? ev.tool ?? ev.name ?? 'Working…';
            if (!this._activityId) {
                const id = this._id();
                this._activityId = id;
                this._pushItem({ kind: KIND.ACTIVITY, id, label });
            } else {
                this._mutateItem(this._activityId, it => ({ ...it, label }));
            }
        }
        else if (t === 'ui' || t === 'ui_partial') {
            // ui: {component, payload}
            const block = ev.payload
                ? { type: ev.component, ...ev.payload }
                : (ev.block ?? ev);
            this._renderUIBlock(block, t === 'ui_partial');
        }
        else if (t === 'change_update') {
            const ch = ev.change ?? ev;
            const existing = this._items.find(
                i => i.kind === KIND.CHANGE_PREVIEW && i.changeId === ch.id
            );
            if (existing) {
                this._mutateItem(existing.id, it => ({
                    ...it, change: { ...it.change, status: ch.status ?? it.change?.status },
                }));
            }
        }
        else if (t === 'turn_complete') {
            if (this._pendingTurnId) {
                this._mutateItem(this._pendingTurnId, it => ({ ...it, streaming: false }));
            }
            this._removeActivity();
        }
        else if (t === 'error') {
            this._pushError(ev.message ?? 'An error occurred.');
        }

        this._scheduleRender();
    }

    // ── UI block renderer ──────────────────────────────────────────────────────
    _renderUIBlock(block, partial) {
        const comp = block.type;
        if (comp === 'metrics' && !this.hideMetrics) {
            this._pushItem({
                kind:    KIND.METRICS,
                id:      this._id(),
                partial,
                title:   block.title ?? null,
                period:  block.period ?? null,
                metrics: block.metrics ?? [],
            });
        } else if (comp === 'digest' && !this.hideDigest) {
            this._pushItem({
                kind:  KIND.DIGEST,
                id:    this._id(),
                partial,
                title: block.title ?? null,
                items: block.items ?? [],
            });
        } else if (comp === 'change_preview' && !this.hideChangePreview) {
            this._pushItem({
                kind:      KIND.CHANGE_PREVIEW,
                id:        this._id(),
                partial,
                changeId:  block.change_id ?? block.change?.change_id ?? null,
                headline:  block.headline ?? null,
                note:      block.note ?? null,
                change:    block.change ?? null,
            });
        }
    }

    // ── Change approve / discard ───────────────────────────────────────────────
    async _applyChange(changeId, action) {
        const existing = this._items.find(
            i => i.kind === KIND.CHANGE_PREVIEW && i.changeId === changeId
        );
        if (!existing) return;
        this._mutateItem(existing.id, it => ({
            ...it, change: { ...it.change, status: action === 'apply' ? 'applying…' : 'discarding…' },
        }));
        const sessionHdr = this._sessionId ? { 'X-Session-Id': this._sessionId } : {};
        try {
            const res = await fetch(
                this._url('changes/' + changeId + '/' + action),
                { method: 'POST', headers: this._hdrs(sessionHdr) }
            );
            if (!res.ok) throw new Error(`${action} ${res.status}`);
            this._mutateItem(existing.id, it => ({
                ...it, change: { ...it.change, status: action === 'apply' ? 'applied' : 'discarded' },
            }));
        } catch (err) {
            this._pushError('Failed to ' + action + ' change: ' + err.message);
        }
        this._scheduleRender();
    }

    // ── Item helpers ───────────────────────────────────────────────────────────
    _id()               { return `sf-${++this._nextId}`; }
    _pushItem(item)     { this._items = [...this._items, item]; this._scheduleRender(); }
    _mutateItem(id, fn) {
        if (!id) return;
        this._items = this._items.map(it => it.id === id ? fn(it) : it);
    }
    _removeSkeleton() {
        if (!this._skeletonId) return;
        this._items = this._items.filter(i => i.id !== this._skeletonId);
        this._skeletonId = null;
    }
    _removeActivity() {
        if (!this._activityId) return;
        this._items = this._items.filter(i => i.id !== this._activityId);
        this._activityId = null;
    }
    _pushError(msg)         { this._pushItem({ kind: KIND.ERROR, id: this._id(), text: msg }); }
    _disableStarters(flag)  { this._starters = this._starters.map(s => ({ ...s, disabled: flag })); }

    _scheduleRender() {
        if (this._rafId) return;
        this._rafId = requestAnimationFrame(() => {
            this._rafId = null;
            const scroll = this.template.querySelector('.sf-scroll');
            if (scroll) scroll.scrollTop = scroll.scrollHeight;
        });
    }

    // ── URL / headers ──────────────────────────────────────────────────────────
    _url(path) {
        const base   = (this.apiUrl    ?? '').replace(/\/$/, '');
        const prefix = (this.apiPrefix ?? '/api/merchant').replace(/\/$/, '');
        return `${base}${prefix}/${path}`;
    }

    _hdrs(extra) {
        return {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream, application/json',
            ...extra,
        };
    }
}
