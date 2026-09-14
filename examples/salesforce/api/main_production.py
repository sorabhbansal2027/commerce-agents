"""Production entry point: Salesforce OMS backend + CORS + proxy-compat patch.

Uvicorn target: salesforce.api.main_production:app  (--app-dir examples)

Direct Anthropic API (recommended — full Claude thinking + streaming):
  ANTHROPIC_API_KEY  — key from console.anthropic.com
  (leave ANTHROPIC_BASE_URL unset)

Via LiteLLM/Bedrock proxy:
  ANTHROPIC_BASE_URL — http://litellm-service:4000
  ANTHROPIC_API_KEY  — proxy master key

When ANTHROPIC_BASE_URL points to a proxy the transport patch strips:
  - eager_input_streaming (Bedrock rejects non-standard tool fields)
  - thinking (Bedrock rejects thinking + forced tool_choice together)

With direct Anthropic API neither strip is applied so full thinking works.

Required env vars (set in Railway):
  SF_INSTANCE_URL   — https://<org>.sandbox.my.salesforce.com
  SF_CLIENT_ID      — Connected App consumer key
  SF_CLIENT_SECRET  — Connected App consumer secret
  CORS_ORIGINS      — comma-separated allowed origins
"""

from __future__ import annotations

import json
import os

import httpx
from starlette.middleware.cors import CORSMiddleware

# Only apply Bedrock-compat strips when routing through a proxy (LiteLLM/Bedrock).
# Direct Anthropic API supports both eager_input_streaming and thinking natively.
_VIA_PROXY = bool(os.environ.get('ANTHROPIC_BASE_URL', '').strip())

# ── Transport patch (must run before salesforce.api.main is imported) ──────────
try:
    from anthropic import AsyncAnthropic

    class _BedrockCompatTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if _VIA_PROXY and '/messages' in request.url.path and request.content:
                try:
                    body = json.loads(request.content)
                    dirty = False

                    # Bedrock rejects non-standard top-level tool fields.
                    if any('eager_input_streaming' in t for t in body.get('tools', [])):
                        for tool in body['tools']:
                            tool.pop('eager_input_streaming', None)
                        dirty = True

                    # Bedrock rejects thinking when tool_choice forces a specific tool.
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
