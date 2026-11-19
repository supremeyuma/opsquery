"""Deterministic, offline SQL planner.

This is the proposer used by ``make eval`` when no provider credentials are
configured. It is deliberately a rules-based system: every SQL statement it
returns is checked by the guard and executed against real seeded rows, so the
measured execution accuracy is a measurement of this planner, not a replayed
answer key.

A provider-backed LLM can replace it behind the same :class:`Plan` contract; the
cassette client in ``opsquery.llm.client`` exists for that path and for tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
MONTH_PATTERN = "|".join(MONTHS)

DESTRUCTIVE = re.compile(
    r"\b(drop|truncate|delete|update|insert|alter|grant|revoke|wipe|erase)\b", re.I
)
OUT_OF_SCOPE = re.compile(
    r"\b(weather|forecast|joke|poem|story|recipe|bitcoin|stock price|employees|payroll|"
    r"bomb|hack|translate|who is the president|home address)\b",
    re.I,
)
PII_REQUEST = re.compile(
    r"(\b(password|passwd|credit card|card number|ssn|social security|email addresses|"
    r"phone numbers|personal data|api key|secret)\b)"
    r"|(\b(export|dump|list|show|give)\b.{0,40}\b(all|every|raw)\b.{0,20}"
    r"\b(customer|user|email|phone)\b)",
    re.I,
)
INJECTION = re.compile(
    r"(ignore (all )?(previous|above|prior) instructions|system prompt|"
    r"you are now|disregard .{0,20}instructions)",
    re.I,
)


@dataclass(frozen=True)
class Plan:
    question: str
    sql: str | None
    refusal: str | None = None
    strategy: str = "rules"
    confidence: float = 0.0

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    @classmethod
    def refuse(cls, question: str, reason: str) -> Plan:
        return cls(question=question, sql=None, refusal=reason, confidence=1.0)

    @classmethod
    def answer(cls, question: str, sql: str, strategy: str, confidence: float = 0.9) -> Plan:
        return cls(question=question, sql=sql, strategy=strategy, confidence=confidence)


@dataclass(frozen=True)
class Period:
    start: str
    end: str
    label: str

    def clause(self, column: str) -> str:
        return f"{column} >= '{self.start}' AND {column} < '{self.end}'"


def _year_period(year: int) -> Period:
    return Period(f"{year}-01-01", f"{year + 1}-01-01", str(year))


def _month_period(year: int, month: int) -> Period:
    start = f"{year}-{month:02d}-01"
    end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    return Period(start, end, f"{year}-{month:02d}")


def extract_period(question: str) -> Period | None:
    lower = question.lower()
    month_match = re.search(rf"\b({MONTH_PATTERN})\b(?:\s+(2024|2025))?", lower)
    year_match = re.search(r"\b(2024|2025)\b", lower)
    if month_match is not None:
        month = MONTHS[month_match.group(1)]
        year = int(month_match.group(2) or (year_match.group(1) if year_match else 2025))
        return _month_period(year, month)
    if year_match is not None:
        return _year_period(int(year_match.group(1)))
    return None


def extract_limit(question: str, default: int = 5) -> int:
    match = re.search(r"\btop\s+(\d+)\b", question.lower())
    if match is not None:
        return max(1, min(int(match.group(1)), 100))
    if "best-selling" in question.lower() or "best selling" in question.lower():
        return 1
    return default


def _has(text: str, *words: str) -> bool:
    return any(word in text for word in words)


def refuse_reason(question: str) -> str | None:
    if INJECTION.search(question):
        return "instruction-injection attempt in the question"
    if DESTRUCTIVE.search(question):
        return "destructive database operation requested"
    if PII_REQUEST.search(question):
        return "request for raw personal data outside the aggregate allowlist"
    if OUT_OF_SCOPE.search(question):
        return "question is outside the operational-data domain"
    return None


def _revenue_by_segment(period: Period | None) -> str:
    where = f" WHERE {period.clause('p.paid_at')}" if period else ""
    return (
        "SELECT c.segment, sum(p.amount) AS revenue FROM customers c "
        "JOIN orders o ON o.customer_id = c.id "
        "JOIN invoices i ON i.order_id = o.id "
        "JOIN payments p ON p.invoice_id = i.id"
        f"{where} GROUP BY c.segment ORDER BY c.segment"
    )


def _top_products(limit: int, period: Period | None) -> str:
    where = f" WHERE {period.clause('o.ordered_at')}" if period else ""
    return (
        "SELECT p.name AS product, sum(oi.line_total) AS revenue FROM order_items oi "
        "JOIN products p ON p.id = oi.product_id "
        "JOIN orders o ON o.id = oi.order_id"
        f"{where} GROUP BY p.id, p.name ORDER BY revenue DESC, p.name LIMIT {limit}"
    )


def _top_customers(limit: int, period: Period | None) -> str:
    where = f" WHERE {period.clause('p.paid_at')}" if period else ""
    return (
        "SELECT c.name AS customer, sum(p.amount) AS revenue FROM customers c "
        "JOIN orders o ON o.customer_id = c.id "
        "JOIN invoices i ON i.order_id = o.id "
        "JOIN payments p ON p.invoice_id = i.id"
        f"{where} GROUP BY c.id, c.name ORDER BY revenue DESC, c.name LIMIT {limit}"
    )


def plan(question: str) -> Plan:
    """Return a bounded SQL statement or an explicit refusal."""

    reason = refuse_reason(question)
    if reason is not None:
        return Plan.refuse(question, reason)

    lower = question.lower()
    period = extract_period(question)
    limit = extract_limit(question)

    # Names, duplicates, emptiness -------------------------------------------------
    if _has(lower, "distinct customer name", "different customer names", "unique customer name"):
        return Plan.answer(
            question,
            "SELECT count(DISTINCT name) AS distinct_names FROM customers",
            "count-distinct-names",
        )
    if _has(lower, "duplicate", "more than once", "same name", "repeated") and _has(
        lower, "customer", "name"
    ):
        return Plan.answer(
            question,
            "SELECT name, count(*) AS n FROM customers GROUP BY name "
            "HAVING count(*) > 1 ORDER BY name",
            "duplicate-names",
        )
    if _has(lower, "customer") and _has(
        lower, "never placed", "no orders", "without orders", "never ordered"
    ):
        return Plan.answer(
            question,
            "SELECT count(*) AS n FROM customers c WHERE NOT EXISTS "
            "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
            "customers-without-orders",
        )
    if _has(lower, "product") and _has(lower, "never been ordered", "never ordered"):
        return Plan.answer(
            question,
            "SELECT count(*) AS n FROM products p WHERE NOT EXISTS "
            "(SELECT 1 FROM order_items oi WHERE oi.product_id = p.id)",
            "products-never-ordered",
        )

    # Inventory -------------------------------------------------------------------
    if _has(lower, "reorder point", "reorder level"):
        if _has(lower, "list", "which inventory", "show"):
            return Plan.answer(
                question,
                "SELECT p.name AS product, i.warehouse, i.quantity_on_hand, "
                "i.reorder_point FROM inventory i JOIN products p ON p.id = i.product_id "
                "WHERE i.quantity_on_hand < i.reorder_point "
                "ORDER BY i.quantity_on_hand - i.reorder_point, p.name, i.warehouse LIMIT 20",
                "inventory-below-reorder-list",
            )
        return Plan.answer(
            question,
            "SELECT count(*) AS n FROM inventory WHERE quantity_on_hand < reorder_point",
            "inventory-below-reorder-count",
        )
    if _has(lower, "quantity on hand", "total quantity", "units in stock"):
        return Plan.answer(
            question,
            "SELECT sum(quantity_on_hand) AS units FROM inventory",
            "inventory-total",
        )

    # Refunds, invoices, tickets, suppliers --------------------------------------
    if "refund" in lower:
        clause = f" WHERE {period.clause('paid_at')}" if period else ""
        if _has(lower, "how many", "number of", "count"):
            return Plan.answer(
                question,
                f"SELECT count(*) AS n FROM payments{clause}"
                + (" AND amount < 0" if clause else " WHERE amount < 0"),
                "refund-count",
            )
        return Plan.answer(
            question,
            f"SELECT sum(amount) AS total FROM payments{clause}"
            + (" AND amount < 0" if clause else " WHERE amount < 0"),
            "refund-total",
        )
    if "overdue" in lower and _has(lower, "invoice", "invoices"):
        return Plan.answer(
            question,
            "SELECT count(*) AS n FROM invoices WHERE status = 'overdue'",
            "overdue-invoices",
        )
    if "satisfaction" in lower:
        if re.search(r"\b(by|per|each)\b[^.]*\bpriority\b", lower):
            return Plan.answer(
                question,
                "SELECT priority, avg(satisfaction) AS avg_satisfaction "
                "FROM support_tickets WHERE satisfaction IS NOT NULL "
                "GROUP BY priority ORDER BY priority",
                "satisfaction-by-priority",
            )
        return Plan.answer(
            question,
            "SELECT avg(satisfaction) AS avg_satisfaction FROM support_tickets "
            "WHERE satisfaction IS NOT NULL",
            "avg-satisfaction",
        )
    if _has(lower, "ticket") and _has(lower, "open", "unresolved"):
        return Plan.answer(
            question,
            "SELECT count(*) AS n FROM support_tickets WHERE closed_at IS NULL",
            "open-tickets",
        )
    if "lead time" in lower and _has(lower, "supplier", "suppliers"):
        if _has(lower, "longest", "highest", "which"):
            return Plan.answer(
                question,
                "SELECT name, lead_time_days FROM suppliers "
                "ORDER BY lead_time_days DESC, name LIMIT 1",
                "supplier-longest-lead",
            )
        return Plan.answer(
            question,
            "SELECT avg(lead_time_days) AS avg_days, max(lead_time_days) AS max_days "
            "FROM suppliers",
            "supplier-lead-time",
        )

    # Timezone / explicit day -----------------------------------------------------
    day_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", question)
    if day_match is not None and _has(lower, "order"):
        day = day_match.group(1)
        from datetime import date, timedelta

        parsed = date.fromisoformat(day)
        end = (parsed + timedelta(days=1)).isoformat()
        return Plan.answer(
            question,
            "SELECT count(*) AS n FROM orders WHERE "
            f"ordered_at >= '{day}' AND ordered_at < '{end}'",
            "orders-on-day-utc",
        )

    # Payments --------------------------------------------------------------------
    if "payment" in lower and _has(lower, "method", "by method", "breakdown"):
        return Plan.answer(
            question,
            "SELECT method, count(*) AS n, sum(amount) AS total FROM payments "
            "GROUP BY method ORDER BY method",
            "payments-by-method",
        )

    # Revenue and order value -----------------------------------------------------
    if _has(lower, "revenue", "sales") and _has(
        lower, "month", "monthly", "each month", "per month"
    ):
        clause = f" WHERE {period.clause('paid_at')}" if period else ""
        return Plan.answer(
            question,
            "SELECT date_trunc('month', paid_at AT TIME ZONE 'UTC')::date AS month, "
            f"sum(amount) AS revenue FROM payments{clause} GROUP BY 1 ORDER BY 1",
            "monthly-revenue",
        )
    if _has(lower, "revenue", "sales") and _has(lower, "segment", "segments"):
        return Plan.answer(question, _revenue_by_segment(period), "revenue-by-segment")
    if _has(lower, "top", "best-selling", "best selling") and "product" in lower:
        return Plan.answer(question, _top_products(limit, period), "top-products")
    if _has(lower, "top", "best") and "customer" in lower:
        return Plan.answer(question, _top_customers(limit, period), "top-customers")
    if _has(lower, "average order", "aov"):
        clause = f" WHERE {period.clause('ordered_at')}" if period else ""
        return Plan.answer(
            question,
            f"SELECT avg(total_amount) AS avg_order_value FROM orders{clause}",
            "average-order-value",
        )
    if _has(lower, "order value", "total sales", "order total") and _has(
        lower, "total", "sum", "what was", "value"
    ):
        clause = f" WHERE {period.clause('ordered_at')}" if period else ""
        return Plan.answer(
            question,
            f"SELECT sum(total_amount) AS total_order_value FROM orders{clause}",
            "total-order-value",
        )
    if _has(lower, "revenue", "sales"):
        clause = f" WHERE {period.clause('paid_at')}" if period else ""
        if _has(lower, "gross", "positive", "before refunds"):
            condition = "amount > 0"
            where = f" WHERE {condition}"
            if period is not None:
                where = f" WHERE {period.clause('paid_at')} AND {condition}"
            return Plan.answer(
                question,
                f"SELECT sum(amount) AS revenue FROM payments{where}",
                "gross-revenue",
            )
        return Plan.answer(
            question,
            f"SELECT sum(amount) AS revenue FROM payments{clause}",
            "net-revenue",
        )

    # Grouped counts --------------------------------------------------------------
    if _has(lower, "per customer segment", "by customer segment", "per segment", "by segment"):
        clause = f" WHERE {period.clause('o.ordered_at')}" if period else ""
        return Plan.answer(
            question,
            "SELECT c.segment, count(*) AS n FROM orders o "
            "JOIN customers c ON c.id = o.customer_id"
            f"{clause} GROUP BY c.segment ORDER BY c.segment",
            "orders-per-segment",
        )
    if "distinct customers" in lower and _has(lower, "order", "ordered", "purchase"):
        clause = f" WHERE {period.clause('ordered_at')}" if period else ""
        return Plan.answer(
            question,
            f"SELECT count(DISTINCT customer_id) AS n FROM orders{clause}",
            "distinct-ordering-customers",
        )
    if _has(lower, "by status", "per status", "each status"):
        if "invoice" in lower:
            return Plan.answer(
                question,
                "SELECT status, count(*) AS n FROM invoices GROUP BY status ORDER BY status",
                "invoices-by-status",
            )
        clause = f" WHERE {period.clause('ordered_at')}" if period else ""
        return Plan.answer(
            question,
            f"SELECT status, count(*) AS n FROM orders{clause} GROUP BY status ORDER BY status",
            "orders-by-status",
        )
    if _has(lower, "by channel", "per channel", "each channel"):
        clause = f" WHERE {period.clause('ordered_at')}" if period else ""
        return Plan.answer(
            question,
            f"SELECT channel, count(*) AS n FROM orders{clause} GROUP BY channel ORDER BY channel",
            "orders-by-channel",
        )
    if _has(lower, "by segment", "each segment", "per segment") and "customer" in lower:
        return Plan.answer(
            question,
            "SELECT segment, count(*) AS n FROM customers GROUP BY segment ORDER BY segment",
            "customers-by-segment",
        )
    if _has(lower, "by category", "each category", "per category"):
        return Plan.answer(
            question,
            "SELECT category, count(*) AS n FROM products GROUP BY category ORDER BY category",
            "products-by-category",
        )
    if _has(lower, "by priority", "per priority", "each priority"):
        return Plan.answer(
            question,
            "SELECT priority, count(*) AS n FROM support_tickets "
            "GROUP BY priority ORDER BY priority",
            "tickets-by-priority",
        )
    if "supplier" in lower and _has(lower, "by country", "each country", "per country"):
        return Plan.answer(
            question,
            "SELECT country, count(*) AS n FROM suppliers GROUP BY country ORDER BY country",
            "suppliers-by-country",
        )
    if "month" in lower and _has(lower, "order", "orders"):
        clause = f" WHERE {period.clause('ordered_at')}" if period else ""
        return Plan.answer(
            question,
            "SELECT date_trunc('month', ordered_at AT TIME ZONE 'UTC')::date AS month, "
            f"count(*) AS n FROM orders{clause} GROUP BY 1 ORDER BY 1",
            "orders-by-month",
        )

    # Simple counts and filters ---------------------------------------------------
    if _has(lower, "how many", "number of", "count") or _has(
        lower, "what is the total", "total number"
    ):
        if "customer" in lower:
            if "vip" in lower:
                return Plan.answer(
                    question,
                    "SELECT count(*) AS n FROM customers WHERE segment = 'vip'",
                    "count-vip-customers",
                )
            if _has(lower, "missing a phone", "no phone", "phone is null", "null phone"):
                return Plan.answer(
                    question,
                    "SELECT count(*) AS n FROM customers WHERE phone IS NULL",
                    "null-phone-count",
                )
            return Plan.answer(question, "SELECT count(*) AS n FROM customers", "count-customers")
        if "supplier" in lower:
            return Plan.answer(question, "SELECT count(*) AS n FROM suppliers", "count-suppliers")
        if "product" in lower:
            if "active" in lower:
                return Plan.answer(
                    question,
                    "SELECT count(*) AS n FROM products WHERE active",
                    "count-active-products",
                )
            return Plan.answer(question, "SELECT count(*) AS n FROM products", "count-products")
        if "ticket" in lower:
            return Plan.answer(
                question, "SELECT count(*) AS n FROM support_tickets", "count-tickets"
            )
        if "invoice" in lower:
            return Plan.answer(question, "SELECT count(*) AS n FROM invoices", "count-invoices")
        if "order" in lower:
            status_map = {
                "cancelled": "cancelled",
                "canceled": "cancelled",
                "shipped": "shipped",
                "delivered": "delivered",
                "returned": "returned",
                "pending": "pending",
            }
            for word, status in status_map.items():
                if word in lower:
                    return Plan.answer(
                        question,
                        f"SELECT count(*) AS n FROM orders WHERE status = '{status}'",
                        "count-orders-by-status",
                    )
            clause = f" WHERE {period.clause('ordered_at')}" if period else ""
            return Plan.answer(
                question, f"SELECT count(*) AS n FROM orders{clause}", "count-orders"
            )

    return Plan.refuse(
        question,
        "no supported plan for the question; refusing rather than guessing",
    )


class NaivePlanner:
    """A deliberately simple single-prompt baseline.

    It understands only "how many <table>" and otherwise selects everything from
    the first table it recognizes. It is used to prove the eval set discriminates.
    """

    TABLE_WORDS = (
        "customers",
        "orders",
        "products",
        "invoices",
        "payments",
        "suppliers",
        "inventory",
        "support_tickets",
    )

    def plan(self, question: str) -> Plan:
        reason = refuse_reason(question)
        if reason is not None:
            return Plan.refuse(question, reason)
        lower = question.lower()
        table = next((t for t in self.TABLE_WORDS if t.rstrip("s") in lower), "orders")
        if "how many" in lower:
            return Plan.answer(question, f"SELECT count(*) AS n FROM {table}", "naive-count")
        return Plan.answer(question, f"SELECT * FROM {table} LIMIT 10", "naive-select-all")
