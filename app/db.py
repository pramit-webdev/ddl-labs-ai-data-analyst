"""SQLite warehouse: schema, views, connections and health checks."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings, settings

SCHEMA_VERSION = "2"

SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT,
    country     TEXT,
    state       TEXT,
    city        TEXT,
    city_tier   TEXT,
    segment     TEXT,
    signup_date TEXT
);

CREATE TABLE products (
    product_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    unit_price  REAL NOT NULL CHECK (unit_price >= 0),
    unit_cost   REAL,
    launch_date TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE orders (
    order_id      TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL,
    order_date    TEXT NOT NULL,
    status         TEXT NOT NULL,
    channel        TEXT,
    payment_method TEXT,
    shipping_cost  REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    item_id      TEXT PRIMARY KEY,
    order_id     TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    unit_price   REAL NOT NULL CHECK (unit_price >= 0),
    discount_pct REAL NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct <= 100),
    line_revenue REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL,
    report_json TEXT NOT NULL
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_status_date ON orders(status, order_date);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
CREATE INDEX idx_customers_signup ON customers(signup_date);

CREATE VIEW v_order_revenue AS
SELECT
    o.order_id,
    o.order_date,
    substr(o.order_date, 1, 7) AS month,
    o.customer_id,
    o.channel,
    o.payment_method,
    c.city_tier,
    c.state,
    c.country,
    SUM(oi.line_revenue) AS revenue,
    SUM(oi.quantity) AS units,
    SUM(oi.quantity * oi.unit_price) AS gross_revenue,
    SUM(oi.quantity * oi.unit_price * oi.discount_pct / 100.0) AS discount_amount
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN customers c ON c.customer_id = o.customer_id
WHERE o.status = 'completed'
GROUP BY o.order_id;

CREATE VIEW v_monthly_revenue AS
SELECT
    month,
    COUNT(*) AS orders,
    SUM(revenue) AS revenue,
    SUM(units) AS units,
    SUM(revenue) / COUNT(*) AS aov
FROM v_order_revenue
GROUP BY month;

CREATE VIEW v_category_revenue AS
SELECT
    p.category AS category,
    substr(o.order_date, 1, 7) AS month,
    SUM(oi.line_revenue) AS revenue,
    SUM(oi.quantity) AS units,
    COUNT(DISTINCT o.order_id) AS orders
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'completed'
GROUP BY p.category, substr(o.order_date, 1, 7);

CREATE VIEW v_product_revenue AS
SELECT
    p.product_id,
    p.name,
    p.category,
    substr(o.order_date, 1, 7) AS month,
    SUM(oi.line_revenue) AS revenue,
    SUM(oi.quantity) AS units,
    COUNT(DISTINCT o.order_id) AS orders
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'completed'
GROUP BY p.product_id, substr(o.order_date, 1, 7);

CREATE VIEW v_customer_revenue AS
SELECT
    c.customer_id,
    c.name,
    c.email,
    c.segment,
    c.country,
    c.state,
    c.city,
    c.city_tier,
    MIN(o.order_date) AS first_order,
    MAX(o.order_date) AS last_order,
    COUNT(o.order_id) AS orders,
    COALESCE(SUM(o.revenue), 0) AS revenue,
    COALESCE(SUM(o.units), 0) AS units
FROM customers c
LEFT JOIN v_order_revenue o ON o.customer_id = c.customer_id
GROUP BY c.customer_id;

CREATE VIEW v_order_items_detail AS
SELECT
    o.order_id,
    o.order_date,
    substr(o.order_date, 1, 7) AS month,
    o.status,
    o.channel,
    o.payment_method,
    o.customer_id,
    c.segment,
    c.country,
    c.state,
    c.city_tier,
    p.product_id,
    p.name AS product_name,
    p.category,
    oi.quantity,
    oi.unit_price,
    oi.discount_pct,
    oi.line_revenue
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN products p ON p.product_id = oi.product_id
JOIN customers c ON c.customer_id = o.customer_id;

CREATE VIEW v_payment_revenue AS
SELECT
    payment_method,
    substr(order_date, 1, 7) AS month,
    SUM(revenue) AS revenue,
    COUNT(*) AS orders
FROM v_order_revenue
GROUP BY payment_method, substr(order_date, 1, 7);

CREATE VIEW v_city_tier_revenue AS
SELECT
    city_tier,
    substr(order_date, 1, 7) AS month,
    SUM(revenue) AS revenue,
    COUNT(*) AS orders,
    COUNT(DISTINCT customer_id) AS customers
FROM v_order_revenue
GROUP BY city_tier, substr(order_date, 1, 7);

CREATE VIEW v_channel_revenue AS
SELECT
    channel,
    substr(order_date, 1, 7) AS month,
    SUM(revenue) AS revenue,
    COUNT(*) AS orders,
    COUNT(DISTINCT customer_id) AS customers
FROM v_order_revenue
GROUP BY channel, substr(order_date, 1, 7);

CREATE VIEW v_state_revenue AS
SELECT
    state,
    substr(order_date, 1, 7) AS month,
    SUM(revenue) AS revenue,
    COUNT(*) AS orders,
    COUNT(DISTINCT customer_id) AS customers
FROM v_order_revenue
GROUP BY state, substr(order_date, 1, 7);
"""

# Tables/views that may be referenced by the read-only query tool.
QUERYABLE_OBJECTS = {
    "customers", "products", "orders", "order_items",
    "v_order_revenue", "v_monthly_revenue", "v_category_revenue",
    "v_product_revenue", "v_customer_revenue", "v_order_items_detail",
    "v_payment_revenue", "v_city_tier_revenue", "v_channel_revenue", "v_state_revenue",
}
BLOCKED_OBJECTS = {"meta", "pipeline_runs", "sqlite_master", "sqlite_schema"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=5.0,
        )
    else:
        conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


class Database:
    """Small repository around the SQLite warehouse."""

    def __init__(self, cfg: Settings = settings):
        self.cfg = cfg
        self.path = cfg.db_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def initialize(self, force: bool = False) -> dict[str, Any]:
        """Create the warehouse if missing (or rebuild when force=True)."""
        if force or not self.exists:
            from app.ingest import build_warehouse

            return build_warehouse(self.cfg)
        return self.meta()

    def ensure_ready(self) -> None:
        if not self.exists:
            self.initialize()

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------
    @contextmanager
    def connection(self, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        self.ensure_ready()
        conn = connect(self.path, readonly=readonly)
        try:
            yield conn
        finally:
            conn.close()

    def query(self, sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.execute(sql, params or ())
            return [dict(row) for row in cur.fetchall()]

    def one(self, sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple | dict | None = None) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    def meta(self) -> dict[str, Any]:
        if not self.exists:
            return {}
        try:
            rows = self.query("SELECT key, value FROM meta")
        except sqlite3.Error:
            return {}
        result: dict[str, Any] = {}
        for row in rows:
            value = row["value"]
            try:
                result[row["key"]] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result[row["key"]] = value
        return result

    @property
    def as_of(self) -> str | None:
        if not self.exists:
            return None
        return self.scalar("SELECT MAX(order_date) FROM orders WHERE status = 'completed'")

    def counts(self) -> dict[str, int]:
        if not self.exists:
            return {}
        return {
            "customers": int(self.scalar("SELECT COUNT(*) FROM customers") or 0),
            "products": int(self.scalar("SELECT COUNT(*) FROM products") or 0),
            "orders": int(self.scalar("SELECT COUNT(*) FROM orders WHERE status = 'completed'") or 0),
            "order_items": int(
                self.scalar(
                    "SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                    "WHERE o.status = 'completed'"
                )
                or 0
            ),
        }

    def quality_report(self) -> dict[str, Any]:
        if self.cfg.quality_report_path.exists():
            try:
                return json.loads(self.cfg.quality_report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def health(self) -> dict[str, Any]:
        try:
            self.ensure_ready()
            with self.connection(readonly=True) as conn:
                conn.execute("SELECT 1").fetchone()
            return {
                "status": "ok",
                "database": "sqlite",
                "as_of": self.as_of,
                "counts": self.counts(),
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {"status": "error", "database": "sqlite", "detail": str(exc)}


db = Database()
