# Merchant AI Agent — Claude Code Setup

You are connected to the **ACME Merchant Agent** via the `merchant` MCP server.

## What you can do

| Ask about | Tools available |
|---|---|
| Daily briefing / what needs attention | `get_business_snapshot`, `get_inventory_alerts`, `get_order_issues` |
| Sales, revenue, conversion rates | `query_metrics`, `get_campaign_performance` |
| Specific products or listings | `search_listings`, `get_listing`, `get_pricing_context` |
| Staged and pending changes | `get_pending_changes` |
| Stage a price, inventory, or listing change | `stage_price_update`, `stage_inventory_action`, `stage_listing_update` |
| Stage a promotion or campaign | `stage_promotion`, `stage_campaign` |
| Apply or discard a staged change | `apply_change`, `discard_change` |
| Remember store preferences | `save_memory`, `recall_memories` |

## How changes work

1. You ask Claude to make a change (e.g. "mark the winter coats down 15%")
2. Claude calls `stage_price_update` — nothing changes in Salesforce yet
3. Claude shows you a before/after preview and asks for approval
4. You say "apply" — Claude calls `apply_change`, which writes to Salesforce OMS
5. If you change your mind, say "discard" at any point before applying

## Starter prompts

- *What needs my attention this morning?*
- *How did sales compare to last week?*
- *Which listings are running low on stock?*
- *Show me the pricing context for [product name]*
- *Stage a 10% markdown on slow-moving items in the outerwear category*
- *What promotions are currently active?*

## Backend

The `merchant` MCP server connects to Salesforce OMS using OAuth2 client credentials.
All reads are safe to run. Staged writes require your explicit approval before going live.
