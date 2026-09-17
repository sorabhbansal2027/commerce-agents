---
name: promotions
description: Surfacing available promotions, volume discounts, and rebates for the account and applying a promotion code to the cart; use when the buyer asks about deals, discounts, offers, or promo codes.
---

# Promotions and incentives

Promotions are account-specific or catalog-wide discounts, rebates, or free-item offers the platform holds for this buyer. Auto-applied promotions activate at checkout; code-based promotions require explicit application.

## Surfacing promotions

Call `get_promotions` when the buyer asks "what deals do I have?", "any discounts on laptops?", "do I qualify for a volume discount?", or before presenting a large cart. Filter by `category` when the buyer is shopping a specific category. Render with `present_promotions`, showing name, benefit, expiry, and whether a code is needed.

## Applying a promotion

Call `apply_promotion` with the `promotion_id` or `code` the buyer provides, or when an auto-applicable promotion is clearly relevant to the current cart. Confirm the applied discount from the result before summarising.

## Volume and contract pricing

Volume discounts activate when quantity thresholds are met; the backend returns them as promotions with `min_order_amount`. When the buyer's cart is close to a threshold, mention it: "Add 2 more laptops to unlock a 5% volume discount."

## What this flow does not do

Creating promotions, adjusting rebate targets, and partner incentive management are merchant-side operations; direct the buyer to their account manager for bespoke deals.
