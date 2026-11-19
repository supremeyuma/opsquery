# Inventory replenishment

## Reorder point

Each product and warehouse has a reorder point. When quantity on hand falls below the reorder point, the item appears on the replenishment report. Quantity on hand can be negative after an adjustment; negative stock is always treated as below the reorder point.

## Reorder quantity

The default reorder quantity is the demand forecast for the supplier lead time plus safety stock. Safety stock is 20 percent of forecast demand for fast-moving items and 10 percent for slow-moving items.

## Review cadence

Fast-moving items are reviewed daily, medium-moving items weekly and slow-moving items monthly. The purchasing team reviews the report every Monday at 09:00 UTC.

## Exceptions

Items with a supplier lead time above 45 days are reviewed manually. Products marked inactive are not replenished, even when stock is below the reorder point.
