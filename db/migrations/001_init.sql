-- Business schema for OpsQuery. Read-only analytics over an ERP/POS system.
-- All timestamps are UTC (timestamptz). Money is NUMERIC(12,2), never float.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE customers (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    email       TEXT        NOT NULL,
    phone       TEXT,
    segment     TEXT        NOT NULL CHECK (segment IN ('retail', 'wholesale', 'vip')),
    city        TEXT        NOT NULL,
    country     TEXT        NOT NULL DEFAULT 'US',
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX customers_email_key ON customers (LOWER(email));
CREATE INDEX customers_name_idx ON customers (name);
CREATE INDEX customers_created_at_idx ON customers (created_at);

CREATE TABLE products (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT          NOT NULL UNIQUE,
    name        TEXT          NOT NULL,
    category    TEXT          NOT NULL,
    unit_price  NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    active      BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ   NOT NULL
);

CREATE INDEX products_category_idx ON products (category);

CREATE TABLE suppliers (
    id             BIGSERIAL PRIMARY KEY,
    name           TEXT          NOT NULL,
    country        TEXT          NOT NULL,
    lead_time_days INTEGER       NOT NULL CHECK (lead_time_days > 0),
    rating         NUMERIC(3,2)  NOT NULL CHECK (rating >= 0 AND rating <= 5)
);

CREATE TABLE inventory (
    id                BIGSERIAL PRIMARY KEY,
    product_id        BIGINT      NOT NULL REFERENCES products (id),
    warehouse         TEXT        NOT NULL,
    quantity_on_hand  INTEGER     NOT NULL,
    reorder_point     INTEGER     NOT NULL CHECK (reorder_point >= 0),
    updated_at        TIMESTAMPTZ NOT NULL,
    UNIQUE (product_id, warehouse)
);

CREATE INDEX inventory_product_idx ON inventory (product_id);

CREATE TABLE orders (
    id           BIGSERIAL PRIMARY KEY,
    order_number TEXT          NOT NULL UNIQUE,
    customer_id  BIGINT        NOT NULL REFERENCES customers (id),
    status       TEXT          NOT NULL CHECK (status IN
                     ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'returned')),
    channel      TEXT          NOT NULL CHECK (channel IN ('web', 'store', 'phone', 'partner')),
    ordered_at   TIMESTAMPTZ   NOT NULL,
    shipped_at   TIMESTAMPTZ,
    currency     CHAR(3)       NOT NULL DEFAULT 'USD',
    total_amount NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0)
);

CREATE INDEX orders_customer_idx ON orders (customer_id);
CREATE INDEX orders_ordered_at_idx ON orders (ordered_at);
CREATE INDEX orders_status_idx ON orders (status);

CREATE TABLE order_items (
    id           BIGSERIAL PRIMARY KEY,
    order_id     BIGINT        NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id   BIGINT        NOT NULL REFERENCES products (id),
    quantity     INTEGER       NOT NULL CHECK (quantity > 0),
    unit_price   NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    discount_pct NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct <= 1),
    line_total   NUMERIC(12,2) NOT NULL CHECK (line_total >= 0)
);

CREATE INDEX order_items_order_idx ON order_items (order_id);
CREATE INDEX order_items_product_idx ON order_items (product_id);

CREATE TABLE invoices (
    id             BIGSERIAL PRIMARY KEY,
    order_id       BIGINT        NOT NULL REFERENCES orders (id),
    invoice_number TEXT          NOT NULL UNIQUE,
    issued_at      DATE          NOT NULL,
    due_at         DATE          NOT NULL,
    status         TEXT          NOT NULL CHECK (status IN ('draft', 'sent', 'paid', 'overdue', 'void')),
    amount         NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    paid_at        DATE
);

CREATE INDEX invoices_order_idx ON invoices (order_id);
CREATE INDEX invoices_issued_at_idx ON invoices (issued_at);
CREATE INDEX invoices_status_idx ON invoices (status);

CREATE TABLE payments (
    id         BIGSERIAL PRIMARY KEY,
    invoice_id BIGINT        NOT NULL REFERENCES invoices (id),
    paid_at    TIMESTAMPTZ   NOT NULL,
    amount     NUMERIC(12,2) NOT NULL,
    method     TEXT          NOT NULL CHECK (method IN ('card', 'bank', 'cash', 'credit')),
    reference  TEXT          NOT NULL
);

CREATE INDEX payments_invoice_idx ON payments (invoice_id);
CREATE INDEX payments_paid_at_idx ON payments (paid_at);

CREATE TABLE support_tickets (
    id           BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT      NOT NULL REFERENCES customers (id),
    opened_at    TIMESTAMPTZ NOT NULL,
    closed_at    TIMESTAMPTZ,
    category     TEXT        NOT NULL,
    priority     TEXT        NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    subject      TEXT        NOT NULL,
    satisfaction SMALLINT    CHECK (satisfaction BETWEEN 1 AND 5)
);

CREATE INDEX support_tickets_customer_idx ON support_tickets (customer_id);
CREATE INDEX support_tickets_opened_at_idx ON support_tickets (opened_at);
