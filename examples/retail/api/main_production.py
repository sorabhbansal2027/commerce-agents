"""Production entry point: CORS for Salesforce + Bedrock-compatible tool definitions.

Uvicorn target: retail.api.main_production:app  (--app-dir examples)

The base app only allows localhost origins. This module adds CORSMiddleware that
reads CORS_ORIGINS (comma-separated) so Railway deployments can serve Salesforce.

It also patches AsyncAnthropic before the agent is created so that the
`eager_input_streaming` field — added to each streaming tool by
commerce_common.prompt_assembly.with_eager_input — is stripped before every
request reaches LiteLLM / Bedrock. AWS Bedrock rejects tool definitions that
contain non-standard fields (it reports them as "custom.<field>" in errors).
"""

from __future__ import annotations

import json
import os

import httpx
from starlette.middleware.cors import CORSMiddleware

# ── Bedrock compatibility patch ────────────────────────────────────────────────
# commerce_common.prompt_assembly.with_eager_input() adds eager_input_streaming=True
# to certain tools at orchestrator setup time.  Bedrock InvokeModel rejects that
# field ("Extra inputs are not permitted").  Inject a custom httpx transport that
# strips it from every /messages request before it reaches LiteLLM.
try:
    from anthropic import AsyncAnthropic

    class _BedrockCompatTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if '/messages' in request.url.path and request.content:
                try:
                    body = json.loads(request.content)
                    # commerce_common.prompt_assembly.with_eager_input adds
                    # eager_input_streaming=True as a top-level tool field for
                    # the Anthropic API's fine-grained streaming feature.
                    # Bedrock rejects it (reports as "custom.eager_input_streaming").
                    dirty = False
                    if any('eager_input_streaming' in t for t in body.get('tools', [])):
                        for tool in body['tools']:
                            tool.pop('eager_input_streaming', None)
                        dirty = True
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
    pass  # Never block startup if the patch fails

# ── App import (after patch so agent picks up patched client) ──────────────────
from retail.api.main import app  # noqa: E402

# ── External CORS for Salesforce ───────────────────────────────────────────────
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
