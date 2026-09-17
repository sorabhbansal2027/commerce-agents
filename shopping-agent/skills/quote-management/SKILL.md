---
name: quote-management
description: Creating, retrieving, and submitting quotes for approval or conversion to an order; use when the buyer asks to quote, price out, get a formal estimate, or save a cart as a quote.
---

# Quote management

A quote is a priced, named snapshot of products and quantities that can be approved internally before becoming an order. It is not a cart: a cart is informal and ephemeral; a quote has an id, a status, an expiry, and may carry negotiated prices.

## When to create a quote

Create a quote when the buyer says "quote this", "price this out formally", "I need a quote for procurement", or "save this as a quote". If a cart is already in session, offer to quote its contents; do not add to the cart first and then quote — convert what is already there.

## Fetching quotes

Use `get_quote` with a quote_id the buyer names. Use `get_quotes` (no argument) to list the buyer's recent quotes when they say "my quotes", "show my open quotes", or similar. Show status, total, expiry, and item count; render the detail with `present_quote`.

## Submitting and converting

`submit_quote` sends the quote for internal review or to a sales rep. `convert_quote_to_order` places the order from an approved quote; confirm the action once before calling it — it is a write that cannot be undone from here.

## What this flow does not do

This flow reads and initiates. Negotiating a price, assigning a sales rep, and changing line items after submission happen in the platform's quote editor; describe the next step and make clear it has not happened here.

## Status vocabulary

- **draft** — saved, not yet submitted
- **submitted** — with the approver or sales rep
- **approved** — ready to convert to order
- **rejected** — declined; describe next options (revise, escalate)
- **expired** — past expiry date; offer to requote
- **ordered** — already converted; show the linked order id
