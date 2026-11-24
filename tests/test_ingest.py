"""Ingestion / data-quality tests."""

from __future__ import annotations

from app import ingest
from app.db import db


def test_expected_row_counts_match_warehouse(expected, quality):
    counts = db.counts()
    for key, value in expected["row_counts"].items():
        assert counts[key] == value, (key, counts[key], value)
    for key in ("customers", "products"):
        assert quality["clean_counts"][key] == expected["row_counts"][key]
    assert quality["clean_counts"]["orders"] > expected["row_counts"]["orders"]


def test_all_quality_checks_pass(quality):
    assert quality["checks"], "No validation checks were recorded"
    assert all(check["status"] == "pass" for check in quality["checks"])


def test_raw_rows_exceed_clean_rows(quality):
    for key in ("customers", "products", "orders", "order_items"):
        assert quality["raw_counts"][key] > quality["clean_counts"][key]


def test_dirt_repairs_are_recorded(quality):
    codes = {issue["code"] for issue in quality["issues"]}
    assert "duplicate_customers_removed" in codes
    assert "duplicate_orders_removed" in codes
    assert "orphan_order_items_dropped" in codes
    assert "normalized_statuses" in codes
    assert "normalized_payment_methods" in codes
    assert "normalized_city_tiers" in codes
    assert "currency_values_parsed" in codes
    assert quality["total_issues"] > 1000


def test_revenue_definition_is_indian_rupees(quality):
    assert "completed orders" in quality["revenue_definition"]
    assert "discount" in quality["revenue_definition"]
    assert "Shipping" in quality["revenue_definition"]
    assert "INR" in quality["currency_definition"]


def test_primitive_parsers():
    assert ingest.parse_date("30/09/2025").isoformat() == "2025-09-30"
    assert ingest.parse_date("2025-09-30").isoformat() == "2025-09-30"
    assert ingest.parse_date("not-a-date") is None
    assert ingest.parse_money("₹1,23,456.78")[0] == 123456.78
    assert ingest.parse_money("$1,234.50")[0] == 1234.5
    assert ingest.normalise_country("IND")[0] == "India"
    assert ingest.normalise_country("uae")[0] == "United Arab Emirates"
    assert ingest.normalise_country("bharat")[0] == "India"


def test_status_and_payment_normalisation_maps():
    assert ingest.STATUS_MAP["complete"] == "completed"
    assert ingest.PAYMENT_MAP["u.p.i."] == "UPI"
    assert ingest.PAYMENT_MAP["cod"] == "Cash on Delivery"
    assert ingest.CHANNEL_MAP["whatsapp"] == "WhatsApp"
    assert ingest.TIER_MAP["tier-2"] == "Tier 2"
