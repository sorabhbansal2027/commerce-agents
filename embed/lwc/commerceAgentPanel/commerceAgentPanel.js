import { LightningElement, api, track } from 'lwc';

const STARTER_PROMPTS = [
    { id: 'p1', label: "How are top categories performing this week?" },
    { id: 'p2', label: "Which products are low on stock?" },
    { id: 'p3', label: "Suggest a flash sale for slow-moving items." },
    { id: 'p4', label: "Summarise yesterday's order volume." },
];

const ACTION_CHIP_RE = /\b(approve|apply|dismiss|discard)\b/i;

const KIND = {
    USER: 'user', TEXT: 'text', ACTIVITY: 'activity',
    SKELETON: 'skeleton', CARD: 'card', CHANGE: 'change',
    ERROR: 'error', CHIPS: 'chips',
};

export default class CommerceAgentPanel extends LightningElement {
    @api apiUrl      = '';
    @api apiPrefix   = '/api/merchant';
    @api panelTitle  = 'Merchant assistant';
    @api introText   = 'Ask about performance, inventory, pricing, or campaigns.';
    @api placeholder = 'Ask about sales, stock, pricing…';

    @track _open      = false;
    @track _busy      = false;
    @track _items     = [];
    @track _draft     = '';
    @track _starters  = STARTER_PROMPTS.map(s => ({ ...s, disabled: false }));
    @track _showIntro = true;

    _sessionId      = null;
    _rafId          = null;
    _pendingTurnId  = null;
    _activityId     = null;
    _skeletonId     = null;
    _abortCtrl      = null;
    _nextId         = 0;

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
    get toggleClass() { return this._busy ? 'ca-toggle busy' : 'ca-toggle'; }
    get actDotClass() { return this._busy ? 'ca-act-dot pulsing' : 'ca-act-dot'; }
    get actLabel()    { return this._busy ? 'Active' : 'Ready'; }

    get itemViews() {
        return this._items.map((it, idx) => {
            const base = {
                ...it,
                uid:        `${it.id}-${idx}`,
                isUser:     it.kind === KIND.USER,
                isText:     it.kind === KIND.TEXT,
                isActivity: it.kind === KIND.ACTIVITY,
                isSkeleton: it.kind === KIND.SKELETON,
                isCard:     it.kind === KIND.CARD,
                isChange:   it.kind === KIND.CHANGE,
                isError:    it.kind === KIND.ERROR,
                isChips:    it.kind === KIND.CHIPS,
            };

            if (base.isText) {
                base.textClass = it.streaming ? 'ca-text streaming' : 'ca-text';
            }

            if (base.isCard) {
                base.dotClass = it.partial ? 'ca-card-dot dim' : 'ca-card-dot';
                const pairs = it.pairs || [];
                const rows  = it.rows  || [];
                base.hasPairs = pairs.length > 0;
                base.hasRows  = rows.length  > 0;
                base.pairs    = pairs.map((p, i) => ({ uid: `${it.id}-kv-${i}`, k: p.k, v: p.v }));
                base.rows     = rows.map((r, i)  => ({ uid: `${it.id}-rw-${i}`, idx: r.idx, title: r.title, detail: r.detail, hasDetail: !!r.detail }));
            }

            if (base.isChange) {
                const s = it.status ?? 'staged';
                base.isStaged    = s === 'staged';
                base.statusLabel = s.charAt(0).toUpperCase() + s.slice(1);
                base.statusClass = 'ca-status ' + s;
                base.hasFrom     = !!it.fromValue;
            }

            return base;
        });
    }

    // ── Toggle / close ─────────────────────────────────────────────────────────
    handleToggle()     { this._open = !this._open; }
    handleClose()      { this._open = false; }

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

    handleSend()             { this._submit(this._draft.trim()); }
    handleFormSubmit(evt)    { evt.preventDefault(); this._submit(this._draft.trim()); }
    handleStarter(evt)       { this._submit(evt.currentTarget.dataset.label); }
    handleChip(evt)          { this._submit(evt.currentTarget.dataset.label); }
    handleApprove(evt)       { this._applyChange(evt.currentTarget.dataset.id, 'apply'); }
    handleDiscard(evt)       { this._applyChange(evt.currentTarget.dataset.id, 'discard'); }

    // ── Session ────────────────────────────────────────────────────────────────
    async _createSession() {
        try {
            const res = await fetch(this._url('session'), { method: 'POST', headers: this._hdrs() });
            if (!res.ok) throw new Error(`Session ${res.status}`);
            const body = await res.json();
            this._sessionId = body.session_id ?? body.id ?? null;
        } catch (err) {
            this._pushError(`Could not start session: ${err.message}`);
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
        this._abortCtrl = new AbortController();

        const skelId = this._id();
        this._skeletonId = skelId;
        this._pushItem({ kind: KIND.SKELETON, id: skelId });

        const payload = { message: text };
        if (this._sessionId) payload.session_id = this._sessionId;

        try {
            const res = await fetch(this._url('chat'), {
                method: 'POST',
                headers: this._hdrs(),
                body: JSON.stringify(payload),
                signal: this._abortCtrl.signal,
            });
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
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let nl;
            while ((nl = buf.indexOf('\n')) !== -1) {
                const line = buf.slice(0, nl).trimEnd();
                buf = buf.slice(nl + 1);
                if (!line.startsWith('data:')) continue;
                const raw = line.slice(5).trim();
                if (!raw || raw === '[DONE]') continue;
                try { this._handleEvent(JSON.parse(raw)); } catch (_) { /* parse error */ }
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
            this._mutateItem(this._pendingTurnId, it => ({ ...it, text: it.text + (ev.delta ?? ''), streaming: true }));
        }
        else if (t === 'progress' || t === 'tool_call') {
            const label = ev.label ?? ev.tool ?? ev.name ?? 'Working…';
            if (!this._activityId) {
                const id = this._id();
                this._activityId = id;
                this._pushItem({ kind: KIND.ACTIVITY, id, label });
            } else {
                this._mutateItem(this._activityId, it => ({ ...it, label }));
            }
        }
        else if (t === 'ui' || t === 'ui_partial') {
            this._renderUIBlock(ev.block ?? ev, t === 'ui_partial');
        }
        else if (t === 'change_update') {
            const ch = ev.change ?? ev;
            const existing = this._items.find(i => i.kind === KIND.CHANGE && i.changeId === ch.id);
            if (existing) {
                this._mutateItem(existing.id, it => ({ ...it, status: ch.status ?? it.status }));
            } else {
                this._pushItem({ kind: KIND.CHANGE, id: this._id(), changeId: ch.id, name: ch.name ?? 'Change', status: ch.status ?? 'staged', fromValue: ch.from ?? '', toValue: ch.to ?? '' });
            }
        }
        else if (t === 'suggestions') {
            const chips = (ev.suggestions ?? [])
                .filter(s => !ACTION_CHIP_RE.test(s))
                .map(s => ({ id: this._id(), label: s }));
            if (chips.length) this._pushItem({ kind: KIND.CHIPS, id: this._id(), chips });
        }
        else if (t === 'turn_complete') {
            if (this._pendingTurnId) this._mutateItem(this._pendingTurnId, it => ({ ...it, streaming: false }));
            this._removeActivity();
        }
        else if (t === 'error') {
            this._pushError(ev.message ?? 'An error occurred.');
        }

        this._scheduleRender();
    }

    _renderUIBlock(block, partial) {
        const btype = block.type ?? block.block_type;
        if (btype === 'kv_list' || btype === 'stat_group') {
            this._pushItem({ kind: KIND.CARD, id: this._id(), partial, header: block.title ?? block.header ?? 'Data', pairs: (block.items ?? block.stats ?? []).map(it => ({ k: it.label ?? it.key ?? '', v: it.value ?? '' })), rows: [] });
        } else if (btype === 'ranked_list' || btype === 'product_list') {
            this._pushItem({ kind: KIND.CARD, id: this._id(), partial, header: block.title ?? block.header ?? 'List', pairs: [], rows: (block.items ?? []).map((it, i) => ({ idx: i + 1, title: it.name ?? it.title ?? '', detail: it.detail ?? it.subtitle ?? '' })) });
        }
    }

    // ── Change apply / discard ─────────────────────────────────────────────────
    async _applyChange(changeId, action) {
        const existing = this._items.find(i => i.kind === KIND.CHANGE && i.changeId === changeId);
        if (!existing) return;
        this._mutateItem(existing.id, it => ({ ...it, status: action === 'apply' ? 'applying…' : 'discarding…' }));
        try {
            const res = await fetch(this._url(`changes/${changeId}/${action}`), { method: 'POST', headers: this._hdrs() });
            if (!res.ok) throw new Error(`${action} ${res.status}`);
            this._mutateItem(existing.id, it => ({ ...it, status: action === 'apply' ? 'applied' : 'discarded' }));
        } catch (err) {
            this._pushError(`Failed to ${action} change: ${err.message}`);
        }
        this._scheduleRender();
    }

    // ── Item helpers ───────────────────────────────────────────────────────────
    _id()             { return `ca-${++this._nextId}`; }
    _pushItem(item)   { this._items = [...this._items, item]; this._scheduleRender(); }
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
            const scroll = this.template.querySelector('.ca-scroll');
            if (scroll) scroll.scrollTop = scroll.scrollHeight;
        });
    }

    // ── URL / headers ──────────────────────────────────────────────────────────
    _url(path) {
        const base   = (this.apiUrl    ?? '').replace(/\/$/, '');
        const prefix = (this.apiPrefix ?? '/api/merchant').replace(/\/$/, '');
        return `${base}${prefix}/${path}`;
    }

    _hdrs() {
        return { 'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json' };
    }
}
