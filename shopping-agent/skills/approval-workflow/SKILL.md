---
name: approval-workflow
description: Submitting a cart, quote, or order for internal approval and tracking where it sits in the approval chain; use when the buyer needs to get spending authorised before placing an order.
---

# Approval workflow

B2B purchases often require manager, procurement, or finance sign-off before an order is placed. This flow submits a request and tracks its progress through the configured approval chain.

## Submitting for approval

Call `submit_for_approval` with `subject_type` ("cart", "quote", or "order") and the relevant id. Before calling it, confirm once with the buyer: state what is being submitted and the total amount. The call is a write — it notifies approvers and cannot be silently undone.

## Checking status

Use `get_approval_status` with the `request_id` returned at submission, or when the buyer asks "where is my approval?", "has my manager approved it?", or similar. Render with `present_approval_status`, showing each step, who the current approver is, and the total.

## Recalling a request

`recall_approval_request` withdraws a pending request. Confirm once before calling — it cancels all in-flight notifications. It is only valid on requests with status **pending**.

## Status vocabulary

- **pending** — waiting on one or more approvers
- **approved** — all steps signed off; cart or quote is ready to order
- **rejected** — one approver declined; give the comments if present, then offer to revise and resubmit
- **recalled** — withdrawn by the submitter

## What this flow does not do

Configuring spending limits, changing who the approvers are, and escalating past a deadline are admin actions in the platform's approval-process configuration; describe them as the next step in the platform.
