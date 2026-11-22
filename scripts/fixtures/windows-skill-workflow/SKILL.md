---
name: synthetic-inventory-allocation
description: Apply the reviewed inventory allocation procedure to an operations team's order and stock CSV attachments.
---

Use only for the exact sender and scope approved by the user. Read the new mail
and its original attachments before executing. Input names are `orders.csv` and
`stock.csv`; the output is `allocation.csv`.

Preserve PO, Line and SKU as text, including leading zeroes. Allocate shared stock
in original order-row sequence. Missing stock means zero. Keep all rows. Compute
shortage value with Decimal and ROUND_HALF_UP to two decimals, without rounding
the unit price first. Reject missing prices, invalid quantities and duplicate
stock SKU rows before creating any output. Do not infer a delivery commitment.

Validate the generated CSV, then attach that exact managed file to a reply draft.
The user reviews the draft; this skill never sends mail or receives credentials.

This is a fixed synthetic acceptance fixture. The independent historical outputs
are the public examples/script-skills completed CSVs. New-order oracle data is
not part of the package or any runtime resource.
