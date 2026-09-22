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
SF_BASE          = os.environ.get("SF_INSTANCE_URL", "").rstrip("/")
SF_CLIENT_ID     = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET", "")
# Experience Cloud site URL — ACCF authorize/token must go through this domain.
SF_COMMUNITY_URL = os.environ.get("SF_COMMUNITY_URL", "").rstrip("/")
# Registered callback URL on the Connected App — not actually redirected to,
# but must match exactly what is entered in the Connected App settings.
SF_CALLBACK_URL  = os.environ.get("SF_CALLBACK_URL", "https://perfect-achievement-production-83ee.up.railway.app/api/oauth/callback")

if not API_KEY:
    print("Error: GEMINI_API_KEY environment variable is required")
    sys.exit(1)


# ── In-memory quote store (survives for the session lifetime) ─────────────────
_quotes: dict[str, dict] = {}


# ── Agent with product + cart capture ────────────────────────────────────────
class TrackedAgent(GeminiUCPAgent):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_products: list[dict] = []
        self.last_tool_calls: list[dict] = []
        self.cart_additions: list[dict] = []  # NEW additions this turn only — sent to frontend
        self.cart: list[dict] = []            # Persistent cart across turns — used for save/checkout
        self.checkout_session: dict | None = None
        self.quote_result: dict | None = None

        from google.genai import types as _gtypes
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
                                "product_id": {"type": "string", "description": "The product_id returned by search_products"},
                                "quantity": {"type": "integer", "description": "Quantity to add (default 1)"},
                            },
                            "required": ["product_id"],
                        },
                    ),
                    _gtypes.FunctionDeclaration(
                        name="save_cart_as_quote",
                        description=(
                            "Save the current cart items as a draft quote. "
                            "Call this when the user asks to save, quote, or get a price quote for cart items."
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Optional quote name or reference"},
                            },
                        },
                    ),
                    _gtypes.FunctionDeclaration(
                        name="load_quote_to_cart",
                        description="Load an existing saved quote's items into the shopping cart.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "quote_id": {"type": "string", "description": "Quote ID (e.g. Q-ABCD1234) from list_quotes"},
                            },
                            "required": ["quote_id"],
                        },
                    ),
                    _gtypes.FunctionDeclaration(
                        name="list_quotes",
                        description="List all saved quotes so the user can pick one to load or review.",
                        parameters={"type": "object", "properties": {}},
                    ),
                    _gtypes.FunctionDeclaration(
                        name="place_b2b_order",
                        description=(
                            "Place a real Salesforce B2B Commerce order. "
                            "ALWAYS use this for any checkout or order placement request — "
                            "never use create_checkout_session instead. "
                            "If this returns an error, report it to the user and stop."
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "payment_handler": {
                                    "type": "string",
                                    "description": "purchase_order or credit_card",
                                },
                                "po_number":   {"type": "string", "description": "Purchase order number provided by the buyer (e.g. PO-2024-001)"},
                                "buyer_name":  {"type": "string", "description": "Buyer full name"},
                                "buyer_email": {"type": "string", "description": "Buyer email"},
                            },
                        },
                    ),
                ]
            )
        )
        # Rebuild config with all injected tools
        self._config = _gtypes.GenerateContentConfig(
            tools=self._tools,
            system_instruction=self._system_prompt(),
        )

    def _call_ucp(self, fn_name: str, args: dict) -> dict:  # noqa: C901
        import uuid as _uuid
        import datetime as _dt

        if fn_name == "add_to_cart":
            pid = args.get("product_id", "")
            qty = int(args.get("quantity", 1))
            product = next((p for p in self.last_products if p.get("product_id") == pid), None)
            # Fallback: live UCP lookup when product isn't in last_products
            # (happens when Gemini calls add_to_cart without a preceding search this turn)
            if not product and pid:
                try:
                    import httpx as _hx
                    r = _hx.get(f"{self.base}/ucp/products/{pid}", timeout=8)
                    if r.status_code == 200:
                        fetched = r.json()
                        if fetched.get("product_id"):
                            product = fetched
                            self.last_products.append(product)
                except Exception:
                    pass
            self.last_tool_calls.append({"tool": fn_name, "args": args})
            if product:
                # Persist in self.cart (survives across turns)
                existing = next((c for c in self.cart if c["product"]["product_id"] == pid), None)
                if existing:
                    existing["quantity"] += qty
                else:
                    self.cart.append({"product": product, "quantity": qty})
                # Also record as this-turn addition so the UI syncs the cart display
                self.cart_additions.append({"product": product, "quantity": qty})
                return {"ok": True, "message": f"Added {qty}x {product.get('title', pid)} to cart."}
            return {"ok": True, "message": f"Added product {pid} (qty {qty}) to cart."}

        if fn_name == "save_cart_as_quote":
            self.last_tool_calls.append({"tool": fn_name, "args": args})
            # Use persistent self.cart, NOT self.cart_additions (which is reset each turn)
            if not self.cart:
                return {"error": "Cart is empty — add items before saving a quote."}
            quote_id = f"Q-{_uuid.uuid4().hex[:8].upper()}"
            name = args.get("name") or f"Quote {len(_quotes) + 1}"
            items = [
                {
                    "product_id": a["product"]["product_id"],
                    "title": a["product"]["title"],
                    "price": a["product"]["price"],
                    "currency": a["product"].get("currency", "USD"),
                    "image_url": a["product"].get("image_url"),
                    "quantity": a["quantity"],
                }
                for a in self.cart
            ]
            quote = {
                "quote_id": quote_id,
                "name": name,
                "items": items,
                "total": round(sum(i["price"] * i["quantity"] for i in items), 2),
                "currency": "USD",
                "created_at": _dt.datetime.utcnow().isoformat(),
            }
            _quotes[quote_id] = quote
            self.quote_result = quote  # captured for UI card rendering
            return {"ok": True, "quote_id": quote_id, "name": name, "total": quote["total"], "items_count": len(items)}

        if fn_name == "load_quote_to_cart":
            self.last_tool_calls.append({"tool": fn_name, "args": args})
            qid = args.get("quote_id", "")
            if qid not in _quotes:
                return {"error": f"Quote {qid!r} not found. Use list_quotes to see available quotes."}
            quote = _quotes[qid]
            for item in quote["items"]:
                product = {
                    "product_id": item["product_id"],
                    "title": item["title"],
                    "price": item["price"],
                    "currency": item.get("currency", "USD"),
                    "image_url": item.get("image_url"),
                    "in_stock": True,
                }
                # Keep both persistent cart and this-turn additions in sync
                existing = next((c for c in self.cart if c["product"]["product_id"] == item["product_id"]), None)
                if existing:
                    existing["quantity"] += item["quantity"]
                else:
                    self.cart.append({"product": product, "quantity": item["quantity"]})
                self.cart_additions.append({"product": product, "quantity": item["quantity"]})
            return {"ok": True, "quote_id": qid, "items_loaded": len(quote["items"]), "total": quote["total"]}

        if fn_name == "list_quotes":
            self.last_tool_calls.append({"tool": fn_name, "args": args})
            if not _quotes:
                return {"quotes": [], "message": "No quotes saved yet."}
            return {
                "quotes": [
                    {"quote_id": q["quote_id"], "name": q["name"], "total": q["total"],
                     "items_count": len(q["items"]), "created_at": q["created_at"]}
                    for q in _quotes.values()
                ]
            }

        if fn_name == "place_b2b_order":
            self.last_tool_calls.append({"tool": fn_name, "args": args})
            # Build line_items from persistent cart and call the real B2B order endpoint
            if not self.cart:
                return {"error": "Cart is empty — add items before placing an order. Do not call any other tool."}
            line_items = [
                {
                    "product_id": c["product"]["product_id"],
                    "title": c["product"]["title"],
                    "unit_price": c["product"]["price"],
                    "quantity": c["quantity"],
                }
                for c in self.cart
            ]
            buyer = {}
            if args.get("buyer_name"):
                buyer["name"] = args["buyer_name"]
            if args.get("buyer_email"):
                buyer["email"] = args["buyer_email"]
            po_number = args.get("po_number", "")
            try:
                import httpx as _httpx_inner
                resp = _httpx_inner.post(
                    f"{self.base}/ucp/orders",
                    json={
                        "line_items": line_items,
                        "payment_handler": args.get("payment_handler", "purchase_order"),
                        "po_number": po_number,
                        "buyer": buyer,
                        "buyer_user_id": _sf_buyer_user_id,
                        "buyer_account_id": _sf_account_id,  # skip SOQL in ucp.py
                    },
                    timeout=30,
                )
                result = resp.json()
                if resp.status_code in (200, 201) and "order_id" in result:
                    if po_number:
                        result["po_number"] = po_number
                    self.checkout_session = result  # reuse card for both pending + placed
                    return {"ok": True, "order_id": result["order_id"], "status": result.get("status", "placed"),
                            "subtotal": result.get("subtotal"), "currency": result.get("currency", "USD"),
                            "po_number": po_number or None}
                return {"error": result.get("error", f"Order failed (HTTP {resp.status_code})") + " Do not call any other tool — report this error to the user."}
            except Exception as exc:
                return {"error": f"Could not reach order endpoint: {exc}. Do not call any other tool — report this error to the user."}

        result = super()._call_ucp(fn_name, args)
        self.last_tool_calls.append({"tool": fn_name, "args": args})
        if fn_name == "search_products":
            self.last_products = result.get("products", [])
        elif fn_name == "create_checkout_session" and "checkout_session_id" in result:
            self.checkout_session = result
        return result

    def reset(self) -> None:
        super().reset()
        self.cart = []
        self.cart_additions = []
        self.quote_result = None
        self.checkout_session = None

    def _system_prompt(self) -> str:
        # Remove the "always confirm first" instruction — the UI renders a
        # CheckoutConfirmation card when create_checkout_session is called,
        # so no separate text confirmation step is needed.
        base = super()._system_prompt()
        base = base.replace(
            "When creating a checkout session, always confirm the items and total with the user first. ",
            "When the user asks to checkout or place an order, always call place_b2b_order immediately. "
            "NEVER call create_checkout_session for checkout — it only creates a pending draft and does "
            "NOT place a real order. Do NOT ask for confirmation in text first. "
            "If place_b2b_order returns an error, relay that exact error message to the user and STOP — "
            "do not call any other tool. ",
        )
        base += (
            "\n\nUI RENDERING RULE: When you call place_b2b_order or save_cart_as_quote, the application "
            "renders a structured UI card automatically. After calling one of these tools, respond with "
            "ONE brief sentence only (e.g. 'Your order has been placed.' or 'Quote saved.'). "
            "Do NOT output markdown lists, item breakdowns, prices, or formatted summaries — "
            "those details are already shown in the card."
        )
        return base


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


async def _sf_authenticate(username: str, password: str) -> tuple[bool, str, str, str]:
    """Authenticate buyer via Salesforce Authorization Code and Credentials Flow (ACCF).

    This headless OAuth 2.0 flow accepts username/password server-side without
    any browser redirect. It works from any IP permanently because it goes through
    the Connected App OAuth layer (not org-level SOAP IP restrictions).

    Requirements (one-time Salesforce setup):
      - Org:  "Allow Authorization Code and Credentials Flows" = ON
      - App:  "Enable Authorization Code and Credentials Flow" = ON (already done)
      - App:  Callback URL includes SF_CALLBACK_URL value

    Flow:
      1. Generate PKCE pair (code_verifier / code_challenge).
      2. POST /services/oauth2/authorize with response_type=code_credentials
         → Salesforce returns {"code": "..."} without any browser redirect.
      3. POST /services/oauth2/token to exchange code for access token.
      4. SOQL to resolve Account Id under the buyer's own token.

    Returns (ok, error, user_id, account_id).
    """
    import hashlib as _hashlib
    import secrets as _secrets

    if not SF_CLIENT_ID or not SF_CLIENT_SECRET or not SF_BASE:
        return False, "Salesforce credentials not configured.", "", ""

    # ── Step 1: PKCE ──────────────────────────────────────────────────────────
    import base64 as _b64
    raw_verifier = _secrets.token_bytes(32)
    code_verifier = _b64.urlsafe_b64encode(raw_verifier).rstrip(b"=").decode()
    code_challenge = _b64.urlsafe_b64encode(
        _hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    # ACCF must go through the Experience Cloud site URL, not the main instance.
    oauth_base = SF_COMMUNITY_URL if SF_COMMUNITY_URL else SF_BASE

    import uuid as _uuid
    nonce = _uuid.uuid4().hex

    # ── Step 2: POST credentials → receive authorization code ─────────────────
    # Salesforce ACCF validates the Origin header — must match a CORS-allowed origin
    # on the Connected App (Setup → Connected App → CORS/Allowed Origins).
    app_origin = SF_CALLBACK_URL.rsplit("/", 2)[0]  # strip path → just the origin
    async with _httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        auth = await client.post(
            f"{oauth_base}/services/oauth2/authorize",
            data={
                "response_type": "code_credentials",
                "client_id": SF_CLIENT_ID,
                "redirect_uri": SF_CALLBACK_URL,
                "scope": "api full",
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "username": username,
                "password": password,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": app_origin,
            },
        )
    if auth.status_code != 200:
        err = auth.json() if auth.headers.get("content-type", "").startswith("application/json") else {}
        raw = err.get("error_description") or err.get("error") or f"HTTP {auth.status_code}"
        # Show full response for debugging
        return False, f"[ACCF authorize {auth.status_code}] {raw} | body={auth.text[:300]}", "", ""

    code = auth.json().get("code", "")
    if not code:
        return False, "No authorization code returned by Salesforce.", "", ""

    # ── Step 3: exchange code for access token ────────────────────────────────
    async with _httpx.AsyncClient(timeout=15) as client:
        tok = await client.post(
            f"{oauth_base}/services/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": SF_CLIENT_ID,
                "client_secret": SF_CLIENT_SECRET,
                "redirect_uri": SF_CALLBACK_URL,
                "code_verifier": code_verifier,
            },
        )
    if tok.status_code != 200:
        err = tok.json() if tok.headers.get("content-type", "").startswith("application/json") else {}
        return False, err.get("error_description", f"Token exchange failed: HTTP {tok.status_code}"), "", ""

    data = tok.json()
    access_token = data.get("access_token", "")
    user_id = data.get("id", "").rsplit("/", 1)[-1]
    auth_hdr = {"Authorization": f"Bearer {access_token}"}

    # ── Step 4: resolve Account Id ────────────────────────────────────────────
    async with _httpx.AsyncClient(timeout=15) as client:
        qs = await client.get(
            f"{SF_BASE}/services/data/v62.0/query",
            params={"q": f"SELECT AccountId, ContactId FROM User WHERE Id = '{user_id}' LIMIT 1"},
            headers=auth_hdr,
        )
    rows = qs.json().get("records", []) if qs.status_code < 400 else []
    account_id = (rows[0].get("AccountId") or "") if rows else ""

    if not account_id and rows and rows[0].get("ContactId"):
        async with _httpx.AsyncClient(timeout=15) as client:
            qs2 = await client.get(
                f"{SF_BASE}/services/data/v62.0/query",
                params={"q": f"SELECT AccountId FROM Contact WHERE Id = '{rows[0]['ContactId']}' LIMIT 1"},
                headers=auth_hdr,
            )
        rows2 = qs2.json().get("records", []) if qs2.status_code < 400 else []
        account_id = rows2[0].get("AccountId", "") if rows2 else ""

    return True, "", user_id, account_id


# Salesforce buyer session — both set on successful login, cleared on logout/redeploy.
# Empty strings mean no active session; place_b2b_order will fail fast.
_sf_buyer_user_id: str = ""
_sf_account_id: str = ""  # returned by buyer/login; eliminates _account_id_for_user SOQL

class LoginRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    message: str

class CartRequest(BaseModel):
    product_id: str
    quantity: int = 1
    product_name: str = ""

class QuoteItemIn(BaseModel):
    product_id: str
    title: str
    price: float
    currency: str = "USD"
    image_url: str | None = None
    quantity: int = 1

class QuoteCreateRequest(BaseModel):
    name: str = ""
    items: list[QuoteItemIn]


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/api/login")
async def login(body: LoginRequest) -> dict:
    global _sf_buyer_user_id, _sf_account_id
    if SF_BASE and SF_CLIENT_ID and SF_CLIENT_SECRET:
        ok, error, user_id, account_id = await _sf_authenticate(body.username, body.password)
        if ok:
            _sf_buyer_user_id = user_id
            _sf_account_id = account_id
            return {"ok": True}
        return {"ok": False, "error": error}
    # Fallback for local dev when SF env vars are not set
    if body.username == DEMO_USER and body.password == DEMO_PASS:
        return {"ok": True}
    return {"ok": False, "error": "Invalid username or password."}


@app.get("/api/active-cart")
async def active_cart() -> dict:
    """Fetch the signed-in buyer's active Salesforce cart and sync it into the agent."""
    if not _sf_buyer_user_id:
        return {"items": []}
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{UCP_BASE}/ucp/cart",
                params={"buyer_user_id": _sf_buyer_user_id},
            )
        data = resp.json()
        items = data.get("items", [])
        # Sync into the agent's persistent cart so tools like save_cart_as_quote work
        agent = get_agent()
        agent.cart = []
        agent.last_products = []
        for it in items:
            product = {
                "product_id": it["product_id"],
                "title": it["title"],
                "price": it["price"],
                "currency": it.get("currency", "USD"),
                "image_url": it.get("image_url"),
                "in_stock": True,
            }
            agent.cart.append({"product": product, "quantity": it["quantity"]})
            agent.last_products.append(product)
        return {"items": items}
    except Exception as exc:
        return {"items": [], "warning": str(exc)}


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "store": UCP_BASE,
        "model": MODEL,
        "auth_mode": "salesforce" if (SF_BASE and SF_CLIENT_ID and SF_CLIENT_SECRET) else "demo",
    }


@app.get("/api/session")
async def session_status() -> dict:
    """Return whether the server has an active buyer session.

    In Salesforce auth mode the session is valid only after a successful login
    that sets the in-memory buyer user ID.  In demo mode a login just checks a
    static password so there is nothing server-side to lose — always valid.
    """
    sf_mode = bool(SF_BASE and SF_CLIENT_ID and SF_CLIENT_SECRET)
    return {
        "authenticated": bool(_sf_buyer_user_id) if sf_mode else True,
        "auth_mode": "salesforce" if sf_mode else "demo",
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
        agent.cart_additions = []       # this-turn additions only; self.cart persists
        agent.checkout_session = None
        agent.quote_result = None
        reply = agent.send(body.message)
        return {
            "reply": reply,
            "products": agent.last_products,
            "tool_calls": agent.last_tool_calls,
            "cart_additions": agent.cart_additions,
            "checkout_session": agent.checkout_session,
            "quote_result": agent.quote_result,
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
    """Clear conversation history and cart state."""
    agent = get_agent()
    agent.reset()   # clears history, cart, cart_additions, quote_result, checkout_session
    agent.last_products = []
    agent.last_tool_calls = []
    return {"ok": True}


# ── Quote endpoints ────────────────────────────────────────────────────────────
@app.post("/api/quotes", status_code=201)
async def create_quote(body: QuoteCreateRequest) -> dict:
    """Create a named quote from an explicit list of cart items."""
    import uuid as _uuid
    import datetime as _dt
    quote_id = f"Q-{_uuid.uuid4().hex[:8].upper()}"
    items = [i.model_dump() for i in body.items]
    quote = {
        "quote_id": quote_id,
        "name": body.name or f"Quote {quote_id}",
        "items": items,
        "total": round(sum(i["price"] * i["quantity"] for i in items), 2),
        "currency": "USD",
        "created_at": _dt.datetime.utcnow().isoformat(),
    }
    _quotes[quote_id] = quote
    return quote


@app.get("/api/quotes")
async def list_quotes() -> dict:
    """Return all saved quotes for the session."""
    return {"quotes": list(_quotes.values())}


@app.get("/api/quotes/{quote_id}")
async def get_quote(quote_id: str) -> dict:
    """Return a single quote by ID."""
    from fastapi import HTTPException
    if quote_id not in _quotes:
        raise HTTPException(status_code=404, detail=f"Quote {quote_id!r} not found")
    return _quotes[quote_id]


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
