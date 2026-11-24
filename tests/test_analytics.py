"""Analytics service tests against the independently computed oracle."""

from __future__ import annotations

from datetime import date

import pytest

from app.analytics import AnalyticsService, compact_money, money, signed_money
from app.errors import InvalidPeriodError


def close(actual, expected, tol=1.0):
    assert abs(float(actual) - float(expected)) <= tol, (actual, expected)


def test_as_of_is_data_anchored(analytics: AnalyticsService, expected):
    assert analytics.as_of == date.fromisoformat(expected["as_of"])
    assert analytics.as_of.isoformat() == "2025-09-30"


def test_all_time_metrics_match_oracle(analytics: AnalyticsService, expected):
    metrics = analytics.kpis("all_time")["current"]
    for key in ("revenue", "orders", "customers", "aov", "units"):
        close(metrics[key], expected["totals"][key])


def test_latest_and_previous_month_match_oracle(analytics: AnalyticsService, expected):
    for period_key, oracle_key in (("latest_month", "latest_month"), ("previous_month", "previous_month")):
        metrics = analytics.kpis(period_key)["current"]
        close(metrics["revenue"], expected[oracle_key]["revenue"])
        assert metrics["orders"] == expected[oracle_key]["orders"]
        assert metrics["customers"] == expected[oracle_key]["customers"]
        assert metrics["units"] == expected[oracle_key]["units"]


def test_top_product_and_category_match_oracle(analytics: AnalyticsService, expected):
    product = analytics.top_products("all_time", "revenue", 1)[0]
    assert product["product_id"] == expected["top_product"]["product_id"]
    assert product["name"] == expected["top_product"]["name"]
    close(product["revenue"], expected["top_product"]["revenue"])
    assert product["units"] == expected["top_product"]["units"]

    category = analytics.revenue_by_category("all_time")[0]
    assert category["category"] == expected["top_category"]["category"]
    close(category["revenue"], expected["top_category"]["revenue"])


def test_monthly_series_matches_oracle(analytics: AnalyticsService, expected):
    rows = analytics.monthly_revenue(24)
    for row in rows:
        close(row["revenue"], expected["monthly_revenue"][row["month"]])
    assert rows[-1]["month"] == "2025-09"


def test_payment_mix_matches_oracle(analytics: AnalyticsService, expected):
    mix = analytics.payment_mix("all_time")
    oracle = {r["payment_method"]: r for r in expected["payment_mix"]}
    for row in mix:
        close(row["revenue"], oracle[row["payment_method"]]["revenue"])
        assert row["orders"] == oracle[row["payment_method"]]["orders"]
    assert abs(sum(r["share_pct"] for r in mix) - 100) < 0.1


def test_city_tier_mix_matches_oracle(analytics: AnalyticsService, expected):
    mix = analytics.city_tier_mix("all_time")
    oracle = {r["city_tier"]: r for r in expected["city_tier_mix"]}
    for row in mix:
        close(row["revenue"], oracle[row["city_tier"]]["revenue"], tol=2.0)
        assert row["orders"] == oracle[row["city_tier"]]["orders"]
    assert any(r["city_tier"] == "Tier 2" for r in mix)
    assert any(r["city_tier"] == "Tier 3" for r in mix)


def test_customer_metrics(analytics: AnalyticsService, expected):
    metrics = analytics.customer_metrics(90)
    assert metrics["total_customers"] == expected["row_counts"]["customers"]
    assert metrics["inactive_customers"] == expected["inactive_customers_90d"]
    assert metrics["never_purchased"] == expected["never_purchased_customers"]
    assert 0 < metrics["repeat_rate_pct"] < 100


def test_decline_analysis_is_multi_step(analytics: AnalyticsService):
    result = analytics.revenue_decline_analysis()
    assert result["month"] == "2025-09"
    assert result["previous_month"] == "2025-08"
    change = result["change"]
    assert change["revenue_delta"] < 0
    assert change["orders_delta"] > 0
    assert change["aov_delta"] < 0

    decomp = result["decomposition"]
    close(decomp["volume_effect"] + decomp["aov_effect"] + decomp["residual"], change["revenue_delta"], 0.05)
    assert decomp["aov_effect"] < 0

    assert result["negative_categories"]
    assert result["negative_products"]
    assert result["channels"]
    assert result["payment_methods"]
    assert result["city_tiers"]
    assert result["states"]
    assert result["context_notes"], "September should carry festive-calendar context"
    assert len(result["summary_lines"]) >= 6


def test_low_performing_products_include_unsold(analytics: AnalyticsService):
    rows = analytics.low_performing_products("all_time", 5)
    assert len(rows) == 5
    assert all(row["revenue"] >= 0 for row in rows)
    assert rows[0]["revenue"] <= rows[-1]["revenue"]


def test_period_validation(analytics: AnalyticsService):
    with pytest.raises(InvalidPeriodError):
        analytics.period("last_week")
    with pytest.raises(InvalidPeriodError):
        analytics.revenue_decline_analysis("2026-01")
    with pytest.raises(InvalidPeriodError):
        analytics.top_products("all_time", "profit", 5)


def test_period_bounds_are_anchored(analytics: AnalyticsService):
    latest = analytics.period("latest_month")
    previous = analytics.period("previous_month")
    assert latest.start.isoformat() == "2025-09-01"
    assert latest.end.isoformat() == "2025-09-30"
    assert previous.end == latest.start.replace(day=1) - __import__("datetime").timedelta(days=1)


def test_indian_currency_formatting():
    assert money(5443685.86) == "₹54,43,685.86"
    assert signed_money(-1071761.97) == "-₹10,71,761.97"
    assert compact_money(151007170.57) == "₹15.10 Cr"
    assert compact_money(5443685.86) == "₹54.44 L"


def test_charts_have_renderable_specs(analytics: AnalyticsService):
    for chart in (
        analytics.monthly_revenue_chart(),
        analytics.revenue_by_category_chart(),
        analytics.payment_mix_chart(),
        analytics.city_tier_chart(),
        analytics.channel_chart(),
        analytics.customer_trend_chart(),
        analytics.top_products_chart(),
    ):
        assert chart["labels"]
        assert chart["datasets"]
        assert chart["datasets"][0]["data"]
        assert chart["type"] in {"line", "bar", "doughnut"}
