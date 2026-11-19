# Seed data generating process

`seed/generator.py` is a pure, seeded generator (`random.Random(42)`). Loading truncates the
public schema and `COPY`s every table, so `make seed` is idempotent. The checksum printed by
`make seed` is a SHA-256 over table names, row counts and every row's canonical repr; it is stable
across runs and platforms.

## Shape

| Table | Rows | Notes |
|---|---|---|
| customers | 5,000 | accounts created 2022-01-01 .. 2023-12-21 |
| products | 300 | 5 categories, 8% inactive |
| suppliers | 40 | lead time 2..60 days |
| inventory | 600 | 2 warehouses per product |
| orders | 50,000 | 2024-01-01 .. 2025-12-31 UTC |
| order_items | ~100,000 | 1..5 lines per order |
| invoices | ~46,000 | one per non-pending, non-cancelled order |
| payments | ~40,000 | positive payments plus refunds |
| support_tickets | 3,000 | 18% still open |

## Deliberate structure

* **Seasonality.** Orders are drawn by month with weights 0.8 in January/February rising to 1.4 in
  November and 1.5 in December. A question about "last quarter" must not assume uniform months.
* **Refunds.** Returned orders get a `void` invoice and, 85% of the time, a negative payment
  labelled `REF-*`. Another 10% of the newest paid invoices get a partial refund. Revenue queries
  that sum `order_items` and revenue queries that sum positive-only payments disagree; the eval
  set tests both definitions explicitly.
* **Duplicate names.** Customer ids 100-114 are all named `Acme Corp` and ids 200-209 are all
  `Maria Garcia`, with unique email addresses. Counting customers and counting distinct names give
  different answers by construction.
* **NULLs.** Every 37th customer has no phone. 8% of tickets have no satisfaction score, 18% have
  no close time, cancelled/pending orders have no invoice and no shipment date.
* **Negative stock.** ~10% of inventory rows are negative adjustments; `quantity_on_hand` is an
  integer with no non-negative check, matching real ERP adjustment rows.
* **UTC boundaries.** Three orders are pinned to 2025-12-31 23:45Z, 2025-12-31 23:59Z and
  2024-01-01 00:05Z. Grouping by local date vs UTC date changes the December total.
* **Unused entities.** Some customers never order and some products are never ordered, so inner
  joins silently drop rows.

## Invariants

* `order_items.line_total = round(quantity * unit_price * (1 - discount_pct), 2)`.
* `orders.total_amount = sum(order_items.line_total)` for that order.
* `invoices.amount = orders.total_amount`.
* `payments.invoice_id` always references an invoice; refunds are payments with a negative amount.
* Money uses `NUMERIC(12,2)`, never floats, in the database. The generator rounds explicitly.
