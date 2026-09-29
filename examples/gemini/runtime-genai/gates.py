"""Safety gates for the genai-raw merchant orchestrator.

Each gate inspects the session and tool arguments before the tool function is
dispatched. A gate returns None to pass or a ``{"error": ..., "gate": ...}``
dict to block — the orchestrator yields that dict as the tool_result SSE frame
and the model sees it instead of the real tool output.

Gate order: provenance → options → guardrail → approval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import GenAISession


def check_provenance_gate(session: "GenAISession", tool_name: str, args: dict) -> dict | None:
    """Block write tools whose listing_id was never returned by a read tool."""
    if tool_name in ("stage_listing_update", "stage_inventory_action"):
        listing_id = args.get("listing_id", "")
        if listing_id not in session.seen_listing_ids:
            return {
                "error": (
                    f"listing {listing_id!r} has not been retrieved in this session; "
                    "call get_listing or search_listings first"
                ),
                "gate": "provenance",
            }
    return None


def check_options_gate(session: "GenAISession", tool_name: str, args: dict) -> dict | None:
    """Block calls whose enum arguments fall outside the declared option set."""
    if tool_name == "stage_inventory_action":
        action = args.get("action", "")
        if action not in ("restock", "pause", "activate"):
            return {
                "error": f"action {action!r} is not valid; choose restock, pause, or activate",
                "gate": "options",
            }
    return None


def check_guardrail_gate(session: "GenAISession", tool_name: str, args: dict) -> dict | None:
    """Block calls that would exceed safe quantity or content-length limits."""
    if tool_name == "stage_inventory_action":
        qty = args.get("quantity", 0)
        if isinstance(qty, int) and qty > 10_000:
            return {
                "error": f"quantity {qty} exceeds the per-action restock cap of 10 000 units",
                "gate": "guardrail",
            }
    return None


def check_approval_gate(session: "GenAISession", tool_name: str, args: dict) -> dict | None:
    """Block apply_change unless the operator has explicitly approved the change_id."""
    if tool_name == "apply_change":
        change_id = args.get("change_id", "")
        if change_id not in session.approved_change_ids:
            return {
                "error": (
                    f"change {change_id!r} has not been approved; "
                    "ask the operator to confirm, then call approve_change first"
                ),
                "gate": "approval",
            }
    return None


def run_gates(session: "GenAISession", tool_name: str, args: dict) -> dict | None:
    """Run all gates in order; return the first failure or None if all pass."""
    for check in (
        check_provenance_gate,
        check_options_gate,
        check_guardrail_gate,
        check_approval_gate,
    ):
        result = check(session, tool_name, args)
        if result is not None:
            return result
    return None
