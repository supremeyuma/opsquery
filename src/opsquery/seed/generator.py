"""Deterministic ERP/POS seed generator.

The generator is pure: ``build_dataset(seed=42)`` always returns the same rows in
the same order. The data generating process is documented in ``seed/DGP.md``.

Awkward rows are deliberate. The eval set probes them:

* duplicate customer names ("Acme Corp", "Maria Garcia") with distinct emails,
* NULL phone numbers and NULL ticket satisfaction,
* negative inventory adjustments and zero stock,
* customer accounts with no orders and products never ordered,
* refunds modelled as negative payments on returned orders,
* orders exactly on UTC year boundaries,
* cancelled orders with no shipment date.
"""

from __future__ import annotations

import hashlib
import random
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg

from opsquery.config import settings

SEED = 42
N_CUSTOMERS = 5_000
N_PRODUCTS = 300
N_SUPPLIERS = 40
N_ORDERS = 50_000
N_TICKETS = 3_000
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, tzinfo=timezone.utc)

FIRST_NAMES = [
    "Ana",
    "Ben",
    "Chen",
    "Dara",
    "Eli",
    "Fatima",
    "Gus",
    "Hana",
    "Ivan",
    "Jo",
    "Kai",
    "Lena",
    "Mira",
    "Noor",
    "Omar",
    "Pia",
    "Quinn",
    "Rosa",
    "Sam",
    "Tara",
]
LAST_NAMES = [
    "Alvarez",
    "Brown",
    "Chen",
    "Das",
    "Evans",
    "Fischer",
    "Garcia",
    "Hansen",
    "Ito",
    "Jones",
    "Kim",
    "Lopez",
    "Muller",
    "Novak",
    "Okafor",
    "Patel",
    "Quinn",
    "Rossi",
    "Silva",
    "Tanaka",
]
CITIES = ["Austin", "Boston", "Chicago", "Denver", "Seattle", "Portland", "Miami", "New York"]
CATEGORIES = ["Electronics", "Accessories", "Home", "Office", "Outdoors"]
WAREHOUSES = ["east", "west"]
SEGMENTS = ["retail", "wholesale", "vip"]
CHANNELS = ["web", "store", "phone", "partner"]
TICKET_CATEGORIES = ["billing", "delivery", "product", "account", "returns"]
PRIORITIES = ["low", "normal", "high", "urgent"]
MONTH_WEIGHTS = [0.80, 0.80, 0.90, 0.90, 1.00, 1.00, 1.00, 1.00, 1.05, 1.15, 1.40, 1.50]

TABLES = [
    "customers",
    "products",
    "suppliers",
    "inventory",
    "orders",
    "order_items",
    "invoices",
    "payments",
    "support_tickets",
]

COLUMNS: dict[str, tuple[str, ...]] = {
    "customers": ("id", "name", "email", "phone", "segment", "city", "country", "created_at"),
    "products": ("id", "sku", "name", "category", "unit_price", "active", "created_at"),
    "suppliers": ("id", "name", "country", "lead_time_days", "rating"),
    "inventory": (
        "id",
        "product_id",
        "warehouse",
        "quantity_on_hand",
        "reorder_point",
        "updated_at",
    ),
    "orders": (
        "id",
        "order_number",
        "customer_id",
        "status",
        "channel",
        "ordered_at",
        "shipped_at",
        "currency",
        "total_amount",
    ),
    "order_items": (
        "id",
        "order_id",
        "product_id",
        "quantity",
        "unit_price",
        "discount_pct",
        "line_total",
    ),
    "invoices": (
        "id",
        "order_id",
        "invoice_number",
        "issued_at",
        "due_at",
        "status",
        "amount",
        "paid_at",
    ),
    "payments": ("id", "invoice_id", "paid_at", "amount", "method", "reference"),
    "support_tickets": (
        "id",
        "customer_id",
        "opened_at",
        "closed_at",
        "category",
        "priority",
        "subject",
        "satisfaction",
    ),
}


def _money(value: float) -> float:
    return round(value + 1e-9, 2)


def _weighted_month(rng: random.Random) -> int:
    return rng.choices(range(12), weights=MONTH_WEIGHTS, k=1)[0]


def _random_ts(rng: random.Random) -> datetime:
    month = _weighted_month(rng)
    year = 2024 + (1 if month == 0 else 0)
    day = rng.randint(1, 28)
    return datetime(
        year, month + 1, day, rng.randint(0, 23), rng.randint(0, 59), tzinfo=timezone.utc
    )


def build_dataset(seed: int = SEED) -> dict[str, list[tuple[Any, ...]]]:
    """Build every table in memory. Deterministic for a given ``seed``."""

    rng = random.Random(seed)
    data: dict[str, list[tuple[Any, ...]]] = {t: [] for t in TABLES}

    # Customers -----------------------------------------------------------------
    for cid in range(1, N_CUSTOMERS + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        name = f"{first} {last}"
        email = f"{first.lower()}.{last.lower()}.{cid}@example.com"
        phone: str | None = f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
        if cid % 37 == 0:
            phone = None
        created = datetime(2022, 1, 1, tzinfo=timezone.utc) + timedelta(
            days=rng.randint(0, 720), seconds=rng.randint(0, 86_399)
        )
        data["customers"].append(
            (cid, name, email, phone, rng.choice(SEGMENTS), rng.choice(CITIES), "US", created)
        )
    # Deliberate duplicate names: same label, different accounts and emails.
    for cid in range(100, 115):
        row = list(data["customers"][cid - 1])
        row[1] = "Acme Corp"
        data["customers"][cid - 1] = tuple(row)
    for cid in range(200, 210):
        row = list(data["customers"][cid - 1])
        row[1] = "Maria Garcia"
        data["customers"][cid - 1] = tuple(row)

    # Products ------------------------------------------------------------------
    for pid in range(1, N_PRODUCTS + 1):
        category = rng.choice(CATEGORIES)
        price = _money(rng.uniform(5, 500))
        data["products"].append(
            (
                pid,
                f"SKU-{pid:04d}",
                f"{category} item {pid}",
                category,
                price,
                rng.random() > 0.08,
                datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 700)),
            )
        )

    # Suppliers -----------------------------------------------------------------
    for sid in range(1, N_SUPPLIERS + 1):
        data["suppliers"].append(
            (
                sid,
                f"Supplier {sid:02d}",
                rng.choice(["US", "CN", "DE", "MX", "VN"]),
                rng.randint(2, 60),
                round(rng.uniform(2.0, 5.0), 2),
            )
        )

    # Inventory: two warehouses per product, some negative adjustments.
    inv_id = 1
    for pid in range(1, N_PRODUCTS + 1):
        for warehouse in WAREHOUSES:
            qty = rng.randint(0, 400)
            if rng.random() < 0.10:
                qty = rng.randint(-25, 5)  # awkward: negative on-hand adjustments
            data["inventory"].append(
                (
                    inv_id,
                    pid,
                    warehouse,
                    qty,
                    rng.randint(10, 60),
                    END - timedelta(days=rng.randint(1, 120)),
                )
            )
            inv_id += 1

    # Orders and items ----------------------------------------------------------
    item_id = 1
    for oid in range(1, N_ORDERS + 1):
        customer_id = rng.randint(1, N_CUSTOMERS)
        ordered_at = _random_ts(rng)
        age_days = (END - ordered_at).days
        if age_days > 120:
            status = rng.choices(
                ["delivered", "returned", "cancelled", "paid"],
                weights=[80, 4, 8, 8],
                k=1,
            )[0]
        elif age_days > 30:
            status = rng.choices(
                ["delivered", "shipped", "paid", "cancelled", "returned"],
                weights=[45, 25, 20, 6, 4],
                k=1,
            )[0]
        else:
            status = rng.choices(
                ["pending", "paid", "shipped", "cancelled"], weights=[25, 45, 22, 8], k=1
            )[0]
        shipped_at = None
        if status in {"shipped", "delivered"}:
            shipped_at = ordered_at + timedelta(days=rng.randint(1, 6))

        n_items = rng.choices([1, 2, 3, 4, 5], weights=[35, 30, 18, 12, 5], k=1)[0]
        total = 0.0
        for _ in range(n_items):
            pid = rng.randint(1, N_PRODUCTS)
            unit_price = data["products"][pid - 1][4]
            quantity = rng.choices([1, 2, 3, 4], weights=[65, 22, 8, 5], k=1)[0]
            discount = rng.choices([0.0, 0.05, 0.10, 0.20], weights=[70, 12, 12, 6], k=1)[0]
            line_total = _money(quantity * unit_price * (1 - discount))
            total += line_total
            data["order_items"].append(
                (item_id, oid, pid, quantity, unit_price, discount, line_total)
            )
            item_id += 1
        data["orders"].append(
            (
                oid,
                f"ORD-{2024 + (oid % 2)}-{oid:06d}",
                customer_id,
                status,
                rng.choice(CHANNELS),
                ordered_at,
                shipped_at,
                "USD",
                _money(total),
            )
        )

    # Force UTC-boundary orders (the timezone trap).
    for index, (ts, status) in enumerate(
        [
            (datetime(2025, 12, 31, 23, 45, tzinfo=timezone.utc), "paid"),
            (datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc), "shipped"),
            (datetime(2024, 1, 1, 0, 5, tzinfo=timezone.utc), "delivered"),
        ]
    ):
        row = list(data["orders"][index])
        row[5] = ts
        row[3] = status
        data["orders"][index] = tuple(row)

    # Invoices and payments -----------------------------------------------------
    invoice_id = 1
    payment_id = 1
    for order in data["orders"]:
        oid = order[0]
        status = order[3]
        ordered_at = order[5]
        total = order[8]
        if status in {"pending", "cancelled"}:
            continue
        issued = ordered_at.date()
        due = issued + timedelta(days=30)
        if status == "returned":
            inv_status = "void"
            paid_at: date | None = None
        elif rng.random() < 0.12 and (END.date() - issued).days > 40:
            inv_status = "overdue"
            paid_at = None
        else:
            inv_status = "paid"
            paid_at = issued + timedelta(days=rng.randint(0, 25))
        amount = _money(total)
        data["invoices"].append(
            (
                invoice_id,
                oid,
                f"INV-{issued.year}-{invoice_id:06d}",
                issued,
                due,
                inv_status,
                amount,
                paid_at,
            )
        )
        if inv_status == "paid":
            data["payments"].append(
                (
                    payment_id,
                    invoice_id,
                    datetime.combine(paid_at or issued, datetime.min.time(), timezone.utc),
                    amount,
                    rng.choice(["card", "bank", "cash", "credit"]),
                    f"PAY-{payment_id:07d}",
                )
            )
            payment_id += 1
        elif inv_status == "void" and rng.random() < 0.85:
            # Refund: negative payment against the original invoice.
            refund_day = issued + timedelta(days=rng.randint(1, 20))
            data["payments"].append(
                (
                    payment_id,
                    invoice_id,
                    datetime.combine(refund_day, datetime.min.time(), timezone.utc),
                    -amount,
                    "card",
                    f"REF-{payment_id:07d}",
                )
            )
            payment_id += 1
        invoice_id += 1

    # A few deliberate refunds on paid invoices (refunds vs revenue trap).
    for invoice in data["invoices"][-800:]:
        if invoice[5] == "paid" and rng.random() < 0.10:
            data["payments"].append(
                (
                    payment_id,
                    invoice[0],
                    datetime.combine(
                        invoice[3] + timedelta(days=3), datetime.min.time(), timezone.utc
                    ),
                    -round(float(invoice[6]) * 0.25, 2),
                    "card",
                    f"REF-{payment_id:07d}",
                )
            )
            payment_id += 1

    # Support tickets -----------------------------------------------------------
    for tid in range(1, N_TICKETS + 1):
        opened = _random_ts(rng)
        closed: datetime | None = opened + timedelta(hours=rng.randint(1, 240))
        if rng.random() < 0.18:
            closed = None
        satisfaction: int | None = rng.randint(1, 5)
        if rng.random() < 0.08:
            satisfaction = None
        data["support_tickets"].append(
            (
                tid,
                rng.randint(1, N_CUSTOMERS),
                opened,
                closed,
                rng.choice(TICKET_CATEGORIES),
                rng.choice(PRIORITIES),
                f"Ticket {tid}",
                satisfaction,
            )
        )

    return data


def dataset_checksum(data: dict[str, list[tuple[Any, ...]]]) -> str:
    """SHA-256 over table names, row counts and every row's canonical repr."""

    digest = hashlib.sha256()
    for table in TABLES:
        rows = data[table]
        digest.update(f"{table}:{len(rows)}\n".encode())
        for row in rows:
            digest.update(repr(row).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def dataset_counts(data: dict[str, list[tuple[Any, ...]]]) -> dict[str, int]:
    return {table: len(data[table]) for table in TABLES}


def load(conn: psycopg.Connection[Any], data: dict[str, list[tuple[Any, ...]]]) -> None:
    """Truncate the public schema and COPY the dataset in."""

    conn.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename)
                        || ' RESTART IDENTITY CASCADE';
            END LOOP;
        END
        $$;
        """
    )
    for table in TABLES:
        columns = ", ".join(COLUMNS[table])
        with conn.cursor().copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
            for row in data[table]:
                copy.write_row(row)
        conn.execute(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            "COALESCE((SELECT max(id) FROM " + table + "), 1))",
            (table,),
        )
    conn.commit()


def main() -> int:
    data = build_dataset(SEED)
    counts = dataset_counts(data)
    checksum = dataset_checksum(data)
    with psycopg.connect(settings().database_url) as conn:
        load(conn, data)
    total = sum(counts.values())
    print(f"seed: {total} rows across {len(counts)} tables (seed={SEED})")
    print("seed: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"seed: checksum={checksum}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
