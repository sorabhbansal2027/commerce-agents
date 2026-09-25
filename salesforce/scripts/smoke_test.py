"""
Post-deploy smoke tests for commerce-agent-api.

Runs a minimal set of HTTP checks to verify the deployed API is reachable
and returning expected responses. Exits non-zero on the first failure.

Usage:
    SMOKE_BASE_URL=https://api.example.com python smoke_test.py

Environment variables:
    SMOKE_BASE_URL   Base URL of the deployed API (required)
    SMOKE_SF_INSTANCE Salesforce instance URL (optional; used in header checks)
    SMOKE_TIMEOUT    Per-request timeout in seconds (default: 15)
"""

from __future__ import annotations

import os
import sys
import time

import requests

BASE_URL   = (os.environ.get("SMOKE_BASE_URL") or "").rstrip("/")
TIMEOUT    = int(os.environ.get("SMOKE_TIMEOUT", "15"))
RETRIES    = 3
RETRY_WAIT = 10

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"


def check(name: str, fn) -> bool:
    for attempt in range(1, RETRIES + 1):
        try:
            result = fn()
            if result:
                print(f"  {PASS} {name}")
                return True
            else:
                if attempt < RETRIES:
                    time.sleep(RETRY_WAIT)
        except Exception as exc:
            if attempt < RETRIES:
                time.sleep(RETRY_WAIT)
            else:
                print(f"  {FAIL} {name} — {exc}")
                return False
    print(f"  {FAIL} {name} — returned falsy after {RETRIES} attempts")
    return False


def get(path: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT, **kwargs)


def post(path: str, **kwargs) -> requests.Response:
    return requests.post(f"{BASE_URL}{path}", timeout=TIMEOUT, **kwargs)


def main() -> int:
    if not BASE_URL:
        print(f"{FAIL} SMOKE_BASE_URL is not set — cannot run smoke tests")
        return 1

    print(f"\nSmoke testing: {BASE_URL}\n")
    failures: list[str] = []

    # ── Health endpoint ────────────────────────────────────────────────────────
    print("Health checks:")

    def health_ok():
        r = get("/health")
        return r.status_code == 200

    if not check("/health returns 200", health_ok):
        failures.append("health")

    # ── API root / docs ────────────────────────────────────────────────────────
    print("\nAPI discovery:")

    def docs_ok():
        r = get("/docs")
        return r.status_code in (200, 404)   # 404 is acceptable in production

    check("/docs reachable", docs_ok)

    # ── Merchant routes ────────────────────────────────────────────────────────
    print("\nMerchant API:")

    def session_ok():
        r = post("/api/merchant/session", json={})
        return r.status_code in (200, 201, 422)   # 422 = missing fields, still alive

    if not check("POST /api/merchant/session reachable", session_ok):
        failures.append("merchant-session")

    def overview_401():
        r = get("/api/merchant/overview")
        return r.status_code in (200, 401, 403, 422)   # anything but 5xx

    if not check("GET /api/merchant/overview responds (not 5xx)", overview_401):
        failures.append("merchant-overview")

    def quotes_401():
        r = get("/api/merchant/quote-approvals")
        return r.status_code in (200, 401, 403, 422)

    if not check("GET /api/merchant/quote-approvals responds (not 5xx)", quotes_401):
        failures.append("merchant-quotes")

    # ── Storefront routes ──────────────────────────────────────────────────────
    print("\nStorefront API:")

    def sf_session_ok():
        r = post("/api/session", json={})
        return r.status_code in (200, 201, 422)

    if not check("POST /api/session reachable", sf_session_ok):
        failures.append("sf-session")

    # ── CORS headers ───────────────────────────────────────────────────────────
    print("\nCORS check:")

    def cors_ok():
        r = requests.options(
            f"{BASE_URL}/api/merchant/overview",
            headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
            timeout=TIMEOUT,
        )
        return r.status_code in (200, 204)

    check("OPTIONS /api/merchant/overview returns 200/204", cors_ok)

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    if failures:
        print(f"{FAIL} Smoke tests FAILED: {', '.join(failures)}")
        return 1

    print(f"{PASS} All smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
