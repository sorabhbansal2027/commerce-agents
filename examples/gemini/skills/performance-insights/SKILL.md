# Skill: Performance Insights

Handles business performance questions: sales, order volumes, conversion trends, and top-level KPIs.

## When this skill applies

- Operator asks about revenue, sales, or GMV
- Operator asks how the store is performing over a period
- Operator asks to compare this period to a prior period
- Operator asks about order counts, cancellations, or returns
- Operator asks what is driving a change in a metric

## Flow

1. Call `get_business_snapshot` with the period the operator specified (default `30d`).
2. Lead with the headline figures: revenue, orders, and period-over-period change if available.
3. If the operator asks about a specific driver ("why did sales drop?"), call `run_analysis` with a precise brief describing the question and the available metrics.
4. Present `run_analysis` findings as-is — do not restate the figures in your own words.
5. For inventory-related performance questions, also call `get_inventory_alerts`.

## Periods

Accepted period strings: `7d`, `30d`, `90d`, or an ISO date range like `2026-01-01:2026-03-31`. If the operator says "this month" or "last quarter", convert to the appropriate ISO range before calling.

## Rules

- Lead with numbers, not prose. "Revenue: $142,300 (+8% vs prior period)" beats a paragraph.
- Do not compute percentages from snapshot figures yourself — call `run_analysis` for derived metrics.
- Never fabricate figures. If the snapshot does not include a field the operator asks about, say so and suggest what tool to call instead.
- Period comparisons require calling `get_business_snapshot` twice (once per period) and presenting both results side by side.
