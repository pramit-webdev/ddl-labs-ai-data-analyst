"""Business analytics: every number the API and agent return comes from here."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.db import Database, db as default_db
from app.errors import InvalidPeriodError

PERIOD_KEYS = (
    "latest_month",
    "previous_month",
    "last_30_days",
    "last_90_days",
    "last_12_months",
    "ytd",
    "all_time",
)
PALETTE = ["#0F766E", "#2563EB", "#F59E0B", "#E11D48", "#7C3AED", "#0891B2", "#65A30D", "#EA580C"]


# ---------------------------------------------------------------------------
# Date and formatting helpers
# ---------------------------------------------------------------------------
def add_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_bounds(month: str) -> tuple[date, date]:
    try:
        year, mon = (int(part) for part in month.split("-"))
        return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])
    except (ValueError, TypeError):
        raise InvalidPeriodError(f"Invalid month '{month}'. Expected YYYY-MM.")


def fmt_day(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def fmt_month(value: date) -> str:
    return value.strftime("%b %Y")


def fmt_short_month(ym: str) -> str:
    return date.fromisoformat(f"{ym}-01").strftime("%b %y")


def _group_indian(whole: str) -> str:
    """Group digits the Indian way: 12,34,56,789."""
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def _format_inr(value: float | int | None, decimals: int = 2) -> str:
    value = float(value or 0)
    sign = "-" if value < 0 else ""
    whole, _, fraction = f"{abs(value):.{decimals}f}".partition(".")
    grouped = _group_indian(whole)
    return f"{sign}₹{grouped}" + (f".{fraction}" if decimals else "")


def money(value: float | int | None) -> str:
    return _format_inr(value, 2)


def signed_money(value: float | int | None) -> str:
    value = float(value or 0)
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_format_inr(abs(value), 2)}"


def compact_money(value: float | int | None) -> str:
    value = float(value or 0)
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    if absolute >= 1_00_00_000:
        return f"{sign}₹{absolute / 1_00_00_000:.2f} Cr"
    if absolute >= 1_00_000:
        return f"{sign}₹{absolute / 1_00_000:.2f} L"
    if absolute >= 1_000:
        return f"{sign}₹{absolute / 1_000:.1f}K"
    return f"{sign}₹{absolute:,.0f}"


def pct(value: float | None) -> str:
    return f"{float(value or 0):.1f}%"


def signed_pct(value: float | None) -> str:
    return f"{float(value or 0):+.1f}%"


def num(value: float | int | None) -> str:
    return f"{int(value or 0):,}"


def _verb(delta: float, up: str = "rose", down: str = "fell") -> str:
    return up if delta >= 0 else down


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: date
    end: date
    previous_start: date | None
    previous_end: date | None
    comparison_label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": (self.end - self.start).days + 1,
            "comparison_start": self.previous_start.isoformat() if self.previous_start else None,
            "comparison_end": self.previous_end.isoformat() if self.previous_end else None,
            "comparison_label": self.comparison_label,
        }


class AnalyticsService:
    def __init__(self, database: Database | None = None):
        self.db = database or default_db

    # ------------------------------------------------------------------
    # Setup / periods
    # ------------------------------------------------------------------
    @property
    def as_of(self) -> date:
        value = self.db.as_of
        if not value:
            raise InvalidPeriodError("The warehouse has no completed orders.")
        return date.fromisoformat(value)

    @property
    def data_bounds(self) -> tuple[date, date]:
        row = self.db.one(
            "SELECT MIN(order_date) AS start, MAX(order_date) AS end "
            "FROM orders WHERE status = 'completed'"
        ) or {}
        return date.fromisoformat(row["start"]), date.fromisoformat(row["end"])

    def period(self, key: str) -> Period:
        if key not in PERIOD_KEYS:
            raise InvalidPeriodError(
                f"Unknown period '{key}'. Choose one of: {', '.join(PERIOD_KEYS)}."
            )
        as_of = self.as_of
        latest_start = date(as_of.year, as_of.month, 1)
        prev_start = add_months(latest_start, -1)
        prev_end = latest_start - timedelta(days=1)
        first_date, _ = self.data_bounds

        if key == "latest_month":
            start, end = latest_start, as_of
            label = f"Latest month ({fmt_month(latest_start)})"
        elif key == "previous_month":
            start, end = prev_start, prev_end
            label = f"Previous month ({fmt_month(prev_start)})"
        elif key == "last_30_days":
            start, end = as_of - timedelta(days=29), as_of
            label = f"Last 30 days ({fmt_day(start)} - {fmt_day(end)})"
        elif key == "last_90_days":
            start, end = as_of - timedelta(days=89), as_of
            label = f"Last 90 days ({fmt_day(start)} - {fmt_day(end)})"
        elif key == "last_12_months":
            start, end = add_months(latest_start, -11), as_of
            label = f"Last 12 months ({fmt_month(start)} - {fmt_month(end)})"
        elif key == "ytd":
            start, end = date(as_of.year, 1, 1), as_of
            label = f"Year to date ({fmt_month(start)} - {fmt_month(end)})"
        else:  # all_time
            start, end = first_date, as_of
            label = f"All time ({fmt_month(start)} - {fmt_month(end)})"

        if key in {"latest_month", "previous_month"}:
            p_start = add_months(start, -1)
            p_end = start - timedelta(days=1)
        elif key == "last_12_months":
            p_start = add_months(start, -12)
            p_end = start - timedelta(days=1)
        elif key == "ytd":
            p_start = date(start.year - 1, 1, 1)
            p_end = date(start.year - 1, end.month, min(end.day, calendar.monthrange(start.year - 1, end.month)[1]))
        elif key == "all_time":
            p_start = p_end = None
        else:
            length = (end - start).days + 1
            p_end = start - timedelta(days=1)
            p_start = p_end - timedelta(days=length - 1)

        if p_start and p_end:
            comparison_label = f"{fmt_day(p_start)} - {fmt_day(p_end)}"
        else:
            comparison_label = "No comparison period"
        return Period(key, label, start, end, p_start, p_end, comparison_label)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    def _metrics(self, start: date, end: date) -> dict[str, Any]:
        row = self.db.one(
            """
            SELECT COUNT(*) AS orders,
                   COALESCE(SUM(revenue), 0) AS revenue,
                   COUNT(DISTINCT customer_id) AS customers,
                   COALESCE(SUM(units), 0) AS units
            FROM v_order_revenue
            WHERE order_date BETWEEN :start AND :end
            """,
            {"start": start.isoformat(), "end": end.isoformat()},
        ) or {}
        orders = int(row.get("orders") or 0)
        revenue = round(float(row.get("revenue") or 0), 2)
        discount = self.db.one(
            """
            SELECT COALESCE(SUM(quantity * unit_price), 0) AS gross,
                   COALESCE(SUM(quantity * unit_price * discount_pct / 100.0), 0) AS discount
            FROM v_order_items_detail
            WHERE status = 'completed' AND order_date BETWEEN :start AND :end
            """,
            {"start": start.isoformat(), "end": end.isoformat()},
        ) or {}
        gross = float(discount.get("gross") or 0)
        discount_amount = float(discount.get("discount") or 0)
        return {
            "revenue": revenue,
            "orders": orders,
            "customers": int(row.get("customers") or 0),
            "units": int(row.get("units") or 0),
            "aov": round(revenue / orders, 2) if orders else 0.0,
            "discount_rate_pct": round(discount_amount / gross * 100, 2) if gross else 0.0,
            "discount_amount": round(discount_amount, 2),
            "gross_revenue": round(gross, 2),
        }

    @staticmethod
    def _kpi(value: float, previous: float | None) -> dict[str, Any]:
        if previous is None:
            return {"value": round(value, 2), "previous": None, "delta": None, "delta_pct": None}
        delta = value - previous
        delta_pct = (delta / previous * 100) if previous else None
        return {
            "value": round(value, 2),
            "previous": round(previous, 2),
            "delta": round(delta, 2),
            "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
        }

    def kpis(self, period_key: str = "last_12_months") -> dict[str, Any]:
        p = self.period(period_key)
        current = self._metrics(p.start, p.end)
        previous = (
            self._metrics(p.previous_start, p.previous_end)
            if p.previous_start and p.previous_end
            else None
        )
        result: dict[str, Any] = {
            "period": p.to_dict(),
            "as_of": self.as_of.isoformat(),
            "current": current,
            "previous": previous,
            "kpis": {},
        }
        for metric in ("revenue", "orders", "customers", "aov"):
            result["kpis"][metric] = self._kpi(
                current[metric], previous[metric] if previous else None
            )
        return result

    # ------------------------------------------------------------------
    # Time series and breakdowns
    # ------------------------------------------------------------------
    def monthly_revenue(self, months: int = 12) -> list[dict[str, Any]]:
        months = max(1, min(36, int(months)))
        latest = date(self.as_of.year, self.as_of.month, 1)
        start = add_months(latest, -(months - 1))
        rows = self.db.query(
            """
            SELECT month, SUM(revenue) AS revenue, SUM(orders) AS orders, SUM(units) AS units
            FROM v_monthly_revenue
            WHERE month BETWEEN :start AND :end
            GROUP BY month ORDER BY month
            """,
            {"start": start.strftime("%Y-%m"), "end": latest.strftime("%Y-%m")},
        )
        by_month = {r["month"]: r for r in rows}
        result = []
        cursor = start
        for _ in range(months):
            key = cursor.strftime("%Y-%m")
            row = by_month.get(key)
            revenue = round(float(row["revenue"]), 2) if row else 0.0
            orders = int(row["orders"]) if row else 0
            result.append({
                "month": key,
                "label": fmt_short_month(key),
                "full_label": fmt_month(cursor),
                "revenue": revenue,
                "orders": orders,
                "units": int(row["units"]) if row else 0,
                "aov": round(revenue / orders, 2) if orders else 0.0,
            })
            cursor = add_months(cursor, 1)
        return result

    def revenue_by_category(self, period_key: str = "last_12_months") -> list[dict[str, Any]]:
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT p.category AS category,
                   SUM(oi.line_revenue) AS revenue,
                   SUM(oi.quantity) AS units,
                   COUNT(DISTINCT o.order_id) AS orders
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.status = 'completed' AND o.order_date BETWEEN :start AND :end
            GROUP BY p.category
            ORDER BY revenue DESC
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat()},
        )
        total = sum(float(r["revenue"]) for r in rows) or 1.0
        return [
            {
                "category": r["category"],
                "revenue": round(float(r["revenue"]), 2),
                "units": int(r["units"]),
                "orders": int(r["orders"]),
                "share_pct": round(float(r["revenue"]) / total * 100, 2),
            }
            for r in rows
        ]

    def payment_mix(self, period_key: str = "last_12_months") -> list[dict[str, Any]]:
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT payment_method,
                   SUM(revenue) AS revenue,
                   COUNT(*) AS orders,
                   COUNT(DISTINCT customer_id) AS customers
            FROM v_order_revenue
            WHERE order_date BETWEEN :start AND :end
            GROUP BY payment_method
            ORDER BY revenue DESC
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat()},
        )
        total = sum(float(r["revenue"]) for r in rows) or 1.0
        return [
            {
                "payment_method": r["payment_method"] or "Unknown",
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "customers": int(r["customers"]),
                "share_pct": round(float(r["revenue"]) / total * 100, 2),
            }
            for r in rows
        ]

    def city_tier_mix(self, period_key: str = "last_12_months") -> list[dict[str, Any]]:
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT city_tier,
                   SUM(revenue) AS revenue,
                   COUNT(*) AS orders,
                   COUNT(DISTINCT customer_id) AS customers
            FROM v_order_revenue
            WHERE order_date BETWEEN :start AND :end
            GROUP BY city_tier
            ORDER BY revenue DESC
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat()},
        )
        total = sum(float(r["revenue"]) for r in rows) or 1.0
        return [
            {
                "city_tier": r["city_tier"] or "Unknown",
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "customers": int(r["customers"]),
                "share_pct": round(float(r["revenue"]) / total * 100, 2),
            }
            for r in rows
        ]

    def channel_performance(self, period_key: str = "last_12_months") -> list[dict[str, Any]]:
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT channel,
                   SUM(revenue) AS revenue,
                   COUNT(*) AS orders,
                   COUNT(DISTINCT customer_id) AS customers,
                   AVG(revenue) AS aov
            FROM v_order_revenue
            WHERE order_date BETWEEN :start AND :end
            GROUP BY channel
            ORDER BY revenue DESC
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat()},
        )
        total = sum(float(r["revenue"]) for r in rows) or 1.0
        return [
            {
                "channel": r["channel"] or "Other",
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "customers": int(r["customers"]),
                "aov": round(float(r["aov"]), 2),
                "share_pct": round(float(r["revenue"]) / total * 100, 2),
            }
            for r in rows
        ]

    def top_states(self, period_key: str = "last_12_months", limit: int = 8) -> list[dict[str, Any]]:
        limit = max(1, min(20, int(limit)))
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT state,
                   SUM(revenue) AS revenue,
                   COUNT(*) AS orders,
                   COUNT(DISTINCT customer_id) AS customers
            FROM v_order_revenue
            WHERE order_date BETWEEN :start AND :end AND state IS NOT NULL
            GROUP BY state
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat(), "limit": limit},
        )
        return [
            {
                "rank": i + 1,
                "state": r["state"],
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "customers": int(r["customers"]),
            }
            for i, r in enumerate(rows)
        ]

    def top_products(
        self,
        period_key: str = "last_12_months",
        metric: str = "revenue",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if metric not in {"revenue", "units"}:
            raise InvalidPeriodError("metric must be 'revenue' or 'units'.")
        limit = max(1, min(20, int(limit)))
        p = self.period(period_key)
        order_by = "revenue DESC" if metric == "revenue" else "units DESC"
        rows = self.db.query(
            f"""
            SELECT p.product_id, p.name, p.category,
                   SUM(oi.line_revenue) AS revenue,
                   SUM(oi.quantity) AS units,
                   COUNT(DISTINCT o.order_id) AS orders
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.status = 'completed' AND o.order_date BETWEEN :start AND :end
            GROUP BY p.product_id
            ORDER BY {order_by}
            LIMIT :limit
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat(), "limit": limit},
        )
        return [
            {
                "rank": i + 1,
                "product_id": r["product_id"],
                "name": r["name"],
                "category": r["category"],
                "revenue": round(float(r["revenue"]), 2),
                "units": int(r["units"]),
                "orders": int(r["orders"]),
            }
            for i, r in enumerate(rows)
        ]

    def low_performing_products(
        self, period_key: str = "last_12_months", limit: int = 5
    ) -> list[dict[str, Any]]:
        limit = max(1, min(20, int(limit)))
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT p.product_id, p.name, p.category, p.unit_price,
                   COALESCE(SUM(CASE WHEN o.order_id IS NOT NULL THEN oi.line_revenue ELSE 0 END), 0) AS revenue,
                   COALESCE(SUM(CASE WHEN o.order_id IS NOT NULL THEN oi.quantity ELSE 0 END), 0) AS units,
                   COUNT(DISTINCT o.order_id) AS orders
            FROM products p
            LEFT JOIN order_items oi ON oi.product_id = p.product_id
            LEFT JOIN orders o ON o.order_id = oi.order_id
                 AND o.status = 'completed' AND o.order_date BETWEEN :start AND :end
            WHERE p.active = 1
              AND (p.launch_date IS NULL OR p.launch_date <= :end)
            GROUP BY p.product_id
            ORDER BY revenue ASC, units ASC
            LIMIT :limit
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat(), "limit": limit},
        )
        return [
            {
                "product_id": r["product_id"],
                "name": r["name"],
                "category": r["category"],
                "unit_price": round(float(r["unit_price"]), 2),
                "revenue": round(float(r["revenue"]), 2),
                "units": int(r["units"]),
                "orders": int(r["orders"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------
    def customer_metrics(self, inactive_days: int = 90) -> dict[str, Any]:
        inactive_days = max(1, min(730, int(inactive_days)))
        cutoff = self.as_of - timedelta(days=inactive_days)
        totals = self.db.one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END) AS repeat_customers,
                   SUM(CASE WHEN last_order IS NULL THEN 1 ELSE 0 END) AS never_purchased,
                   AVG(CASE WHEN orders > 0 THEN orders END) AS avg_orders
            FROM v_customer_revenue
            """
        ) or {}
        total = int(totals.get("total") or 0)
        never = int(totals.get("never_purchased") or 0)
        purchasing = total - never
        repeat = int(totals.get("repeat_customers") or 0)
        active = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM v_customer_revenue WHERE last_order >= :cutoff",
                {"cutoff": cutoff.isoformat()},
            ) or 0
        )
        inactive = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM v_customer_revenue "
                "WHERE last_order IS NOT NULL AND last_order < :cutoff",
                {"cutoff": cutoff.isoformat()},
            ) or 0
        )
        new_recent = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM v_customer_revenue WHERE first_order >= :cutoff",
                {"cutoff": (self.as_of - timedelta(days=29)).isoformat()},
            ) or 0
        )
        lifetime = float(
            self.db.scalar("SELECT COALESCE(SUM(revenue), 0) FROM v_customer_revenue") or 0
        )
        return {
            "as_of": self.as_of.isoformat(),
            "inactive_days": inactive_days,
            "cutoff": cutoff.isoformat(),
            "total_customers": total,
            "purchasing_customers": purchasing,
            "never_purchased": never,
            "active_customers": active,
            "inactive_customers": inactive,
            "new_customers_last_30_days": new_recent,
            "repeat_customers": repeat,
            "repeat_rate_pct": round(repeat / purchasing * 100, 2) if purchasing else 0.0,
            "inactive_rate_pct": round(inactive / total * 100, 2) if total else 0.0,
            "avg_orders_per_purchasing_customer": round(float(totals.get("avg_orders") or 0), 2),
            "lifetime_revenue": round(lifetime, 2),
            "avg_lifetime_revenue": round(lifetime / purchasing, 2) if purchasing else 0.0,
        }

    def inactive_customers(self, inactive_days: int = 90, limit: int = 10) -> dict[str, Any]:
        inactive_days = max(1, min(730, int(inactive_days)))
        limit = max(1, min(50, int(limit)))
        cutoff = self.as_of - timedelta(days=inactive_days)
        rows = self.db.query(
            """
            SELECT customer_id, name, email, segment, country, city,
                   first_order, last_order, orders, revenue
            FROM v_customer_revenue
            WHERE last_order IS NOT NULL AND last_order < :cutoff
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            {"cutoff": cutoff.isoformat(), "limit": limit},
        )
        total = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM v_customer_revenue "
                "WHERE last_order IS NOT NULL AND last_order < :cutoff",
                {"cutoff": cutoff.isoformat()},
            ) or 0
        )
        customers = []
        for r in rows:
            last = date.fromisoformat(r["last_order"])
            customers.append({
                "customer_id": r["customer_id"],
                "name": r["name"],
                "email": r["email"],
                "segment": r["segment"],
                "country": r["country"],
                "city": r["city"],
                "first_order": r["first_order"],
                "last_order": r["last_order"],
                "days_since_last_order": (self.as_of - last).days,
                "orders": int(r["orders"]),
                "lifetime_revenue": round(float(r["revenue"]), 2),
            })
        return {
            "inactive_days": inactive_days,
            "cutoff": cutoff.isoformat(),
            "total_inactive": total,
            "returned": len(customers),
            "customers": customers,
        }

    def top_customers(
        self, period_key: str = "last_12_months", limit: int = 5
    ) -> list[dict[str, Any]]:
        limit = max(1, min(20, int(limit)))
        p = self.period(period_key)
        rows = self.db.query(
            """
            SELECT c.customer_id, c.name, c.segment, c.country,
                   SUM(o.revenue) AS revenue,
                   COUNT(o.order_id) AS orders,
                   MAX(o.order_date) AS last_order
            FROM v_order_revenue o
            JOIN customers c ON c.customer_id = o.customer_id
            WHERE o.order_date BETWEEN :start AND :end
            GROUP BY c.customer_id
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            {"start": p.start.isoformat(), "end": p.end.isoformat(), "limit": limit},
        )
        return [
            {
                "rank": i + 1,
                "customer_id": r["customer_id"],
                "name": r["name"],
                "segment": r["segment"],
                "country": r["country"],
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "last_order": r["last_order"],
            }
            for i, r in enumerate(rows)
        ]

    def customer_trend(self, months: int = 12) -> list[dict[str, Any]]:
        months = max(1, min(36, int(months)))
        latest = date(self.as_of.year, self.as_of.month, 1)
        start = add_months(latest, -(months - 1))
        rows = self.db.query(
            """
            WITH first_orders AS (
                SELECT customer_id, MIN(order_date) AS first_order
                FROM v_order_revenue GROUP BY customer_id
            ),
            monthly AS (
                SELECT o.month, o.order_id, o.revenue, o.customer_id, f.first_order
                FROM v_order_revenue o
                JOIN first_orders f ON f.customer_id = o.customer_id
                WHERE o.month BETWEEN :start AND :end
            )
            SELECT month,
                   COUNT(DISTINCT CASE WHEN substr(first_order, 1, 7) = month THEN customer_id END) AS new_customers,
                   COUNT(DISTINCT CASE WHEN substr(first_order, 1, 7) < month THEN customer_id END) AS returning_customers,
                   SUM(CASE WHEN substr(first_order, 1, 7) = month THEN revenue ELSE 0 END) AS new_revenue,
                   SUM(CASE WHEN substr(first_order, 1, 7) < month THEN revenue ELSE 0 END) AS returning_revenue,
                   COUNT(DISTINCT order_id) AS orders
            FROM monthly GROUP BY month ORDER BY month
            """,
            {"start": start.strftime("%Y-%m"), "end": latest.strftime("%Y-%m")},
        )
        by_month = {r["month"]: r for r in rows}
        result = []
        cursor = start
        for _ in range(months):
            key = cursor.strftime("%Y-%m")
            row = by_month.get(key) or {}
            result.append({
                "month": key,
                "label": fmt_short_month(key),
                "new_customers": int(row.get("new_customers") or 0),
                "returning_customers": int(row.get("returning_customers") or 0),
                "new_revenue": round(float(row.get("new_revenue") or 0), 2),
                "returning_revenue": round(float(row.get("returning_revenue") or 0), 2),
                "orders": int(row.get("orders") or 0),
            })
            cursor = add_months(cursor, 1)
        return result

    # ------------------------------------------------------------------
    # Multi-step decline analysis
    # ------------------------------------------------------------------
    def _month_metrics(self, month: str) -> dict[str, Any]:
        start, end = month_bounds(month)
        end = min(end, self.as_of)
        base = self._metrics(start, end)
        new_returning = self.db.one(
            """
            WITH first_orders AS (
                SELECT customer_id, MIN(order_date) AS first_order
                FROM v_order_revenue GROUP BY customer_id
            )
            SELECT
                COUNT(DISTINCT CASE WHEN substr(f.first_order, 1, 7) = :month THEN o.order_id END) AS new_orders,
                COUNT(DISTINCT CASE WHEN substr(f.first_order, 1, 7) < :month THEN o.order_id END) AS returning_orders,
                COALESCE(SUM(CASE WHEN substr(f.first_order, 1, 7) = :month THEN o.revenue ELSE 0 END), 0) AS new_revenue,
                COALESCE(SUM(CASE WHEN substr(f.first_order, 1, 7) < :month THEN o.revenue ELSE 0 END), 0) AS returning_revenue
            FROM v_order_revenue o
            JOIN first_orders f ON f.customer_id = o.customer_id
            WHERE o.order_date BETWEEN :start AND :end
            """,
            {"month": month, "start": start.isoformat(), "end": end.isoformat()},
        ) or {}
        base.update({
            "month": month,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "new_customer_orders": int(new_returning.get("new_orders") or 0),
            "returning_customer_orders": int(new_returning.get("returning_orders") or 0),
            "new_customer_revenue": round(float(new_returning.get("new_revenue") or 0), 2),
            "returning_customer_revenue": round(float(new_returning.get("returning_revenue") or 0), 2),
        })
        base["aov_raw"] = base["revenue"] / base["orders"] if base["orders"] else 0.0
        return base

    def _breakdown(self, month: str, dimension: str) -> dict[str, dict[str, float]]:
        if dimension == "category":
            sql = """
                SELECT p.category AS name, SUM(oi.line_revenue) AS revenue,
                       SUM(oi.quantity) AS units, COUNT(DISTINCT o.order_id) AS orders
                FROM orders o
                JOIN order_items oi ON oi.order_id = o.order_id
                JOIN products p ON p.product_id = oi.product_id
                WHERE o.status = 'completed' AND substr(o.order_date, 1, 7) = :month
                GROUP BY p.category
            """
        elif dimension == "product":
            sql = """
                SELECT p.product_id AS id, p.name AS name, p.category AS category,
                       SUM(oi.line_revenue) AS revenue, SUM(oi.quantity) AS units,
                       COUNT(DISTINCT o.order_id) AS orders
                FROM orders o
                JOIN order_items oi ON oi.order_id = o.order_id
                JOIN products p ON p.product_id = oi.product_id
                WHERE o.status = 'completed' AND substr(o.order_date, 1, 7) = :month
                GROUP BY p.product_id
            """
        elif dimension == "channel":
            sql = """
                SELECT channel AS name, SUM(revenue) AS revenue, COUNT(*) AS orders
                FROM v_order_revenue WHERE month = :month GROUP BY channel
            """
        elif dimension == "segment":
            sql = """
                SELECT c.segment AS name, SUM(o.revenue) AS revenue, COUNT(DISTINCT o.order_id) AS orders
                FROM v_order_revenue o JOIN customers c ON c.customer_id = o.customer_id
                WHERE o.month = :month GROUP BY c.segment
            """
        elif dimension == "payment":
            sql = """
                SELECT payment_method AS name, SUM(revenue) AS revenue, COUNT(*) AS orders
                FROM v_order_revenue WHERE month = :month GROUP BY payment_method
            """
        elif dimension == "city_tier":
            sql = """
                SELECT city_tier AS name, SUM(revenue) AS revenue, COUNT(*) AS orders
                FROM v_order_revenue WHERE month = :month GROUP BY city_tier
            """
        elif dimension == "state":
            sql = """
                SELECT state AS name, SUM(revenue) AS revenue, COUNT(*) AS orders
                FROM v_order_revenue
                WHERE month = :month AND state IS NOT NULL GROUP BY state
            """
        else:
            raise ValueError(dimension)
        rows = self.db.query(sql, {"month": month})
        return {r.get("id") or r["name"]: r for r in rows}

    def _deltas(self, dimension: str, previous_month: str, current_month: str, limit: int = 8) -> list[dict[str, Any]]:
        prev = self._breakdown(previous_month, dimension)
        cur = self._breakdown(current_month, dimension)
        total_prev = sum(float(r["revenue"]) for r in prev.values())
        total_cur = sum(float(r["revenue"]) for r in cur.values())
        total_delta = total_cur - total_prev
        keys = set(prev) | set(cur)
        rows = []
        for key in keys:
            p = prev.get(key, {})
            c = cur.get(key, {})
            revenue_prev = float(p.get("revenue") or 0)
            revenue_cur = float(c.get("revenue") or 0)
            delta = revenue_cur - revenue_prev
            rows.append({
                "key": key,
                "name": c.get("name") or p.get("name") or key,
                "category": c.get("category") or p.get("category"),
                "revenue_previous": round(revenue_prev, 2),
                "revenue_current": round(revenue_cur, 2),
                "delta": round(delta, 2),
                "delta_pct": round(delta / revenue_prev * 100, 2) if revenue_prev else None,
                "share_of_change_pct": round(delta / abs(total_delta) * 100, 2) if total_delta else None,
                "orders_previous": int(p.get("orders") or 0),
                "orders_current": int(c.get("orders") or 0),
                "units_previous": int(p.get("units") or 0),
                "units_current": int(c.get("units") or 0),
            })
        rows.sort(key=lambda x: x["delta"])
        return rows[:limit]

    def revenue_decline_analysis(self, month: str | None = None) -> dict[str, Any]:
        latest = self.as_of.strftime("%Y-%m")
        month = month or latest
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise InvalidPeriodError("month must be in YYYY-MM format.")
        start, _ = month_bounds(month)
        if start > self.as_of:
            raise InvalidPeriodError(f"{month} is after the latest data ({latest}).")
        previous = add_months(start, -1).strftime("%Y-%m")

        current = self._month_metrics(month)
        prior = self._month_metrics(previous)
        delta = current["revenue"] - prior["revenue"]
        delta_pct = (delta / prior["revenue"] * 100) if prior["revenue"] else None
        orders_delta = current["orders"] - prior["orders"]
        aov_delta = current["aov_raw"] - prior["aov_raw"]
        volume_effect = orders_delta * prior["aov_raw"]
        aov_effect = aov_delta * current["orders"]
        residual = delta - volume_effect - aov_effect

        categories = self._deltas("category", previous, month)
        products = self._deltas("product", previous, month, limit=10)
        channels = self._deltas("channel", previous, month, limit=10)
        segments = self._deltas("segment", previous, month, limit=10)
        payment_methods = self._deltas("payment", previous, month, limit=10)
        city_tiers = self._deltas("city_tier", previous, month, limit=10)
        states = self._deltas("state", previous, month, limit=10)
        churned = self.db.query(
            """
            WITH prev AS (
                SELECT customer_id, SUM(revenue) AS revenue
                FROM v_order_revenue WHERE month = :previous GROUP BY customer_id
            ), cur AS (
                SELECT DISTINCT customer_id FROM v_order_revenue WHERE month = :month
            )
            SELECT c.customer_id, c.name, c.segment, c.country, prev.revenue
            FROM prev
            JOIN customers c ON c.customer_id = prev.customer_id
            LEFT JOIN cur ON cur.customer_id = prev.customer_id
            WHERE cur.customer_id IS NULL
            ORDER BY prev.revenue DESC LIMIT 5
            """,
            {"previous": previous, "month": month},
        )
        churned = [
            {
                "customer_id": r["customer_id"],
                "name": r["name"],
                "segment": r["segment"],
                "country": r["country"],
                "revenue_previous": round(float(r["revenue"]), 2),
            }
            for r in churned
        ]

        negative_categories = sorted([c for c in categories if c["delta"] < 0], key=lambda x: x["delta"])
        positive_categories = sorted([c for c in categories if c["delta"] > 0], key=lambda x: -x["delta"])
        negative_products = sorted([p for p in products if p["delta"] < 0], key=lambda x: x["delta"])[:5]
        positive_products = sorted([p for p in products if p["delta"] > 0], key=lambda x: -x["delta"])[:5]

        summary_lines = [
            f"Revenue {_verb(delta)} from {money(prior['revenue'])} in {fmt_month(date.fromisoformat(previous + '-01'))} "
            f"to {money(current['revenue'])} in {fmt_month(date.fromisoformat(month + '-01'))} "
            f"({signed_money(delta)}, {signed_pct(delta_pct)}).",
            f"Orders {_verb(orders_delta)} from {num(prior['orders'])} to {num(current['orders'])} "
            f"({orders_delta:+d}), while average order value {_verb(aov_delta)} from "
            f"{money(prior['aov'])} to {money(current['aov'])} "
            f"({signed_money(aov_delta)}, {signed_pct(aov_delta / prior['aov'] * 100 if prior['aov'] else 0)}).",
            f"Volume accounted for {signed_money(volume_effect)} and AOV for {signed_money(aov_effect)} "
            f"of the {signed_money(delta)} change.",
        ]
        if negative_categories:
            top = negative_categories[0]
            summary_lines.append(
                f"{top['name']} accounted for {abs(top['share_of_change_pct']):.1f}% of the decline "
                f"({signed_money(top['delta'])})."
            )
        if negative_products:
            top = negative_products[0]
            summary_lines.append(
                f"The largest product-level decline was {top['name']} "
                f"({signed_money(top['delta'])})."
            )
        summary_lines.append(
            f"Discount rate moved from {pct(prior['discount_rate_pct'])} to "
            f"{pct(current['discount_rate_pct'])} "
            f"({current['discount_rate_pct'] - prior['discount_rate_pct']:+.1f} pp)."
        )
        summary_lines.append(
            f"Returning-customer orders moved from {num(prior['returning_customer_orders'])} to "
            f"{num(current['returning_customer_orders'])}; new-customer orders moved from "
            f"{num(prior['new_customer_orders'])} to {num(current['new_customer_orders'])}."
        )
        summary_lines.append(
            f"Purchasing customers moved from {num(prior['customers'])} to {num(current['customers'])}."
        )

        context_notes: list[str] = []
        if month.endswith("-09"):
            context_notes.append(
                "September falls in the pre-Diwali Shradh lull in the Indian retail calendar. "
                "Festive purchases typically shift into October, so part of the month-on-month "
                "softness can be seasonal rather than a loss of demand."
            )
        if month.endswith("-10") or month.endswith("-11"):
            context_notes.append(
                "October-November is the Diwali festive window in India, when electronics and "
                "gifting categories usually peak."
            )
        if month.endswith("-06") or month.endswith("-07"):
            context_notes.append(
                "June-July is the monsoon season, which typically softens outdoor and travel categories."
            )

        return {
            "month": month,
            "previous_month": previous,
            "current": current,
            "previous": prior,
            "change": {
                "revenue_delta": round(delta, 2),
                "revenue_delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
                "orders_delta": orders_delta,
                "orders_delta_pct": round(orders_delta / prior["orders"] * 100, 2) if prior["orders"] else None,
                "aov_delta": round(aov_delta, 2),
                "aov_delta_pct": round(aov_delta / prior["aov_raw"] * 100, 2) if prior["aov_raw"] else None,
                "discount_rate_delta_pp": round(current["discount_rate_pct"] - prior["discount_rate_pct"], 2),
                "customers_delta": current["customers"] - prior["customers"],
            },
            "decomposition": {
                "volume_effect": round(volume_effect, 2),
                "aov_effect": round(aov_effect, 2),
                "residual": round(residual, 2),
            },
            "categories": categories,
            "negative_categories": negative_categories,
            "positive_categories": positive_categories,
            "products": products,
            "negative_products": negative_products,
            "positive_products": positive_products,
            "channels": channels,
            "segments": segments,
            "payment_methods": payment_methods,
            "city_tiers": city_tiers,
            "states": states,
            "churned_customers": churned,
            "context_notes": context_notes,
            "summary_lines": summary_lines,
        }

    # ------------------------------------------------------------------
    # Charts and insights
    # ------------------------------------------------------------------
    @staticmethod
    def _chart(
        chart_type: str,
        title: str,
        subtitle: str,
        labels: list[str],
        datasets: list[dict],
        value_format: str = "currency",
        meta: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "type": chart_type,
            "title": title,
            "subtitle": subtitle,
            "labels": labels,
            "datasets": datasets,
            "value_format": value_format,
            "meta": meta or {},
        }

    def monthly_revenue_chart(self, months: int = 12) -> dict[str, Any]:
        rows = self.monthly_revenue(months)
        return self._chart(
            "line",
            "Monthly revenue",
            f"{rows[0]['full_label']} - {rows[-1]['full_label']}",
            [r["label"] for r in rows],
            [{
                "label": "Revenue",
                "data": [r["revenue"] for r in rows],
                "color": PALETTE[0],
                "fill": True,
            }],
            meta={"months": months},
        )

    def revenue_by_category_chart(self, period_key: str = "last_12_months") -> dict[str, Any]:
        rows = self.revenue_by_category(period_key)
        p = self.period(period_key)
        return self._chart(
            "bar",
            "Revenue by category",
            p.label,
            [r["category"] for r in rows],
            [{
                "label": "Revenue",
                "data": [r["revenue"] for r in rows],
                "colors": PALETTE[: len(rows)],
            }],
            meta={"period": period_key},
        )

    def payment_mix_chart(self, period_key: str = "last_12_months") -> dict[str, Any]:
        rows = self.payment_mix(period_key)
        p = self.period(period_key)
        return self._chart(
            "doughnut",
            "Revenue by payment method",
            p.label,
            [r["payment_method"] for r in rows],
            [{
                "label": "Revenue",
                "data": [r["revenue"] for r in rows],
                "colors": PALETTE[: len(rows)],
            }],
            meta={"period": period_key},
        )

    def city_tier_chart(self, period_key: str = "last_12_months") -> dict[str, Any]:
        rows = self.city_tier_mix(period_key)
        p = self.period(period_key)
        return self._chart(
            "bar",
            "Revenue by city tier",
            p.label,
            [r["city_tier"] for r in rows],
            [{
                "label": "Revenue",
                "data": [r["revenue"] for r in rows],
                "colors": PALETTE[: len(rows)],
            }],
            meta={"period": period_key},
        )

    def channel_chart(self, period_key: str = "last_12_months") -> dict[str, Any]:
        rows = self.channel_performance(period_key)
        p = self.period(period_key)
        return self._chart(
            "bar",
            "Revenue by channel",
            p.label,
            [r["channel"] for r in rows],
            [{
                "label": "Revenue",
                "data": [r["revenue"] for r in rows],
                "colors": PALETTE[: len(rows)],
            }],
            meta={"period": period_key},
        )

    def top_products_chart(self, period_key: str = "last_12_months", limit: int = 8) -> dict[str, Any]:
        rows = self.top_products(period_key, "revenue", limit)
        p = self.period(period_key)
        return self._chart(
            "bar",
            "Top products by revenue",
            p.label,
            [r["name"] if len(r["name"]) <= 28 else r["name"][:26] + "..." for r in rows],
            [{
                "label": "Revenue",
                "data": [r["revenue"] for r in rows],
                "color": PALETTE[1],
            }],
            meta={"period": period_key, "horizontal": True},
        )

    def customer_trend_chart(self, months: int = 12) -> dict[str, Any]:
        rows = self.customer_trend(months)
        return self._chart(
            "bar",
            "New vs returning customers",
            f"{rows[0]['label']} - {rows[-1]['label']}",
            [r["label"] for r in rows],
            [
                {"label": "New", "data": [r["new_customers"] for r in rows], "color": PALETTE[2]},
                {"label": "Returning", "data": [r["returning_customers"] for r in rows], "color": PALETTE[0]},
            ],
            value_format="number",
            meta={"months": months},
        )

    def deterministic_insights(self, period_key: str = "last_12_months") -> list[dict[str, Any]]:
        p = self.period(period_key)
        k = self.kpis(period_key)
        revenue = k["kpis"]["revenue"]
        orders = k["kpis"]["orders"]
        aov = k["kpis"]["aov"]
        categories = self.revenue_by_category(period_key)
        products = self.top_products(period_key, "revenue", 1)
        customers = self.customer_metrics()
        insights: list[dict[str, Any]] = []

        trend_word = "up" if (revenue["delta_pct"] or 0) >= 0 else "down"
        if revenue["delta_pct"] is not None:
            body = (
                f"Revenue reached {money(revenue['value'])} in {p.label}, "
                f"{trend_word} {abs(revenue['delta_pct']):.1f}% versus the comparison period. "
                f"Orders reached {num(orders['value'])} and average order value was {money(aov['value'])}."
            )
            severity = "positive" if (revenue["delta_pct"] or 0) >= 0 else "negative"
        else:
            body = (
                f"Revenue reached {money(revenue['value'])} across {num(orders['value'])} orders, "
                f"with an average order value of {money(aov['value'])}."
            )
            severity = "neutral"
        insights.append({"title": "Revenue performance", "body": body, "severity": severity, "metric": "revenue"})

        if categories:
            top = categories[0]
            insights.append({
                "title": "Category concentration",
                "body": (
                    f"{top['category']} generated {money(top['revenue'])}, or {top['share_pct']:.1f}% "
                    f"of revenue, across {num(top['orders'])} orders."
                ),
                "severity": "neutral",
                "metric": "category",
            })

        if products:
            top = products[0]
            insights.append({
                "title": "Best-selling product",
                "body": (
                    f"{top['name']} was the top product by revenue with {money(top['revenue'])} "
                    f"from {num(top['units'])} units and {num(top['orders'])} orders."
                ),
                "severity": "positive",
                "metric": "product",
            })

        insights.append({
            "title": "Customer health",
            "body": (
                f"{num(customers['purchasing_customers'])} customers have purchased; "
                f"{customers['repeat_rate_pct']:.1f}% are repeat buyers. "
                f"{num(customers['inactive_customers'])} customers ({customers['inactive_rate_pct']:.1f}%) "
                f"have not purchased in {customers['inactive_days']}+ days."
            ),
            "severity": "negative" if customers["inactive_rate_pct"] > 35 else "neutral",
            "metric": "customers",
        })

        payments = self.payment_mix(period_key)
        if payments:
            top = payments[0]
            upi = next((p for p in payments if p["payment_method"] == "UPI"), None)
            body = (
                f"{top['payment_method']} carried {top['share_pct']:.1f}% of revenue "
                f"({money(top['revenue'])} across {num(top['orders'])} orders)."
            )
            if upi and upi["payment_method"] != top["payment_method"]:
                body += f" UPI contributed {upi['share_pct']:.1f}%."
            insights.append({
                "title": "Payment mix",
                "body": body,
                "severity": "neutral",
                "metric": "payments",
            })

        tiers = self.city_tier_mix(period_key)
        if tiers:
            tier_map = {t["city_tier"]: t for t in tiers}
            small_towns = sum(
                t["share_pct"] for t in tiers if t["city_tier"] in {"Tier 2", "Tier 3"}
            )
            top = tiers[0]
            insights.append({
                "title": "Bharat demand",
                "body": (
                    f"{top['city_tier']} cities led with {top['share_pct']:.1f}% of revenue. "
                    f"Tier 2 and Tier 3 cities together contributed {small_towns:.1f}% "
                    f"({money(sum(t['revenue'] for t in tiers if t['city_tier'] in {'Tier 2', 'Tier 3'}))})."
                ),
                "severity": "neutral",
                "metric": "city_tier",
            })

        if self.as_of.month == 9:
            insights.append({
                "title": "Festive calendar",
                "body": (
                    f"The snapshot ends in {fmt_month(self.as_of)}, the pre-Diwali Shradh lull. "
                    "Indian festive demand typically shifts into October-November, so month-on-month "
                    "softness should be read alongside the seasonal calendar rather than as pure churn."
                ),
                "severity": "neutral",
                "metric": "seasonality",
            })

        if period_key in {"latest_month", "last_30_days"}:
            decline = self.revenue_decline_analysis()
            change = decline["change"]
            if change["revenue_delta"] < 0:
                top = decline["negative_categories"][0] if decline["negative_categories"] else None
                body = (
                    f"Revenue in {decline['month']} was {money(decline['current']['revenue'])}, "
                    f"{abs(change['revenue_delta_pct'] or 0):.1f}% below {decline['previous_month']}. "
                    f"Orders fell {abs(change['orders_delta_pct'] or 0):.1f}% and AOV fell "
                    f"{abs(change['aov_delta_pct'] or 0):.1f}%."
                )
                if top:
                    body += f" {top['name']} accounted for {money(top['delta'])} of the decline."
                insights.append({
                    "title": "Latest-month decline",
                    "body": body,
                    "severity": "negative",
                    "metric": "decline",
                })

        return insights

    def overview(self, period_key: str = "last_12_months") -> dict[str, Any]:
        p = self.period(period_key)
        quality = self.db.quality_report()
        return {
            "as_of": self.as_of.isoformat(),
            "period": p.to_dict(),
            "kpis": self.kpis(period_key),
            "monthly_revenue_chart": self.monthly_revenue_chart(12),
            "category_chart": self.revenue_by_category_chart(period_key),
            "payment_chart": self.payment_mix_chart(period_key),
            "city_tier_chart": self.city_tier_chart(period_key),
            "channel_chart": self.channel_chart(period_key),
            "top_products": self.top_products(period_key, "revenue", 6),
            "top_products_chart": self.top_products_chart(period_key, 8),
            "customer_trend_chart": self.customer_trend_chart(12),
            "customer_metrics": self.customer_metrics(),
            "payment_mix": self.payment_mix(period_key),
            "city_tier_mix": self.city_tier_mix(period_key),
            "channel_performance": self.channel_performance(period_key),
            "top_states": self.top_states(period_key, 6),
            "insights": self.deterministic_insights(period_key),
            "insight_mode": "rule",
            "business": {
                "name": "BharatCart Retail",
                "currency": "INR",
                "currency_symbol": "₹",
                "market": "India",
                "as_of": self.as_of.isoformat(),
            },
            "data_quality": {
                "generated_at": quality.get("generated_at"),
                "raw_counts": quality.get("raw_counts", {}),
                "clean_counts": quality.get("clean_counts", {}),
                "total_issues": quality.get("total_issues", 0),
                "top_issues": (quality.get("issues") or [])[:5],
                "checks": quality.get("checks", []),
                "revenue_definition": quality.get("revenue_definition"),
                "currency_definition": quality.get("currency_definition"),
            },
        }
