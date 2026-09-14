"""Production entry point that extends retail.api.main with external CORS support.

Uvicorn target: retail.api.main_production:app  (--app-dir examples)

The base app only allows localhost origins (local dev). This module adds an outer
CORSMiddleware that reads CORS_ORIGINS (comma-separated) so Railway deployments can
serve Salesforce or other external frontends without changing any existing files.
"""

from __future__ import annotations

import os

from starlette.middleware.cors import CORSMiddleware

from retail.api.main import app

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
