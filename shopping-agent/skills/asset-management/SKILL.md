---
name: asset-management
description: Looking up installed products, serial numbers, warranty status, and service tags for assets the account owns; use when the buyer asks about their equipment, what is under warranty, or a specific device by serial or service tag.
---

# Asset and installed-base management

An asset is a product the account has already purchased and owns — a laptop, server, network switch, or any piece of equipment tracked by serial number or service tag. This flow reads the installed base; it does not add, retire, or transfer assets.

## Looking up assets

Use `get_assets` when the buyer asks "what laptops do we have?", "show me our servers", "what's under warranty", or "find asset by service tag". Apply filters for category, status, or location when stated. Render with `present_assets`.

Use `get_asset_details` for a single asset identified by asset_id, serial number, or service tag. Show full detail: purchase date, warranty expiry, assigned user, location.

## Warranty

Surface warranty status prominently. Flag assets where `warranty_expiry` is within 90 days as **expiring soon** and offer to check renewal options via the subscriptions flow. Assets with no warranty_expiry on record should be noted as "warranty status unknown — check with your account manager."

## Connecting to adjacent flows

- An asset nearing end of life → link to `search-discovery` to find a replacement.
- A warranty expiring soon → link to `subscriptions` to renew the service contract.
- A faulty or damaged asset → link to `customer-care` for return/RMA.

## What this flow does not do

Creating, updating location, reassigning, or retiring assets happens in the platform's asset management module; describe it as the next step.
