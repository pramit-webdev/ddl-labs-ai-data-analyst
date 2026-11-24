"""Agent, grounding and SQL safety tests."""

from __future__ import annotations

import pytest

from app.agent.grounding import extract_answer_numbers, validate_answer
from app.agent.orchestrator import AgentOrchestrator
from app.agent.planner import classify
from app.agent.query_guard import SafeQueryError, run_safe_query, validate_sql
from app.agent.tools import ToolRegistry
from app.analytics import AnalyticsService


def test_grounding_accepts_tool_figures():
    evidence = {"revenue": 5443685.86, "orders": 526, "delta_pct": -16.45}
    answer = "Revenue was ₹54,43,685.86 across 526 orders, down -16.4% over the last 12 months."
    result = validate_answer(answer, evidence, "How did we do?")
    assert result.ok
    assert result.checked >= 3
    assert result.to_dict()["status"] == "verified"


def test_grounding_rejects_invented_figure():
    evidence = {"revenue": 5443685.86, "orders": 526}
    result = validate_answer("Revenue was ₹99,99,999.00 and we served 42,000 orders.", evidence)
    assert not result.ok
    assert result.ungrounded


def test_number_extraction_ignores_labels():
    found = extract_answer_numbers("last 12 months, 90+ days, 5 products, 526 orders, 56.5%")
    values = [item[1] for item in found]
    assert 12 not in values
    assert 90 not in values
    assert 5 not in values
    assert 526 in values
    assert 56.5 in values


def test_sql_guard_blocks_writes_and_unapproved_objects():
    assert validate_sql("SELECT revenue FROM v_order_revenue LIMIT 10").endswith("LIMIT 10")
    with pytest.raises(SafeQueryError):
        validate_sql("DELETE FROM orders")
    with pytest.raises(SafeQueryError):
        validate_sql("SELECT * FROM meta")
    with pytest.raises(SafeQueryError):
        validate_sql("SELECT 1; DROP TABLE orders")
    with pytest.raises(SafeQueryError):
        validate_sql("SELECT * FROM orders -- comment")


def test_safe_query_executes_read_only_sql(analytics: AnalyticsService):
    result = run_safe_query(
        "SELECT COUNT(*) AS n FROM orders WHERE status = 'completed'", analytics.db
    )
    assert result["row_count"] == 1
    assert result["rows"][0][0] > 0
    with pytest.raises(SafeQueryError):
        run_safe_query("SELECT * FROM meta", analytics.db)


def test_tool_registry_validates_arguments(analytics: AnalyticsService):
    registry = ToolRegistry(analytics)
    bad = registry.execute("get_top_products", {"limit": 999, "metric": "profit"})
    assert not bad.ok
    assert bad.error

    unknown = registry.execute("delete_everything", {})
    assert not unknown.ok
    assert "Unknown tool" in unknown.error

    good = registry.execute("get_sales_metrics", {"period": "latest_month"})
    assert good.ok
    assert "revenue" in good.summary.lower()


def test_tool_registry_includes_indian_dimensions(analytics: AnalyticsService):
    names = {schema["function"]["name"] for schema in ToolRegistry.schemas()}
    assert "get_payment_mix" in names
    assert "get_city_tier_mix" in names
    assert "get_channel_performance" in names
    assert "get_top_states" in names
    assert "get_revenue_decline_analysis" in names
    assert "query_data" in names


def test_planner_classifies_key_questions():
    assert classify("Why did revenue decline last month?").name == "decline"
    assert classify("How much comes from UPI?").name == "payment_mix"
    assert classify("How are Tier 2 cities doing?").name == "city_tier_mix"
    assert classify("Which state leads?").name == "top_states"
    assert classify("Forecast next quarter revenue.").name == "unsupported"
    assert not classify("Forecast next quarter revenue.").supported
    assert classify("Generate a doughnut chart of payments").chart_metric == "payment_mix"


def test_offline_agent_answer_is_supported_and_verified(analytics: AnalyticsService):
    agent = AgentOrchestrator(analytics=analytics)
    result = agent.ask("Why did revenue decline last month?")
    assert result.mode == "offline"
    assert result.supported
    assert result.grounding["status"] == "verified"
    assert result.tool_trace[0]["name"] == "get_revenue_decline_analysis"
    assert "Electronics" in result.answer
    assert "₹" in result.answer


def test_offline_agent_handles_unsupported_question(analytics: AnalyticsService):
    agent = AgentOrchestrator(analytics=analytics)
    result = agent.ask("Predict next year's revenue.")
    assert not result.supported
    assert "can't answer" in result.answer.lower()
    assert result.grounding["status"] == "verified"


def test_chart_question_produces_chart(analytics: AnalyticsService):
    agent = AgentOrchestrator(analytics=analytics)
    result = agent.ask("Generate a chart of revenue by category")
    assert result.chart is not None
    assert result.chart["type"] == "bar"
    assert result.chart["labels"]
