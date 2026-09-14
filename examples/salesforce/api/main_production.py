"""Production entry point: Salesforce OMS backend + CORS + Bedrock-compatible tools.

Uvicorn target: salesforce.api.main_production:app  (--app-dir examples)

Applies the same two production patches as retail.api.main_production:
  1. Strips eager_input_streaming from tool definitions (Bedrock rejects it).
  2. Adds CORSMiddleware for the Salesforce sandbox origin via CORS_ORIGINS env var.

Required env vars (set in Railway):
  SF_INSTANCE_URL   — https://<org>.sandbox.my.salesforce.com
  SF_CLIENT_ID      — Connected App consumer key
  SF_CLIENT_SECRET  — Connected App consumer secret
  ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY — LiteLLM proxy (same as retail)
  CORS_ORIGINS      — comma-separated allowed origins
"""

from __future__ import annotations

import json
import os

import httpx
from starlette.middleware.cors import CORSMiddleware

# ── Bedrock compatibility patch (must run before salesforce.api.main is imported) ─
try:
    from anthropic import AsyncAnthropic

    class _BedrockCompatTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if '/messages' in request.url.path and request.content:
                try:
                    body = json.loads(request.content)
                    dirty = False

                    # Strip eager_input_streaming — Bedrock rejects non-standard tool fields.
                    if any('eager_input_streaming' in t for t in body.get('tools', [])):
                        for tool in body['tools']:
                            tool.pop('eager_input_streaming', None)
                        dirty = True

                    # Strip thinking — Bedrock rejects thinking when tool_choice forces
                    # a specific tool ("Thinking may not be enabled when tool_choice
                    # forces tool use").
                    if 'thinking' in body:
                        body.pop('thinking')
                        dirty = True

                    if dirty:
                        content = json.dumps(body).encode('utf-8')
                        headers = dict(request.headers)
                        headers['content-length'] = str(len(content))
                        request = httpx.Request(
                            method=request.method,
                            url=request.url,
                            headers=headers,
                            content=content,
                        )
                except Exception:
                    pass
            return await super().handle_async_request(request)

    _orig_init = AsyncAnthropic.__init__

    def _patched_init(self, *args, **kwargs):
        if 'http_client' not in kwargs:
            base_url = (
                kwargs.get('base_url')
                or os.environ.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')
            )
            kwargs['http_client'] = httpx.AsyncClient(
                base_url=str(base_url).rstrip('/'),
                transport=_BedrockCompatTransport(),
                timeout=httpx.Timeout(600.0),
            )
        _orig_init(self, *args, **kwargs)

    AsyncAnthropic.__init__ = _patched_init  # type: ignore[method-assign]
except Exception:
    pass

# ── App import (after patch so agents pick up patched client) ──────────────────
from salesforce.api.main import app  # noqa: E402

# ── External CORS for Salesforce LWC ──────────────────────────────────────────
_origins = [o.strip() for o in os.environ.get('CORS_ORIGINS', '').split(',') if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=['*'],
        allow_headers=['*'],
        allow_credentials=True,
    )
