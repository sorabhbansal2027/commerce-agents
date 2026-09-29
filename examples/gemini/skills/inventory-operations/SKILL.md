# Skill: Inventory Operations

Handles requests about stock levels, low-stock alerts, and restocking actions.

## When this skill applies

- Operator asks about inventory status, stock levels, or out-of-stock products
- Operator asks to restock, pause, or activate a product
- Operator asks what needs restocking or what is running low

## Flow

1. Call `get_inventory_alerts` to get the current low/out-of-stock list.
2. Present the alerts grouped by severity: out-of-stock first, then low-stock.
3. For each alert, show: product name, current quantity, threshold, and recommended action.
4. If the operator wants to act, call `stage_inventory_action` with the specific listing ID and action.
5. Show the staged action and ask for approval before applying.
6. Call `apply_change` only after explicit operator approval.

## Rules

- `restock` requires a positive `quantity` argument — ask the operator how many units to add if they don't specify.
- `pause` and `activate` do not need a quantity.
- Never restock a listing without first reading its current stock from `get_inventory_alerts` or `get_listing`.
- If inventory alerts return an empty list, tell the operator all stock levels are healthy.
