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
<title>Gemini × UCP + MCP Apps UI</title>
<style>
  :root {
    --ink:     #1a1f2e;
    --ink2:    #6b7280;
    --line:    #e5e7eb;
    --bg:      #f9fafb;
    --card:    #ffffff;
    --brand:   #1e2c4f;
    --ok:      #16a34a;
    --warn:    #d97706;
    --radius:  10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--ink); height: 100vh; display: flex;
         flex-direction: column; overflow: hidden; }
  header { background: var(--brand); color: #fff; padding: 10px 20px;
           display: flex; align-items: center; justify-content: space-between;
           flex-shrink: 0; }
  header h1 { font-size: 15px; font-weight: 600; letter-spacing: .01em; }
  header .tags { display: flex; gap: 8px; }
  .tag { font-size: 10px; padding: 2px 8px; border-radius: 20px; font-weight: 600; }
  .tag-ucp { background: rgba(255,255,255,.15); }
  .tag-mcp { background: rgba(22,163,74,.3); }
  .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 0; flex: 1; overflow: hidden; }
  .panel { display: flex; flex-direction: column; overflow: hidden; }
  .panel-head { font-size: 11px; font-weight: 600; color: var(--ink2);
                padding: 8px 16px; border-bottom: 1px solid var(--line);
                background: var(--card); text-transform: uppercase; letter-spacing: .06em;
                display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
  .dot { width: 8px; height: 8px; border-radius: 50%; }
  .panel-chat { border-right: 1px solid var(--line); }
  #messages { flex: 1; overflow-y: auto; padding: 14px 16px; display: flex;
              flex-direction: column; gap: 10px; }
  .msg { max-width: 88%; }
  .msg-gemini { align-self: flex-start; }
  .msg-user  { align-self: flex-end; }
  .msg-system { align-self: center; }
  .bubble { padding: 9px 13px; border-radius: var(--radius); font-size: 13px;
            line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
  .msg-gemini .bubble { background: var(--card); border: 1px solid var(--line); }
  .msg-user   .bubble { background: var(--brand); color: #fff; }
  .msg-system .bubble { background: #fef9c3; border: 1px solid #fde047;
                         font-size: 11.5px; color: #713f12; }
  .msg-label { font-size: 10.5px; color: var(--ink2); margin-bottom: 3px; padding-left: 2px; }
  .tools-log { padding: 6px 16px; border-top: 1px solid var(--line);
               background: #f0fdf4; font-size: 10.5px; color: #15803d;
               min-height: 26px; flex-shrink: 0; white-space: nowrap; overflow: hidden;
               text-overflow: ellipsis; }
  .input-row { display: flex; gap: 8px; padding: 12px 16px;
               border-top: 1px solid var(--line); background: var(--card);
               flex-shrink: 0; }
  #msg-input { flex: 1; padding: 8px 12px; border: 1px solid var(--line);
               border-radius: var(--radius); font-size: 13px; outline: none; }
  #msg-input:focus { border-color: var(--brand); }
  #send-btn { padding: 8px 16px; background: var(--brand); color: #fff;
              border: none; border-radius: var(--radius); font-size: 13px;
              font-weight: 600; cursor: pointer; white-space: nowrap; }
  #send-btn:disabled { opacity: .5; cursor: not-allowed; }
  #reset-btn { padding: 8px 10px; background: none; border: 1px solid var(--line);
               border-radius: var(--radius); font-size: 12px; cursor: pointer;
               color: var(--ink2); }
  #grid-frame { flex: 1; width: 100%; border: none; background: var(--card); }
  .typing { display: flex; gap: 4px; align-items: center; padding: 10px 13px; }
  .typing span { width: 6px; height: 6px; background: var(--ink2); border-radius: 50%;
                 animation: blink 1.2s infinite; }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes blink { 0%,60%,100%{opacity:.2} 30%{opacity:1} }
  .mcp-badge { background: rgba(22,163,74,.12); color: var(--ok); font-size: 9px;
               padding: 1px 6px; border-radius: 8px; margin-left: 4px; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Gemini &times; UCP Commerce + MCP Apps UI</h1>
  <div class="tags">
    <span class="tag tag-ucp">UCP REST</span>
    <span class="tag tag-mcp">MCP Apps SEP-1865</span>
  </div>
</header>

<div class="panels">
  <!-- Left: Gemini chat -->
  <div class="panel panel-chat">
    <div class="panel-head">
      <div class="dot" style="background:#4f90ea"></div>
      Gemini 3.6 Flash &nbsp;&mdash;&nbsp; UCP shopping agent
    </div>
    <div id="messages">
      <div class="msg msg-system">
        <div class="bubble">Ask Gemini to find products. Results appear in the MCP Apps grid &rarr;</div>
      </div>
    </div>
    <div class="tools-log" id="tools-log">Ready.</div>
    <div class="input-row">
      <input id="msg-input" type="text" placeholder='Try "show me laptops under $2000"' autocomplete="off"/>
      <button id="reset-btn" title="Reset conversation" onclick="resetConv()">&#8635;</button>
      <button id="send-btn" onclick="send()">Send</button>
    </div>
  </div>

  <!-- Right: MCP Apps product grid iframe -->
  <div class="panel">
    <div class="panel-head">
      <div class="dot" style="background:#16a34a"></div>
      MCP Apps product grid
      <span class="mcp-badge">ui://storefront/product-grid</span>
    </div>
    <iframe id="grid-frame" src="/ui/product-grid" sandbox="allow-scripts allow-same-origin"></iframe>
  </div>
</div>

<script>
const iframe = document.getElementById("grid-frame");
const messages = document.getElementById("messages");
const input = document.getElementById("msg-input");
const sendBtn = document.getElementById("send-btn");
const toolsLog = document.getElementById("tools-log");
let iframeReady = false;
let pendingProducts = null;

// ── postMessage bridge from MCP Apps iframe ───────────────────────────────────
window.addEventListener("message", async (evt) => {
  const msg = evt.data;
  if (!msg || msg.jsonrpc !== "2.0") return;

  if (msg.method === "mcp/ui_ready") {
    iframeReady = true;
    if (pendingProducts) { pushProducts(pendingProducts); pendingProducts = null; }
    return;
  }

  if (msg.method === "mcp/call_tool") {
    const { name, arguments: args } = msg.params;
    if (name === "add_to_cart") {
      addMsg("system", `Cart action: adding ${args.product_id} (qty ${args.quantity || 1})…`);
      setBusy(true);
      try {
        const res = await fetch("/api/cart", {
          method: "POST", headers: {"Content-Type":"application/json"},
          body: JSON.stringify(args)
        });
        const data = await res.json();
        // Respond to iframe so button state updates
        iframe.contentWindow.postMessage({ jsonrpc:"2.0", id: msg.id, result:{} }, "*");
        logTools(data.tool_calls);
        addMsg("gemini", data.reply);
      } finally { setBusy(false); }
    }
  }
});

// ── Send chat message ─────────────────────────────────────────────────────────
async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMsg("user", text);
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
    addMsg("gemini", data.reply);
    if (data.products && data.products.length > 0) {
      if (iframeReady) pushProducts(data.products);
      else { pendingProducts = data.products; iframe.src = "/ui/product-grid"; }
    }
  } catch(e) {
    removeTyping();
    addMsg("system", "Error: " + e.message);
  } finally { setBusy(false); }
}

function pushProducts(products) {
  iframe.contentWindow.postMessage({ products }, "*");
}

async function resetConv() {
  await fetch("/api/reset", { method: "POST" });
  messages.innerHTML = '<div class="msg msg-system"><div class="bubble">Conversation reset. Ask Gemini to find products.</div></div>';
  iframeReady = false;
  iframe.src = "/ui/product-grid";
  toolsLog.textContent = "Ready.";
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function addMsg(role, text) {
  const wrap = document.createElement("div");
  wrap.className = "msg msg-" + role;
  if (role !== "system") {
    const lbl = document.createElement("div");
    lbl.className = "msg-label";
    lbl.textContent = role === "gemini" ? "Gemini" : "You";
    wrap.appendChild(lbl);
  }
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  wrap.appendChild(b);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
}

function showTyping() {
  const t = document.createElement("div");
  t.id = "typing";
  t.className = "msg msg-gemini";
  t.innerHTML = '<div class="bubble"><div class="typing"><span></span><span></span><span></span></div></div>';
  messages.appendChild(t);
  messages.scrollTop = messages.scrollHeight;
}
function removeTyping() { document.getElementById("typing")?.remove(); }

function setBusy(b) {
  sendBtn.disabled = b;
  input.disabled = b;
}

function logTools(calls) {
  if (!calls || !calls.length) return;
  toolsLog.textContent = calls.map(c => {
    const args = Object.entries(c.args || {})
      .filter(([,v]) => v != null)
      .map(([k,v]) => k + "=" + JSON.stringify(v))
      .join(", ");
    return "→ " + c.tool + "(" + args + ")";
  }).join("  |  ");
}

input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) send(); });

// iframe ready on load
iframe.addEventListener("load", () => {
  iframeReady = false; // reset until mcp/ui_ready fires
});
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
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
