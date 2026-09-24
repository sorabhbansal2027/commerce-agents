#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Gemini × UCP + MCP Apps UI — split-screen demo server.

Left panel: Gemini agent chat (shopping via UCP REST endpoints).
Right panel: MCP Apps product-grid iframe (same HTML the MCP server serves at
             ui://storefront/product-grid), connected via the postMessage bridge.

When Gemini calls search_products, the results are pushed to the iframe.
When the user clicks "Add to cart" in the iframe, the postMessage fires back
to this page, which calls /api/cart and Gemini creates a checkout session.

Run:
    pip install fastapi uvicorn        # one-time, if not already installed
    GEMINI_API_KEY=your-key python scripts/gemini_mcp_ui_server.py
    open http://localhost:8099

Env vars:
    GEMINI_API_KEY   required
    UCP_BASE         defaults to the Railway deployment
    GEMINI_UI_PORT   defaults to 8099
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "gemini_ucp_agent",
    REPO_ROOT / "examples" / "demo_common" / "gemini_ucp_agent.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
GeminiUCPAgent = _mod.GeminiUCPAgent

_PRODUCT_GRID_DISK = (
    REPO_ROOT
    / "shopping-agent"
    / "managed-agents"
    / "storefront-mcp-server"
    / "product_grid_ui.html"
)

UCP_BASE = os.environ.get("UCP_BASE", "https://diligent-flow-production-afd7.up.railway.app")
STOREFRONT_MCP_URL = os.environ.get("STOREFRONT_MCP_URL", "http://127.0.0.1:8200/mcp")
PORT = int(os.environ.get("GEMINI_UI_PORT", "8099"))

# Populated at startup — either from the MCP server or the local file fallback.
_product_grid_html: str = ""


async def _fetch_html_from_mcp() -> str:
    """Read ui://storefront/product-grid from the MCP server."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(STOREFRONT_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource("ui://storefront/product-grid")
            for item in result.contents:
                if hasattr(item, "text") and item.text:
                    return item.text
    raise RuntimeError("MCP resource returned no HTML content")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _product_grid_html
    try:
        _product_grid_html = await _fetch_html_from_mcp()
        print(f"[MCP] Loaded product-grid HTML from {STOREFRONT_MCP_URL}")
    except Exception as exc:
        _product_grid_html = _PRODUCT_GRID_DISK.read_text()
        print(f"[MCP] Fallback to disk ({exc})")
    yield


# ── Agent with product-result capture ─────────────────────────────────────────

class TrackedAgent(GeminiUCPAgent):
    """GeminiUCPAgent that records UCP call results so the server can forward
    product lists to the MCP Apps iframe after each turn."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_products: list = []
        self.last_tool_calls: list = []

    def _call_ucp(self, fn_name: str, args: dict) -> dict:
        result = super()._call_ucp(fn_name, args)
        self.last_tool_calls.append({"tool": fn_name, "args": args})
        if fn_name == "search_products":
            self.last_products = result.get("products", [])
        return result


_agent: TrackedAgent | None = None


def get_agent() -> TrackedAgent:
    global _agent
    if _agent is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _agent = TrackedAgent(ucp_base=UCP_BASE, api_key=key)
    return _agent


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="Gemini × UCP + MCP Apps UI", lifespan=lifespan)


@app.get("/ui/product-grid", response_class=HTMLResponse)
async def product_grid_resource() -> HTMLResponse:
    """Serves the MCP Apps HTML resource — same bytes as ui://storefront/product-grid."""
    return HTMLResponse(_product_grid_html)


def _gemini_error_message(exc: Exception) -> str:
    msg = str(exc)
    if "503" in msg or "UNAVAILABLE" in msg:
        return "Gemini is temporarily overloaded. Please try again in a moment."
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "Gemini rate limit reached. Please wait a few seconds and retry."
    if "401" in msg or "API_KEY" in msg:
        return "Invalid Gemini API key. Check your GEMINI_API_KEY environment variable."
    return f"Gemini error: {msg[:120]}"


@app.post("/api/chat")
async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    agent = get_agent()
    agent.last_products = []
    agent.last_tool_calls = []
    try:
        reply = agent.send(body.get("message", ""))
    except Exception as exc:
        return JSONResponse({"reply": _gemini_error_message(exc), "products": [], "tool_calls": []})
    return JSONResponse({
        "reply": reply,
        "products": agent.last_products,
        "tool_calls": agent.last_tool_calls,
    })


@app.post("/api/cart")
async def cart(request: Request) -> JSONResponse:
    """Called when the user clicks Add to Cart in the MCP Apps iframe.
    Passes the product_id to Gemini so it can create a UCP checkout session."""
    body = await request.json()
    product_id = body.get("product_id", "")
    quantity = int(body.get("quantity", 1))
    agent = get_agent()
    agent.last_products = []
    agent.last_tool_calls = []
    try:
        reply = agent.send(
            f"The user clicked Add to Cart for product_id='{product_id}' (quantity {quantity}). "
            "Create a UCP checkout session for this item and confirm the details."
        )
    except Exception as exc:
        return JSONResponse({"reply": _gemini_error_message(exc), "tool_calls": []})
    return JSONResponse({
        "reply": reply,
        "tool_calls": agent.last_tool_calls,
    })


@app.post("/api/reset")
async def reset() -> JSONResponse:
    get_agent().reset()
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_DEMO_HTML)


# ── Demo page ─────────────────────────────────────────────────────────────────

_DEMO_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gemini Shopping Agent</title>
<style>
  :root {
    --ink:    #0f172a;
    --ink2:   #64748b;
    --line:   #e2e8f0;
    --bg:     #f8fafc;
    --card:   #ffffff;
    --brand:  #4f46e5;
    --brand2: #6366f1;
    --ok:     #16a34a;
    --danger: #ef4444;
    --radius: 16px;
    --max-w:  680px;
    --cart-w: 280px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--ink); height: 100vh;
         display: flex; flex-direction: column; overflow: hidden; }

  /* Header */
  header { background: var(--card); border-bottom: 1px solid var(--line);
           padding: 12px 20px; display: flex; align-items: center;
           justify-content: space-between; flex-shrink: 0; }
  .header-left { display: flex; align-items: center; gap: 10px; }
  .agent-avatar { width: 34px; height: 34px; border-radius: 50%;
                  background: linear-gradient(135deg, var(--brand), var(--brand2));
                  display: flex; align-items: center; justify-content: center;
                  color: #fff; font-size: 16px; flex-shrink: 0; }
  .agent-name { font-size: 14px; font-weight: 700; color: var(--ink); }
  .agent-sub  { font-size: 11px; color: var(--ink2); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
                display: inline-block; margin-right: 4px; }
  #reset-btn { padding: 6px 12px; background: none; border: 1px solid var(--line);
               border-radius: 8px; font-size: 12px; cursor: pointer; color: var(--ink2); }
  #reset-btn:hover { background: var(--bg); }

  /* Main area: chat + cart side by side */
  .main { display: flex; flex: 1; overflow: hidden; }

  /* Chat column */
  .chat-col { display: flex; flex-direction: column; flex: 1; overflow: hidden; }

  /* Messages */
  #messages { flex: 1; overflow-y: auto; padding: 24px 20px;
              display: flex; flex-direction: column; gap: 16px; }
  .thread { display: flex; flex-direction: column; gap: 16px;
            max-width: var(--max-w); width: 100%; margin: 0 auto; }

  .row { display: flex; gap: 10px; align-items: flex-start; }
  .row.user { flex-direction: row-reverse; }

  .avatar { width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            font-size: 13px; font-weight: 700; }
  .avatar-agent { background: linear-gradient(135deg, var(--brand), var(--brand2)); color: #fff; }
  .avatar-user  { background: var(--ink); color: #fff; font-size: 11px; }

  .bubble { padding: 10px 14px; border-radius: 16px; font-size: 13.5px;
            line-height: 1.6; word-break: break-word; max-width: calc(var(--max-w) - 80px); }
  .bubble-agent { background: var(--card); border: 1px solid var(--line);
                  border-top-left-radius: 4px; }
  .bubble-user  { background: var(--brand); color: #fff;
                  border-top-right-radius: 4px; }
  .bubble b  { font-weight: 700; }
  .bubble ul { padding-left: 18px; margin: 4px 0; }
  .bubble li { margin: 2px 0; }

  /* Typing indicator */
  .typing { display: flex; gap: 5px; align-items: center; padding: 4px 2px; }
  .typing span { width: 7px; height: 7px; background: var(--ink2); border-radius: 50%;
                 animation: blink 1.3s infinite; }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes blink { 0%,60%,100%{opacity:.15} 30%{opacity:1} }

  /* Product grid */
  .product-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;
                  margin-left: 40px; max-width: calc(var(--max-w) - 40px); }
  .pcard { border: 1px solid var(--line); border-radius: 14px; background: var(--card);
           overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.07);
           transition: transform .15s, box-shadow .15s; cursor: default; }
  .pcard:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,.11); }
  .pcard-img-wrap { position: relative; height: 130px; overflow: hidden; background: #f1f5f9; }
  .pcard-img-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .pcard-img-placeholder { width: 100%; height: 100%; display: flex; align-items: center;
                            justify-content: center; font-size: 36px; background: linear-gradient(135deg,#f1f5f9,#e2e8f0); }
  .pcard-badge { position: absolute; top: 8px; left: 8px; background: rgba(22,163,74,.9);
                 color: #fff; font-size: 9px; font-weight: 700; padding: 2px 7px;
                 border-radius: 20px; text-transform: uppercase; letter-spacing: .04em; }
  .pcard-body { padding: 12px 12px 10px; }
  .pcard-category { font-size: 10px; color: var(--ink2); font-weight: 600;
                    text-transform: uppercase; letter-spacing: .05em; margin-bottom: 4px; }
  .pcard-title { font-weight: 700; font-size: 13px; color: var(--ink); line-height: 1.35;
                 margin-bottom: 6px; display: -webkit-box; -webkit-line-clamp: 2;
                 -webkit-box-orient: vertical; overflow: hidden; }
  .pcard-price { color: var(--ok); font-weight: 800; font-size: 16px; margin-bottom: 10px; }
  .pcard-btn { width: 100%; padding: 8px 0; background: var(--brand); color: #fff;
               border: none; border-radius: 9px; font-size: 12px; font-weight: 700;
               cursor: pointer; transition: background .15s, opacity .15s; letter-spacing: .02em; }
  .pcard-btn:hover { background: var(--brand2); }
  .pcard-btn:disabled { opacity: .5; cursor: not-allowed; background: var(--ok); }

  /* Tool trace */
  #tools-log { font-size: 10.5px; color: var(--ink2); background: var(--bg);
               border-top: 1px solid var(--line); padding: 5px 20px;
               min-height: 22px; flex-shrink: 0; white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; }

  /* Input bar */
  .input-bar { display: flex; gap: 8px; padding: 12px 20px;
               border-top: 1px solid var(--line); background: var(--card);
               flex-shrink: 0; }
  .input-wrap { flex: 1; max-width: var(--max-w); margin: 0 auto;
                display: flex; gap: 8px; }
  #msg-input { flex: 1; padding: 10px 14px; border: 1px solid var(--line);
               border-radius: 12px; font-size: 14px; outline: none;
               background: var(--bg); }
  #msg-input:focus { border-color: var(--brand); background: var(--card); }
  #send-btn { padding: 10px 20px; background: var(--brand); color: #fff;
              border: none; border-radius: 12px; font-size: 14px; font-weight: 600;
              cursor: pointer; transition: opacity .15s; }
  #send-btn:hover { opacity: .88; }
  #send-btn:disabled { opacity: .45; cursor: not-allowed; }

  /* Cart panel */
  .cart-panel { width: var(--cart-w); border-left: 1px solid var(--line);
                background: var(--card); display: flex; flex-direction: column;
                flex-shrink: 0; overflow: hidden; }
  .cart-header { padding: 14px 16px 10px; border-bottom: 1px solid var(--line);
                 display: flex; align-items: center; justify-content: space-between; }
  .cart-title { font-size: 13px; font-weight: 700; display: flex; align-items: center; gap: 6px; }
  .cart-count { background: var(--brand); color: #fff; font-size: 10px; font-weight: 700;
                border-radius: 20px; padding: 1px 7px; min-width: 20px; text-align: center; }
  .cart-items { flex: 1; overflow-y: auto; padding: 10px 12px;
                display: flex; flex-direction: column; gap: 8px; }
  .cart-empty { flex: 1; display: flex; flex-direction: column; align-items: center;
                justify-content: center; gap: 8px; color: var(--ink2);
                font-size: 12px; padding: 20px; text-align: center; }
  .cart-empty-icon { font-size: 32px; opacity: .4; }
  .ci { display: flex; gap: 8px; align-items: flex-start; padding: 8px;
        border: 1px solid var(--line); border-radius: 10px; background: var(--bg); }
  .ci-img { width: 44px; height: 44px; border-radius: 7px; object-fit: cover; flex-shrink: 0;
            background: var(--line); }
  .ci-info { flex: 1; min-width: 0; }
  .ci-name { font-size: 11px; font-weight: 600; line-height: 1.35;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ci-price { font-size: 11px; color: var(--ok); font-weight: 700; margin-top: 2px; }
  .ci-qty { display: flex; align-items: center; gap: 4px; margin-top: 5px; }
  .qty-btn { width: 20px; height: 20px; border-radius: 5px; border: 1px solid var(--line);
             background: var(--card); font-size: 13px; cursor: pointer; display: flex;
             align-items: center; justify-content: center; color: var(--ink2); flex-shrink: 0; }
  .qty-btn:hover { background: var(--bg); }
  .qty-val { font-size: 11px; font-weight: 600; min-width: 16px; text-align: center; }
  .ci-remove { background: none; border: none; font-size: 14px; cursor: pointer;
               color: var(--ink2); padding: 2px; flex-shrink: 0; line-height: 1; }
  .ci-remove:hover { color: var(--danger); }
  .cart-footer { border-top: 1px solid var(--line); padding: 12px 14px; }
  .cart-total { display: flex; justify-content: space-between; align-items: center;
                margin-bottom: 10px; }
  .cart-total-label { font-size: 12px; color: var(--ink2); }
  .cart-total-val { font-size: 15px; font-weight: 700; color: var(--ink); }
  #checkout-btn { width: 100%; padding: 10px; background: var(--brand); color: #fff;
                  border: none; border-radius: 10px; font-size: 13px; font-weight: 600;
                  cursor: pointer; transition: opacity .15s; }
  #checkout-btn:hover { opacity: .88; }
  #checkout-btn:disabled { opacity: .4; cursor: not-allowed; }

  /* Checkout form view */
  #checkout-view { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  .co-back { padding: 10px 14px; font-size: 12px; color: var(--brand); cursor: pointer;
             border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 4px; }
  .co-back:hover { background: var(--bg); }
  .co-summary { padding: 10px 14px; font-size: 12px; color: var(--ink2);
                border-bottom: 1px solid var(--line); background: var(--bg); }
  .co-summary b { color: var(--ink); }
  .co-fields { flex: 1; overflow-y: auto; padding: 12px 14px;
               display: flex; flex-direction: column; gap: 10px; }
  .co-field label { font-size: 11px; font-weight: 600; color: var(--ink2);
                    display: block; margin-bottom: 3px; }
  .co-field input, .co-field select {
    width: 100%; padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 8px; font-size: 13px; outline: none; background: var(--bg); }
  .co-field input:focus, .co-field select:focus { border-color: var(--brand); background: var(--card); }
  .co-footer { border-top: 1px solid var(--line); padding: 12px 14px; }
  #place-order-btn { width: 100%; padding: 11px; background: var(--ok); color: #fff;
                     border: none; border-radius: 10px; font-size: 13px; font-weight: 700;
                     cursor: pointer; transition: opacity .15s; }
  #place-order-btn:hover { opacity: .88; }
  #place-order-btn:disabled { opacity: .4; cursor: not-allowed; }

  /* Order success state */
  .order-success { display: flex; flex-direction: column; align-items: center;
                   justify-content: center; flex: 1; padding: 20px; text-align: center; gap: 8px; }
  .order-success-icon { font-size: 36px; }
  .order-success h3 { font-size: 14px; font-weight: 700; color: var(--ok); }
  .order-success p { font-size: 12px; color: var(--ink2); }
  .order-success button { margin-top: 8px; padding: 8px 16px; background: var(--brand); color: #fff;
                          border: none; border-radius: 8px; font-size: 12px; font-weight: 600;
                          cursor: pointer; }
</style>
</head>
<body>

<header>
  <div class="header-left">
    <div class="agent-avatar">&#10024;</div>
    <div>
      <div class="agent-name">Shopping Agent</div>
      <div class="agent-sub"><span class="status-dot"></span>Gemini 2.0 Flash &middot; UCP Commerce</div>
    </div>
  </div>
  <button id="reset-btn" onclick="resetConv()">&#8635; New chat</button>
</header>

<div class="main">
  <!-- Chat column -->
  <div class="chat-col">
    <div id="messages">
      <div class="thread">
        <div class="row">
          <div class="avatar avatar-agent">&#10024;</div>
          <div class="bubble bubble-agent">
            Hi! I&rsquo;m your shopping assistant. Tell me what you&rsquo;re looking for and I&rsquo;ll find the best options for you.
          </div>
        </div>
      </div>
    </div>
    <div id="tools-log"></div>
    <div class="input-bar">
      <div class="input-wrap">
        <input id="msg-input" type="text" placeholder='Try "show me laptops under $2000"' autocomplete="off"/>
        <button id="send-btn" onclick="send()">Send</button>
      </div>
    </div>
  </div>

  <!-- Cart panel -->
  <div class="cart-panel">
    <div class="cart-header">
      <div class="cart-title">
        &#x1F6D2; Cart
        <span class="cart-count" id="cart-count">0</span>
      </div>
    </div>
    <!-- Cart items view -->
    <div id="cart-view" style="display:flex;flex-direction:column;flex:1;overflow:hidden">
      <div class="cart-items" id="cart-items">
        <div class="cart-empty">
          <div class="cart-empty-icon">&#x1F6D2;</div>
          <div>Your cart is empty</div>
          <div style="font-size:11px;opacity:.7">Add items from the chat</div>
        </div>
      </div>
      <div class="cart-footer">
        <div class="cart-total">
          <span class="cart-total-label">Total</span>
          <span class="cart-total-val" id="cart-total">$0.00</span>
        </div>
        <button id="checkout-btn" disabled onclick="showCheckoutForm()">Checkout &rarr;</button>
      </div>
    </div>

    <!-- Checkout form view -->
    <div id="checkout-view" style="display:none;flex-direction:column;flex:1;overflow:hidden">
      <div class="co-back" onclick="showCartView()">&#8592; Back to cart</div>
      <div class="co-summary" id="co-summary"></div>
      <div class="co-fields">
        <div class="co-field">
          <label>Name <span style="font-weight:400;color:var(--ink2)">(optional)</span></label>
          <input id="co-name" type="text" placeholder="Your name" autocomplete="name"/>
        </div>
        <div class="co-field">
          <label>Email <span style="font-weight:400;color:var(--ink2)">(optional)</span></label>
          <input id="co-email" type="email" placeholder="you@example.com" autocomplete="email"/>
        </div>
        <div class="co-field">
          <label>Payment Method</label>
          <select id="co-payment">
            <option value="purchase_order">Purchase Order</option>
            <option value="credit_card">Credit Card</option>
          </select>
        </div>
      </div>
      <div class="co-footer">
        <button id="place-order-btn" onclick="placeOrder()">&#x2713; Place Order</button>
      </div>
    </div>
  </div>
</div>

<script>
const messages   = document.getElementById("messages");
const input      = document.getElementById("msg-input");
const sendBtn    = document.getElementById("send-btn");
const toolsLog   = document.getElementById("tools-log");
const cartItems   = document.getElementById("cart-items");
const cartCount   = document.getElementById("cart-count");
const cartTotal   = document.getElementById("cart-total");
const checkoutBtn = document.getElementById("checkout-btn");
const cartView    = document.getElementById("cart-view");
const checkoutView= document.getElementById("checkout-view");

// Cart state: { product_id: {title, price, image_url, qty} }
const cart = {};

function cartAddItem(p) {
  if (cart[p.product_id]) {
    cart[p.product_id].qty++;
  } else {
    cart[p.product_id] = { title: p.title, price: p.price, image_url: p.image_url, qty: 1 };
  }
  renderCart();
}

function cartRemove(pid) {
  delete cart[pid];
  renderCart();
}

function cartSetQty(pid, qty) {
  if (qty < 1) { cartRemove(pid); return; }
  if (cart[pid]) { cart[pid].qty = qty; renderCart(); }
}

function renderCart() {
  const items = Object.entries(cart);
  const totalQty = items.reduce((s,[,v]) => s + v.qty, 0);
  const totalPrice = items.reduce((s,[,v]) => s + v.price * v.qty, 0);
  cartCount.textContent = totalQty;
  cartTotal.textContent = "$" + totalPrice.toFixed(2);
  checkoutBtn.disabled = items.length === 0;

  if (items.length === 0) {
    cartItems.innerHTML = `<div class="cart-empty">
      <div class="cart-empty-icon">&#x1F6D2;</div>
      <div>Your cart is empty</div>
      <div style="font-size:11px;opacity:.7">Add items from the chat</div>
    </div>`;
    return;
  }

  cartItems.innerHTML = "";
  items.forEach(([pid, item]) => {
    const div = document.createElement("div");
    div.className = "ci";
    const imgHtml = item.image_url
      ? `<img class="ci-img" src="${item.image_url}" alt="" onerror="this.style.background='#e2e8f0'">`
      : `<div class="ci-img"></div>`;
    div.innerHTML = `${imgHtml}
      <div class="ci-info">
        <div class="ci-name" title="${(item.title||"").replace(/"/g,"&quot;")}">${(item.title||"").replace(/</g,"&lt;")}</div>
        <div class="ci-price">$${(item.price||0).toFixed(2)}</div>
        <div class="ci-qty">
          <button class="qty-btn" onclick="cartSetQty('${pid}', ${item.qty-1})">&#8722;</button>
          <span class="qty-val">${item.qty}</span>
          <button class="qty-btn" onclick="cartSetQty('${pid}', ${item.qty+1})">+</button>
        </div>
      </div>
      <button class="ci-remove" onclick="cartRemove('${pid}')" title="Remove">&#10005;</button>`;
    cartItems.appendChild(div);
  });
}

function getThread() {
  let t = messages.querySelector(".thread");
  if (!t) { t = document.createElement("div"); t.className = "thread"; messages.appendChild(t); }
  return t;
}

function renderMarkdown(text) {
  const lines = text.split("\\n");
  let html = "";
  let inList = false;
  for (const raw of lines) {
    let line = raw
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
      .replace(/\\*\\*(.+?)\\*\\*/g,"<b>$1</b>")
      .replace(/`([^`]+)`/g,"<code>$1</code>");
    const bullet = line.match(/^\\s*[\\*\\-]\\s+(.*)/);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + bullet[1] + "</li>";
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim()) html += "<p style='margin:2px 0'>" + line + "</p>";
    }
  }
  if (inList) html += "</ul>";
  return html;
}

function addAgentMsg(text) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `<div class="avatar avatar-agent">&#10024;</div>
    <div class="bubble bubble-agent">${renderMarkdown(text)}</div>`;
  getThread().appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

function addUserMsg(text) {
  const esc = text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  const row = document.createElement("div");
  row.className = "row user";
  row.innerHTML = `<div class="avatar avatar-user">You</div>
    <div class="bubble bubble-user">${esc}</div>`;
  getThread().appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

function showTyping() {
  const row = document.createElement("div");
  row.id = "typing-row";
  row.className = "row";
  row.innerHTML = `<div class="avatar avatar-agent">&#10024;</div>
    <div class="bubble bubble-agent"><div class="typing"><span></span><span></span><span></span></div></div>`;
  getThread().appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}
function removeTyping() { document.getElementById("typing-row")?.remove(); }

function setBusy(b) { sendBtn.disabled = b; input.disabled = b; }

function logTools(calls) {
  if (!calls || !calls.length) { toolsLog.textContent = ""; return; }
  toolsLog.textContent = calls.map(c => {
    const args = Object.entries(c.args || {})
      .filter(([,v]) => v != null)
      .map(([k,v]) => k + "=" + JSON.stringify(v)).join(", ");
    return "⚙ " + c.tool + "(" + args + ")";
  }).join("  ·  ");
}

function addProductCards(products) {
  if (!products || !products.length) return;
  const grid = document.createElement("div");
  grid.className = "product-grid";
  products.forEach(p => {
    const card = document.createElement("div");
    card.className = "pcard";
    const price = p.price != null ? `$${Number(p.price).toFixed(2)}` : "";
    const pid = (p.product_id || "").replace(/'/g,"\\'");
    const imgSection = p.image_url
      ? `<div class="pcard-img-wrap">
           <img src="${p.image_url}" alt="" onerror="this.parentElement.innerHTML='<div class=pcard-img-placeholder>&#x1F4BB;</div>'">
           ${p.in_stock !== false ? '<span class="pcard-badge">In Stock</span>' : ''}
         </div>`
      : `<div class="pcard-img-wrap"><div class="pcard-img-placeholder">&#x1F4BB;</div></div>`;
    card.innerHTML = `${imgSection}
      <div class="pcard-body">
        ${p.category ? `<div class="pcard-category">${p.category.replace(/</g,"&lt;")}</div>` : ""}
        <div class="pcard-title">${(p.title||"").replace(/</g,"&lt;")}</div>
        <div class="pcard-price">${price}</div>
        <button class="pcard-btn" onclick="cartAction('${pid}',this)">&#x1F6D2; Add to Cart</button>
      </div>`;
    card.querySelector(".pcard-btn").dataset.product = JSON.stringify(p);
    grid.appendChild(card);
  });
  getThread().appendChild(grid);
  messages.scrollTop = messages.scrollHeight;
}

function cartAction(productId, btn) {
  const p = JSON.parse(btn.dataset.product || "{}");
  cartAddItem(p);
  btn.textContent = "Added ✓";
  btn.disabled = true;
  btn.style.background = "var(--ok)";
}

function showCheckoutForm() {
  const items = Object.entries(cart);
  if (!items.length) return;
  const totalQty   = items.reduce((s,[,v]) => s + v.qty, 0);
  const totalPrice = items.reduce((s,[,v]) => s + v.price * v.qty, 0);
  document.getElementById("co-summary").innerHTML =
    `<b>${totalQty} item${totalQty > 1 ? "s" : ""}</b> &middot; Total: <b>$${totalPrice.toFixed(2)}</b>`;
  cartView.style.display = "none";
  checkoutView.style.display = "flex";
}

function showCartView() {
  checkoutView.style.display = "none";
  cartView.style.display = "flex";
}

async function placeOrder() {
  const items = Object.entries(cart);
  if (!items.length) return;
  const btn = document.getElementById("place-order-btn");
  btn.disabled = true;
  btn.textContent = "Placing order…";

  const name    = document.getElementById("co-name").value.trim();
  const email   = document.getElementById("co-email").value.trim();
  const payment = document.getElementById("co-payment").value;
  const summary = items.map(([,v]) => `${v.title} (qty: ${v.qty}, $${(v.price*v.qty).toFixed(2)})`).join("; ");
  const total   = items.reduce((s,[,v]) => s + v.price * v.qty, 0);

  let msg = `Please create a checkout session and place the order. Items: ${summary}. Total: $${total.toFixed(2)}. Payment method: ${payment}.`;
  if (name)  msg += ` Customer name: ${name}.`;
  if (email) msg += ` Email: ${email}.`;

  // Switch back to chat view and send
  showCartView();
  input.value = msg;
  await send();

  // Clear cart after sending
  Object.keys(cart).forEach(k => delete cart[k]);
  renderCart();
  btn.disabled = false;
  btn.textContent = "&#x2713; Place Order";
}

async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addUserMsg(text);
  setBusy(true);
  showTyping();
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    removeTyping();
    logTools(data.tool_calls);
    if (data.products && data.products.length > 0) {
      addAgentMsg(`Found ${data.products.length} result${data.products.length > 1 ? "s" : ""} for you:`);
      addProductCards(data.products);
    } else {
      addAgentMsg(data.reply);
    }
  } catch(e) {
    removeTyping();
    addAgentMsg("Something went wrong: " + e.message);
  } finally { setBusy(false); }
}

async function resetConv() {
  await fetch("/api/reset", { method: "POST" });
  const t = messages.querySelector(".thread");
  if (t) t.innerHTML = `<div class="row"><div class="avatar avatar-agent">&#10024;</div>
    <div class="bubble bubble-agent">Hi! I&rsquo;m your shopping assistant. Tell me what you&rsquo;re looking for.</div></div>`;
  toolsLog.textContent = "";
  Object.keys(cart).forEach(k => delete cart[k]);
  renderCart();
  showCartView();
}

input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) send(); });
</script>
</body>
</html>"""


if __name__ == "__main__":
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is required.")
        sys.exit(1)
    print(f"{'='*60}")
    print("Gemini × UCP + MCP Apps UI Demo")
    print(f"{'='*60}")
    print(f"URL        : http://localhost:{PORT}")
    print(f"Storefront : {UCP_BASE}")
    print(f"MCP server : {STOREFRONT_MCP_URL}  (STOREFRONT_MCP_URL)")
    print(f"{'='*60}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
