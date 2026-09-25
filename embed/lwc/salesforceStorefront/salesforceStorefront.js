import { LightningElement, api, track } from 'lwc';
import userId from '@salesforce/user/Id';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const STARTER_PROMPTS = [
    { id: 's1', label: 'Equip 10 new engineers — laptops, monitors, keyboards, mice.' },
    { id: 's2', label: 'Show me laptops under $1,500.' },
    { id: 's3', label: 'Compare workstations for heavy data workloads.' },
    { id: 's4', label: "What's the status of my recent order?" },
];

const KIND = {
    USER: 'user', TEXT: 'text', ACTIVITY: 'activity',
    SKELETON: 'skeleton', ERROR: 'error', CHIPS: 'chips',
    PRODUCTS: 'products', COMPARISON: 'comparison', PLAN: 'plan',
    GUIDE: 'guide', ORDER_STATUS: 'order_status', CHECKOUT: 'checkout',
    QUOTE: 'quote', ASSETS: 'assets', SUBSCRIPTIONS: 'subscriptions',
};

const OPEN_STATUSES = new Set(['pending', 'shipped', 'in_transit', 'processing', 'confirmed']);

// ─────────────────────────────────────────────────────────────────────────────
// Formatting helpers
// ─────────────────────────────────────────────────────────────────────────────

function fmtMoney(v, currency) {
    if (v == null) return '—';
    const sym = (currency && currency !== 'USD') ? currency + ' ' : '$';
    if (v >= 1000000) return sym + (v / 1000000).toFixed(1) + 'M';
    if (v >= 1000)    return sym + (v / 1000).toFixed(1) + 'K';
    return sym + Number(v).toFixed(2);
}

function fmtDate(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch (_) { return ''; }
}

function greetingWord() {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
}

function todayFull() {
    return new Date().toLocaleDateString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric',
    });
}

function titleCase(s) {
    return (s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function orderStatusClass(status) {
    const s = (status || '').replace(/\s+/g, '_');
    if (s === 'delivered')   return 'sfs-badge-delivered';
    if (s === 'delayed')     return 'sfs-badge-delayed';
    if (s === 'cancelled')   return 'sfs-badge-cancelled';
    if (s === 'returned')    return 'sfs-badge-cancelled';
    return 'sfs-badge-pending';
}

function resolveImageUrl(url, apiUrl) {
    if (!url) return null;
    if (url.startsWith('http')) return url;
    if (url.startsWith('/')) return (apiUrl || '').replace(/\/$/, '') + url;
    return url;
}

function productInitial(title) {
    return (title || 'P')[0].toUpperCase();
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export default class SalesforceStorefront extends LightningElement {
    @api apiUrl        = '';
    @api apiPrefix     = '/api';
    @api assistantName = 'Procurement Assistant';
    @api brandName     = 'IT Hardware Store';
    @api placeholder   = 'Ask about products, place orders, or check order status…';
    @api introText     = 'Your AI procurement assistant. Describe what your team needs and I\'ll find, compare, and order the right IT hardware.';

    @track _open         = false;
    @track _busy         = false;
    @track _listening    = false;
    @track _items        = [];
    @track _draft        = '';
    @track _starters     = STARTER_PROMPTS.map(s => ({ ...s, disabled: false }));
    @track _activeView   = 'assistant';
    @track _catalog      = [];
    @track _cart         = null;
    @track _orders       = [];
    @track _shopper      = null;
    @track _catalogReady = false;

    _sessionId     = null;
    _rafId         = null;
    _pendingTurnId = null;
    _activityId    = null;
    _skeletonId    = null;
    _abortCtrl     = null;
    _recognition   = null;
    _nextId        = 0;

    // ── Lifecycle ──────────────────────────────────────────────────────────────
    connectedCallback()    { this._bootstrap(); }
    disconnectedCallback() {
        if (this._abortCtrl)    this._abortCtrl.abort();
        if (this._recognition)  { this._recognition.abort(); this._recognition = null; }
    }

    async _bootstrap() {
        if (!(this.apiUrl ?? '').trim()) {
            this._pushError('API URL is not configured. Set the apiUrl property in Lightning App Builder.');
            return;
        }
        await this._createSession();
        // Load catalog, cart, and orders in parallel after session is ready
        Promise.all([this._loadCatalog(), this._loadCart(), this._loadOrders()]);
    }

    // ── Session ────────────────────────────────────────────────────────────────
    async _createSession() {
        try {
            const body_payload = userId ? JSON.stringify({ user_id: userId }) : undefined;
            const res = await fetch(this._url('session'), {
                method: 'POST',
                headers: this._hdrs(),
                body: body_payload,
            });
            if (!res.ok) throw new Error(`Session ${res.status}`);
            const body = await res.json();
            this._sessionId = body.session_id ?? body.id ?? null;
            this._shopper   = { name: body.name ?? 'Guest', tier: body.tier ?? null };
        } catch (err) {
            this._pushError('Could not start session: ' + err.message);
        }
    }

    // ── Data loading ───────────────────────────────────────────────────────────
    async _loadCatalog() {
        try {
            const res = await fetch(this._url('products') + '?limit=100', { headers: this._hdrs() });
            if (!res.ok) return;
            const data = await res.json();
            this._catalog      = data.products ?? data ?? [];
            this._catalogReady = true;
        } catch (_) { /* non-fatal */ }
    }

    async _loadCart() {
        if (!this._sessionId) return;
        try {
            const res = await fetch(this._url('cart'), { headers: this._hdrs(this._sessionHdrs()) });
            if (!res.ok) return;
            this._cart = await res.json();
        } catch (_) { /* non-fatal */ }
    }

    async _loadOrders() {
        if (!this._sessionId) return;
        try {
            const res = await fetch(this._url('orders'), { headers: this._hdrs(this._sessionHdrs()) });
            if (!res.ok) return;
            const data = await res.json();
            this._orders = Array.isArray(data) ? data : (data.orders ?? []);
        } catch (_) { /* non-fatal */ }
    }

    // ── Getters: general ───────────────────────────────────────────────────────
    get isOpen()       { return this._open; }
    get isBusy()       { return this._busy; }
    get draftValue()   { return this._draft; }
    get canSend()      { return !this._busy && this._draft.trim().length > 0; }
    get cannotSend()   { return !this.canSend; }
    get starters()     { return this._starters; }
    get greetingText() { return greetingWord(); }
    get dateText()     { return todayFull(); }
    get shopperName()  { return this._shopper?.name ?? 'Guest'; }

    get toggleClass()  { return this._busy ? 'sfs-toggle busy' : 'sfs-toggle'; }
    get actDotClass()  { return this._busy ? 'sfs-act-dot pulsing' : 'sfs-act-dot'; }
    get actLabel()     { return this._busy ? 'Active' : 'Ready'; }

    // ── Getters: voice ─────────────────────────────────────────────────────────
    get isListening()   { return this._listening; }
    get micClass()      { return 'sfs-mic-btn' + (this._listening ? ' listening' : ''); }
    get micAriaLabel()  { return this._listening ? 'Stop listening' : 'Speak your message'; }

    // ── Getters: view switching ────────────────────────────────────────────────
    // Home: no messages yet; Chat: after first message or while busy
    get showHome()     { return this._activeView === 'assistant' && this._items.length === 0 && !this._busy; }
    get showChat()     { return this._activeView === 'assistant' && (this._items.length > 0 || this._busy); }
    get showOrders()   { return this._activeView === 'orders'; }
    get showCart()     { return this._activeView === 'cart'; }

    get assistantTabClass() { return 'sfs-tab' + (this._activeView === 'assistant' ? ' active' : ''); }
    get ordersTabClass()    { return 'sfs-tab' + (this._activeView === 'orders'    ? ' active' : ''); }
    get cartTabClass()      { return 'sfs-tab' + (this._activeView === 'cart'      ? ' active' : ''); }

    // ── Getters: cart badge ────────────────────────────────────────────────────
    get cartCount()     { return this._cart?.item_count ?? 0; }
    get hasCartCount()  { return this.cartCount > 0; }
    get cartCountText() { return String(this.cartCount); }

    // ── Getters: orders alert badge ────────────────────────────────────────────
    get ordersLateCount()  { return this._orders.filter(o => o.status === 'delayed').length; }
    get hasOrdersAlert()   { return this.ordersLateCount > 0; }
    get ordersAlertText()  { return String(this.ordersLateCount); }

    // ── Getters: home — greeting brief ────────────────────────────────────────
    get briefText() {
        const open = this._orders.filter(o => OPEN_STATUSES.has(o.status));
        if (!open.length) return 'Ask about a product, a project, or a return.';
        const late = open.filter(o => o.status === 'delayed');
        const suffix = late.length
            ? ` ${late.length === 1 ? 'One is' : late.length + ' are'} running late.`
            : '';
        return `${open.length} order${open.length === 1 ? '' : 's'} on the way.${suffix}`;
    }

    // ── Getters: home — featured products ─────────────────────────────────────
    get featuredProducts() {
        const base = (this.apiUrl ?? '').replace(/\/$/, '');
        // Prefer labeled products; fall back to any in-stock products with images
        const labeled = this._catalog.filter(
            p => p.labels?.some(l => l === 'bestseller' || l === 'new') && p.in_stock !== false
        );
        const pool = labeled.length >= 4
            ? labeled
            : this._catalog.filter(p => p.in_stock !== false && p.image_url);
        return pool
            .sort((a, b) => Number(Boolean(b.image_url)) - Number(Boolean(a.image_url)))
            .slice(0, 4)
            .map((p, i) => {
                const imageUrl = resolveImageUrl(p.image_url, base);
                const badge    = p.labels?.includes('new') ? 'New' : p.labels?.includes('bestseller') ? 'Bestseller' : p.category || '';
                return {
                    uid:       `fp-${i}`,
                    title:     p.title,
                    brand:     p.brand || '',
                    hasBrand:  !!p.brand,
                    price:     fmtMoney(p.price, p.currency),
                    image_url: imageUrl || '',
                    hasImage:  !!imageUrl,
                    initial:   productInitial(p.title),
                    badge,
                    hasBadge:  !!badge,
                    badgeClass: 'sfs-tile-badge sfs-badge-' + (p.labels?.includes('new') ? 'new' : 'bestseller'),
                };
            });
    }

    get hasFeaturedProducts() { return this.featuredProducts.length > 0; }

    // ── Getters: home — arriving orders ───────────────────────────────────────
    get arrivingOrders() {
        return this._orders
            .filter(o => OPEN_STATUSES.has(o.status))
            .slice(0, 3)
            .map((o, i) => ({
                uid:         `ao-${i}`,
                id:          o.order_id,
                status:      titleCase(o.status),
                statusClass: 'sfs-order-badge ' + orderStatusClass(o.status),
                meta:        [fmtDate(o.placed_at), o.items ? `${o.items} items` : null].filter(Boolean).join(' · '),
            }));
    }

    get hasArrivingOrders() { return this.arrivingOrders.length > 0; }

    // ── Getters: orders view ───────────────────────────────────────────────────
    get orderViews() {
        return this._orders.slice(0, 12).map((o, i) => ({
            uid:         `ov-${i}`,
            id:          o.order_id,
            meta:        [fmtDate(o.placed_at), fmtMoney(o.total, o.currency)].filter(Boolean).join(' · '),
            statusLabel: titleCase(o.status || 'processing'),
            statusClass: 'sfs-order-badge ' + orderStatusClass(o.status),
        }));
    }

    get hasOrders()  { return this.orderViews.length > 0; }
    get noOrders()   { return !this.hasOrders; }

    // ── Getters: cart view ─────────────────────────────────────────────────────
    get cartLineItems() {
        const base     = (this.apiUrl ?? '').replace(/\/$/, '');
        const currency = this._cart?.currency;
        return (this._cart?.items ?? []).map((item, i) => {
            const imageUrl = resolveImageUrl(item.image_url, base);
            return {
                uid:       `ci-${i}`,
                title:     item.title,
                quantity:  item.quantity,
                price:     fmtMoney(item.price, currency),
                lineTotal: fmtMoney(item.line_total, currency),
                image_url: imageUrl || '',
                hasImage:  !!imageUrl,
                initial:   productInitial(item.title),
            };
        });
    }

    get hasCartItems() { return this.cartLineItems.length > 0; }
    get cartIsEmpty()  { return !this.hasCartItems; }
    get cartSubtotal() { return fmtMoney(this._cart?.subtotal ?? 0, this._cart?.currency); }
    get cartItemLabel() {
        const n = this.cartCount;
        return n === 1 ? '1 item' : `${n} items`;
    }

    // ── Getters: transcript ────────────────────────────────────────────────────
    get itemViews() {
        const apiBase = (this.apiUrl ?? '').replace(/\/$/, '');
        return this._items.map((it, idx) => {
            const base = {
                ...it,
                uid:           `${it.id}-${idx}`,
                isUser:        it.kind === KIND.USER,
                isText:        it.kind === KIND.TEXT,
                isActivity:    it.kind === KIND.ACTIVITY,
                isSkeleton:    it.kind === KIND.SKELETON,
                isError:       it.kind === KIND.ERROR,
                isChips:       it.kind === KIND.CHIPS,
                isProducts:    it.kind === KIND.PRODUCTS,
                isComparison:  it.kind === KIND.COMPARISON,
                isPlan:        it.kind === KIND.PLAN,
                isGuide:       it.kind === KIND.GUIDE,
                isOrderStatus:   it.kind === KIND.ORDER_STATUS,
                isCheckout:      it.kind === KIND.CHECKOUT,
                isQuote:         it.kind === KIND.QUOTE,
                isAssets:        it.kind === KIND.ASSETS,
                isSubscriptions: it.kind === KIND.SUBSCRIPTIONS,
            };

            if (base.isText) {
                base.textClass = it.streaming ? 'sfs-text streaming' : 'sfs-text';
            }

            if (base.isProducts) {
                base.blockTitle  = it.title || 'Products';
                base.hasTitle    = !!it.title;
                base.productCards = (it.items || []).map((entry, pi) => {
                    const p       = entry.product || entry;
                    const imgUrl  = resolveImageUrl(p.image_url, apiBase);
                    return {
                        uid:       `${it.id}-p${pi}`,
                        title:     p.title || '',
                        brand:     p.brand || '',
                        hasBrand:  !!p.brand,
                        price:     fmtMoney(p.price, p.currency),
                        image_url: imgUrl || '',
                        hasImage:  !!imgUrl,
                        initial:   productInitial(p.title),
                        inStock:   p.in_stock !== false,
                        outOfStock: p.in_stock === false,
                        reason:    entry.reason || '',
                        hasReason: !!entry.reason,
                    };
                });
                base.hasProducts = base.productCards.length > 0;
            }

            if (base.isComparison) {
                base.blockTitle   = it.title || 'Compare Products';
                base.compareCards = (it.entries || []).map((e, ci) => {
                    const p      = e.product || {};
                    const imgUrl = resolveImageUrl(p.image_url, apiBase);
                    return {
                        uid:           `${it.id}-c${ci}`,
                        product_id:    e.product_id || p.product_id || '',
                        title:         p.title || '',
                        price:         fmtMoney(p.price, p.currency),
                        image_url:     imgUrl || '',
                        hasImage:      !!imgUrl,
                        initial:       productInitial(p.title),
                        pros:          (e.pros || []).map((pro, pi2) => ({ uid: `${it.id}-c${ci}-pr${pi2}`, text: pro })),
                        cons:          (e.cons || []).map((con, ci2) => ({ uid: `${it.id}-c${ci}-cn${ci2}`, text: con })),
                        hasPros:       (e.pros || []).length > 0,
                        hasCons:       (e.cons || []).length > 0,
                        bestFor:       e.best_for || '',
                        hasBestFor:    !!e.best_for,
                        isRecommended: it.recommended_product_id && it.recommended_product_id === (e.product_id || p.product_id),
                    };
                });
                base.hasCompare = base.compareCards.length > 0;
            }

            if (base.isPlan) {
                base.blockTitle   = it.title || 'Recommendations';
                base.planIntro    = it.intro  || '';
                base.hasPlanIntro = !!it.intro;
                base.planSteps    = (it.steps || []).map((step, si) => ({
                    uid:         `${it.id}-s${si}`,
                    num:         si + 1,
                    label:       step.label || '',
                    detail:      step.detail || '',
                    hasDetail:   !!step.detail,
                    stepProducts: (step.products || []).slice(0, 2).map((p, spi) => ({
                        uid:   `${it.id}-s${si}-sp${spi}`,
                        title: p.title,
                        price: fmtMoney(p.price, p.currency),
                    })),
                    hasStepProducts: (step.products || []).length > 0,
                }));
            }

            if (base.isGuide) {
                base.blockTitle    = it.title || 'Guide';
                base.guideSections = (it.sections || []).map((sec, gi) => ({
                    uid:     `${it.id}-g${gi}`,
                    heading: sec.heading || '',
                    body:    sec.body    || '',
                }));
                base.relatedProducts = (it.related_products || []).slice(0, 2).map((p, rpi) => ({
                    uid:   `${it.id}-rp${rpi}`,
                    title: p.title,
                    price: fmtMoney(p.price, p.currency),
                }));
                base.hasRelated = (it.related_products || []).length > 0;
            }

            if (base.isOrderStatus) {
                const ord         = it.order || {};
                base.orderId      = it.order_id || ord.order_id || 'Order';
                base.summary      = it.summary  || '';
                base.nextStep     = it.next_step || '';
                base.hasNextStep  = !!it.next_step;
                base.orderStatus  = titleCase(ord.status || '');
                base.statusClass  = 'sfs-order-badge ' + orderStatusClass(ord.status);
                base.placedDate   = fmtDate(ord.placed_at);
                base.delivery     = ord.estimated_delivery ? fmtDate(ord.estimated_delivery) : '';
                base.hasDelivery  = !!ord.estimated_delivery;
                base.trackingUrl  = ord.tracking_url || '';
                base.hasTracking  = !!ord.tracking_url;
                base.orderLineItems = (ord.items || []).slice(0, 4).map((item, oi) => ({
                    uid:   `${it.id}-oi${oi}`,
                    title: item.title,
                    qty:   item.quantity,
                    price: fmtMoney(item.price, ord.currency),
                }));
                base.hasOrderLineItems = (ord.items || []).length > 0;
            }

            if (base.isCheckout) {
                const cartData      = it.cart || {};
                const currency      = cartData.currency;
                base.checkoutItems  = (cartData.items || []).slice(0, 5).map((item, chi) => {
                    const imgUrl = resolveImageUrl(item.image_url, apiBase);
                    return {
                        uid:      `${it.id}-ch${chi}`,
                        title:    item.title,
                        qty:      item.quantity,
                        total:    fmtMoney(item.line_total, currency),
                        image_url: imgUrl || '',
                        hasImage: !!imgUrl,
                        initial:  productInitial(item.title),
                    };
                });
                base.checkoutTotal   = fmtMoney(cartData.subtotal, currency);
                base.checkoutCount   = cartData.item_count ?? (cartData.items || []).length;
                base.checkoutNote    = it.note || '';
                base.hasNote         = !!it.note;
                base.handoffs        = (it.handoffs || []).map((h, hi) => ({
                    uid:   `${it.id}-ho${hi}`,
                    label: h.label || 'Proceed to checkout',
                    url:   h.url  || '#',
                }));
                base.hasHandoffs     = (it.handoffs || []).length > 0;
            }

            if (base.isQuote) {
                const q        = it.quote || {};
                const currency = q.currency || 'USD';
                const st       = (q.status || '').toLowerCase().replace(/_/g, ' ');
                const stMap    = { draft: 'sfs-badge-gray', 'needs review': 'sfs-badge-blue',
                                   submitted: 'sfs-badge-blue', approved: 'sfs-badge-green',
                                   rejected: 'sfs-badge-red', expired: 'sfs-badge-red' };
                base.quoteName      = q.name || it.quote_id || 'Quote';
                base.quoteStatus    = titleCase(st);
                base.quoteStatusCls = 'sfs-badge ' + (stMap[st] || 'sfs-badge-gray');
                base.quoteExpiry    = q.expiry_date ? fmtDate(q.expiry_date) : '';
                base.hasExpiry      = !!q.expiry_date;
                base.quoteSubtotal  = fmtMoney(q.subtotal, currency);
                base.quoteSummary   = it.summary || '';
                base.quoteNextStep  = it.next_step || '';
                base.hasQuoteNext   = !!it.next_step;
                base.quoteId       = it.quote_id || '';
                base.isDraft       = st === 'draft';
                base.isApproved    = st === 'approved';
                base.canAct        = base.isDraft || base.isApproved;
                base.quoteActionLabel = base.isDraft ? 'Submit for Review' : 'Add to Cart';
                base.quoteActionKey   = base.isDraft ? 'submit' : 'cart';
                base.quoteLineItems = (q.items || []).map((item, qi) => {
                    const imgUrl = resolveImageUrl(item.image_url, apiBase);
                    const qty    = item.quantity || 1;
                    return {
                        uid:        `${it.id}-ql${qi}`,
                        title:      item.title || 'Product',
                        qty,
                        qtyMinus:   qty - 1,
                        qtyPlus:    qty + 1,
                        atMin:      qty <= 1,
                        price:      fmtMoney(item.unit_price, currency),
                        total:      fmtMoney(item.line_total ?? (item.quantity * item.unit_price), currency),
                        image_url:  imgUrl || '',
                        hasImage:   !!imgUrl,
                        initial:    productInitial(item.title),
                        quoteId:    it.quote_id || '',
                        lineItemId: item.line_item_id || '',
                    };
                });
                base.hasQuoteItems = base.quoteLineItems.length > 0;
            }

            if (base.isAssets) {
                base.assetsTitle   = it.title || 'Installed Assets';
                base.warrantyAlert = it.warranty_alert || '';
                base.hasWarning    = !!it.warranty_alert;
                const today = new Date();
                base.assetCards = (it.entries || []).map((entry, ai) => {
                    const a      = entry.asset || {};
                    const expiry = a.warranty_expiry ? new Date(a.warranty_expiry) : null;
                    const daysLeft = expiry ? Math.ceil((expiry - today) / 86400000) : null;
                    const warnCls  = daysLeft === null ? '' : daysLeft < 0 ? 'sfs-asset-expired'
                                   : daysLeft < 90 ? 'sfs-asset-expiring' : 'sfs-asset-ok';
                    return {
                        uid:        `${it.id}-as${ai}`,
                        name:       a.name || entry.asset_id,
                        serial:     a.serial_number || '',
                        hasSerial:  !!a.serial_number,
                        category:   a.category || '',
                        highlight:  entry.highlight || '',
                        hasHighlight: !!entry.highlight,
                        warnCls,
                        warranty:   expiry ? fmtDate(a.warranty_expiry) : 'Unknown',
                        expired:    daysLeft !== null && daysLeft < 0,
                        expiringSoon: daysLeft !== null && daysLeft >= 0 && daysLeft < 90,
                    };
                });
                base.hasAssets = base.assetCards.length > 0;
            }

            if (base.isSubscriptions) {
                base.subsTitle    = it.title || 'Service Contracts';
                base.renewalAlert = it.renewal_alert || '';
                base.hasRenewal   = !!it.renewal_alert;
                const today2 = new Date();
                base.subCards = (it.entries || []).map((entry, si) => {
                    const s       = entry.subscription || {};
                    const end     = s.end_date ? new Date(s.end_date) : null;
                    const daysLeft2 = end ? Math.ceil((end - today2) / 86400000) : null;
                    const stSub   = (s.status || '').toLowerCase().replace(/_/g, ' ');
                    const stMap2  = { active: 'sfs-badge-green', 'expiring soon': 'sfs-badge-orange',
                                      expired: 'sfs-badge-red', cancelled: 'sfs-badge-gray' };
                    return {
                        uid:       `${it.id}-sc${si}`,
                        name:      s.name || entry.subscription_id,
                        status:    titleCase(stSub),
                        statusCls: 'sfs-badge ' + (stMap2[stSub] || 'sfs-badge-gray'),
                        endDate:   end ? fmtDate(s.end_date) : 'Unknown',
                        highlight: entry.highlight || '',
                        hasHighlight: !!entry.highlight,
                        expiringSoon2: daysLeft2 !== null && daysLeft2 >= 0 && daysLeft2 < 90,
                        expired2:  daysLeft2 !== null && daysLeft2 < 0,
                    };
                });
                base.hasSubs = base.subCards.length > 0;
            }

            return base;
        });
    }

    // ── Handlers: panel toggle + views ─────────────────────────────────────────
    handleToggle()        { this._open = !this._open; }
    handleClose()         { this._open = false; }
    handleViewAssistant() { this._activeView = 'assistant'; }
    handleViewOrders()    { this._activeView = 'orders'; }
    handleViewCart()      { this._activeView = 'cart'; this._loadCart(); }

    // ── Handlers: home actions ─────────────────────────────────────────────────
    handleFeaturedAsk(evt) {
        const title = evt.currentTarget.dataset.title;
        if (title) this._submit(`Tell me about the ${title}.`);
    }

    handleArrivingOrderAsk(evt) {
        const id = evt.currentTarget.dataset.id;
        if (id) this._submit(`What's the status of order ${id}?`);
    }

    handleSeeAllOrders() {
        this._submit('Show me all my recent orders.');
    }

    // ── Handlers: chat block actions ───────────────────────────────────────────
    handleProductAsk(evt) {
        const title = evt.currentTarget.dataset.title;
        if (title) this._submit(`Tell me more about the ${title}.`);
    }

    handleProductAdd(evt) {
        const title = evt.currentTarget.dataset.title;
        if (title) this._submit(`Add the ${title} to my cart.`);
    }

    handleStepProductAdd(evt) {
        const title = evt.currentTarget.dataset.title;
        if (title) this._submit(`Add the ${title} to my cart.`);
    }

    handleRelatedProductAsk(evt) {
        const title = evt.currentTarget.dataset.title;
        if (title) this._submit(`Tell me about the ${title}.`);
    }

    // ── Handlers: orders view ──────────────────────────────────────────────────
    handleOrderAsk(evt) {
        const id = evt.currentTarget.dataset.id;
        if (id) { this._activeView = 'assistant'; this._submit(`What's the status of order ${id}?`); }
    }

    // ── Handlers: cart view ────────────────────────────────────────────────────
    handleCheckout() {
        this._activeView = 'assistant';
        this._submit('Check out my cart.');
    }

    handleClearCart() {
        this._activeView = 'assistant';
        this._submit('Remove everything from my cart.');
    }

    handleCartAsk() {
        this._activeView = 'assistant';
        this._submit('Look over my cart — anything missing or worth swapping?');
    }

    // ── Handlers: quote card ──────────────────────────────────────────────────
    handleQtyMinus(evt) {
        const { quoteId, lineId, title, qty } = evt.currentTarget.dataset;
        const newQty = parseInt(qty, 10);
        if (newQty < 1) return;
        this._activeView = 'assistant';
        this._submit(`Change the quantity of ${title} to ${newQty} in my quote. [quote_id: ${quoteId}, line_item_id: ${lineId}]`);
    }

    handleQtyPlus(evt) {
        const { quoteId, lineId, title, qty } = evt.currentTarget.dataset;
        const newQty = parseInt(qty, 10);
        this._activeView = 'assistant';
        this._submit(`Change the quantity of ${title} to ${newQty} in my quote. [quote_id: ${quoteId}, line_item_id: ${lineId}]`);
    }

    handleQuoteAction(evt) {
        const { quoteId, quoteName, action } = evt.currentTarget.dataset;
        this._activeView = 'assistant';
        if (action === 'submit') {
            this._submit(`Submit quote ${quoteName} for review. [quote_id: ${quoteId}]`);
        } else {
            this._submit(`Load all items from approved quote ${quoteName} into my cart. [quote_id: ${quoteId}]`);
        }
    }

    // ── Handlers: composer ─────────────────────────────────────────────────────
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

    // ── Handlers: voice ────────────────────────────────────────────────────────
    handleMic() {
        if (this._listening) { this._stopListening(); } else { this._startListening(); }
    }

    _startListening() {
        try {
            // Locker Service wraps window — try the bare global names first
            /* eslint-disable no-undef */
            const SR = (typeof SpeechRecognition !== 'undefined' ? SpeechRecognition : null)
                    || (typeof webkitSpeechRecognition !== 'undefined' ? webkitSpeechRecognition : null)
                    || (window.SpeechRecognition)
                    || (window.webkitSpeechRecognition);
            /* eslint-enable no-undef */
            if (!SR) { this._pushError('Voice input is not supported in this browser.'); return; }
            this._recognition = new SR();
            this._recognition.continuous    = false;
            this._recognition.interimResults = true;
            this._recognition.lang          = 'en-US';

            this._recognition.onresult = (evt) => {
                const transcript = Array.from(evt.results)
                    .map(r => r[0].transcript).join('');
                this._draft = transcript;
                const ta = this.template.querySelector('textarea');
                if (ta) {
                    ta.value = transcript;
                    ta.style.height = 'auto';
                    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
                }
                if (evt.results[evt.results.length - 1].isFinal && transcript.trim()) {
                    this._stopListening();
                    this._submit(transcript.trim());
                }
            };
            this._recognition.onerror = () => { this._listening = false; this._recognition = null; };
            this._recognition.onend   = () => { this._listening = false; this._recognition = null; };

            this._recognition.start();
            this._listening = true;
        } catch (_) { this._listening = false; }
    }

    _stopListening() {
        this._listening = false;
        if (this._recognition) { try { this._recognition.stop(); } catch (_) {} this._recognition = null; }
    }

    // ── Submit ─────────────────────────────────────────────────────────────────
    _submit(text) {
        if (!text || this._busy) return;
        this._draft = '';
        const ta = this.template.querySelector('textarea');
        if (ta) { ta.value = ''; ta.style.height = 'auto'; }
        this._activeView = 'assistant';
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

        const fetchOpts = {
            method:  'POST',
            headers: this._hdrs(this._sessionHdrs()),
            body:    JSON.stringify({ message: text }),
        };
        if (this._abortCtrl) fetchOpts.signal = this._abortCtrl.signal;

        try {
            let res = await fetch(this._url('chat'), fetchOpts);
            if (res.status === 401) {
                // Session expired (e.g. server restarted) — reinitialize and retry once
                await this._createSession();
                fetchOpts.headers = this._hdrs(this._sessionHdrs());
                res = await fetch(this._url('chat'), fetchOpts);
            }
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
            // Reload orders so UI stays in sync; cart is updated via cart_update SSE events
            this._loadOrders();
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
                if (line.startsWith('event:')) { sseType = line.slice(6).trim(); continue; }
                if (!line.startsWith('data:')) continue;
                const raw = line.slice(5).trim();
                if (!raw || raw === '[DONE]') { sseType = null; continue; }
                try {
                    const ev = JSON.parse(raw);
                    if (!ev.type && sseType) ev.type = sseType;
                    this._handleEvent(ev);
                } catch (_) { /* parse error — skip */ }
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
            this._mutateItem(this._pendingTurnId, it => ({
                ...it, text: it.text + (ev.text ?? ev.delta ?? ''), streaming: true,
            }));
        } else if (t === 'progress' || t === 'tool_call') {
            const label = ev.message ?? ev.label ?? ev.tool ?? ev.name ?? 'Working…';
            if (!this._activityId) {
                const id = this._id();
                this._activityId = id;
                this._pushItem({ kind: KIND.ACTIVITY, id, label });
            } else {
                this._mutateItem(this._activityId, it => ({ ...it, label }));
            }
        } else if (t === 'ui' || t === 'ui_partial') {
            const block = ev.payload ? { type: ev.component, ...ev.payload } : (ev.block ?? ev);
            this._renderUIBlock(block, t === 'ui_partial');
        } else if (t === 'cart_update') {
            const cart = ev.data?.cart ?? ev.cart;
            if (cart && cart.items !== undefined) this._cart = cart;
        } else if (t === 'suggestions') {
            const chips = (ev.suggestions ?? []).map(s => ({ id: this._id(), label: s }));
            if (chips.length) this._pushItem({ kind: KIND.CHIPS, id: this._id(), chips });
        } else if (t === 'turn_complete') {
            if (this._pendingTurnId) {
                this._mutateItem(this._pendingTurnId, it => ({ ...it, streaming: false }));
            }
            this._removeActivity();
        } else if (t === 'error') {
            this._pushError(ev.message ?? 'An error occurred.');
        }

        this._scheduleRender();
    }

    // ── UI block renderer ──────────────────────────────────────────────────────
    _renderUIBlock(block, partial) {
        const comp = block.type ?? block.component;
        if (comp === 'products') {
            this._pushItem({ kind: KIND.PRODUCTS, id: this._id(), partial, title: block.title ?? null, items: block.items ?? [] });
        } else if (comp === 'comparison') {
            this._pushItem({ kind: KIND.COMPARISON, id: this._id(), partial, title: block.title ?? null, entries: block.entries ?? [], recommended_product_id: block.recommended_product_id ?? null });
        } else if (comp === 'plan') {
            this._pushItem({ kind: KIND.PLAN, id: this._id(), partial, title: block.title ?? null, intro: block.intro ?? null, steps: block.steps ?? [] });
        } else if (comp === 'guide') {
            this._pushItem({ kind: KIND.GUIDE, id: this._id(), partial, title: block.title ?? null, sections: block.sections ?? [], related_products: block.related_products ?? [] });
        } else if (comp === 'order_status') {
            if (!partial) {
                this._pushItem({ kind: KIND.ORDER_STATUS, id: this._id(), order_id: block.order_id ?? null, summary: block.summary ?? '', next_step: block.next_step ?? null, order: block.order ?? {} });
            }
        } else if (comp === 'checkout') {
            if (!partial) {
                this._pushItem({ kind: KIND.CHECKOUT, id: this._id(), cart: block.cart ?? {}, note: block.note ?? null, handoffs: block.handoffs ?? [] });
            }
        } else if (comp === 'quote') {
            if (!partial) {
                this._pushItem({ kind: KIND.QUOTE, id: this._id(), quote_id: block.quote_id ?? null, summary: block.summary ?? '', next_step: block.next_step ?? null, quote: block.quote ?? {} });
            }
        } else if (comp === 'assets') {
            if (!partial) {
                this._pushItem({ kind: KIND.ASSETS, id: this._id(), title: block.title ?? null, entries: block.entries ?? [], warranty_alert: block.warranty_alert ?? null });
            }
        } else if (comp === 'subscriptions') {
            if (!partial) {
                this._pushItem({ kind: KIND.SUBSCRIPTIONS, id: this._id(), title: block.title ?? null, entries: block.entries ?? [], renewal_alert: block.renewal_alert ?? null });
            }
        } else if (comp === 'suggestions') {
            const chips = (block.suggestions ?? []).map(s => ({ id: this._id(), label: s }));
            if (chips.length) this._pushItem({ kind: KIND.CHIPS, id: this._id(), chips });
        }
    }

    // ── Item helpers ───────────────────────────────────────────────────────────
    _id()               { return `sfs-${++this._nextId}`; }
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
    _pushError(msg)        { this._pushItem({ kind: KIND.ERROR, id: this._id(), text: msg }); }
    _disableStarters(flag) { this._starters = this._starters.map(s => ({ ...s, disabled: flag })); }

    _scheduleRender() {
        if (this._rafId) return;
        this._rafId = requestAnimationFrame(() => {
            this._rafId = null;
            const scroll = this.template.querySelector('.sfs-scroll');
            if (scroll) scroll.scrollTop = scroll.scrollHeight;
        });
    }

    // ── URL / headers ──────────────────────────────────────────────────────────
    _url(path) {
        const base   = (this.apiUrl    ?? '').replace(/\/$/, '');
        const prefix = (this.apiPrefix ?? '/api').replace(/\/$/, '');
        return `${base}${prefix}/${path}`;
    }

    _sessionHdrs() {
        return this._sessionId ? { 'X-Session-Id': this._sessionId } : {};
    }

    _hdrs(extra) {
        return { 'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json', ...(extra || {}) };
    }
}
