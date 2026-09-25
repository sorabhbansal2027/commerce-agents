#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Interactive Gemini × UCP demo.

Connects a Google Gemini agent to the Salesforce UCP storefront endpoint,
demonstrates auto-discovery from the manifest, and runs a shopping conversation.

    # Point at the Railway deployment (default):
    GEMINI_API_KEY=your-key python scripts/gemini_ucp_demo.py

    # Point at a local API server:
    GEMINI_API_KEY=your-key UCP_BASE=http://localhost:8005 python scripts/gemini_ucp_demo.py

    # Interactive REPL:
    GEMINI_API_KEY=your-key python scripts/gemini_ucp_demo.py --interactive

Requires:
    pip install google-genai
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Import GeminiUCPAgent directly from its file to avoid pulling in the full
# demo_common package (which requires fastapi, uvicorn, etc. at import time).
import importlib.util as _ilu

_agent_file = Path(__file__).resolve().parent.parent / "examples" / "demo_common" / "gemini_ucp_agent.py"
_spec = _ilu.spec_from_file_location("gemini_ucp_agent", _agent_file)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
GeminiUCPAgent = _mod.GeminiUCPAgent

UCP_BASE = os.environ.get("UCP_BASE", "https://diligent-flow-production-afd7.up.railway.app")

DEMO_TURNS = [
    "What kinds of products do you carry?",
    "Show me laptops under $2000 — just give me the top 3.",
    "Tell me more about the cheapest laptop you found.",
    "Create a checkout session for 1 of that laptop. My name is Alex Rivera.",
]


def run_demo(agent: object) -> None:
    """Run a canned multi-turn shopping conversation."""
    for turn_idx, message in enumerate(DEMO_TURNS, 1):
        print(f"\n{'─' * 60}")
        print(f"[Turn {turn_idx}] User: {message}")
        print("─" * 60)
        reply = agent.send(message)
        print(f"Gemini: {reply}")

    print(f"\n{'═' * 60}")
    print("Demo complete.")


def run_interactive(agent: object) -> None:
    """Drop into a REPL for free-form shopping."""
    print("\nEntering interactive mode. Type 'quit' or Ctrl-C to exit.")
    print("Type 'reset' to start a fresh conversation.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("[Conversation reset]\n")
            continue

        reply = agent.send(user_input)
        print(f"\nGemini: {reply}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini × UCP shopping demo")
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Drop into interactive REPL instead of running the canned demo",
    )
    parser.add_argument(
        "--base",
        default=UCP_BASE,
        help=f"UCP storefront base URL (default: {UCP_BASE})",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.6-flash",
        help="Gemini model to use (default: gemini-3.6-flash)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        print("Get a key at: https://aistudio.google.com/apikey")
        sys.exit(1)

    print(f"{'═' * 60}")
    print("Gemini × UCP Commerce Demo")
    print(f"{'═' * 60}")
    print(f"Storefront : {args.base}")
    print(f"Model      : {args.model}")
    print("Discovering UCP manifest…", end=" ", flush=True)

    try:
        agent = GeminiUCPAgent(ucp_base=args.base, model=args.model, api_key=api_key)
    except Exception as exc:
        print(f"\nFailed to connect: {exc}")
        sys.exit(1)

    print("OK")
    print(f"Store      : {agent.store_name}")
    print(f"Tools      : {', '.join(agent.tool_names)}")

    caps = agent.manifest.get("capabilities", {})
    print("Capabilities:")
    for cap_name, cap in caps.items():
        status = "✓" if cap.get("supported") else "~"
        print(f"  {status} {cap_name}")

    if args.interactive:
        run_interactive(agent)
    else:
        run_demo(agent)


if __name__ == "__main__":
    main()
