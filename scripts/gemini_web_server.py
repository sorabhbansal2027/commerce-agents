#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Gemini Commerce Web Server
==========================
FastAPI backend that powers the React shopping app.
Wraps GeminiUCPAgent and exposes clean JSON endpoints with CORS
so any browser-based React / Next.js app can connect.

Run:
    pip install fastapi uvicorn google-genai httpx
    GEMINI_API_KEY=your-key python scripts/gemini_web_server.py

Endpoints:
    POST /api/chat        { message }  → { reply, products, tool_calls }
    POST /api/cart        { product_id, quantity? } → { reply, cart_reply }
    POST /api/reset       {} → { ok }
    GET  /api/health      → { status, store, model }
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Load GeminiUCPAgent from gemini_mcp_ui_server ─────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "gemini_ucp_agent",
    REPO_ROOT / "examples" / "demo_common" / "gemini_ucp_agent.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
GeminiUCPAgent = _mod.GeminiUCPAgent

# ── Config ────────────────────────────────────────────────────────────────────
UCP_BASE    = os.environ.get("UCP_BASE", "https://diligent-flow-production-afd7.up.railway.app")
PORT        = int(os.environ.get("PORT", os.environ.get("GEMINI_WEB_PORT", "8090")))
API_KEY     = os.environ.get("GEMINI_API_KEY")
MODEL       = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
SF_BASE     = os.environ.get("SF_INSTANCE_URL", "").rstrip("/")
SF_CLIENT_ID     = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET", "")

if not API_KEY:
    print("Error: GEMINI_API_KEY environment variable is required")
    sys.exit(1)


# ── Agent with product + cart capture ────────────────────────────────────────
class TrackedAgent(GeminiUCPAgent):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_products: list[dict] = []
        self.last_tool_calls: list[dict] = []
        self.cart_items: list[dict] = []  # tracks what's been added

    def _call_ucp(self, fn_name: str, args: dict) -> dict:
        result = super()._call_ucp(fn_name, args)
        self.last_tool_calls.append({"tool": fn_name, "args": args})
        if fn_name == "search_products":
            self.last_products = result.get("products", [])
        if fn_name == "create_checkout_session":
            # Capture checkout result
            self.last_checkout = result
        return result


_agent: TrackedAgent | None = None


def get_agent() -> TrackedAgent:
    global _agent
    if _agent is None:
        _agent = TrackedAgent(ucp_base=UCP_BASE, api_key=API_KEY, model=MODEL)
    return _agent


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Gemini Commerce API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # lock this down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response models ─────────────────────────────────────────────────
DEMO_USER = os.environ.get("DEMO_USER", "demo")
DEMO_PASS = os.environ.get("DEMO_PASSWORD", "demo123")

import httpx as _httpx  # noqa: E402 – already a dep, imported here for clarity


async def _sf_authenticate(username: str, password: str) -> tuple[bool, str]:
    """Validate credentials via Salesforce Username-Password OAuth flow.

    Returns (True, "") on success or (False, error_message) on failure.
    The Connected App must have 'Allow Username-Password Flows' enabled.
    If the user's IP is not in the org's trusted range, they must append
    their security token to the password (password+token).
    """
    async with _httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{SF_BASE}/services/oauth2/token",
            data={
                "grant_type": "password",
                "client_id": SF_CLIENT_ID,
                "client_secret": SF_CLIENT_SECRET,
                "username": username,
                "password": password,
            },
        )
    if resp.status_code == 200:
        return True, ""
    try:
        err = resp.json()
        msg = err.get("error_description") or err.get("error") or "Authentication failed."
    except Exception:
        msg = f"Salesforce returned HTTP {resp.status_code}."
    return False, msg


class LoginRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    message: str

class CartRequest(BaseModel):
    product_id: str
    quantity: int = 1
    product_name: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/api/login")
async def login(body: LoginRequest) -> dict:
    if SF_BASE and SF_CLIENT_ID and SF_CLIENT_SECRET:
        ok, error = await _sf_authenticate(body.username, body.password)
        return {"ok": ok} if ok else {"ok": False, "error": error}
    # Fallback for local dev when SF env vars are not set
    if body.username == DEMO_USER and body.password == DEMO_PASS:
        return {"ok": True}
    return {"ok": False, "error": "Invalid username or password."}


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "store": UCP_BASE,
        "model": MODEL,
        "auth_mode": "salesforce" if (SF_BASE and SF_CLIENT_ID and SF_CLIENT_SECRET) else "demo",
    }


@app.post("/api/chat")
async def chat(body: ChatRequest) -> dict:
    """Send a message to Gemini and return the reply + any products found."""
    agent = get_agent()
    agent.last_products = []
    agent.last_tool_calls = []
    try:
        reply = agent.send(body.message)
        return {
            "reply": reply,
            "products": agent.last_products,
            "tool_calls": agent.last_tool_calls,
        }
    except Exception as e:
        error_msg = str(e)
        if "API key" in error_msg or "INVALID_ARGUMENT" in error_msg:
            return {"reply": "Invalid Gemini API key. Please check your GEMINI_API_KEY.", "products": [], "tool_calls": []}
        return {"reply": f"Error: {error_msg}", "products": [], "tool_calls": []}


@app.post("/api/cart")
async def add_to_cart(body: CartRequest) -> dict:
    """Tell Gemini to add a specific product to the cart and proceed to checkout."""
    agent = get_agent()
    agent.last_tool_calls = []
    name_hint = f" ({body.product_name})" if body.product_name else ""
    msg = f"Add product {body.product_id}{name_hint} to my cart, quantity {body.quantity}."
    try:
        reply = agent.send(msg)
        return {
            "reply": reply,
            "tool_calls": agent.last_tool_calls,
        }
    except Exception as e:
        return {"reply": f"Error: {e}", "tool_calls": []}


@app.post("/api/reset")
async def reset() -> dict:
    """Clear the conversation history."""
    agent = get_agent()
    agent.reset()
    agent.last_products = []
    agent.last_tool_calls = []
    return {"ok": True}


# ── Static frontend (production: dist/ built by Dockerfile.gemini) ───────────
_dist = Path(
    os.environ.get(
        "STATIC_DIR",
        str(REPO_ROOT / "examples" / "gemini-storefront" / "dist"),
    )
)
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Gemini Commerce API  [{MODEL}]")
    print(f"Store  : {UCP_BASE}")
    print(f"API    : http://localhost:{PORT}")
    print(f"React  : point your app at http://localhost:{PORT}/api\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
