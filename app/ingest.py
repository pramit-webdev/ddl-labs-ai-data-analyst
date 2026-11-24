"""Data cleaning and warehouse build pipeline.

Raw CSVs are intentionally messy. This module validates and normalises them,
loads a clean star schema into SQLite, creates analytics views and writes a
machine-readable data-quality report.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import Settings, settings
from app.db import SCHEMA_VERSION, create_schema

CURRENCY_RE = re.compile(r"[₹$,\s]")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CANONICAL_COUNTRIES = {
    "india", "united arab emirates", "united states", "united kingdom",
    "singapore", "canada", "australia", "germany", "france", "netherlands",
}
COUNTRY_ALIASES = {
    "ind": "India",
    "bharat": "India",
    "india ": "India",
    "uae": "United Arab Emirates",
    "usa": "United States",
    "us": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "singapore ": "Singapore",
}
STATUS_MAP = {
    "completed": "completed",
    "complete": "completed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "returned": "returned",
    "pending": "pending",
    "processing": "pending",
}
CHANNEL_MAP = {
    "website": "Website",
    "web": "Website",
    "mobile app": "Mobile App",
    "mobile": "Mobile App",
    "app": "Mobile App",
    "marketplace": "Marketplace",
    "whatsapp": "WhatsApp",
    "retail store": "Retail Store",
    "retail": "Retail Store",
    "store": "Retail Store",
}
PAYMENT_MAP = {
    "upi": "UPI",
    "u.p.i.": "UPI",
    "credit card": "Credit Card",
    "debit card": "Debit Card",
    "net banking": "Net Banking",
    "netbanking": "Net Banking",
    "cash on delivery": "Cash on Delivery",
    "cod": "Cash on Delivery",
    "wallet": "Wallet",
}
TIER_MAP = {
    "tier 1": "Tier 1", "tier-1": "Tier 1", "tier1": "Tier 1", "1": "Tier 1",
    "metro": "Tier 1", "tier 1 city": "Tier 1",
    "tier 2": "Tier 2", "tier-2": "Tier 2", "tier2": "Tier 2", "2": "Tier 2",
    "tier 3": "Tier 3", "tier-3": "Tier 3", "tier3": "Tier 3", "3": "Tier 3",
    "international": "International",
}
SEGMENT_MAP = {"consumer": "Consumer", "smb": "SMB", "enterprise": "Enterprise"}
REQUIRED_FILES = ("customers.csv", "products.csv", "orders.csv", "order_items.csv")


class Report:
    """Collects cleaning counters and validation checks."""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self._index: dict[str, dict[str, Any]] = {}
        self.checks: list[dict[str, Any]] = []
        self.raw_counts: dict[str, int] = {}
        self.clean_counts: dict[str, int] = {}

    def add(self, code: str, count: int, table: str, action: str, severity: str = "warning") -> None:
        if count <= 0:
            return
        if code in self._index:
            self._index[code]["count"] += count
            return
        item = {
            "code": code,
            "count": int(count),
            "table": table,
            "action": action,
            "severity": severity,
        }
        self._index[code] = item
        self.issues.append(item)

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})

    def as_dict(self, as_of: str | None) -> dict[str, Any]:
        issues = sorted(self.issues, key=lambda x: (-x["count"], x["code"]))
        return {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "as_of": as_of,
            "schema_version": SCHEMA_VERSION,
            "revenue_definition": (
                "Net product revenue from completed orders only: "
                "quantity x unit_price x (1 - discount%). Shipping and taxes excluded. "
                "Cancelled, returned and pending orders are excluded."
            ),
            "currency_definition": (
                "All monetary values are Indian Rupees (INR), inclusive of line-level "
                "discounts but excluding shipping and taxes."
            ),
            "raw_counts": self.raw_counts,
            "clean_counts": self.clean_counts,
            "issues": issues,
            "total_issues": sum(i["count"] for i in issues),
            "checks": self.checks,
        }


# ---------------------------------------------------------------------------
# Primitive parsers
# ---------------------------------------------------------------------------
def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_date(value: Any) -> date | None:
    raw = text(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(value: Any) -> tuple[float | None, bool]:
    raw = text(value)
    if not raw:
        return None, False
    had_currency = bool(CURRENCY_RE.search(raw))
    cleaned = CURRENCY_RE.sub("", raw)
    try:
        return float(cleaned), had_currency
    except ValueError:
        return None, had_currency


def parse_int(value: Any) -> int | None:
    raw = text(value)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def parse_bool(value: Any) -> tuple[bool | None, bool]:
    raw = text(value).lower()
    if raw in {"true", "1", "yes", "y", "active"}:
        return True, False
    if raw in {"false", "0", "no", "n", "inactive"}:
        return False, False
    if not raw:
        return None, True
    return None, True


def normalise_country(value: Any) -> tuple[str | None, bool]:
    raw = text(value)
    if not raw:
        return None, False
    key = re.sub(r"\s+", " ", raw.lower())
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key], True
    if key in CANONICAL_COUNTRIES:
        canonical = " ".join(part.capitalize() for part in key.split())
        if canonical == "United States Of America":
            canonical = "United States"
        return canonical, canonical != raw
    return raw.title(), True


def dedupe(rows: list[dict], key: str) -> tuple[list[dict], int]:
    seen: set[str] = set()
    out: list[dict] = []
    removed = 0
    for row in rows:
        value = text(row.get(key))
        if not value:
            out.append(row)  # handled later with an explicit missing-id counter
            continue
        if value in seen:
            removed += 1
            continue
        seen.add(value)
        out.append(row)
    return out, removed


# ---------------------------------------------------------------------------
# Readers and cleaners
# ---------------------------------------------------------------------------
def read_csv(path: Path) -> list[dict]:
    import pandas as pd

    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    return df.to_dict(orient="records")


def clean_customers(rows: list[dict], report: Report) -> list[dict]:
    report.raw_counts["customers"] = len(rows)
    rows, duplicates = dedupe(rows, "customer_id")
    report.add("duplicate_customers_removed", duplicates, "customers", "Kept the first record per customer_id.")

    cleaned: list[dict] = []
    missing_ids = missing_email = invalid_email = normalized_country = normalized_segment = 0
    missing_names = invalid_signup = normalized_tier = 0
    for row in rows:
        cid = text(row.get("customer_id"))
        if not cid:
            missing_ids += 1
            continue
        name = text(row.get("name"))
        if not name:
            name = f"Customer {cid}"
            missing_names += 1
        email = text(row.get("email")).lower()
        if not email:
            missing_email += 1
            email = None
        elif not EMAIL_RE.match(email):
            invalid_email += 1
            email = None

        country, changed = normalise_country(row.get("country"))
        if changed:
            normalized_country += 1
        segment_raw = text(row.get("segment"))
        segment = SEGMENT_MAP.get(segment_raw.lower())
        if segment is None:
            segment = "Unknown" if segment_raw else "Consumer"
            normalized_segment += 1
        elif segment != segment_raw:
            normalized_segment += 1

        tier_raw = text(row.get("city_tier"))
        city_tier = TIER_MAP.get(tier_raw.lower())
        if city_tier is None:
            city_tier = "Tier 1" if country == "India" else "International"
            normalized_tier += 1
        elif city_tier != tier_raw:
            normalized_tier += 1

        signup = parse_date(row.get("signup_date"))
        if signup is None:
            invalid_signup += 1
        cleaned.append({
            "customer_id": cid,
            "name": name,
            "email": email,
            "country": country,
            "state": text(row.get("state")).title() or None,
            "city": text(row.get("city")).title() or None,
            "city_tier": city_tier,
            "segment": segment,
            "signup_date": signup.isoformat() if signup else None,
        })

    report.add("missing_customer_ids_dropped", missing_ids, "customers", "Dropped rows without a customer_id.", "error")
    report.add("missing_customer_names_filled", missing_names, "customers", "Generated a placeholder name.")
    report.add("missing_customer_emails", missing_email, "customers", "Kept the customer; email stored as NULL.")
    report.add("invalid_customer_emails", invalid_email, "customers", "Replaced malformed email with NULL.")
    report.add("normalized_countries", normalized_country, "customers", "Mapped aliases/casing to canonical country names.")
    report.add("normalized_segments", normalized_segment, "customers", "Mapped segment casing; unknown values kept as 'Unknown'.")
    report.add("normalized_city_tiers", normalized_tier, "customers", "Mapped tier aliases to Tier 1 / Tier 2 / Tier 3 / International.")
    report.add("invalid_signup_dates", invalid_signup, "customers", "Kept customer; signup_date stored as NULL.")
    report.clean_counts["customers"] = len(cleaned)
    return cleaned


def clean_products(rows: list[dict], report: Report) -> list[dict]:
    report.raw_counts["products"] = len(rows)
    rows, duplicates = dedupe(rows, "product_id")
    report.add("duplicate_products_removed", duplicates, "products", "Kept the first record per product_id.")

    cleaned: list[dict] = []
    missing_ids = missing_names = missing_categories = invalid_prices = 0
    currency_values = normalized_bools = invalid_dates = 0
    for row in rows:
        pid = text(row.get("product_id"))
        if not pid:
            missing_ids += 1
            continue
        name = text(row.get("name"))
        if not name:
            name = f"Product {pid}"
            missing_names += 1
        category = text(row.get("category"))
        if not category:
            category = "Uncategorized"
            missing_categories += 1
        price, currency = parse_money(row.get("unit_price"))
        if currency:
            currency_values += 1
        cost, cost_currency = parse_money(row.get("unit_cost"))
        if cost_currency:
            currency_values += 1
        if price is None or price <= 0:
            invalid_prices += 1
            continue
        if cost is None or cost < 0:
            cost = round(price * 0.55, 2)
        launch = parse_date(row.get("launch_date"))
        if launch is None:
            invalid_dates += 1
        active, changed = parse_bool(row.get("active"))
        if changed:
            normalized_bools += 1
        if active is None:
            active = True
        cleaned.append({
            "product_id": pid,
            "name": name,
            "category": category,
            "unit_price": round(float(price), 2),
            "unit_cost": round(float(cost), 2),
            "launch_date": launch.isoformat() if launch else None,
            "active": 1 if active else 0,
        })

    report.add("missing_product_ids_dropped", missing_ids, "products", "Dropped rows without a product_id.", "error")
    report.add("missing_product_names_filled", missing_names, "products", "Generated a placeholder product name.")
    report.add("missing_product_categories_filled", missing_categories, "products", "Filled blank categories with 'Uncategorized'.")
    report.add("invalid_product_prices_dropped", invalid_prices, "products", "Dropped products with missing/non-positive price.", "error")
    report.add("currency_values_parsed", currency_values, "products", "Parsed '₹'/'$' prefixes and thousands separators.")
    report.add("invalid_product_dates", invalid_dates, "products", "Kept product; launch_date stored as NULL.")
    report.add("normalized_booleans", normalized_bools, "products", "Normalised active flags to 0/1.")
    report.clean_counts["products"] = len(cleaned)
    return cleaned


def clean_orders(rows: list[dict], report: Report, customer_ids: set[str]) -> list[dict]:
    report.raw_counts["orders"] = len(rows)
    rows, duplicates = dedupe(rows, "order_id")
    report.add("duplicate_orders_removed", duplicates, "orders", "Kept the first record per order_id.")

    cleaned: list[dict] = []
    missing_ids = unknown_customers = invalid_dates = invalid_statuses = 0
    normalized_status = normalized_channel = invalid_shipping = normalized_payment = 0
    for row in rows:
        oid = text(row.get("order_id"))
        if not oid:
            missing_ids += 1
            continue
        cid = text(row.get("customer_id"))
        if cid not in customer_ids:
            unknown_customers += 1
            continue
        order_date = parse_date(row.get("order_date"))
        if order_date is None:
            invalid_dates += 1
            continue
        status_raw = text(row.get("status")).lower()
        status = STATUS_MAP.get(status_raw)
        if status is None:
            invalid_statuses += 1
            continue
        if status != text(row.get("status")):
            normalized_status += 1
        channel_raw = text(row.get("channel"))
        channel = CHANNEL_MAP.get(channel_raw.lower())
        if channel is None:
            channel = "Other"
            normalized_channel += 1
        elif channel != channel_raw:
            normalized_channel += 1
        payment_raw = text(row.get("payment_method"))
        payment = PAYMENT_MAP.get(payment_raw.lower())
        if payment is None:
            payment = "Other"
            normalized_payment += 1
        elif payment != payment_raw:
            normalized_payment += 1
        shipping, _ = parse_money(row.get("shipping_cost"))
        if shipping is None or shipping < 0:
            shipping = 0.0
            invalid_shipping += 1
        cleaned.append({
            "order_id": oid,
            "customer_id": cid,
            "order_date": order_date.isoformat(),
            "status": status,
            "channel": channel,
            "payment_method": payment,
            "shipping_cost": round(float(shipping), 2),
        })

    report.add("missing_order_ids_dropped", missing_ids, "orders", "Dropped rows without an order_id.", "error")
    report.add("unknown_customers_dropped", unknown_customers, "orders", "Dropped orders referencing a missing customer.", "error")
    report.add("invalid_order_dates_dropped", invalid_dates, "orders", "Dropped orders with unparseable dates.", "error")
    report.add("invalid_order_statuses_dropped", invalid_statuses, "orders", "Dropped orders with unknown status values.", "error")
    report.add("normalized_statuses", normalized_status, "orders", "Mapped status casing/synonyms to completed/cancelled/returned/pending.")
    report.add("normalized_channels", normalized_channel, "orders", "Mapped channel casing/synonyms to canonical channels.")
    report.add("normalized_payment_methods", normalized_payment, "orders", "Mapped UPI/COD/card aliases to canonical payment methods.")
    report.add("invalid_shipping_costs", invalid_shipping, "orders", "Replaced invalid shipping costs with 0.")
    report.clean_counts["orders"] = len(cleaned)
    return cleaned


def clean_items(rows: list[dict], report: Report, order_ids: set[str], products: dict[str, dict]) -> list[dict]:
    report.raw_counts["order_items"] = len(rows)
    rows, duplicates = dedupe(rows, "item_id")
    report.add("duplicate_order_items_removed", duplicates, "order_items", "Kept the first record per item_id.")

    cleaned: list[dict] = []
    missing_ids = orphan_orders = orphan_products = invalid_quantities = 0
    imputed_prices = missing_discounts = normalized_discounts = currency_values = 0
    for row in rows:
        iid = text(row.get("item_id"))
        if not iid:
            missing_ids += 1
            continue
        oid = text(row.get("order_id"))
        if oid not in order_ids:
            orphan_orders += 1
            continue
        pid = text(row.get("product_id"))
        product = products.get(pid)
        if product is None:
            orphan_products += 1
            continue
        qty = parse_int(row.get("quantity"))
        if qty is None or qty <= 0:
            invalid_quantities += 1
            continue
        price, currency = parse_money(row.get("unit_price"))
        if currency:
            currency_values += 1
        if price is None or price <= 0:
            price = product["unit_price"]
            imputed_prices += 1
        discount_raw = text(row.get("discount_pct"))
        if not discount_raw:
            discount = 0.0
            missing_discounts += 1
        else:
            try:
                discount = float(discount_raw.replace("%", "").strip())
            except ValueError:
                discount = 0.0
                normalized_discounts += 1
        if discount < 0 or discount > 90:
            discount = min(90.0, max(0.0, discount))
            normalized_discounts += 1
        line_revenue = round(qty * float(price) * (1 - discount / 100.0), 2)
        cleaned.append({
            "item_id": iid,
            "order_id": oid,
            "product_id": pid,
            "quantity": qty,
            "unit_price": round(float(price), 2),
            "discount_pct": round(float(discount), 2),
            "line_revenue": line_revenue,
        })

    report.add("missing_item_ids_dropped", missing_ids, "order_items", "Dropped rows without an item_id.", "error")
    report.add("orphan_order_items_dropped", orphan_orders, "order_items", "Dropped items whose order_id does not exist.", "error")
    report.add("orphan_product_items_dropped", orphan_products, "order_items", "Dropped items whose product_id does not exist.", "error")
    report.add("invalid_item_quantities_dropped", invalid_quantities, "order_items", "Dropped items with quantity <= 0.", "error")
    report.add("imputed_item_prices", imputed_prices, "order_items", "Used the catalogue price when item price was invalid.")
    report.add("missing_discounts_filled", missing_discounts, "order_items", "Filled missing discounts with 0%.")
    report.add("normalized_discounts", normalized_discounts, "order_items", "Clamped discounts to 0-90%.")
    report.add("currency_values_parsed", currency_values, "order_items", "Parsed '₹'/'$' prefixes and thousands separators.")
    report.clean_counts["order_items"] = len(cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Warehouse build
# ---------------------------------------------------------------------------
def _insert_many(conn: sqlite3.Connection, table: str, columns: list[str], rows: Iterable[dict]) -> None:
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(row.get(c) for c in columns) for row in rows])


def build_warehouse(cfg: Settings = settings, *, quiet: bool = False) -> dict[str, Any]:
    missing = [f for f in REQUIRED_FILES if not (cfg.raw_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            "Missing raw data files: "
            + ", ".join(missing)
            + f". Run `python scripts/generate_sample_data.py` to create them in {cfg.raw_dir}."
        )

    report = Report()
    raw = {name: read_csv(cfg.raw_dir / name) for name in REQUIRED_FILES}

    customers = clean_customers(raw["customers.csv"], report)
    products = clean_products(raw["products.csv"], report)
    customer_ids = {c["customer_id"] for c in customers}
    product_map = {p["product_id"]: p for p in products}
    orders = clean_orders(raw["orders.csv"], report, customer_ids)
    order_ids = {o["order_id"] for o in orders}
    items = clean_items(raw["order_items.csv"], report, order_ids, product_map)

    as_of = max((o["order_date"] for o in orders if o["status"] == "completed"), default=None)

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = cfg.db_path.with_name(cfg.db_path.name + ".tmp")
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(tmp_path) + suffix)
        if candidate.exists():
            candidate.unlink()

    conn = sqlite3.connect(tmp_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = DELETE")
        create_schema(conn)
        _insert_many(conn, "customers",
                     ["customer_id", "name", "email", "country", "state", "city", "city_tier", "segment", "signup_date"], customers)
        _insert_many(conn, "products",
                     ["product_id", "name", "category", "unit_price", "unit_cost", "launch_date", "active"], products)
        _insert_many(conn, "orders",
                     ["order_id", "customer_id", "order_date", "status", "channel", "payment_method", "shipping_cost"], orders)
        _insert_many(conn, "order_items",
                     ["item_id", "order_id", "product_id", "quantity", "unit_price", "discount_pct", "line_revenue"], items)

        # Validation checks against the loaded database.
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        report.check("referential_integrity", not fk_errors,
                     "All order and item foreign keys resolve." if not fk_errors else f"{len(fk_errors)} violations.")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        report.check("database_integrity", integrity == "ok", f"SQLite integrity_check: {integrity}.")
        negative = conn.execute("SELECT COUNT(*) FROM order_items WHERE line_revenue < 0").fetchone()[0]
        report.check("non_negative_revenue", negative == 0, f"{negative} negative line revenue rows.")
        orphan = conn.execute(
            "SELECT COUNT(*) FROM order_items oi LEFT JOIN orders o ON o.order_id = oi.order_id WHERE o.order_id IS NULL"
        ).fetchone()[0]
        report.check("no_orphan_items", orphan == 0, f"{orphan} orphan items.")
        completed = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'").fetchone()[0]
        report.check("completed_orders_present", completed > 0, f"{completed} completed orders available.")
        report.check("date_coverage", as_of is not None, f"Data through {as_of}.")

        clean_report = report.as_dict(as_of)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "business_name": "BharatCart Retail",
            "currency": "INR",
            "currency_symbol": "₹",
            "dataset_as_of": as_of,
            "ingested_at": clean_report["generated_at"],
            "clean_row_counts": report.clean_counts,
            "raw_row_counts": report.raw_counts,
        }
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in meta.items()],
        )
        conn.execute(
            "INSERT INTO pipeline_runs (run_at, report_json) VALUES (?, ?)",
            (clean_report["generated_at"], json.dumps(clean_report)),
        )
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, cfg.db_path)
    cfg.quality_report_path.write_text(json.dumps(clean_report, indent=2) + "\n", encoding="utf-8")

    if not quiet:
        print(f"Warehouse built: {cfg.db_path}")
        print(f"Rows: {report.clean_counts}")
        print(f"Data through: {as_of}")
        print(f"Quality report: {cfg.quality_report_path}")
        for issue in clean_report["issues"]:
            print(f"  - {issue['code']}: {issue['count']} ({issue['table']})")
    return meta


def main() -> int:
    try:
        build_warehouse(settings)
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
