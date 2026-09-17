---
name: subscriptions
description: Viewing active service contracts, warranties, and managed-service subscriptions, and initiating a renewal; use when the buyer asks about their support contracts, renewals, or what services are active on their account.
---

# Subscriptions and services

A subscription is a recurring service the account pays for — a support contract, extended warranty, managed service, or software licence with an annual renewal. This flow reads active subscriptions and initiates renewals; it does not create new subscriptions from scratch.

## Listing subscriptions

Call `get_subscriptions` when the buyer asks "what support contracts do I have?", "when does my warranty expire?", "show my active services", or "what's up for renewal". Filter by `status` when they ask specifically about expiring or active ones. Render with `present_subscriptions`.

## Subscription detail

Call `get_subscription_details` for a single subscription by `subscription_id`. Show all fields: dates, quantity, annual value, auto-renew status, and linked asset or product.

## Initiating renewal

Call `renew_subscription` after confirming once with the buyer — state the subscription name, the renewal term, and the amount. The call creates a renewal order or quote depending on platform configuration; relay what the result says happened.

## Status vocabulary

- **active** — running, no action needed
- **expiring_soon** — within 90 days of end_date; proactively flag these
- **expired** — already lapsed; offer to check reinstatement options with account manager
- **pending_renewal** — renewal initiated, awaiting approval or payment
- **cancelled** — terminated; offer alternative coverage if available

## Connecting to adjacent flows

- Expiring warranty on a specific asset → surface via `asset-management` first, then link here.
- Renewal requires approval → hand off to `approval-workflow` after creating the renewal quote.
