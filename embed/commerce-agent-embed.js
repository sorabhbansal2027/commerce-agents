// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * commerce-agent-embed.js — Merchant assistant as a script-tag widget.
 *
 * Drop one <script> tag into any page — Salesforce Experience Cloud, Visualforce,
 * or any HTML — to get the merchant assistant panel with the same look and feel
 * as the Next.js portal.
 *
 * Usage:
 *   <script src="/embed/commerce-agent-embed.js"
 *           data-api="https://your-api.example.com"
 *           data-prefix="/api/merchant"
 *           data-title="Merchant assistant"
 *           data-operator="Jane Smith"
 *           data-starters='["What needs my attention?","Show low stock"]'>
 *   </script>
 *
 * Attributes (all optional except data-api):
 *   data-api          API root URL (required)
 *   data-prefix       Route prefix, default "/api/merchant"
 *   data-title        Panel header title
 *   data-intro        Empty-state intro text
 *   data-placeholder  Composer placeholder
 *   data-starters     JSON array of starter prompt strings
 *
 * Salesforce notes:
 *   1. Add the API origin to Setup → CSP Trusted Sites (connect-src) and
 *      Remote Site Settings.
 *   2. The widget uses Shadow DOM, so Salesforce LWS/LockerService cannot
 *      interfere with its styles or globals.
 *   3. Streaming uses fetch + ReadableStream (not EventSource), which works
 *      inside Salesforce's restricted iframe sandbox.
 */
(function () {
  'use strict';

  // ── Config ──────────────────────────────────────────────────────────────────
  const script = document.currentScript;
  const API_ROOT    = (script.getAttribute('data-api') || '').replace(/\/$/, '');
  const API_PREFIX  = script.getAttribute('data-prefix')      || '/api/merchant';
  const BASE        = API_ROOT + API_PREFIX;
  const TITLE       = script.getAttribute('data-title')       || 'Merchant assistant';
  const INTRO       = script.getAttribute('data-intro')       || 'Ask about performance, inventory, pricing, or campaigns.';
  const PLACEHOLDER = script.getAttribute('data-placeholder') || 'Ask about sales, stock, pricing…';
  const STARTERS    = (() => {
    try { return JSON.parse(script.getAttribute('data-starters') || ''); } catch {}
    return [
      'What needs my attention this morning?',
      'How did sales do this week compared to last?',
      'Which listings are running low on stock?',
      'Which slow movers should we mark down?',
    ];
  })();

  const SESSION_HEADER = 'X-Session-Id';
  // Chips that only make sense with a full portal (approve/discard buttons live on the card)
  const ACTION_CHIP_RE = /\b(approve|apply|dismiss|discard)\b/i;

  // ── SVG icons ───────────────────────────────────────────────────────────────
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const ICON_PATHS = {
    spark:        'M9.5 3C9.5 3 9 6 7 8C5 10 2 10.5 2 10.5C2 10.5 5 11 7 13C9 15 9.5 18 9.5 18C9.5 18 10 15 12 13C14 11 17 10.5 17 10.5C17 10.5 14 10 12 8C10 6 9.5 3 9.5 3Z M15 2C15 2 14.7 3.8 13.5 5C12.3 6.2 10.5 6.5 10.5 6.5C10.5 6.5 12.3 6.8 13.5 8C14.7 9.2 15 11 15 11C15 11 15.3 9.2 16.5 8C17.7 6.8 19.5 6.5 19.5 6.5C19.5 6.5 17.7 6.2 16.5 5C15.3 3.8 15 2 15 2Z',
    'arrow-up':   'M12 19V5M5 12l7-7 7 7',
    'arrow-right':'M5 12h14M12 5l7 7-7 7',
    x:            'M18 6 6 18M6 6l12 12',
    check:        'M20 6 9 17l-5-5',
    ban:          'M18.364 5.636 5.636 18.364M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z',
  };

  function mkIcon(name, size = 16) {
    const filled = name === 'spark';
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('width',          String(size));
    svg.setAttribute('height',         String(size));
    svg.setAttribute('viewBox',        '0 0 24 24');
    svg.setAttribute('fill',           filled ? 'currentColor' : 'none');
    svg.setAttribute('stroke',         filled ? 'none' : 'currentColor');
    svg.setAttribute('stroke-width',   '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin','round');
    svg.setAttribute('aria-hidden',    'true');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', ICON_PATHS[name] || '');
    svg.appendChild(path);
    return svg;
  }

  // ── CSS — merchant design tokens + component styles ─────────────────────────
  // Tokens match examples/retail/merchant-web/app/globals.css exactly.
  // All styles live in the shadow root so Salesforce LWS cannot bleed in or out.
  const CSS = `
    :host {
      --ground:        #f4f5f8;
      --surface:       #ffffff;
      --card:          #ffffff;
      --well:          #eef1f5;
      --ink:           #1e2c4f;
      --ink-2:         #3b4763;
      --ink-soft:      #66708a;
      --ink-faint:     #9aa2b5;
      --line:          rgba(30,44,79,.10);
      --line-strong:   rgba(30,44,79,.17);
      --accent:        #d97f5f;
      --accent-strong: #c4643f;
      --accent-soft:   #faece5;
      --accent-ink:    #8a3f22;
      --on-accent:     #ffffff;
      --ok:            #1f8f5f;
      --ok-soft:       #e3f4ec;
      --warn:          #b06a00;
      --warn-soft:     #fdf1da;
      --danger:        #c9252d;
      --danger-soft:   #fce9ea;
      --violet:        #6b4fd6;
      --violet-soft:   #eeeafd;
      --shadow-sm:     0 1px 2px rgba(30,44,79,.06),0 1px 1px rgba(30,44,79,.04);
      --shadow:        0 1px 2px rgba(30,44,79,.06),0 4px 14px -6px rgba(30,44,79,.14);
      --shadow-lg:     0 12px 40px -12px rgba(30,44,79,.28),0 2px 6px rgba(30,44,79,.08);
      --radius:        12px;
      font-family: "Instrument Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      font-size: 14px;
      line-height: 1.4;
      -webkit-font-smoothing: antialiased;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* ── Toggle button ─────────────────────────────────────────────────────── */
    #ca-toggle {
      position: fixed;
      bottom: 24px; right: 24px;
      z-index: 9998;
      display: flex; align-items: center; gap: 8px;
      padding: 10px 16px 10px 12px;
      border-radius: 999px; border: none;
      background: var(--ink); color: var(--surface);
      font-family: inherit; font-size: 14px; font-weight: 600;
      cursor: pointer;
      box-shadow: var(--shadow-lg);
      transition: transform 120ms, background 150ms;
    }
    #ca-toggle:hover { background: var(--ink-2); transform: scale(1.03); }
    .ca-toggle-spark { color: var(--accent); }
    .ca-toggle-ping  { position: relative; width: 8px; height: 8px; }
    .ca-toggle-ping::after {
      content: '';
      position: absolute; inset: 0;
      border-radius: 50%; background: var(--accent);
    }
    #ca-toggle.busy .ca-toggle-ping::before {
      content: '';
      position: absolute; inset: 0;
      border-radius: 50%; background: var(--accent);
      animation: ca-ping 1.4s ease-in-out infinite;
    }

    /* ── Panel ─────────────────────────────────────────────────────────────── */
    #ca-panel {
      position: fixed;
      bottom: 24px; right: 24px;
      z-index: 9999;
      width: 380px; height: 600px;
      max-height: calc(100dvh - 48px);
      display: flex; flex-direction: column;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--card);
      box-shadow: var(--shadow-lg);
      overflow: hidden;
      transform-origin: bottom right;
      animation: ca-open 220ms cubic-bezier(0.25,0.8,0.3,1) both;
    }
    #ca-panel[hidden] { display: none !important; }

    @media (max-width: 440px) {
      #ca-panel  { bottom:0; right:0; width:100vw; height:100dvh; max-height:100dvh; border-radius:0; border:none; }
      #ca-toggle { bottom:16px; right:16px; }
    }

    /* ── Header ────────────────────────────────────────────────────────────── */
    #ca-header {
      display: flex; align-items: center; gap: 10px;
      padding: 12px 10px 12px 16px;
      border-bottom: 1px solid var(--line);
      flex-shrink: 0;
    }
    .ca-hmark {
      display: grid; place-items: center;
      width: 30px; height: 30px; border-radius: 10px;
      background: var(--accent); color: var(--on-accent);
      flex-shrink: 0;
    }
    .ca-htext { flex: 1; min-width: 0; }
    .ca-htitle {
      font-size: 14px; font-weight: 600; color: var(--ink);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .ca-hsub { font-size: 11.5px; color: var(--ink-soft); }

    .ca-act-badge {
      display: flex; align-items: center; gap: 5px;
      padding: 4px 8px; border-radius: 999px;
      border: 1px solid var(--line-strong);
      font-size: 12px; font-weight: 500; color: var(--ink-soft);
      flex-shrink: 0;
    }
    .ca-act-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
    .ca-act-dot.pulsing { animation: ca-pulse 1s ease-in-out infinite; }

    .ca-icon-btn {
      display: grid; place-items: center;
      width: 30px; height: 30px; border-radius: 8px;
      border: none; background: transparent; color: var(--ink-soft);
      cursor: pointer; font-family: inherit; flex-shrink: 0;
      transition: background 120ms, color 120ms;
    }
    .ca-icon-btn:hover { background: var(--well); color: var(--ink); }

    /* ── Scroll area ───────────────────────────────────────────────────────── */
    #ca-scroll {
      flex: 1; min-height: 0; overflow-y: auto;
      padding: 16px;
      scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent;
    }
    #ca-scroll::-webkit-scrollbar { width: 6px; }
    #ca-scroll::-webkit-scrollbar-thumb { border-radius: 999px; background: var(--line-strong); }
    #ca-scroll::-webkit-scrollbar-track { background: transparent; }

    /* ── Empty state ───────────────────────────────────────────────────────── */
    .ca-intro { font-size: 16px; line-height: 1.55; color: var(--ink); margin: 8px 0 16px; }
    .ca-starters { display: flex; flex-direction: column; gap: 8px; }
    .ca-starter {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 14px; border-radius: 12px;
      border: 1px solid var(--line); background: var(--card);
      font-family: inherit; font-size: 13.5px; color: var(--ink);
      text-align: left; cursor: pointer;
      box-shadow: var(--shadow-sm);
      transition: border-color 150ms, background 150ms, color 150ms;
    }
    .ca-starter:hover:not(:disabled) { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-ink); }
    .ca-starter:disabled { opacity: 0.5; cursor: not-allowed; }
    .ca-starter-spark { color: var(--accent); flex-shrink: 0; }
    .ca-starter-label { flex: 1; min-width: 0; }
    .ca-starter-arrow { color: var(--ink-faint); flex-shrink: 0; transition: color 150ms; }
    .ca-starter:hover:not(:disabled) .ca-starter-arrow { color: var(--accent-ink); }

    /* ── Transcript ────────────────────────────────────────────────────────── */
    .ca-turn { display: flex; flex-direction: column; gap: 8px; }
    .ca-turn + .ca-turn { margin-top: 4px; }

    .ca-user-row  { display: flex; justify-content: flex-end; }
    .ca-user-bubble {
      max-width: 72%; padding: 8px 14px; font-size: 14.5px; line-height: 1.45;
      border-radius: 16px 16px 5px 16px;
      background: var(--ink); color: var(--surface);
    }

    /* Mirrors portal/portal.css `.chip` and `MessageBubble.tsx` */
    .ca-text { font-size: 14.5px; line-height: 1.6; color: var(--ink); }
    .ca-text.streaming::after {
      content: "▎"; margin-left: 1px; color: var(--ink);
      animation: ca-blink 1s steps(1) infinite;
    }
    .ca-text strong { font-weight: 600; }
    .ca-text em     { font-style: italic; }
    .ca-text code {
      font-family: ui-monospace,"Cascadia Code",monospace; font-size: .88em;
      padding: 1px 5px; border-radius: 4px;
      background: var(--well); color: var(--accent-ink);
    }
    .ca-text p { margin: 0 0 8px; }
    .ca-text p:last-child { margin-bottom: 0; }
    .ca-text ul, .ca-text ol { padding-left: 18px; margin: 0 0 8px; }
    .ca-text li { margin-bottom: 2px; }

    .ca-error {
      padding: 8px 12px; border-radius: 8px;
      border: 1px solid rgba(201,37,45,.4); background: var(--danger-soft);
      font-size: 13px; color: var(--danger);
    }

    /* Activity line + skeleton — mirrors Transcript.tsx `ActivityLine` */
    .ca-activity {
      display: flex; align-items: center; gap: 8px;
      font-size: 13px; color: var(--ink-soft);
    }
    .ca-activity-pulse {
      width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
      animation: ca-pulse 1s ease-in-out infinite; flex-shrink: 0;
    }
    .ca-activity-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ca-skeleton-wrap { display: flex; flex-direction: column; gap: 8px; }
    .ca-skeleton {
      height: 16px; border-radius: 6px;
      background: linear-gradient(100deg, var(--well) 35%, var(--card) 50%, var(--well) 65%);
      background-size: 200% 100%;
      animation: ca-shimmer 1.4s linear infinite;
    }

    /* ── Generative data cards ─────────────────────────────────────────────── */
    .ca-card {
      border: 1px solid var(--line); border-radius: var(--radius);
      background: var(--card); box-shadow: var(--shadow-sm); overflow: hidden;
      animation: ca-reveal 240ms cubic-bezier(0.25,0.8,0.3,1) both;
    }
    .ca-card-hdr {
      display: flex; align-items: center; gap: 8px;
      padding: 9px 14px; border-bottom: 1px solid var(--line);
      font-size: 11.5px; font-weight: 700; color: var(--ink-soft);
      text-transform: uppercase; letter-spacing: .05em;
    }
    .ca-card-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); flex-shrink: 0; }
    .ca-card-dot.dim { opacity: .35; }
    .ca-card-body { padding: 12px 14px; }

    /* Key-value grid (flat payloads) */
    .ca-kv { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; font-size: 13px; }
    .ca-kv-k { color: var(--ink-soft); }
    .ca-kv-v { color: var(--ink); font-weight: 500; text-align: right; }

    /* List items (array payloads) */
    .ca-list { display: flex; flex-direction: column; gap: 5px; }
    .ca-list-row {
      display: flex; gap: 8px; padding: 7px 10px;
      border-radius: 8px; background: var(--well); font-size: 13px; color: var(--ink);
    }
    .ca-list-idx  { font-size: 11px; font-weight: 700; color: var(--ink-soft); padding-top: 1px; flex-shrink: 0; }
    .ca-list-body { flex: 1; min-width: 0; }
    .ca-list-title  { font-weight: 500; }
    .ca-list-detail { font-size: 12px; color: var(--ink-soft); margin-top: 1px; }

    /* ── Change-preview card — mirrors portal ChangePreviewCard ────────────── */
    .ca-change-hdr {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding: 10px 14px; border-bottom: 1px solid var(--line);
    }
    .ca-change-name { font-size: 12.5px; font-weight: 600; color: var(--ink); }
    .ca-status {
      font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
      text-transform: uppercase; letter-spacing: .04em;
    }
    .ca-status.staged    { background: var(--violet-soft); color: var(--violet); }
    .ca-status.applied   { background: var(--ok-soft);     color: var(--ok); }
    .ca-status.discarded { background: var(--well);         color: var(--ink-soft); }
    .ca-change-diff { padding: 10px 14px; }
    .ca-diff-from { font-size: 12px; color: var(--ink-soft); text-decoration: line-through; margin-bottom: 4px; }
    .ca-diff-to   { font-size: 13.5px; font-weight: 500; color: var(--ink); }
    .ca-change-actions {
      display: flex; gap: 8px; padding: 10px 14px; border-top: 1px solid var(--line);
    }
    .ca-btn-approve {
      flex: 1; display: flex; align-items: center; justify-content: center; gap: 6px;
      padding: 7px 12px; border-radius: 8px; border: none;
      background: var(--accent-strong); color: var(--on-accent);
      font-family: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
      transition: opacity 150ms;
    }
    .ca-btn-approve:hover:not(:disabled) { opacity: .88; }
    .ca-btn-approve:disabled { opacity: .4; cursor: not-allowed; }
    .ca-btn-discard {
      flex: 1; display: flex; align-items: center; justify-content: center; gap: 6px;
      padding: 7px 12px; border-radius: 8px;
      border: 1px solid var(--line-strong); background: var(--card); color: var(--ink-2);
      font-family: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
      transition: background 150ms, border-color 150ms;
    }
    .ca-btn-discard:hover:not(:disabled) { background: var(--well); border-color: var(--ink-2); }
    .ca-btn-discard:disabled { opacity: .4; cursor: not-allowed; }

    /* ── Suggestion chips — mirrors portal/portal.css `.chip` ─────────────── */
    .ca-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
    .ca-chip {
      display: inline-flex; align-items: center;
      border-radius: 999px; border: 1px solid var(--line-strong); background: var(--card);
      padding: 4px 12px; font-family: inherit; font-size: 12.5px; font-weight: 600; color: var(--ink);
      cursor: pointer;
      transition: border-color 150ms, background 150ms, color 150ms;
    }
    .ca-chip:hover:not(:disabled) { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-ink); }
    .ca-chip:disabled { opacity: .5; cursor: not-allowed; }

    /* ── Composer ──────────────────────────────────────────────────────────── */
    #ca-footer { flex-shrink: 0; padding: 12px; border-top: 1px solid var(--line); }
    .ca-form {
      display: flex; align-items: center; gap: 6px;
      padding: 5px 5px 5px 14px; border-radius: 14px;
      border: 1px solid var(--line-strong); background: var(--card);
      box-shadow: var(--shadow-sm);
      transition: border-color 150ms;
    }
    .ca-form:focus-within { border-color: var(--accent); }
    #ca-textarea {
      flex: 1; min-width: 0; resize: none; max-height: 120px;
      border: none; background: transparent; outline: none;
      font-family: inherit; font-size: 14.5px; color: var(--ink); padding: 6px 0;
    }
    #ca-textarea::placeholder { color: rgba(102,112,138,.7); }
    #ca-send {
      display: grid; place-items: center;
      width: 32px; height: 32px; border-radius: 10px;
      border: none; background: var(--ink); color: var(--surface);
      cursor: pointer; flex-shrink: 0;
      transition: opacity 150ms, transform 120ms;
    }
    #ca-send:disabled { opacity: .35; cursor: not-allowed; }
    #ca-send:not(:disabled):hover { transform: scale(1.06); }

    /* ── Keyframe animations ───────────────────────────────────────────────── */
    @keyframes ca-open    { from{opacity:0;transform:scale(.92) translateY(8px)} to{opacity:1;transform:none} }
    @keyframes ca-reveal  { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:none} }
    @keyframes ca-blink   { 0%,100%{opacity:1} 50%{opacity:0} }
    @keyframes ca-pulse   { 0%,100%{opacity:1} 50%{opacity:.3} }
    @keyframes ca-ping    { 0%{transform:scale(1);opacity:.7} 80%,100%{transform:scale(2.4);opacity:0} }
    @keyframes ca-shimmer { from{background-position:200% 0} to{background-position:-200% 0} }

    @media (prefers-reduced-motion: reduce) {
      .ca-activity-pulse, .ca-act-dot.pulsing, .ca-skeleton,
      #ca-toggle.busy .ca-toggle-ping::before { animation: none; }
    }
  `;

  // ── Shadow DOM ──────────────────────────────────────────────────────────────
  const host = document.createElement('div');
  const shadow = host.attachShadow({ mode: 'open' });
  document.body.appendChild(host);
  const styleEl = document.createElement('style');
  styleEl.textContent = CSS;
  shadow.appendChild(styleEl);

  // ── State ───────────────────────────────────────────────────────────────────
  let sessionId    = null;
  let busy         = false;
  let panelOpen    = false;
  let items        = [];   // {kind:'user',text} | {kind:'assistant',turn,segments,suggestions,changeIds,pending,activity,tools}
  let turnCounter  = 0;
  let renderQueued = false;

  // ── Build static DOM ────────────────────────────────────────────────────────

  // Toggle button (always visible)
  const toggleBtn = document.createElement('button');
  toggleBtn.id = 'ca-toggle';
  toggleBtn.setAttribute('aria-label', 'Open merchant assistant');
  toggleBtn.setAttribute('aria-expanded', 'false');
  const tSpark = mkIcon('spark', 16);
  tSpark.classList.add('ca-toggle-spark');
  toggleBtn.appendChild(tSpark);
  const tLabel = document.createElement('span');
  tLabel.textContent = 'Assistant';
  toggleBtn.appendChild(tLabel);
  const tPing = document.createElement('span');
  tPing.className = 'ca-toggle-ping';
  tPing.setAttribute('aria-hidden', 'true');
  toggleBtn.appendChild(tPing);
  shadow.appendChild(toggleBtn);

  // Panel
  const panel = document.createElement('div');
  panel.id = 'ca-panel';
  panel.hidden = true;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'false');
  panel.setAttribute('aria-label', TITLE);
  shadow.appendChild(panel);

  // Header
  const panelHeader = document.createElement('div');
  panelHeader.id = 'ca-header';

  const hMark = document.createElement('span');
  hMark.className = 'ca-hmark';
  hMark.setAttribute('aria-hidden', 'true');
  hMark.appendChild(mkIcon('spark', 15));

  const hText = document.createElement('div');
  hText.className = 'ca-htext';
  const hTitle = document.createElement('div');
  hTitle.className = 'ca-htitle';
  hTitle.textContent = TITLE;
  const hSub = document.createElement('div');
  hSub.className = 'ca-hsub';
  hSub.textContent = 'You approve every change';
  hText.appendChild(hTitle);
  hText.appendChild(hSub);

  const actBadge = document.createElement('div');
  actBadge.className = 'ca-act-badge';
  actBadge.setAttribute('aria-live', 'polite');
  const actDot = document.createElement('span');
  actDot.className = 'ca-act-dot';
  const actText = document.createElement('span');
  actText.textContent = 'Activity';
  actBadge.appendChild(actDot);
  actBadge.appendChild(actText);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'ca-icon-btn';
  closeBtn.setAttribute('aria-label', 'Hide assistant');
  closeBtn.appendChild(mkIcon('x', 16));
  closeBtn.addEventListener('click', closePanel);

  panelHeader.appendChild(hMark);
  panelHeader.appendChild(hText);
  panelHeader.appendChild(actBadge);
  panelHeader.appendChild(closeBtn);
  panel.appendChild(panelHeader);

  // Scroll / transcript area
  const scrollEl = document.createElement('div');
  scrollEl.id = 'ca-scroll';
  scrollEl.setAttribute('role', 'log');
  scrollEl.setAttribute('aria-live', 'polite');
  scrollEl.setAttribute('aria-atomic', 'false');
  panel.appendChild(scrollEl);

  // Footer / composer
  const footer = document.createElement('div');
  footer.id = 'ca-footer';
  const form = document.createElement('form');
  form.className = 'ca-form';
  form.setAttribute('role', 'form');

  const textarea = document.createElement('textarea');
  textarea.id = 'ca-textarea';
  textarea.rows = 1;
  textarea.setAttribute('aria-label', 'Message the merchant assistant');
  textarea.placeholder = PLACEHOLDER;

  const sendBtn = document.createElement('button');
  sendBtn.id = 'ca-send';
  sendBtn.type = 'submit';
  sendBtn.setAttribute('aria-label', 'Send');
  sendBtn.disabled = true;
  sendBtn.appendChild(mkIcon('arrow-up', 16));

  textarea.addEventListener('input', () => {
    sendBtn.disabled = busy || !sessionId || !textarea.value.trim();
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
  });
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!sendBtn.disabled) form.requestSubmit();
    }
  });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = textarea.value.trim();
    if (!text || busy || !sessionId) return;
    textarea.value = '';
    textarea.style.height = 'auto';
    sendBtn.disabled = true;
    send(text);
  });

  form.appendChild(textarea);
  form.appendChild(sendBtn);
  footer.appendChild(form);
  panel.appendChild(footer);

  // ── Panel toggle ────────────────────────────────────────────────────────────
  toggleBtn.addEventListener('click', () => { panelOpen ? closePanel() : openPanel(); });

  function openPanel() {
    panelOpen = true;
    panel.hidden = false;
    toggleBtn.setAttribute('aria-expanded', 'true');
    scheduleRender();
    if (sessionId && !busy) textarea.focus();
  }

  function closePanel() {
    panelOpen = false;
    panel.hidden = true;
    toggleBtn.setAttribute('aria-expanded', 'false');
  }

  // ── Render (rAF-batched) ────────────────────────────────────────────────────
  function scheduleRender() {
    if (renderQueued || !panelOpen) return;
    renderQueued = true;
    requestAnimationFrame(render);
  }

  function render() {
    renderQueued = false;
    if (!panelOpen) return;

    // Header badge
    if (busy) {
      actDot.classList.add('pulsing');
      actText.textContent = 'Working';
      toggleBtn.classList.add('busy');
    } else {
      actDot.classList.remove('pulsing');
      actText.textContent = 'Activity';
      toggleBtn.classList.remove('busy');
    }

    // Composer
    textarea.placeholder = busy ? 'Working…' : PLACEHOLDER;
    textarea.disabled = !sessionId || busy;
    sendBtn.disabled  = busy || !sessionId || !textarea.value.trim();

    const atBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 80;

    scrollEl.innerHTML = '';

    if (items.length === 0) {
      renderEmpty();
    } else {
      for (const item of items) {
        scrollEl.appendChild(
          item.kind === 'user' ? renderUser(item) : renderAssistant(item)
        );
      }
    }

    if (atBottom || busy) scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  // ── Empty state ─────────────────────────────────────────────────────────────
  function renderEmpty() {
    const intro = document.createElement('p');
    intro.className = 'ca-intro';
    intro.textContent = INTRO;
    scrollEl.appendChild(intro);

    const wrap = document.createElement('div');
    wrap.className = 'ca-starters';
    for (const text of STARTERS) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ca-starter';
      btn.disabled = busy || !sessionId;
      const spark = mkIcon('spark', 14);
      spark.classList.add('ca-starter-spark');
      btn.appendChild(spark);
      const lbl = document.createElement('span');
      lbl.className = 'ca-starter-label';
      lbl.textContent = text;
      btn.appendChild(lbl);
      const arrow = mkIcon('arrow-right', 14);
      arrow.classList.add('ca-starter-arrow');
      btn.appendChild(arrow);
      btn.addEventListener('click', () => { if (!btn.disabled) send(text); });
      wrap.appendChild(btn);
    }
    scrollEl.appendChild(wrap);
  }

  // ── User bubble ─────────────────────────────────────────────────────────────
  function renderUser(item) {
    const row = document.createElement('div');
    row.className = 'ca-user-row';
    const bubble = document.createElement('div');
    bubble.className = 'ca-user-bubble';
    bubble.textContent = item.text;
    row.appendChild(bubble);
    return row;
  }

  // ── Assistant turn ──────────────────────────────────────────────────────────
  function renderAssistant(item) {
    const isLast = item === items[items.length - 1];
    const wrap = document.createElement('div');
    wrap.className = 'ca-turn';
    wrap.dataset.turn = item.turn;

    item.segments.forEach((seg, i) => {
      const isLastSeg = i === item.segments.length - 1;
      if (seg.type === 'text') {
        const el = document.createElement('div');
        el.className = 'ca-text' + (item.pending && isLastSeg ? ' streaming' : '');
        el.innerHTML = renderMd(seg.text);
        wrap.appendChild(el);
      } else if (seg.type === 'error') {
        const el = document.createElement('div');
        el.className = 'ca-error';
        el.setAttribute('role', 'alert');
        el.textContent = seg.text;
        wrap.appendChild(el);
      } else if (seg.type === 'ui') {
        wrap.appendChild(renderUIBlock(seg));
      }
    });

    if (item.pending) {
      if (item.activity) {
        const line = document.createElement('div');
        line.className = 'ca-activity';
        line.setAttribute('role', 'status');
        const pulse = document.createElement('span');
        pulse.className = 'ca-activity-pulse';
        const lbl = document.createElement('span');
        lbl.className = 'ca-activity-label';
        lbl.textContent = item.activity;
        line.appendChild(pulse);
        line.appendChild(lbl);
        wrap.appendChild(line);
      } else if (!item.segments.length) {
        const skel = document.createElement('div');
        skel.className = 'ca-skeleton-wrap';
        skel.setAttribute('role', 'status');
        skel.setAttribute('aria-label', 'Working');
        [60, 40].forEach(pct => {
          const s = document.createElement('div');
          s.className = 'ca-skeleton';
          s.style.width = pct + '%';
          skel.appendChild(s);
        });
        wrap.appendChild(skel);
      }
    }

    // Suggestion chips on the last settled turn
    if (!item.pending && isLast && item.suggestions.length) {
      const chips = document.createElement('div');
      chips.className = 'ca-chips';
      for (const sug of item.suggestions) {
        if (ACTION_CHIP_RE.test(sug)) continue;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'ca-chip';
        chip.textContent = sug;
        chip.disabled = busy;
        chip.addEventListener('click', () => { if (!chip.disabled) send(sug); });
        chips.appendChild(chip);
      }
      if (chips.childElementCount) wrap.appendChild(chips);
    }

    return wrap;
  }

  // ── Generative block renderer ───────────────────────────────────────────────
  function renderUIBlock(seg) {
    const { block, status } = seg;
    if (block.component === 'change_preview') return renderChangeCard(block.payload, status);

    const card = document.createElement('div');
    card.className = 'ca-card';

    const hdr = document.createElement('div');
    hdr.className = 'ca-card-hdr';
    const dot = document.createElement('span');
    dot.className = 'ca-card-dot' + (status === 'pending' ? ' dim' : '');
    const lbl = document.createElement('span');
    lbl.textContent = humanize(block.component);
    hdr.appendChild(dot);
    hdr.appendChild(lbl);
    card.appendChild(hdr);

    const body = document.createElement('div');
    body.className = 'ca-card-body';
    body.appendChild(renderPayload(block.payload));
    card.appendChild(body);

    return card;
  }

  // ── Change preview card (approve / discard buttons) ─────────────────────────
  function renderChangeCard(payload, status) {
    const changeId     = payload?.change_id;
    const changeStatus = payload?.status || (status === 'final' ? 'staged' : status);
    const fieldName    = humanize(String(payload?.field || payload?.type || 'Change'));
    const from         = payload?.from;
    const to           = payload?.to ?? payload?.value;

    const card = document.createElement('div');
    card.className = 'ca-card';

    const hdr = document.createElement('div');
    hdr.className = 'ca-change-hdr';
    const name = document.createElement('span');
    name.className = 'ca-change-name';
    name.textContent = fieldName;
    const statusBadge = document.createElement('span');
    statusBadge.className = 'ca-status ' + (changeStatus || 'staged');
    statusBadge.textContent = changeStatus || 'staged';
    hdr.appendChild(name);
    hdr.appendChild(statusBadge);
    card.appendChild(hdr);

    const diff = document.createElement('div');
    diff.className = 'ca-change-diff';
    if (from !== undefined) {
      const fromEl = document.createElement('div');
      fromEl.className = 'ca-diff-from';
      fromEl.textContent = String(from);
      diff.appendChild(fromEl);
    }
    if (to !== undefined) {
      const toEl = document.createElement('div');
      toEl.className = 'ca-diff-to';
      toEl.textContent = String(to);
      diff.appendChild(toEl);
    }
    card.appendChild(diff);

    if (changeId && (!changeStatus || changeStatus === 'staged')) {
      const actions = document.createElement('div');
      actions.className = 'ca-change-actions';

      const discardBtn = document.createElement('button');
      discardBtn.type = 'button';
      discardBtn.className = 'ca-btn-discard';
      discardBtn.appendChild(mkIcon('ban', 13));
      const dl = document.createElement('span');
      dl.textContent = 'Discard';
      discardBtn.appendChild(dl);

      const approveBtn = document.createElement('button');
      approveBtn.type = 'button';
      approveBtn.className = 'ca-btn-approve';
      approveBtn.appendChild(mkIcon('check', 13));
      const al = document.createElement('span');
      al.textContent = 'Approve';
      approveBtn.appendChild(al);

      const actOnChange = async (action) => {
        approveBtn.disabled = true;
        discardBtn.disabled = true;
        try {
          const res = await fetch(
            `${BASE}/changes/${encodeURIComponent(changeId)}/${action}`,
            { method: 'POST', headers: { 'Content-Type': 'application/json', [SESSION_HEADER]: sessionId } }
          );
          if (res.ok) {
            const data = await res.json();
            if (data?.change) applyChangeUpdate(data.change);
          }
        } catch {
          approveBtn.disabled = false;
          discardBtn.disabled = false;
        }
      };

      approveBtn.addEventListener('click', () => actOnChange('apply'));
      discardBtn.addEventListener('click', () => actOnChange('discard'));
      actions.appendChild(discardBtn);
      actions.appendChild(approveBtn);
      card.appendChild(actions);
    }

    return card;
  }

  // ── Payload renderer ─────────────────────────────────────────────────────────
  const ARRAY_KEYS = ['items','entries','steps','products','days','sections','metrics','changes','alerts'];

  function renderPayload(payload) {
    if (!payload || typeof payload !== 'object') {
      const p = document.createElement('p');
      p.style.cssText = 'font-size:13px;color:var(--ink)';
      p.textContent = String(payload ?? '');
      return p;
    }

    for (const key of ARRAY_KEYS) {
      if (Array.isArray(payload[key])) {
        const frag = document.createDocumentFragment();
        const list = document.createElement('div');
        list.className = 'ca-list';
        payload[key].slice(0, 8).forEach((item, i) => {
          const row = document.createElement('div');
          row.className = 'ca-list-row';
          const idx = document.createElement('span');
          idx.className = 'ca-list-idx';
          idx.textContent = String(i + 1);
          const body = document.createElement('div');
          body.className = 'ca-list-body';
          const [title, detail] = extractItem(item);
          const tEl = document.createElement('div');
          tEl.className = 'ca-list-title';
          tEl.textContent = title;
          body.appendChild(tEl);
          if (detail) {
            const dEl = document.createElement('div');
            dEl.className = 'ca-list-detail';
            dEl.textContent = detail;
            body.appendChild(dEl);
          }
          row.appendChild(idx);
          row.appendChild(body);
          list.appendChild(row);
        });
        frag.appendChild(list);
        if (payload[key].length > 8) {
          const more = document.createElement('div');
          more.style.cssText = 'font-size:12px;color:var(--ink-soft);margin-top:6px;padding-left:2px';
          more.textContent = '+' + (payload[key].length - 8) + ' more';
          frag.appendChild(more);
        }
        return frag;
      }
    }

    // Flat key-value
    const grid = document.createElement('div');
    grid.className = 'ca-kv';
    Object.entries(payload)
      .filter(([, v]) => typeof v !== 'object' || v === null)
      .slice(0, 14)
      .forEach(([k, v]) => {
        const ke = document.createElement('span');
        ke.className = 'ca-kv-k';
        ke.textContent = k.replace(/_/g, ' ');
        const ve = document.createElement('span');
        ve.className = 'ca-kv-v';
        ve.textContent = fmtVal(v);
        grid.appendChild(ke);
        grid.appendChild(ve);
      });
    return grid.childElementCount ? grid : document.createElement('div');
  }

  function extractItem(item) {
    if (typeof item === 'string') return [item, null];
    if (typeof item !== 'object' || !item) return [String(item), null];
    const tk = ['title','name','label','product','description','message','alert'].find(k => item[k]);
    const vk = ['value','amount','price','count','quantity','revenue','units','pct_change'].find(k => item[k] !== undefined);
    const title  = tk ? String(item[tk]) : JSON.stringify(item).slice(0, 60);
    const detail = vk ? fmtVal(item[vk]) : null;
    return [title, detail];
  }

  function fmtVal(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    if (typeof v === 'number') return v.toLocaleString('en-US');
    return String(v);
  }

  function humanize(str) {
    return str.replace(/^present_/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  // ── Markdown — inline only, XSS-safe (escape first, then annotate) ──────────
  function renderMd(text) {
    return text.split(/\n\n+/).map(para => {
      let html = esc(para);
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/\*(.+?)\*/g,     '<em>$1</em>');
      html = html.replace(/`(.+?)`/g,       '<code>$1</code>');
      html = html.replace(/\n/g,            '<br>');
      return '<p>' + html + '</p>';
    }).join('');
  }

  function esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── API ──────────────────────────────────────────────────────────────────────
  async function startSession() {
    try {
      const res = await fetch(`${BASE}/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error('session');
      const data = await res.json();
      sessionId = data.session_id;
    } catch {
      // Surfaced on first send attempt
    }
    scheduleRender();
  }

  async function* readEventStream(body) {
    const reader  = body.getReader();
    const decoder = new TextDecoder();
    let buffer    = '';
    let eventType = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl).trimEnd();
        buffer = buffer.slice(nl + 1);
        if (line.startsWith('event: '))           eventType = line.slice(7).trim();
        else if (line.startsWith('data: ') && eventType) {
          try { yield { type: eventType, data: JSON.parse(line.slice(6)) }; } catch {}
        } else if (line === '') {
          eventType = null;
        }
      }
    }
  }

  // ── Turn management ──────────────────────────────────────────────────────────
  function mutTurn(turn, fn) {
    items = items.map(item => item.kind === 'assistant' && item.turn === turn ? fn(item) : item);
    scheduleRender();
  }

  function applyChangeUpdate(change) {
    items = items.map(item => {
      if (item.kind !== 'assistant' || !item.changeIds?.includes(change.change_id)) return item;
      return {
        ...item,
        suggestionsStale: item.suggestionsStale || change.status !== 'staged',
        segments: item.segments.map(seg => {
          if (seg.type !== 'ui' || seg.block.component !== 'change_preview') return seg;
          if (seg.block.payload?.change_id !== change.change_id) return seg;
          return { ...seg, block: { ...seg.block, payload: { ...seg.block.payload, ...change } } };
        }),
      };
    });
    scheduleRender();
  }

  async function send(text) {
    if (!text.trim() || busy || !sessionId) return;
    busy = true;
    const turn = ++turnCounter;
    items = [
      ...items,
      { kind: 'user', text },
      { kind: 'assistant', turn, segments: [], suggestions: [], changeIds: [], pending: true, activity: undefined, tools: [] },
    ];
    scheduleRender();

    try {
      const res = await fetch(`${BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', [SESSION_HEADER]: sessionId },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok || !res.body) throw new Error('chat ' + res.status);
      for await (const event of readEventStream(res.body)) handleEvent(turn, event);
    } catch {
      mutTurn(turn, item =>
        item.segments.length ? item :
        { ...item, segments: [{ type: 'error', text: 'Could not reach the assistant. Check your connection and try again.' }] }
      );
    } finally {
      mutTurn(turn, item => ({
        ...item, pending: false, activity: undefined,
        segments: item.segments
          .filter(s => s.type !== 'ui' || (s.status !== 'pending' && s.status !== 'retrying'))
          .map(s => s.type === 'ui' && s.status === 'partial' ? { ...s, status: 'final' } : s),
      }));
      busy = false;
      scheduleRender();
    }
  }

  // ── Event dispatcher (mirrors web-shared/turn.ts handleEvent) ───────────────
  function handleEvent(turn, event) {
    switch (event.type) {

      case 'text_delta': {
        const delta = String(event.data.text ?? '');
        mutTurn(turn, item => {
          const segs = [...item.segments];
          const last = segs[segs.length - 1];
          if (last?.type === 'text') segs[segs.length - 1] = { type: 'text', text: last.text + delta };
          else segs.push({ type: 'text', text: delta });
          return { ...item, segments: segs, activity: undefined };
        });
        break;
      }

      case 'ui': {
        const component = String(event.data.component ?? '');
        if (component === 'suggestions') {
          const suggestions = event.data.payload?.suggestions ?? [];
          mutTurn(turn, item => ({ ...item, suggestions, activity: undefined }));
          break;
        }
        const block    = { component, payload: event.data.payload ?? {} };
        const streamId = event.data.stream_id ? String(event.data.stream_id) : null;
        mutTurn(turn, item => {
          const segs = [...item.segments];
          const idx  = segs.findIndex(s => s.type === 'ui' && s.block.component === component);
          const seg  = { type: 'ui', block, slotKey: `${turn}-${component}`, status: 'final', streamId };
          if (idx >= 0) segs[idx] = seg; else segs.push(seg);
          const changeIds = component === 'change_preview' && block.payload?.change_id
            ? [...(item.changeIds || []), block.payload.change_id]
            : item.changeIds;
          return { ...item, segments: segs, changeIds };
        });
        break;
      }

      case 'ui_partial': {
        const component = String(event.data.component ?? '');
        if (component === 'suggestions') break;
        const block    = { component, payload: event.data.payload ?? {} };
        const streamId = event.data.stream_id ? String(event.data.stream_id) : null;
        mutTurn(turn, item => {
          const segs = [...item.segments];
          const idx  = segs.findIndex(s => s.type === 'ui' && s.block.component === component);
          const seg  = { type: 'ui', block, slotKey: `${turn}-${component}`, status: 'partial', streamId };
          if (idx >= 0) segs[idx] = seg; else segs.push(seg);
          return { ...item, segments: segs };
        });
        break;
      }

      case 'tool_call': {
        const tool  = String(event.data.tool ?? 'tool');
        const label = typeof event.data.label === 'string' ? event.data.label.trim() : '';
        mutTurn(turn, item => ({
          ...item,
          activity: label || 'Using ' + tool.replace(/_/g, ' '),
          tools: [...item.tools, tool],
        }));
        break;
      }

      case 'progress': {
        const msg = String(event.data.message ?? '').trim();
        if (msg) mutTurn(turn, item => ({ ...item, activity: msg }));
        break;
      }

      case 'error': {
        const msg = String(event.data.message ?? 'Something went wrong.');
        mutTurn(turn, item => ({ ...item, segments: [...item.segments, { type: 'error', text: msg }] }));
        break;
      }

      case 'change_update': {
        const change = event.data.change;
        if (change?.change_id) applyChangeUpdate(change);
        break;
      }
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────────
  startSession();
})();
