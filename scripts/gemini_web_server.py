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
        self.cart_additions: list[dict] = []  # {product, quantity} pairs for UI sync

        # Inject a virtual add_to_cart tool so Gemini uses it instead of going
        # straight to create_checkout_session when the user says "add to cart".
        from google.genai import types as _gtypes  # already imported transitively
        self._tools.append(
            _gtypes.Tool(
                function_declarations=[
                    _gtypes.FunctionDeclaration(
                        name="add_to_cart",
                        description=(
                            "Add a product to the shopping cart. Call this whenever the user "
                            "asks to add an item to the cart. Do NOT proceed to "
                            "create_checkout_session without the user's explicit confirmation."
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "product_id": {
                                    "type": "string",
                                    "description": "The product_id returned by search_products",
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": "Quantity to add (default 1)",
                                },
                            },
                            "required": ["product_id"],
                        },
                    )
                ]
            )
        )
        # Rebuild the generation config so the new tool is included
        self._config = _gtypes.GenerateContentConfig(
            tools=self._tools,
            system_instruction=self._system_prompt(),
        )

    def _call_ucp(self, fn_name: str, args: dict) -> dict:
        if fn_name == "add_to_cart":
            # Virtual tool: record the addition for UI sync and return success.
            pid = args.get("product_id", "")
            qty = int(args.get("quantity", 1))
            product = next((p for p in self.last_products if p.get("product_id") == pid), None)
            if product:
                self.cart_additions.append({"product": product, "quantity": qty})
                return {"ok": True, "message": f"Added {qty}x {product.get('title', pid)} to cart."}
            return {"ok": True, "message": f"Added product {pid} (qty {qty}) to cart."}

        result = super()._call_ucp(fn_name, args)
        self.last_tool_calls.append({"tool": fn_name, "args": args})
        if fn_name == "search_products":
            self.last_products = result.get("products", [])
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
    """Validate credentials via the Salesforce SOAP Partner API login endpoint.

    Works without any Connected App flow setting — the classic SOAP login
    accepts username + password directly against any Salesforce org.
    Sandbox orgs use test.salesforce.com; production uses login.salesforce.com.
    If the user's IP is not in the org's trusted range they must append their
    security token to the password (e.g. MyPassword + MyToken).
    """
    import re as _re
    login_host = "test.salesforce.com" if "sandbox" in SF_BASE else "login.salesforce.com"
    soap_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:urn="urn:partner.soap.sforce.com">'
        "<soapenv:Body><urn:login>"
        f"<urn:username>{username}</urn:username>"
        f"<urn:password>{password}</urn:password>"
        "</urn:login></soapenv:Body></soapenv:Envelope>"
    )
    async with _httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://{login_host}/services/Soap/u/59.0",
            content=soap_body.encode(),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        )
    if resp.status_code == 200 and "<sessionId>" in resp.text:
        return True, ""
    fault = _re.search(r"<faultstring>(.*?)</faultstring>", resp.text)
    msg = fault.group(1) if fault else f"Salesforce returned HTTP {resp.status_code}."
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


@app.get("/api/server-ip")
async def server_ip() -> dict:
    """Return this server's outbound public IP (used to configure Salesforce trusted IP ranges)."""
    async with _httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://api.ipify.org?format=json")
        return r.json()


@app.post("/api/chat")
async def chat(body: ChatRequest) -> dict:
    """Send a message to Gemini and return the reply + any products found."""
    global _agent
    try:
        agent = get_agent()
        agent.last_products = []
        agent.last_tool_calls = []
        agent.cart_additions = []
        reply = agent.send(body.message)
        return {
            "reply": reply,
            "products": agent.last_products,
            "tool_calls": agent.last_tool_calls,
            "cart_additions": agent.cart_additions,
        }
    except Exception as e:
        _agent = None  # reset so next request retries agent init
        error_msg = str(e)
        if "API key" in error_msg or "INVALID_ARGUMENT" in error_msg:
            return {"reply": "Invalid Gemini API key. Please check your GEMINI_API_KEY.", "products": [], "tool_calls": []}
        if any(x in error_msg for x in ["502", "503", "ucp", "manifest", "connect"]):
            return {"reply": f"The storefront backend is not responding ({UCP_BASE}). Please check that it is running.", "products": [], "tool_calls": []}
        return {"reply": f"Error: {error_msg}", "products": [], "tool_calls": []}


@app.post("/api/cart")
async def add_to_cart(body: CartRequest) -> dict:
    """Tell Gemini to add a specific product to the cart and proceed to checkout."""
    global _agent
    try:
        agent = get_agent()
    except Exception as e:
        _agent = None
        return {"reply": f"Error initialising agent: {e}", "tool_calls": []}
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
