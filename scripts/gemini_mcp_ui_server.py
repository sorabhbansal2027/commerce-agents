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

# Single-user demo session (name, email, resolved Salesforce User ID)
_session: dict = {"name": "", "email": "", "sf_user_id": ""}


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
        self.last_order: dict = {}
        self.buyer_user_id: str = ""

    def _call_ucp(self, fn_name: str, args: dict) -> dict:
        # Inject the logged-in buyer's SF User ID into place_order so the backend
        # can resolve effectiveAccountId without falling back to the env var default.
        if fn_name == "place_order" and self.buyer_user_id:
            args = {**args, "buyer_user_id": self.buyer_user_id}
        result = super()._call_ucp(fn_name, args)
        self.last_tool_calls.append({"tool": fn_name, "args": args})
        if fn_name == "search_products":
            products = result.get("products", [])
            if products:
                self.last_products = products
        elif fn_name == "get_product" and result.get("product_id"):
            self.last_products = [result]
        elif fn_name == "place_order" and result.get("status") == "placed":
            self.last_order = result
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


@app.post("/api/login")
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    _session["name"] = (body.get("name") or "").strip()
    _session["email"] = (body.get("email") or "").strip()
    # Accept an explicit SF User ID from the request; fall back to the env var.
    _session["sf_user_id"] = (
        (body.get("sf_user_id") or "").strip()
        or os.environ.get("SF_BUYER_USER_ID", "")
    )
    return JSONResponse({"ok": True, "session": _session})


@app.get("/api/session")
async def get_session_ep() -> JSONResponse:
    return JSONResponse(_session)


@app.post("/api/logout")
async def logout() -> JSONResponse:
    _session.update({"name": "", "email": "", "sf_user_id": ""})
    if _agent:
        _agent.reset()
    return JSONResponse({"ok": True})


@app.post("/api/chat")
async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    agent = get_agent()
    agent.last_products = []
    agent.last_tool_calls = []
    agent.last_order = {}
    # Hydrate buyer identity from the active session so place_order
    # can resolve effectiveAccountId for the correct buyer.
    agent.buyer_user_id = _session.get("sf_user_id") or os.environ.get("SF_BUYER_USER_ID", "")
    try:
        reply = agent.send(body.get("message", ""))
    except Exception as exc:
        return JSONResponse({"reply": _gemini_error_message(exc), "products": [], "tool_calls": []})
    return JSONResponse({
        "reply": reply,
        "products": agent.last_products,
        "tool_calls": agent.last_tool_calls,
        "order": agent.last_order,
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
<title>Shopping Agent · Gemini × UCP</title>
<style>
  :root {
    --ink:    #111827;
    --ink2:   #6b7280;
    --ink3:   #9ca3af;
    --line:   #e5e7eb;
    --bg:     #f3f4f6;
    --card:   #ffffff;
    --brand:  #4f46e5;
    --brand2: #7c3aed;
    --ok:     #059669;
    --danger: #dc2626;
    --hdr:    64px;
    --max-w:  720px;
    --cart-w: 360px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--ink); height: 100vh;
         display: flex; flex-direction: column; overflow: hidden; }

  /* ── Header ── */
  header { height: var(--hdr); background: linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);
           padding: 0 24px; display: flex; align-items: center;
           justify-content: space-between; flex-shrink: 0;
           box-shadow: 0 2px 16px rgba(79,70,229,.35); }
  .h-brand { display: flex; align-items: center; gap: 12px; }
  .h-logo { width: 38px; height: 38px; border-radius: 12px;
            background: rgba(255,255,255,.18); backdrop-filter: blur(8px);
            border: 1px solid rgba(255,255,255,.28);
            display: flex; align-items: center; justify-content: center; font-size: 18px; }
  .h-name { font-size: 16px; font-weight: 700; color: #fff; letter-spacing: -.01em; }
  .h-sub  { font-size: 11px; color: rgba(255,255,255,.75);
            display: flex; align-items: center; gap: 5px; }
  .h-dot  { width: 6px; height: 6px; border-radius: 50%; background: #4ade80; }
  #reset-btn { padding: 7px 14px; background: rgba(255,255,255,.15);
               border: 1px solid rgba(255,255,255,.25); border-radius: 10px;
               font-size: 12px; font-weight: 600; cursor: pointer; color: #fff;
               transition: background .15s; }
  #reset-btn:hover { background: rgba(255,255,255,.28); }

  /* ── Main layout ── */
  .main { display: flex; flex: 1; overflow: hidden; }

  /* ── Chat column ── */
  .chat-col { display: flex; flex-direction: column; flex: 1; overflow: hidden; min-width: 0; }

  /* ── Messages ── */
  #messages { flex: 1; overflow-y: auto; padding: 24px 24px 16px;
              display: flex; flex-direction: column; gap: 0; }
  #messages::-webkit-scrollbar { width: 4px; }
  #messages::-webkit-scrollbar-thumb { background: var(--line); border-radius: 2px; }
  .thread { display: flex; flex-direction: column; gap: 16px;
            max-width: var(--max-w); width: 100%; margin: 0 auto; }

  /* ── Message rows ── */
  .row { display: flex; gap: 10px; align-items: flex-start; }
  .row.user { flex-direction: row-reverse; }

  .avatar { width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            font-size: 14px; font-weight: 700; margin-top: 2px; }
  .avatar-agent { background: linear-gradient(135deg,var(--brand),var(--brand2));
                  color: #fff; box-shadow: 0 2px 8px rgba(79,70,229,.3); }
  .avatar-user  { background: var(--ink); color: #fff; font-size: 11px; }

  .bubble { padding: 11px 16px; border-radius: 18px; font-size: 14px;
            line-height: 1.65; word-break: break-word; max-width: calc(var(--max-w) - 90px); }
  .bubble-agent { background: var(--card); border: 1px solid var(--line);
                  border-top-left-radius: 5px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .bubble-user  { background: var(--brand); color: #fff;
                  border-top-right-radius: 5px; box-shadow: 0 1px 4px rgba(79,70,229,.3); }
  .bubble p  { margin: 3px 0; }
  .bubble b  { font-weight: 700; }
  .bubble code { background: rgba(0,0,0,.06); padding: 1px 5px; border-radius: 4px; font-size: .9em; }
  .bubble-user code { background: rgba(255,255,255,.2); }
  .bubble ul { padding-left: 20px; margin: 4px 0; }
  .bubble li { margin: 2px 0; }

  /* ── Typing indicator ── */
  .typing { display: flex; gap: 5px; align-items: center; padding: 4px 2px; }
  .typing span { width: 7px; height: 7px; background: var(--ink2); border-radius: 50%;
                 animation: blink 1.3s infinite; }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes blink { 0%,60%,100%{opacity:.15} 30%{opacity:1} }

  /* ── Product grid ── */
  .product-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 12px;
                  margin-left: 42px; max-width: calc(var(--max-w) - 42px); }
  .pcard { border: 1px solid var(--line); border-radius: 16px; background: var(--card);
           overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.05);
           transition: transform .18s ease, box-shadow .18s ease; cursor: default; }
  .pcard:hover { transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,.1); }
  .pcard-img-wrap { position: relative; height: 150px; overflow: hidden;
                    background: linear-gradient(135deg,#f0f4ff,#e8ecff); }
  .pcard-img-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .pcard-img-placeholder { width: 100%; height: 100%; display: flex; align-items: center;
                            justify-content: center; font-size: 42px;
                            background: linear-gradient(135deg,#f0f4ff,#e8ecff); }
  .pcard-badge { position: absolute; top: 10px; left: 10px;
                 background: rgba(5,150,105,.9); color: #fff;
                 font-size: 9px; font-weight: 700; padding: 3px 8px;
                 border-radius: 20px; text-transform: uppercase; letter-spacing: .05em; }
  .pcard-body { padding: 14px 14px 12px; }
  .pcard-category { font-size: 10px; color: var(--brand); font-weight: 700;
                    text-transform: uppercase; letter-spacing: .06em; margin-bottom: 5px; }
  .pcard-title { font-weight: 700; font-size: 13px; color: var(--ink); line-height: 1.4;
                 margin-bottom: 8px; display: -webkit-box; -webkit-line-clamp: 2;
                 -webkit-box-orient: vertical; overflow: hidden; min-height: 36px; }
  .pcard-price { color: var(--ok); font-weight: 800; font-size: 17px;
                 margin-bottom: 12px; letter-spacing: -.01em; }
  .pcard-btn { width: 100%; padding: 9px 0; background: var(--brand); color: #fff;
               border: none; border-radius: 10px; font-size: 12.5px; font-weight: 700;
               cursor: pointer; transition: background .15s, transform .1s; letter-spacing: .02em; }
  .pcard-btn:hover { background: var(--brand2); transform: scale(1.01); }
  .pcard-btn:disabled { background: var(--ok); opacity: .8; cursor: not-allowed; transform: none; }

  /* ── Tool trace ── */
  #tools-log { font-size: 10.5px; color: var(--ink3); background: var(--bg);
               border-top: 1px solid var(--line); padding: 5px 24px;
               min-height: 22px; flex-shrink: 0; white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; }

  /* ── Input bar ── */
  .input-bar { display: flex; gap: 8px; padding: 14px 24px;
               border-top: 1px solid var(--line); background: var(--card);
               flex-shrink: 0; box-shadow: 0 -2px 12px rgba(0,0,0,.04); }
  .input-wrap { flex: 1; max-width: var(--max-w); margin: 0 auto; display: flex; gap: 8px; }
  #msg-input { flex: 1; padding: 11px 16px; border: 1.5px solid var(--line);
               border-radius: 14px; font-size: 14px; outline: none;
               background: var(--bg); transition: border-color .15s, background .15s; }
  #msg-input:focus { border-color: var(--brand); background: var(--card); }
  #send-btn { padding: 11px 22px; background: var(--brand); color: #fff;
              border: none; border-radius: 14px; font-size: 14px; font-weight: 600;
              cursor: pointer; transition: opacity .15s, transform .1s; }
  #send-btn:hover { opacity: .9; transform: scale(1.02); }
  #send-btn:disabled { opacity: .45; cursor: not-allowed; transform: none; }

  /* ── Cart panel shell ── */
  .cart-panel { width: var(--cart-w); border-left: 1px solid var(--line);
                background: var(--card); display: flex; flex-direction: column;
                flex-shrink: 0; overflow: hidden; }
  .cart-header { height: var(--hdr); padding: 0 18px; border-bottom: 1px solid var(--line);
                 display: flex; align-items: center; justify-content: space-between;
                 flex-shrink: 0; }
  .cart-title { font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
  .cart-count { background: var(--brand); color: #fff; font-size: 10px; font-weight: 700;
                border-radius: 20px; padding: 2px 8px; min-width: 22px; text-align: center; }
  .cart-hdr-total { font-size: 13px; font-weight: 700; color: var(--ink2); }

  /* ── Cart view ── */
  #cart-view { display: flex; flex-direction: column; flex: 1; overflow: hidden; }
  .cart-items { flex: 1; overflow-y: auto; padding: 14px 16px;
                display: flex; flex-direction: column; gap: 10px; }
  .cart-items::-webkit-scrollbar { width: 3px; }
  .cart-items::-webkit-scrollbar-thumb { background: var(--line); }
  .cart-empty { flex: 1; display: flex; flex-direction: column; align-items: center;
                justify-content: center; gap: 10px; color: var(--ink2);
                font-size: 13px; padding: 30px 20px; text-align: center; }
  .cart-empty-icon { font-size: 40px; opacity: .35; }
  .cart-empty-title { font-weight: 600; color: var(--ink); }
  .cart-empty-sub { font-size: 12px; color: var(--ink3); }

  /* Cart item card */
  .ci { display: flex; gap: 10px; align-items: flex-start; padding: 10px;
        border: 1px solid var(--line); border-radius: 12px; background: var(--bg);
        transition: box-shadow .15s; }
  .ci:hover { box-shadow: 0 2px 8px rgba(0,0,0,.06); }
  .ci-thumb { width: 50px; height: 50px; border-radius: 9px; object-fit: cover;
              flex-shrink: 0; background: var(--line); }
  .ci-info { flex: 1; min-width: 0; }
  .ci-name { font-size: 12px; font-weight: 600; line-height: 1.35; color: var(--ink);
             display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .ci-price { font-size: 12px; color: var(--ok); font-weight: 700; margin-top: 3px; }
  .ci-controls { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
  .qty-btn { width: 22px; height: 22px; border-radius: 6px; border: 1px solid var(--line);
             background: var(--card); font-size: 14px; cursor: pointer;
             display: flex; align-items: center; justify-content: center;
             color: var(--ink2); flex-shrink: 0; transition: background .1s; }
  .qty-btn:hover { background: var(--line); color: var(--ink); }
  .qty-val { font-size: 12px; font-weight: 700; min-width: 18px; text-align: center; }
  .ci-remove { background: none; border: none; font-size: 15px; cursor: pointer;
               color: var(--ink3); padding: 2px; flex-shrink: 0; line-height: 1;
               margin-left: auto; transition: color .15s; }
  .ci-remove:hover { color: var(--danger); }

  /* Cart footer */
  .cart-footer { border-top: 1px solid var(--line); padding: 16px 18px; }
  .cart-total-row { display: flex; justify-content: space-between; align-items: center;
                    margin-bottom: 14px; }
  .cart-total-label { font-size: 14px; font-weight: 700; color: var(--ink); }
  .cart-total-val { font-size: 18px; font-weight: 800; color: var(--ink); }
  #checkout-btn { width: 100%; padding: 12px;
                  background: linear-gradient(135deg,var(--brand),var(--brand2));
                  color: #fff; border: none; border-radius: 12px;
                  font-size: 14px; font-weight: 700; cursor: pointer;
                  transition: opacity .15s, transform .1s;
                  box-shadow: 0 3px 10px rgba(79,70,229,.3); }
  #checkout-btn:hover { opacity: .92; transform: translateY(-1px); }
  #checkout-btn:disabled { opacity: .35; cursor: not-allowed; transform: none; box-shadow: none; }

  /* ── Checkout view ── */
  #checkout-view { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  .co-hdr { height: 48px; padding: 0 16px; border-bottom: 1px solid var(--line);
            display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .co-back-btn { background: none; border: none; cursor: pointer; color: var(--brand);
                 font-size: 20px; display: flex; align-items: center; padding: 3px;
                 border-radius: 6px; line-height: 1; transition: background .1s; }
  .co-back-btn:hover { background: var(--bg); }
  .co-hdr-title { font-size: 14px; font-weight: 700; color: var(--ink); }

  .co-order-summary { margin: 12px 16px 4px; background: var(--bg);
                      border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  .co-order-item { display: flex; align-items: center; gap: 10px; padding: 9px 12px;
                   border-bottom: 1px solid var(--line); font-size: 12px; }
  .co-order-item:last-child { border-bottom: none; }
  .co-item-thumb { width: 36px; height: 36px; border-radius: 7px; object-fit: cover;
                   background: var(--line); flex-shrink: 0; }
  .co-item-name { flex: 1; font-weight: 600; color: var(--ink); line-height: 1.3; }
  .co-item-qty  { color: var(--ink2); margin: 0 6px; }
  .co-item-price { font-weight: 700; color: var(--ink); }

  .co-fields { flex: 1; overflow-y: auto; padding: 8px 16px 12px;
               display: flex; flex-direction: column; gap: 12px; }
  .co-field label { font-size: 11px; font-weight: 700; color: var(--ink2);
                    display: block; margin-bottom: 5px;
                    text-transform: uppercase; letter-spacing: .04em; }
  .co-field input, .co-field select {
    width: 100%; padding: 10px 12px; border: 1.5px solid var(--line);
    border-radius: 10px; font-size: 13.5px; outline: none; background: var(--bg);
    transition: border-color .15s, background .15s; color: var(--ink); }
  .co-field input:focus, .co-field select:focus { border-color: var(--brand); background: var(--card); }

  .co-footer { border-top: 1px solid var(--line); padding: 14px 16px; flex-shrink: 0; }
  .co-total-row { display: flex; justify-content: space-between; align-items: center;
                  margin-bottom: 12px; }
  .co-total-label { font-size: 13px; font-weight: 600; color: var(--ink2); }
  .co-total-val { font-size: 17px; font-weight: 800; color: var(--ok); }
  #place-order-btn { width: 100%; padding: 12px;
                     background: linear-gradient(135deg,var(--ok),#047857);
                     color: #fff; border: none; border-radius: 12px;
                     font-size: 14px; font-weight: 700; cursor: pointer;
                     transition: opacity .15s, transform .1s;
                     box-shadow: 0 3px 10px rgba(5,150,105,.3); }
  #place-order-btn:hover { opacity: .92; transform: translateY(-1px); }
  #place-order-btn:disabled { opacity: .4; cursor: not-allowed; transform: none; box-shadow: none; }

  /* ── Order confirm view ── */
  #order-confirm { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  .oc-body { flex: 1; overflow-y: auto; display: flex; flex-direction: column;
             align-items: center; padding: 28px 20px 16px; gap: 14px; }
  .oc-body::-webkit-scrollbar { width: 3px; }
  .oc-body::-webkit-scrollbar-thumb { background: var(--line); }
  .oc-icon { font-size: 52px; animation: popIn .4s ease; }
  @keyframes popIn { 0%{transform:scale(.3);opacity:0} 70%{transform:scale(1.1)} 100%{transform:scale(1);opacity:1} }
  .oc-title { font-size: 17px; font-weight: 800; color: var(--ok); }
  .oc-sub { font-size: 12.5px; color: var(--ink2); text-align: center; }
  .oc-items-card { width: 100%; background: var(--bg); border: 1px solid var(--line);
                   border-radius: 14px; overflow: hidden; }
  .oc-items-hdr { padding: 9px 14px; font-size: 10px; font-weight: 700;
                  text-transform: uppercase; letter-spacing: .06em; color: var(--ink2);
                  border-bottom: 1px solid var(--line); background: var(--card); }
  .oc-item { display: flex; justify-content: space-between; align-items: center;
             padding: 10px 14px; font-size: 12.5px; border-bottom: 1px solid var(--line); }
  .oc-item:last-child { border-bottom: none; }
  .oc-item-name { font-weight: 600; color: var(--ink); flex: 1; }
  .oc-item-qty  { color: var(--ink2); margin: 0 10px; font-size: 12px; }
  .oc-item-price { font-weight: 700; color: var(--ink); }
  .oc-meta { width: 100%; background: var(--bg); border: 1px solid var(--line);
             border-radius: 12px; overflow: hidden; }
  .oc-meta-row { display: flex; justify-content: space-between; align-items: center;
                 padding: 10px 14px; font-size: 12.5px; border-bottom: 1px solid var(--line); }
  .oc-meta-row:last-child { border-bottom: none; }
  .oc-meta-label { color: var(--ink2); }
  .oc-meta-val { font-weight: 700; color: var(--ink); }
  .oc-total-banner { width: 100%; padding: 14px 16px; border-radius: 12px;
                     background: linear-gradient(135deg,#ecfdf5,#d1fae5);
                     border: 1px solid #a7f3d0;
                     display: flex; justify-content: space-between; align-items: center; }
  .oc-total-label { font-size: 14px; font-weight: 700; color: var(--ok); }
  .oc-total-val   { font-size: 20px; font-weight: 800; color: var(--ok); }
  .oc-footer { border-top: 1px solid var(--line); padding: 14px 16px; flex-shrink: 0; }
  .oc-new-btn { width: 100%; padding: 12px;
                background: linear-gradient(135deg,var(--brand),var(--brand2));
                color: #fff; border: none; border-radius: 12px;
                font-size: 13.5px; font-weight: 700; cursor: pointer;
                transition: opacity .15s, transform .1s;
                box-shadow: 0 3px 10px rgba(79,70,229,.25); }
  .oc-new-btn:hover { opacity: .9; transform: translateY(-1px); }

  /* ── Order error view ── */
  .oe-body { flex: 1; display: flex; flex-direction: column; align-items: center;
             justify-content: center; padding: 28px 20px; gap: 14px; }
  .oe-icon { font-size: 46px; }
  .oe-title { font-size: 16px; font-weight: 800; color: var(--danger); }
  .oe-msg { font-size: 12.5px; color: var(--ink2); text-align: center; line-height: 1.5; }
  .oe-footer { border-top: 1px solid var(--line); padding: 14px 16px; flex-shrink: 0;
               display: flex; gap: 8px; }
  .oe-retry-btn { flex: 1; padding: 11px; background: var(--brand); color: #fff;
                  border: none; border-radius: 11px; font-size: 13px; font-weight: 700;
                  cursor: pointer; transition: opacity .15s; }
  .oe-retry-btn:hover { opacity: .88; }
  .oe-cart-btn { flex: 1; padding: 11px; background: var(--bg); color: var(--ink);
                 border: 1px solid var(--line); border-radius: 11px; font-size: 13px;
                 font-weight: 600; cursor: pointer; transition: background .15s; }
  .oe-cart-btn:hover { background: var(--line); }

  /* ── Login overlay ── */
  #login-overlay { position:fixed; inset:0; z-index:999;
                   background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);
                   display:flex; align-items:center; justify-content:center; }
  .login-card { background:#fff; border-radius:20px; padding:36px 32px;
                width:100%; max-width:380px;
                box-shadow:0 20px 60px rgba(0,0,0,.28); }
  .login-logo { width:52px; height:52px; border-radius:16px;
                background:linear-gradient(135deg,#4f46e5,#7c3aed);
                display:flex; align-items:center; justify-content:center;
                font-size:24px; margin:0 auto 16px; }
  .login-title { font-size:20px; font-weight:800; color:var(--ink);
                 text-align:center; margin-bottom:4px; }
  .login-sub { font-size:13px; color:var(--ink2); text-align:center; margin-bottom:24px; }
  .login-field { margin-bottom:14px; }
  .login-field label { display:block; font-size:11px; font-weight:700; color:var(--ink2);
                       text-transform:uppercase; letter-spacing:.05em; margin-bottom:6px; }
  .login-field input { width:100%; padding:11px 14px; border:1.5px solid var(--line);
                       border-radius:11px; font-size:14px; outline:none;
                       background:var(--bg); transition:border-color .15s, background .15s;
                       color:var(--ink); }
  .login-field input:focus { border-color:var(--brand); background:#fff; }
  .login-btn { width:100%; padding:13px; margin-top:6px;
               background:linear-gradient(135deg,var(--brand),var(--brand2));
               color:#fff; border:none; border-radius:12px; font-size:15px;
               font-weight:700; cursor:pointer; transition:opacity .15s, transform .1s;
               box-shadow:0 4px 14px rgba(79,70,229,.4); letter-spacing:.01em; }
  .login-btn:hover { opacity:.92; transform:translateY(-1px); }

  /* ── User chip in header ── */
  #user-chip { display:none; align-items:center; gap:10px; }
  .user-avatar { width:34px; height:34px; border-radius:50%;
                 background:rgba(255,255,255,.25); border:2px solid rgba(255,255,255,.4);
                 display:flex; align-items:center; justify-content:center;
                 font-size:13px; font-weight:700; color:#fff; flex-shrink:0; }
  .user-name { font-size:13px; font-weight:600; color:#fff;
               max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #logout-btn { padding:6px 13px; background:rgba(255,255,255,.15);
                border:1px solid rgba(255,255,255,.25); border-radius:9px;
                font-size:11px; font-weight:600; cursor:pointer; color:#fff;
                transition:background .15s; }
  #logout-btn:hover { background:rgba(255,255,255,.28); }
</style>
</head>
<body>

<!-- Login overlay — hidden once session is established -->
<div id="login-overlay">
  <div class="login-card">
    <div class="login-logo">&#x2728;</div>
    <div class="login-title">Shopping Agent</div>
    <div class="login-sub">Powered by Gemini &middot; B2B Commerce</div>
    <div class="login-field">
      <label>Your Name <span style="text-transform:none;letter-spacing:0;font-weight:400;color:var(--danger)">*</span></label>
      <input id="login-name" type="text" placeholder="e.g. Jane Smith" autocomplete="name"/>
    </div>
    <div class="login-field">
      <label>Email <span style="text-transform:none;letter-spacing:0;font-weight:400">(optional)</span></label>
      <input id="login-email" type="email" placeholder="you@company.com" autocomplete="email"/>
    </div>
    <button class="login-btn" onclick="doLogin()">Start Shopping &#x2192;</button>
  </div>
</div>

<header>
  <div class="h-brand">
    <div class="h-logo">&#x2728;</div>
    <div>
      <div class="h-name">Shopping Agent</div>
      <div class="h-sub"><span class="h-dot"></span>Gemini 2.0 Flash &middot; UCP Commerce</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <div id="user-chip">
      <div class="user-avatar" id="user-avatar-text"></div>
      <span class="user-name" id="user-name-display"></span>
      <button id="logout-btn" onclick="doLogout()">Sign out</button>
    </div>
    <button id="reset-btn" onclick="resetConv()">&#8635; New chat</button>
  </div>
</header>

<div class="main">
  <!-- Chat column -->
  <div class="chat-col">
    <div id="messages">
      <div class="thread">
        <div class="row">
          <div class="avatar avatar-agent">&#x2728;</div>
          <div class="bubble bubble-agent">
            Hi! I&rsquo;m your shopping assistant powered by Gemini. Tell me what you&rsquo;re looking for and I&rsquo;ll find the best options for you.
          </div>
        </div>
      </div>
    </div>
    <div id="tools-log"></div>
    <div class="input-bar">
      <div class="input-wrap">
        <input id="msg-input" type="text" placeholder='Try "show me laptops under $2000"' autocomplete="off"/>
        <button id="send-btn" onclick="send()">Send &#8594;</button>
      </div>
    </div>
  </div>

  <!-- Cart panel -->
  <div class="cart-panel">
    <div class="cart-header">
      <div class="cart-title">
        &#x1F6D2; Your Cart
        <span class="cart-count" id="cart-count">0</span>
      </div>
      <div class="cart-hdr-total" id="cart-hdr-total"></div>
    </div>

    <!-- View 1: Cart items -->
    <div id="cart-view">
      <div class="cart-items" id="cart-items">
        <div class="cart-empty">
          <div class="cart-empty-icon">&#x1F6D2;</div>
          <div class="cart-empty-title">Your cart is empty</div>
          <div class="cart-empty-sub">Search for products and add them here</div>
        </div>
      </div>
      <div class="cart-footer">
        <div class="cart-total-row">
          <span class="cart-total-label">Total</span>
          <span class="cart-total-val" id="cart-total">$0.00</span>
        </div>
        <button id="checkout-btn" disabled onclick="showCheckoutForm()">Proceed to Checkout &rarr;</button>
      </div>
    </div>

    <!-- View 2: Checkout form -->
    <div id="checkout-view">
      <div class="co-hdr">
        <button class="co-back-btn" onclick="showCartView()" title="Back">&#8592;</button>
        <span class="co-hdr-title">Checkout</span>
      </div>
      <div class="co-order-summary" id="co-order-summary"></div>
      <div class="co-fields">
        <div class="co-field">
          <label>Name <span style="text-transform:none;letter-spacing:0;font-weight:400">(optional)</span></label>
          <input id="co-name" type="text" placeholder="Your name" autocomplete="name"/>
        </div>
        <div class="co-field">
          <label>Email <span style="text-transform:none;letter-spacing:0;font-weight:400">(optional)</span></label>
          <input id="co-email" type="email" placeholder="you@example.com" autocomplete="email"/>
        </div>
        <div class="co-field">
          <label>Payment Method</label>
          <select id="co-payment">
            <option value="purchase_order">&#x1F4CB; Purchase Order</option>
            <option value="credit_card">&#x1F4B3; Credit Card</option>
          </select>
        </div>
      </div>
      <div class="co-footer">
        <div class="co-total-row">
          <span class="co-total-label">Order total</span>
          <span class="co-total-val" id="co-total-display">$0.00</span>
        </div>
        <button id="place-order-btn" onclick="placeOrder()">&#x2713;&ensp;Place Order</button>
      </div>
    </div>

    <!-- View 3: Order confirmation -->
    <div id="order-confirm">
      <!-- filled by showOrderConfirmation() -->
    </div>
  </div>
</div>

<script>
const messages     = document.getElementById("messages");
const input        = document.getElementById("msg-input");
const sendBtn      = document.getElementById("send-btn");
const toolsLog     = document.getElementById("tools-log");
const cartItems    = document.getElementById("cart-items");
const cartCount    = document.getElementById("cart-count");
const cartTotal    = document.getElementById("cart-total");
const cartHdrTotal = document.getElementById("cart-hdr-total");
const checkoutBtn  = document.getElementById("checkout-btn");
const cartView     = document.getElementById("cart-view");
const checkoutView = document.getElementById("checkout-view");
const orderConfirm = document.getElementById("order-confirm");

// ── Cart state ──────────────────────────────────────────────────────────────
const cart = {};

function cartAddItem(p) {
  if (cart[p.product_id]) { cart[p.product_id].qty++; }
  else { cart[p.product_id] = { title: p.title, price: p.price, image_url: p.image_url, qty: 1 }; }
  renderCart();
}

function cartRemove(pid) { delete cart[pid]; renderCart(); }
function cartSetQty(pid, qty) {
  if (qty < 1) { cartRemove(pid); return; }
  if (cart[pid]) { cart[pid].qty = qty; renderCart(); }
}

function esc(s) { return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;"); }

function renderCart() {
  const items      = Object.entries(cart);
  const totalQty   = items.reduce((s,[,v]) => s + v.qty, 0);
  const totalPrice = items.reduce((s,[,v]) => s + v.price * v.qty, 0);

  cartCount.textContent     = totalQty;
  cartTotal.textContent     = "$" + totalPrice.toFixed(2);
  cartHdrTotal.textContent  = totalQty > 0 ? "$" + totalPrice.toFixed(2) : "";
  checkoutBtn.disabled      = items.length === 0;

  if (items.length === 0) {
    cartItems.innerHTML = `<div class="cart-empty">
      <div class="cart-empty-icon">&#x1F6D2;</div>
      <div class="cart-empty-title">Your cart is empty</div>
      <div class="cart-empty-sub">Search for products and add them here</div>
    </div>`;
    return;
  }
  cartItems.innerHTML = "";
  items.forEach(([pid, item]) => {
    const div = document.createElement("div");
    div.className = "ci";
    const img = item.image_url
      ? `<img class="ci-thumb" src="${esc(item.image_url)}" alt="" onerror="this.style.background='#e5e7eb'">`
      : `<div class="ci-thumb"></div>`;
    div.innerHTML = `${img}
      <div class="ci-info">
        <div class="ci-name" title="${esc(item.title)}">${esc(item.title)}</div>
        <div class="ci-price">$${(item.price||0).toFixed(2)}</div>
        <div class="ci-controls">
          <button class="qty-btn" onclick="cartSetQty('${pid}',${item.qty-1})">&#8722;</button>
          <span class="qty-val">${item.qty}</span>
          <button class="qty-btn" onclick="cartSetQty('${pid}',${item.qty+1})">+</button>
        </div>
      </div>
      <button class="ci-remove" onclick="cartRemove('${pid}')" title="Remove">&#10005;</button>`;
    cartItems.appendChild(div);
  });
}

// ── View switching ──────────────────────────────────────────────────────────
function showCartView() {
  cartView.style.display     = "flex";
  checkoutView.style.display = "none";
  orderConfirm.style.display = "none";
}

function showCheckoutForm() {
  const items = Object.entries(cart);
  if (!items.length) return;
  const total = items.reduce((s,[,v]) => s + v.price * v.qty, 0);

  // Render order summary with thumbnails
  document.getElementById("co-order-summary").innerHTML = items.map(([,item]) => {
    const img = item.image_url
      ? `<img class="co-item-thumb" src="${esc(item.image_url)}" alt="" onerror="this.style.background='#e5e7eb'">`
      : `<div class="co-item-thumb"></div>`;
    return `<div class="co-order-item">${img}
      <span class="co-item-name">${esc(item.title)}</span>
      <span class="co-item-qty">&times;${item.qty}</span>
      <span class="co-item-price">$${(item.price*item.qty).toFixed(2)}</span>
    </div>`;
  }).join("");

  document.getElementById("co-total-display").textContent = "$" + total.toFixed(2);
  cartView.style.display     = "none";
  checkoutView.style.display = "flex";
  orderConfirm.style.display = "none";
}

function showOrderError(message) {
  orderConfirm.innerHTML = `
    <div class="oe-body">
      <div class="oe-icon">&#x26A0;&#xFE0F;</div>
      <div class="oe-title">Order Failed</div>
      <div class="oe-msg">${esc(message)}</div>
    </div>
    <div class="oe-footer">
      <button class="oe-retry-btn" onclick="showCheckoutForm()">&#8592;&ensp;Try Again</button>
      <button class="oe-cart-btn" onclick="showCartView()">Edit Cart</button>
    </div>`;
  cartView.style.display     = "none";
  checkoutView.style.display = "none";
  orderConfirm.style.display = "flex";
}

function showOrderConfirmation(itemsSnap, total, payment, name, sfOrderId) {
  const payLabel  = payment === "purchase_order" ? "Purchase Order" : "Credit Card";
  const itemsHtml = itemsSnap.map(v => `
    <div class="oc-item">
      <span class="oc-item-name">${esc(v.title)}</span>
      <span class="oc-item-qty">&times;${v.qty}</span>
      <span class="oc-item-price">$${(v.price*v.qty).toFixed(2)}</span>
    </div>`).join("");

  const orderNumHtml = sfOrderId
    ? `<div class="oc-meta-row">
         <span class="oc-meta-label">Order #</span>
         <span class="oc-meta-val" style="font-family:monospace;font-size:11px">${esc(sfOrderId)}</span>
       </div>`
    : "";

  orderConfirm.innerHTML = `
    <div class="oc-body">
      <div class="oc-icon">&#x2705;</div>
      <div class="oc-title">Order Confirmed!</div>
      <div class="oc-sub">${name ? "Thank you, " + esc(name) + "!" : "Your order has been placed in Salesforce."}</div>
      <div class="oc-items-card">
        <div class="oc-items-hdr">Order Items</div>
        ${itemsHtml}
      </div>
      <div class="oc-meta">
        ${orderNumHtml}
        <div class="oc-meta-row">
          <span class="oc-meta-label">Payment</span>
          <span class="oc-meta-val">${payLabel}</span>
        </div>
      </div>
      <div class="oc-total-banner">
        <span class="oc-total-label">Total</span>
        <span class="oc-total-val">$${total.toFixed(2)}</span>
      </div>
    </div>
    <div class="oc-footer">
      <button class="oc-new-btn" onclick="startNewOrder()">+ Start New Order</button>
    </div>`;

  cartView.style.display     = "none";
  checkoutView.style.display = "none";
  orderConfirm.style.display = "flex";
}

function startNewOrder() {
  Object.keys(cart).forEach(k => delete cart[k]);
  renderCart();
  showCartView();
}

// ── Chat helpers ────────────────────────────────────────────────────────────
function getThread() {
  let t = messages.querySelector(".thread");
  if (!t) { t = document.createElement("div"); t.className = "thread"; messages.appendChild(t); }
  return t;
}

function renderMarkdown(text) {
  const lines = text.split("\\n");
  let html = "", inList = false;
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
      if (line.trim()) html += "<p style='margin:3px 0'>" + line + "</p>";
    }
  }
  if (inList) html += "</ul>";
  return html;
}

function addAgentMsg(text) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `<div class="avatar avatar-agent">&#x2728;</div>
    <div class="bubble bubble-agent">${renderMarkdown(text)}</div>`;
  getThread().appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

function addUserMsg(text) {
  const row = document.createElement("div");
  row.className = "row user";
  row.innerHTML = `<div class="avatar avatar-user">You</div>
    <div class="bubble bubble-user">${esc(text)}</div>`;
  getThread().appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

function showTyping() {
  const row = document.createElement("div");
  row.id = "typing-row";
  row.className = "row";
  row.innerHTML = `<div class="avatar avatar-agent">&#x2728;</div>
    <div class="bubble bubble-agent"><div class="typing"><span></span><span></span><span></span></div></div>`;
  getThread().appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}
function removeTyping() { document.getElementById("typing-row")?.remove(); }
function setBusy(b) { sendBtn.disabled = b; input.disabled = b; }

function logTools(calls) {
  if (!calls || !calls.length) { toolsLog.textContent = ""; return; }
  toolsLog.textContent = calls.map(c => {
    const args = Object.entries(c.args||{})
      .filter(([,v]) => v != null)
      .map(([k,v]) => k + "=" + JSON.stringify(v)).join(", ");
    return "\\u2699 " + c.tool + "(" + args + ")";
  }).join("  \\u00b7  ");
}

// ── Product cards ───────────────────────────────────────────────────────────
function addProductCards(products) {
  if (!products || !products.length) return;
  const grid = document.createElement("div");
  grid.className = "product-grid";
  products.forEach(p => {
    const card = document.createElement("div");
    card.className = "pcard";
    const price = p.price != null ? `$${Number(p.price).toFixed(2)}` : "";
    const pid = (p.product_id||"").replace(/'/g,"\\'");
    const imgSection = p.image_url
      ? `<div class="pcard-img-wrap">
           <img src="${p.image_url}" alt=""
                onerror="this.parentElement.innerHTML='<div class=pcard-img-placeholder>&#x1F4BB;</div>'">
           ${p.in_stock !== false ? '<span class="pcard-badge">In Stock</span>' : ''}
         </div>`
      : `<div class="pcard-img-wrap"><div class="pcard-img-placeholder">&#x1F4BB;</div></div>`;
    card.innerHTML = `${imgSection}
      <div class="pcard-body">
        ${p.category ? `<div class="pcard-category">${esc(p.category)}</div>` : ""}
        <div class="pcard-title">${esc(p.title||"")}</div>
        <div class="pcard-price">${price}</div>
        <button class="pcard-btn" onclick="cartAction('${pid}',this)">&#x1F6D2;&ensp;Add to Cart</button>
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
  btn.innerHTML = "&#x2713;&ensp;Added";
  btn.disabled = true;
  btn.style.background = "var(--ok)";
}

// ── Place order ─────────────────────────────────────────────────────────────
async function placeOrder() {
  const items = Object.entries(cart);
  if (!items.length) return;
  const btn = document.getElementById("place-order-btn");
  btn.disabled = true;
  btn.innerHTML = "Placing order&#8230;";

  const name      = document.getElementById("co-name").value.trim();
  const email     = document.getElementById("co-email").value.trim();
  const payment   = document.getElementById("co-payment").value;
  const payLabel  = payment === "purchase_order" ? "Purchase Order" : "Credit Card";
  const itemsSnap = items.map(([,v]) => ({...v}));
  const total     = items.reduce((s,[,v]) => s + v.price * v.qty, 0);
  const summary   = items.map(([,v]) => `${v.title} (qty:${v.qty}, $${(v.price*v.qty).toFixed(2)})`).join("; ");

  addUserMsg(`Place my order — ${items.length} item${items.length>1?"s":""}, $${total.toFixed(2)} via ${payLabel}`);
  showCartView();
  setBusy(true);
  showTyping();

  // Build a structured message the agent can parse into a place_order tool call.
  const lineItemsJson = JSON.stringify(items.map(([pid, v]) => ({
    product_id: pid, quantity: v.qty, title: v.title, unit_price: v.price
  })));
  let apiMsg = `Place this B2B order immediately via the place_order tool (no further confirmation needed). ` +
    `Line items: ${lineItemsJson}. Payment: ${payment}. Total: $${total.toFixed(2)}.`;
  if (name)  apiMsg += ` Buyer name: ${name}.`;
  if (email) apiMsg += ` Buyer email: ${email}.`;

  try {
    const res  = await fetch("/api/chat", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:apiMsg}) });
    const data = await res.json();
    removeTyping();
    logTools(data.tool_calls);
    const sfOrderId = data.order?.order_id || data.order?.order_number || "";
    const orderOk   = data.order && (data.order.status === "placed" || sfOrderId);
    if (orderOk) {
      // Success — show confirmation panel and clear cart; suppress verbose agent text.
      showOrderConfirmation(itemsSnap, total, payment, name, sfOrderId);
      Object.keys(cart).forEach(k => delete cart[k]);
      renderCart();
    } else {
      // Extract the first error detail line from the agent reply for the cart panel.
      const errLine = (data.reply || "")
        .split("\\n").find(l => l.toLowerCase().includes("error") || l.toLowerCase().includes("failed") || l.toLowerCase().includes("detail"))
        || "Order could not be placed. Please try again.";
      showOrderError(errLine.replace(/^[>*#\s]+/, "").trim());
      btn.disabled = false;
      btn.innerHTML = "&#x2713;&ensp;Place Order";
    }
  } catch(e) {
    removeTyping();
    showOrderError("Network error — please try again.");
    btn.disabled = false;
    btn.innerHTML = "&#x2713;&ensp;Place Order";
  } finally { setBusy(false); }
}

// ── Send chat message ───────────────────────────────────────────────────────
async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addUserMsg(text);
  setBusy(true);
  showTyping();
  try {
    const res  = await fetch("/api/chat", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:text}) });
    const data = await res.json();
    removeTyping();
    logTools(data.tool_calls);
    if (data.products && data.products.length > 0) {
      addAgentMsg(`Found ${data.products.length} result${data.products.length > 1 ? "s" : ""} for you:`);
      addProductCards(data.products);
    } else {
      addAgentMsg(data.reply);
      if (data.tool_calls && data.tool_calls.some(c => c.tool === "search_products") && !data.products?.length) {
        addNoResultsCard();
      }
    }
  } catch(e) {
    removeTyping();
    addAgentMsg("Something went wrong — please try again.");
  } finally { setBusy(false); }
}

// ── No-results card ─────────────────────────────────────────────────────────
function addNoResultsCard() {
  const el = document.createElement("div");
  el.style.cssText = "margin-left:42px;padding:16px 20px;background:var(--card);border:1px solid var(--line);border-radius:14px;max-width:calc(var(--max-w) - 42px);display:flex;align-items:center;gap:14px;box-shadow:0 1px 4px rgba(0,0,0,.05)";
  el.innerHTML = `<div style="font-size:32px">&#x1F50D;</div>
    <div>
      <div style="font-size:13px;font-weight:700;color:var(--ink);margin-bottom:3px">No products found</div>
      <div style="font-size:12px;color:var(--ink2)">Try different keywords or a broader search</div>
    </div>`;
  getThread().appendChild(el);
  messages.scrollTop = messages.scrollHeight;
}

// ── Reset conversation ──────────────────────────────────────────────────────
async function resetConv() {
  await fetch("/api/reset", { method: "POST" });
  const t = messages.querySelector(".thread");
  if (t) t.innerHTML = `<div class="row"><div class="avatar avatar-agent">&#x2728;</div>
    <div class="bubble bubble-agent">Hi! I&rsquo;m your shopping assistant powered by Gemini. Tell me what you&rsquo;re looking for and I&rsquo;ll find the best options for you.</div></div>`;
  Array.from(messages.children).forEach(c => { if (!c.classList.contains("thread")) c.remove(); });
  toolsLog.textContent = "";
  Object.keys(cart).forEach(k => delete cart[k]);
  renderCart();
  showCartView();
}

// ── Login / session ─────────────────────────────────────────────────────────
async function initSession() {
  // Try a previously-stored client-side session first (avoids a round-trip).
  const ss = sessionStorage.getItem("shopping_session");
  if (ss) { try { applySession(JSON.parse(ss)); return; } catch(_) {} }
  // Fall back to the server session (survives page refreshes).
  try {
    const r = await fetch("/api/session");
    const s = await r.json();
    if (s.name) { applySession(s); return; }
  } catch(_) {}
  // Show login overlay if no active session.
  document.getElementById("login-overlay").style.display = "flex";
}

function applySession(sess) {
  sessionStorage.setItem("shopping_session", JSON.stringify(sess));
  const overlay = document.getElementById("login-overlay");
  if (overlay) overlay.style.display = "none";
  const chip = document.getElementById("user-chip");
  chip.style.display = "flex";
  const displayName = sess.name || sess.email || "Guest";
  document.getElementById("user-name-display").textContent = displayName;
  const initials = displayName.split(" ").map(w => w[0] || "").join("").toUpperCase().slice(0, 2) || "?";
  document.getElementById("user-avatar-text").textContent = initials;
  // Pre-fill checkout name/email from session (only if fields are still blank).
  const coName  = document.getElementById("co-name");
  const coEmail = document.getElementById("co-email");
  if (sess.name  && !coName.value)  coName.value  = sess.name;
  if (sess.email && !coEmail.value) coEmail.value = sess.email;
}

async function doLogin() {
  const nameEl = document.getElementById("login-name");
  const name   = nameEl.value.trim();
  if (!name) { nameEl.focus(); return; }
  const email  = document.getElementById("login-email").value.trim();
  try {
    const r = await fetch("/api/login", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name, email}),
    });
    const d = await r.json();
    if (d.ok) applySession(d.session);
  } catch(e) { alert("Login failed — please try again."); }
}

async function doLogout() {
  await fetch("/api/logout", {method:"POST"});
  sessionStorage.removeItem("shopping_session");
  document.getElementById("user-chip").style.display = "none";
  document.getElementById("login-name").value  = "";
  document.getElementById("login-email").value = "";
  document.getElementById("login-overlay").style.display = "flex";
  // Clear conversation and cart.
  await fetch("/api/reset", {method:"POST"});
  const t = messages.querySelector(".thread");
  if (t) t.innerHTML = `<div class="row"><div class="avatar avatar-agent">&#x2728;</div>
    <div class="bubble bubble-agent">Hi! I&rsquo;m your shopping assistant powered by Gemini. Tell me what you&rsquo;re looking for and I&rsquo;ll find the best options for you.</div></div>`;
  Array.from(messages.children).forEach(c => { if (!c.classList.contains("thread")) c.remove(); });
  toolsLog.textContent = "";
  Object.keys(cart).forEach(k => delete cart[k]);
  renderCart();
  showCartView();
}

initSession();

document.getElementById("login-name").addEventListener("keydown",  e => { if (e.key === "Enter") doLogin(); });
document.getElementById("login-email").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
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
